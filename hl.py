"""Shared helpers: Binance USDT-M Futures PUBLIC market data (read-only, no keys) + indicators.
Drop-in replacement of the Hyperliquid hl.py: same function names/outputs so the strategy code is unchanged.
Coin name = Binance symbol without the 'USDT' suffix (BTCUSDT -> BTC, 1000PEPEUSDT -> 1000PEPE)."""
import requests,time,threading,pandas as pd,numpy as np,os
BASE=os.environ.get('BNB_DATA_BASE') or 'https://fapi.binance.com'      # optional alternate FAPI-compatible host (e.g. testnet); default unchanged
_S=requests.Session();_L=threading.Lock()
IV_MS={'1m':60000,'3m':180000,'5m':300000,'15m':900000,'30m':1800000,'1h':3600000,'2h':7200000,'4h':14400000,'6h':21600000,'12h':43200000,'1d':86400000,'1w':604800000}
LEV_PROVIDER=None          # set by bnx.BNX when an API key is loaded: callable() -> {coin: max leverage} (GET /fapi/v1/leverageBracket, signed)
DEFAULT_MAX_LEV=20         # without a key (dry-run/public): Binance max leverage is not public -> assumed >= 5x (checked again live via leverageBracket)
def get(path,params=None,retry=4):
    last=None
    for i in range(retry):
        try:
            r=_S.get(BASE+path,params=params,timeout=20)
            if r.status_code==200:return r.json()
            last=f'HTTP {r.status_code} {r.text[:120]}'
            if r.status_code in(418,429):time.sleep(30+30*i);continue
        except Exception as ex:last=type(ex).__name__
        time.sleep(2+3*i)
    raise RuntimeError(f'BNB API fail {path} {last}')
def sym(coin):return coin+'USDT'
def coin_of(symbol):return symbol[:-4] if symbol.endswith('USDT') else symbol
_EI={'t':0,'d':None}
def exchange_info(max_age=3600):
    if _EI['d'] is None or time.time()-_EI['t']>max_age:_EI['d']=get('/fapi/v1/exchangeInfo');_EI['t']=time.time()
    return _EI['d']
# Vien 2026-10-27: drop commodity (gold etc.) and equity/TradFi names from the crypto trade universe.
# TRADIFI_PERPETUAL already fails the PERPETUAL contractType check; still filter by underlyingType + gold RWAs
# so a Binance relabel cannot put them back on the shortlist. Open positions on excluded coins stay managed via candles/mids.
TRADFI_ASSET_TYPES=frozenset({'COMMODITY','EQUITY','CN_EQUITY','HK_EQUITY','KR_EQUITY','FX'})
GOLD_RWA=frozenset({'PAXG','XAUT'})  # tokenized gold (COIN/RWA), treated as commodity per Vien
def is_commodity_or_equity(coin, info=None):
    """True for TradFi stock/commodity underlyings and gold RWAs (PAXG/XAUT)."""
    c=str(coin).upper()
    if c in GOLD_RWA:return True
    try:
        ei=info or exchange_info()
        for s in ei['symbols']:
            if s.get('symbol')==c+'USDT':
                if s.get('underlyingType') in TRADFI_ASSET_TYPES:return True
                if 'TradFi' in (s.get('underlyingSubType') or []):return True
                return False
    except Exception:pass
    return False
def perp_symbols():
    """USDT-margined PERPETUAL contracts currently TRADING (not delisted/settling), crypto-only (no gold RWA / TradFi)."""
    return [s for s in exchange_info()['symbols'] if s.get('contractType')=='PERPETUAL' and s.get('quoteAsset')=='USDT' and s.get('marginAsset')=='USDT' and s.get('status')=='TRADING' and s['symbol'].endswith('USDT') and coin_of(s['symbol']) not in GOLD_RWA and s.get('underlyingType') not in TRADFI_ASSET_TYPES and 'TradFi' not in (s.get('underlyingSubType') or [])]
