"""bnx.py - Binance USDT-M Futures execution client for the LIVE bot. Same interface as hlx.HLX (Hyperliquid) so livecore/strategy are unchanged.
SAFETY
 * DRY_RUN is the default. Nothing is sent to a signed TRADE endpoint unless BNX(dry_run=False) AND env BNB_DRY_RUN == '0'.
 * API key/secret are read ONLY from env BNB_API_KEY/BNB_API_SECRET or from secrets\\binance_api_key.txt + secrets\\binance_api_secret.txt
   (env BNB_KEY_DIR overrides the folder). Never logged, printed or stored elsewhere.
 * Opening orders (not reduce-only) are refused if their notional exceeds max_open_notional (same guard as hlx).
Exchange mapping
 * entries/adds/closes: LIMIT IOC with the same slippage cap as on Hyperliquid (marketable limit = market order with a price guard)
 * protective stop: Algo Order API (POST /fapi/v1/algoOrder, algoType CONDITIONAL) STOP_MARKET closePosition=true, workingType MARK_PRICE
 * leverage 5 + margin type ISOLATED per symbol (mirrors the 30%-per-position cap: a position can never use more margin than it was given)
 * LOT_SIZE / MARKET_LOT_SIZE stepSize+minQty, PRICE_FILTER tickSize, MIN_NOTIONAL notional: orders below the minimum are SKIPPED (never upsized)."""
import os,time,json,hmac,hashlib,threading,math
from decimal import Decimal,ROUND_UP,ROUND_DOWN,ROUND_HALF_EVEN
from urllib.parse import urlencode
import requests
import hl
BASE='https://fapi.binance.com'
MIN_NOTIONAL=5.0          # typical Binance USDT-M minimum; the per-symbol value from exchangeInfo is used (min_notional(coin))
class HLXError(Exception):pass
BNXError=HLXError
B_DIR=os.path.dirname(os.path.abspath(__file__))
def _read1(fp):
    with open(fp,'r',encoding='utf-8-sig') as f:
        for line in f:
            line=line.strip()
            if line and not line.startswith('#'):return line
    return ''
def load_key():
    """-> (api_key, api_secret). Raises HLXError whose message never contains the key."""
    k=os.environ.get('BNB_API_KEY','').strip();s=os.environ.get('BNB_API_SECRET','').strip()
    if not (k and s):
        d=os.environ.get('BNB_KEY_DIR','').strip() or os.path.join(B_DIR,'secrets')
        try:k=_read1(os.path.join(d,'binance_api_key.txt'));s=_read1(os.path.join(d,'binance_api_secret.txt'))
        except OSError as ex:raise HLXError(f'cannot read key files in secrets: {type(ex).__name__}')
    if not k or not s:raise HLXError('no key: run "BNB X5 - NHAP API KEY"')
    for v,n in((k,'API key'),(s,'API secret')):
        if len(v)<32 or any(ch.isspace() for ch in v) or not v.isascii():raise HLXError(f'{n} has wrong format')
    return k,s
# ---------------------------------------------------------------- symbol filters / rounding
class Spec:
    """Per-symbol trading rules from exchangeInfo. Passed where hlx passed szDecimals."""
    def __init__(s,tick,step,min_qty,min_notional,max_qty=None,mkt_step=None,mkt_min=None,mkt_max=None):
        s.tick=Decimal(str(tick));s.step=Decimal(str(step));s.min_qty=float(min_qty);s.min_notional=float(min_notional);s.max_qty=float(max_qty) if max_qty else None
        s.mkt_max=float(mkt_max) if mkt_max else None
    def __repr__(s):return f'Spec(tick={s.tick},step={s.step},minQty={s.min_qty},minNotional={s.min_notional})'
    def __str__(s):return s.__repr__()
def _spec(x):
    if isinstance(x,Spec):return x
    return Spec(Decimal(1).scaleb(-max(0,6-int(x))),Decimal(1).scaleb(-int(x)),0,MIN_NOTIONAL)      # int szDecimals (legacy/tests)
def _q(v,unit,mode):
    d=Decimal(repr(float(v)));rm={'up':ROUND_UP,'down':ROUND_DOWN,'nearest':ROUND_HALF_EVEN}[mode]
    return float((d/unit).quantize(Decimal(1),rounding=rm)*unit)