def candles(coin,interval='4h',days=400):
    """Same output as the HL version: index = bar OPEN time in Vietnam time (UTC+7), columns o h l c v closed."""
    now=int(time.time()*1000);start=int(now-days*86400000);step=IV_MS[interval];rows=[]
    while True:
        d=get('/fapi/v1/klines',{'symbol':sym(coin),'interval':interval,'startTime':start,'endTime':now,'limit':1500})
        if not d:break
        rows+=d
        if len(d)<1500:break
        start=int(d[-1][0])+step
        if start>now:break
        time.sleep(0.15)
    if not rows:return None
    x=pd.DataFrame([[int(r[0]),int(r[6]),float(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[5])] for r in rows],columns=['t','T','o','h','l','c','v']).drop_duplicates('t')
    x.index=pd.to_datetime(x.t,unit='ms')+pd.Timedelta(hours=7)   # Vietnam time (UTC+7)
    x['closed']=x['T']<now
    return x[['o','h','l','c','v','closed']]
def mids():
    """coin -> mid price (best bid+ask)/2 for every USDT perpetual (HL allMids equivalent)."""
    out={}
    for b in get('/fapi/v1/ticker/bookTicker'):
        s=b['symbol']
        if not s.endswith('USDT'):continue
        bp,ap=float(b['bidPrice'] or 0),float(b['askPrice'] or 0)
        if bp>0 and ap>0:out[coin_of(s)]=(bp+ap)/2
    return out
def max_lev_map():
    if LEV_PROVIDER:
        try:return LEV_PROVIDER()
        except Exception:pass
    return {}
def meta_ctx(oi_vol_min=1e6):
    """HL 'metaAndAssetCtxs' equivalent: ([{'name','maxLeverage','isDelisted'}], [{'dayNtlVlm','markPx','openInterest'}]).
    dayNtlVlm = 24h quote volume (USDT); openInterest in coins (x markPx = USD), fetched only for coins with 24h volume >= oi_vol_min (others get 0: they fail vol_min anyway)."""
    syms=perp_symbols();tk={t['symbol']:t for t in get('/fapi/v1/ticker/24hr')};pi={p['symbol']:p for p in get('/fapi/v1/premiumIndex')};ML=max_lev_map()
    U=[];C=[]
    for s in syms:
        n=s['symbol'];t=tk.get(n,{});vol=float(t.get('quoteVolume') or 0);mk=float((pi.get(n) or {}).get('markPrice') or t.get('lastPrice') or 0)
        oi=0.0
        if vol>=oi_vol_min:
            try:oi=float(get('/fapi/v1/openInterest',{'symbol':n})['openInterest']);time.sleep(0.05)
            except Exception:oi=0.0
        c=coin_of(n);U.append(dict(name=c,maxLeverage=int(ML.get(c,DEFAULT_MAX_LEV)),isDelisted=False));C.append(dict(dayNtlVlm=vol,markPx=mk,openInterest=oi))
    return [dict(universe=U),C]
def post(body,retry=4):
    """Compatibility shim for code written against the HL /info API. Supports metaAndAssetCtxs and allMids only."""
    t=body.get('type')
    if t=='metaAndAssetCtxs':return meta_ctx()
    if t=='allMids':return {k:str(v) for k,v in mids().items()}
    raise RuntimeError(f'hl.post shim: unsupported type {t}')
def meta():
    u=meta_ctx();return [(a['name'],float(c.get('dayNtlVlm',0)),a.get('maxLeverage')) for a,c in zip(u[0]['universe'],u[1])]
def bb(x,n=20,k=2):
    x=x.copy();m=x.c.rolling(n).mean();sd=x.c.rolling(n).std(ddof=0)
    x['mid']=m;x['up']=m+k*sd;x['lo']=m-k*sd;x['pb']=(x.c-x.lo)/(x.up-x.lo);x['bw']=(x.up-x.lo)/m*100
    x['vma']=x.v.rolling(20).mean().shift();return x
def fng():
    try:
        d=requests.get('https://api.alternative.me/fng/?limit=1',timeout=15).json()['data'][0];return int(d['value']),d['value_classification']
    except Exception:return None,None