def px_round(px,spec,mode='nearest',perp=True):
    sp=_spec(spec)
    if px<=0:raise HLXError('price must be positive')
    q=_q(px,sp.tick,mode)
    if q<=0:raise HLXError('rounded price <= 0')
    return q
def sz_round(sz,spec,mode='down'):return _q(sz,_spec(spec).step,mode)
def fmt(v,unit):
    """decimal string at exactly the precision of `unit` (no scientific notation)."""
    u=Decimal(str(unit)).normalize();dec=max(0,-u.as_tuple().exponent)
    return f'{float(v):.{dec}f}'
def plan_size(notional_target,px,spec,max_notional,min_notional=None,min_buf=0.02):
    """Order size for a target notional: rounded DOWN to stepSize (never above the 30% cap). Below minQty or the symbol's MIN_NOTIONAL -> SKIP (no upsizing).
    Returns (sz, notional, note)."""
    sp=_spec(spec);mn=sp.min_notional if min_notional is None else float(min_notional)
    cap=min(notional_target,max_notional);note=[]
    if notional_target>max_notional:note.append(f'target {notional_target:.2f}$ capped to max_notional {max_notional:.2f}$')
    sz=sz_round(cap/px,sp,'down')
    if sp.mkt_max and sz>sp.mkt_max:sz=sz_round(sp.mkt_max,sp,'down');note.append(f'size capped to exchange max qty {sp.mkt_max}')
    if sz<=0 or sz<sp.min_qty:return 0.0,0.0,f'SKIP: size {sz} below exchange minQty {sp.min_qty} (target {notional_target:.2f}$)'
    if sz*px<mn:return 0.0,0.0,f'SKIP: order value below exchange minimum {mn:g}$ (target {notional_target:.2f}$, size {sz} = {sz*px:.2f}$)'
    err=(sz*px/notional_target-1)*100 if notional_target>0 else 0
    if abs(err)>0.01:note.append(f'size rounding {err:+.2f}% vs target notional')
    return sz,sz*px,'; '.join(note)
def spec_from_symbol(s):
    F={f['filterType']:f for f in s.get('filters',[])};lot=F.get('LOT_SIZE',{});ml=F.get('MARKET_LOT_SIZE',{})
    return Spec(F['PRICE_FILTER']['tickSize'],lot['stepSize'],lot.get('minQty',0),float((F.get('MIN_NOTIONAL') or {}).get('notional',MIN_NOTIONAL)),lot.get('maxQty'),mkt_max=ml.get('maxQty'))
# ---------------------------------------------------------------- HL-shaped responses (so livecore.parse_statuses works unchanged)
def _neg_code(c):
    try:return int(c)<0
    except (TypeError,ValueError):return False
def _ok(statuses):return {'status':'ok','response':{'type':'order','data':{'statuses':statuses}}}
def parse_statuses(resp):
    if not isinstance(resp,dict):raise HLXError(f'bad response {str(resp)[:200]}')
    if resp.get('status')!='ok':raise HLXError(f'exchange error: {str(resp.get("response",resp))[:300]}')
    out=[]
    for s in (resp.get('response',{}).get('data') or {}).get('statuses',[]):
        if isinstance(s,str):out.append(dict(kind='ok' if s=='success' else 'error',status=s,error=None if s=='success' else s));continue
        if 'filled' in s:f=s['filled'];out.append(dict(kind='filled',oid=f.get('oid'),avgPx=float(f['avgPx']),totalSz=float(f['totalSz'])))
        elif 'resting' in s:out.append(dict(kind='resting',oid=s['resting'].get('oid')))
        elif 'error' in s:out.append(dict(kind='error',error=s['error']))
        else:out.append(dict(kind='error',error=str(s)[:200]))
    return out
def order_result_to_status(j):
    """Binance /fapi/v1/order (newOrderRespType=RESULT) -> HL status dict."""
    if 'code' in j and 'orderId' not in j:return {'error':f"{j.get('code')} {j.get('msg')}"}
    ex=float(j.get('executedQty') or 0)
    if ex>0:return {'filled':{'oid':j['orderId'],'avgPx':float(j.get('avgPrice') or 0) or (float(j.get('cumQuote') or 0)/ex if float(j.get('cumQuote') or 0)>0 else float(j.get('price'))),'totalSz':ex}}   # FIX 2026-10-30: never fall back to the LIMIT price when avgPrice is 0 (use cumQuote/qty; livecore re-reads real fills anyway)
    if j.get('status') in('NEW','PARTIALLY_FILLED'):return {'resting':{'oid':j['orderId']}}
    return {'error':f"order {j.get('status')} (not filled)"}
class BNX:
    def __init__(s,address=None,key=None,dry_run=None,log=None,max_open_notional=25.0,sign_in_dry=False,base=BASE,expect_agent=None):
        s.base=base;env_live=os.environ.get('BNB_DRY_RUN','1')=='0'
        s.dry_run=True if dry_run is None else bool(dry_run)
        if not env_live:s.dry_run=True              # a live client needs BOTH dry_run=False AND env BNB_DRY_RUN=0
        s.max_open_notional=float(max_open_notional);s.log=log or (lambda e:None);s.sign_in_dry=sign_in_dry
        s._k=s._s=None;s.key_error=None
        if key is None:
            try:s._k,s._s=load_key()
            except HLXError as ex:
                if not s.dry_run:raise
                s.key_error=str(ex)
        elif isinstance(key,tuple):s._k,s._s=key
        s.sess=requests.Session();s.n_sent=0;s.assets={};s.meta_t=0;s._lev={};s._lev_t=0;s._toff=0;s._toff_t=0;s._lk=threading.Lock()
        if s._k:hl.LEV_PROVIDER=s.max_leverages
    @property
    def has_key(s):return bool(s._k)
    # ---- HTTP
    def pub(s,path,params=None,retry=3):
        last=None
        for i in range(retry):
            try:
                r=s.sess.get(s.base+path,params=params,timeout=20)
                if r.status_code==200:return r.json()
                last=f'HTTP {r.status_code} {r.text[:150]}'
            except Exception as ex:last=type(ex).__name__
            time.sleep(1+2*i)
        raise HLXError(f'GET {path} failed: {last}')
    def _ts(s):
        if time.time()-s._toff_t>600:
            try:st=s.pub('/fapi/v1/time')['serverTime'];s._toff=st-int(time.time()*1000);s._toff_t=time.time()
            except HLXError:pass
        return int(time.time()*1000)+s._toff
    def signed(s,method,path,params=None,retry=3):
        if not s._k:raise HLXError('no API key loaded')
        last=None
        for i in range(retry if method=='GET' else 1):        # never auto-retry a POST/DELETE (no double orders)
            p=dict(params or {});p['timestamp']=s._ts();p['recvWindow']=10000
            q=urlencode(p);sig=hmac.new(s._s.encode(),q.encode(),hashlib.sha256).hexdigest()
            try:
                r=s.sess.request(method,f'{s.base}{path}?{q}&signature={sig}',headers={'X-MBX-APIKEY':s._k},timeout=20)
                try:j=r.json()
                except Exception:j={'code':r.status_code,'msg':r.text[:200]}
                if r.status_code==200:return j
                if method!='GET':return j if isinstance(j,dict) else {'code':r.status_code,'msg':str(j)[:200]}
                last=f"HTTP {r.status_code} {j.get('code') if isinstance(j,dict) else ''} {j.get('msg') if isinstance(j,dict) else ''}"
                if r.status_code in(400,401) and isinstance(j,dict) and j.get('code')==-1021:s._toff_t=0
                elif r.status_code in(401,403):break
            except Exception as ex:last=type(ex).__name__
            time.sleep(1+2*i)
        raise HLXError(f'{method} {path} failed: {last}')
    # ---- info (read only)
    def load_meta(s,force=False):
        if s.assets and not force and time.time()-s.meta_t<3600:return s.assets
        A={};ML=s.max_leverages() if s._k else {}
        if force:hl.exchange_info(0)
        for x in hl.perp_symbols():
            c=hl.coin_of(x['symbol']);sp=spec_from_symbol(x)
            A[c]=dict(asset=x['symbol'],szDecimals=sp,spec=sp,maxLeverage=int(ML.get(c,hl.DEFAULT_MAX_LEV)),onlyIsolated=False,marginMode=None)
        s.assets=A;s.meta_t=time.time();return A
    def asset(s,coin):
        A=s.load_meta()
        if coin not in A:raise HLXError(f'unknown/not-trading coin {coin}')
        return A[coin]
    def min_notional(s,coin):return s.asset(coin)['spec'].min_notional
    def max_leverages(s):
        if s._lev and time.time()-s._lev_t<3600:return s._lev
        d=s.signed('GET','/fapi/v1/leverageBracket');L={}
        for x in d:L[hl.coin_of(x['symbol'])]=max(int(b['initialLeverage']) for b in x['brackets'])
        s._lev=L;s._lev_t=time.time();return L
    def mids(s):
        try:return hl.mids()
        except Exception as ex:raise HLXError(f'mids failed: {ex}')
    def book(s,coin):
        b=s.pub('/fapi/v1/ticker/bookTicker',{'symbol':hl.sym(coin)})
        bp,ap=float(b.get('bidPrice') or 0),float(b.get('askPrice') or 0);return (bp or None,ap or None)
    def account(s):return s.signed('GET','/fapi/v3/account')
    def user_state(s):
        """base = USDT wallet balance (realized equity used for sizing); equity = base + unrealized PnL. Positions keyed by coin (one-way mode).
        abstraction: 'binanceUSDM' normally; 'hedgeMode'/'multiAssetsMode' when the account is in a mode this bot does not support (bot halts entries)."""
        if not s._k:return dict(abstraction='nokey',usdc_total=0.0,usdc_hold=0.0,perps_account_value=0.0,base=0.0,upnl=0.0,equity=0.0,positions={},time=None,available=0.0)
        a=s.account();pr=s.signed('GET','/fapi/v3/positionRisk')
        usdt=next((x for x in a.get('assets',[]) if x.get('asset')=='USDT'),{})
        base=float(usdt.get('walletBalance',0) or 0);avail=float(usdt.get('availableBalance',a.get('availableBalance',0)) or 0)
        pos={};up=0.0
        for p in pr:
            szi=float(p.get('positionAmt',0) or 0)
            if szi==0:continue
            c=hl.coin_of(p['symbol']);u=float(p.get('unRealizedProfit',0) or 0);up+=u
            mu=float(p.get('isolatedMargin') or 0) or float(p.get('initialMargin') or 0)
            lq=float(p.get('liquidationPrice') or 0)
            pos[c]=dict(szi=szi,entryPx=float(p['entryPrice']),upnl=u,marginUsed=mu,positionValue=abs(float(p.get('notional') or szi*float(p.get('markPrice',0)))),
                        liqPx=lq or None,lev=dict(type='isolated' if p.get('marginType','').lower()=='isolated' or float(p.get('isolatedMargin') or 0)>0 else 'cross',value=p.get('leverage')))
        ab='binanceUSDM'
        try:
            if s._mode_t+600<time.time():raise AttributeError
        except AttributeError:
            s._dual=s.signed('GET','/fapi/v1/positionSide/dual').get('dualSidePosition');s._multi=s.signed('GET','/fapi/v1/multiAssetsMargin').get('multiAssetsMargin');s._mode_t=time.time()
        if s._dual:ab='hedgeMode'
        elif s._multi:ab='multiAssetsMode'
        return dict(abstraction=ab,usdc_total=base,usdc_hold=0.0,perps_account_value=float(a.get('totalMarginBalance',0) or 0),base=base,upnl=up,equity=base+up,positions=pos,time=a.get('updateTime'),available=avail)
    def open_orders(s):
        """Regular open orders + open algo (conditional) orders, normalized to the HL frontendOpenOrders fields livecore reads."""
        out=[]
        for o in s.signed('GET','/fapi/v1/openOrders'):
            out.append(dict(coin=hl.coin_of(o['symbol']),oid=o['orderId'],isTrigger=False,reduceOnly=bool(o.get('reduceOnly')),orderType=o.get('type'),triggerPx=o.get('stopPrice') or '0',sz=o.get('origQty'),side=o.get('side')))
        for o in s.signed('GET','/fapi/v1/openAlgoOrders'):
            ot=o.get('orderType') or o.get('type') or ''
            out.append(dict(coin=hl.coin_of(o['symbol']),oid=f"algo:{o['algoId']}",isTrigger=True,reduceOnly=bool(o.get('reduceOnly')) or str(o.get('closePosition')).lower()=='true',
                            orderType=('Take Profit' if 'TAKE_PROFIT' in ot else 'Stop Market'),triggerPx=o.get('triggerPrice'),sz=o.get('quantity'),side=o.get('side')))
        return out
    def fills_since(s,start_ms,coin=None):
        """userTrades of one coin since start_ms -> HL fill shape (oid, fee, closedPnl, dir, px, sz, time). Fees in non-USDT assets (BNB) are converted at the mid price."""
        if coin is None:
            cs=list(s.user_state()['positions']) if s._k else []
            return [f for c in cs for f in s.fills_since(start_ms,c)]
        d=s.signed('GET','/fapi/v1/userTrades',{'symbol':hl.sym(coin),'startTime':int(start_ms),'limit':1000});out=[];first=None
        for t in sorted(d,key=lambda t:t['time']):
            fee=float(t['commission'])
            if t.get('commissionAsset') not in(None,'USDT'):
                try:fee*=float(hl.mids().get(t['commissionAsset'],0))
                except Exception:pass
            if first is None:first=t['side']
            rp=float(t.get('realizedPnl') or 0)
            out.append(dict(coin=coin,oid=t['orderId'],fee=fee,closedPnl=rp,dir=('Close' if (t['side']!=first or rp!=0) else 'Open'),px=float(t['price']),sz=float(t['qty']),time=int(t['time']),side=t['side']))
        return out
    def asset_data(s,coin):
        """HL activeAssetData shape: leverage {type,value} for this symbol, availableToTrade [buy,sell] in USDT margin."""
        cfg=s.signed('GET','/fapi/v1/symbolConfig',{'symbol':hl.sym(coin)})
        c=cfg[0] if isinstance(cfg,list) and cfg else {}
        avail=float(s.account().get('availableBalance',0) or 0)
        return dict(leverage=dict(type=str(c.get('marginType','CROSSED')).lower().replace('crossed','cross'),value=int(c.get('leverage',0) or 0)),availableToTrade=[avail,avail],marginAvailable=avail)
    # ---- exchange (TRADE)
    def _guard(s,opening,notional,is_trigger=False):
        if not opening:return
        if is_trigger:raise HLXError('non-reduce-only trigger orders are not allowed by this client')
        if notional>s.max_open_notional*1.02:raise HLXError(f'GUARD: opening order notional {notional:.2f} > max_open_notional {s.max_open_notional}')
    def send(s,method,path,params,label='',kind='order'):
        preview=dict(method=method,path=path,params=params)
        if s.dry_run:
            s.log(dict(event='DRY_RUN_WOULD_SEND',label=label,payload=preview));return {'dry':True}
        s.log(dict(event='LIVE_SEND',label=label,payload=preview))
        j=s.signed(method,path,params);s.n_sent+=1
        s.log(dict(event='LIVE_RESPONSE',label=label,response=j));return j
    def ioc(s,coin,is_buy,sz,px,reduce_only=False,label=''):
        a=s.asset(coin);sp=a['spec'];s._guard(not reduce_only,float(px)*float(sz))
        p=dict(symbol=a['asset'],side='BUY' if is_buy else 'SELL',type='LIMIT',timeInForce='IOC',quantity=fmt(sz,sp.step),price=fmt(px,sp.tick),newOrderRespType='RESULT')
        if reduce_only:p['reduceOnly']='true'
        j=s.send('POST','/fapi/v1/order',p,label or f'ioc {coin}')
        if j.get('dry'):return j
        st=order_result_to_status(j)
        if 'error' in st and 'code' in j:return {'status':'err','response':j}
        return _ok([st])
    def limit_postonly(s,coin,is_buy,sz,px,reduce_only=False,label=''):
        """Post-only LIMIT (Binance timeInForce=GTX). Rejected if it would take liquidity."""
        a=s.asset(coin);sp=a['spec'];s._guard(not reduce_only,float(px)*float(sz))
        p=dict(symbol=a['asset'],side='BUY' if is_buy else 'SELL',type='LIMIT',timeInForce='GTX',quantity=fmt(sz,sp.step),price=fmt(px,sp.tick),newOrderRespType='RESULT')
        if reduce_only:p['reduceOnly']='true'
        j=s.send('POST','/fapi/v1/order',p,label or f'postonly {coin}')
        if j.get('dry'):return j
        st=order_result_to_status(j)
        if 'error' in st and 'code' in j:return {'status':'err','response':j}
        return _ok([st])
    def stop(s,coin,close_is_buy,sz,trigger_px,limit_px=None,label=''):
        """Protective stop: algo STOP_MARKET closePosition=true (closes the whole position when MARK price crosses trigger). sz/limit_px unused (kept for the hlx interface)."""
        a=s.asset(coin);sp=a['spec']
        p=dict(algoType='CONDITIONAL',symbol=a['asset'],side='BUY' if close_is_buy else 'SELL',type='STOP_MARKET',triggerPrice=fmt(trigger_px,sp.tick),closePosition='true',workingType='MARK_PRICE',priceProtect='false')
        j=s.send('POST','/fapi/v1/algoOrder',p,label or f'stop {coin}')
        if j.get('dry'):return j
        if j.get('algoId') is None and j.get('code')==-4130 and sz:      # a closePosition stop already rests on this side: place the new one as a reduce-only quantity stop (new first, old cancelled after)
            p2=dict(p);p2.pop('closePosition');p2.update(quantity=fmt(sz,sp.step),reduceOnly='true')
            j=s.send('POST','/fapi/v1/algoOrder',p2,(label or f'stop {coin}')+' (reduce-only qty)')
        if j.get('algoId') is not None:return _ok([{'resting':{'oid':f"algo:{j['algoId']}"}}])
        return {'status':'err','response':j}
    def limit_reduce(s,coin,is_buy,sz,px,label=''):
        """STEP3 take-profit: resting GTC LIMIT reduce-only order."""
        a=s.asset(coin);sp=a['spec']
        p=dict(symbol=a['asset'],side='BUY' if is_buy else 'SELL',type='LIMIT',timeInForce='GTC',quantity=fmt(sz,sp.step),price=fmt(px,sp.tick),reduceOnly='true',newOrderRespType='RESULT')
        j=s.send('POST','/fapi/v1/order',p,label or f'tp {coin}')
        if j.get('dry'):return j
        st=order_result_to_status(j)
        if 'error' in st and 'code' in j:return {'status':'err','response':j}
        return _ok([st])
    def cancel(s,coin,oid,label=''):
        sy=hl.sym(coin)
        if str(oid).startswith('algo:'):j=s.send('DELETE','/fapi/v1/algoOrder',dict(symbol=sy,algoId=str(oid)[5:]),label or f'cancel {coin} {oid}')
        else:j=s.send('DELETE','/fapi/v1/order',dict(symbol=sy,orderId=oid),label or f'cancel {coin} {oid}')
        if j.get('dry'):return j
        if isinstance(j,dict) and _neg_code(j.get('code')):raise HLXError(f"cancel failed {j.get('code')} {j.get('msg')}")
        return _ok(['success'])
    def set_leverage(s,coin,lev,cross=False,label=''):
        sy=s.asset(coin)['asset']
        j1=s.send('POST','/fapi/v1/marginType',dict(symbol=sy,marginType='CROSSED' if cross else 'ISOLATED'),(label or f'margin {coin}')+' marginType')
        if not j1.get('dry') and j1.get('code') not in(200,-4046,None):return {'status':'err','response':j1}     # -4046: no need to change margin type
        j2=s.send('POST','/fapi/v1/leverage',dict(symbol=sy,leverage=int(lev)),(label or f'leverage {coin}')+' leverage')
        if j2.get('dry'):return {'status':'ok','dry':True}
        if int(j2.get('leverage',0) or 0)!=int(lev):return {'status':'err','response':j2}
        return {'status':'ok'}
HLX=BNX
