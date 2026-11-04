"""Dynamic market-wide shortlist (Vien 2026-10-09 11:04): "không khoá tập token tại 1 thời điểm, sau khi chốt lệnh chạy lại
shortlist trên toàn thị trường". Background thread, read-only Hyperliquid public API, rate-limited.
Pipeline: all perps (metaAndAssetCtxs) -> not delisted -> max leverage >= lev_min -> 24h notional volume >= vol_min
-> open interest (USD) >= oi_min -> enough history (4h bars / daily candles) -> BB-respect score (bbrank.now, 90d of
closed 4h bars) >= bb_min -> 1D stability (bbrank.stab_eval) -> ranked by score, capped at max_size.
Cache: score per coin keyed by its last CLOSED 4h bar; daily stability keyed by the last closed daily candle. A refresh
inside the same 4h candle therefore only re-reads meta (volume/OI/price) and recomputes nothing it doesn't need to."""
import threading,queue,time,datetime as dt,pandas as pd
import hl,bbrank
DEF=dict(vol_min=3e6,oi_min=5e6,lev_min=5,min_4h_bars=300,min_daily=90,max_size=15,pace_s=2.5,periodic_h=4,max_age_h=24,after_close_s=120,periodic_min=15,offset_s=90,pace_inc_s=1.2,inc_days_4h=3,inc_days_1d=5)
def vn():return (dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=7)).replace(tzinfo=None,microsecond=0)
class Scanner(threading.Thread):
    def __init__(s,cfg,bb_min):
        super().__init__(daemon=True);s.C=dict(DEF,**{k:v for k,v in (cfg or {}).items() if k in DEF});s.bb_min=bb_min
        s.req=threading.Event();s.trig=[];s.out=queue.Queue();s.c4={};s.c1={};s.last=0;s.slot=None;s.lk=threading.Lock();s.busy=False;s.err=None
        s.raw={};s.n_scans=0;s.skipped=0;s.last_secs=None;s.last_end=None;s.last_why=None;s.started_at=None;s.api=dict(cold=0,inc=0,meta=0);s.last_api=None
    def request(s,why):
        with s.lk:s.trig.append(why)
        s.req.set()
    # Vien 10/10 02:00: full whole-market rescan every 15 min, ~offset_s (90 s) after each 15m candle close (plus after-close/startup requests).
    # Single thread => scans never overlap; a slot that passes while a scan is still running is skipped (counted in s.skipped).
    def _slot(s):return int((time.time()-s.C['offset_s'])//(s.C['periodic_min']*60))
    def next_due(s):
        P=s.C['periodic_min']*60;return str((dt.datetime.fromtimestamp(((s._slot()+1)*P+s.C['offset_s']),dt.timezone.utc)+dt.timedelta(hours=7)).replace(tzinfo=None,microsecond=0))
    def run(s):
        while True:
            s.req.wait(timeout=30);why=None
            with s.lk:
                if s.trig:why='; '.join(dict.fromkeys(s.trig));s.trig=[]
            s.req.clear()
            if why is None:
                if s.slot is not None and s._slot()!=s.slot:
                    why=f"quét định kỳ {s.C['periodic_min']} phút (sau nến 15m đóng)"
                elif s.last and time.time()-s.last>s.C['max_age_h']*3600:why='làm mới hằng ngày'
            if why is None:continue
            try:
                s0=s._slot();s.busy=True;s.started_at=str(vn());a0=dict(s.api);r=s.scan(why);s.last_api={k:s.api[k]-a0[k] for k in s.api};r['api']=s.last_api;s.out.put(r);s.err=None
                s.n_scans+=1;s.last_secs=r['secs'];s.last_end=r['time'];s.last_why=why
            except Exception as ex:s.err=f'{vn()} {ex!r}'[:200]
            finally:
                s.busy=False;s.last=time.time()
                if s._slot()>s0:s.skipped+=s._slot()-s0      # 15-min slot(s) that came due while this scan was running: skipped, not queued
                s.slot=s._slot()
    def _get(s,c,iv,days):
        """Same window as a full hl.candles(c,iv,days) fetch, but reuse cached CLOSED bars: if we hold this coin/interval, only the last
        inc_days are refetched (small request, shorter pause) and merged (new rows replace overlapping ones), then trimmed to the exact window a
        full fetch returns (bars whose close is after now-days). Scores computed on it are identical to a cold fetch."""
        P={'4h':4,'1d':24}[iv];k=(c,iv);old=s.raw.get(k);now_ms=int(time.time()*1000)
        inc=s.C['inc_days_4h'] if iv=='4h' else s.C['inc_days_1d']
        if old is not None and len(old) and pd.Timestamp(old.index[-1])>=pd.Timestamp(vn())-pd.Timedelta(days=inc-1):
            n=hl.candles(c,iv,inc);s.api['inc']+=1;time.sleep(s.C['pace_inc_s'])
            if n is None or not len(n):raise RuntimeError(f'{c} {iv}: no incremental candles')
            x=pd.concat([old[~old.index.isin(n.index)],n]).sort_index()
        else:
            x=hl.candles(c,iv,days);s.api['cold']+=1;time.sleep(s.C['pace_s'])
            if x is None:raise RuntimeError(f'{c} {iv}: no candles')
        st=pd.to_datetime(now_ms-days*86400000,unit='ms')+pd.Timedelta(hours=7)
        x=x[x.index+pd.Timedelta(hours=P)>st];s.raw[k]=x;return x.copy()
    def scan(s,why):
        C=s.C;t0=time.time();u=hl.post({'type':'metaAndAssetCtxs'});s.api['meta']+=1;time.sleep(s.C['pace_s'])
        rows=[];cnt=dict(total=0,not_delisted=0,lev=0,vol=0,oi=0,history=0,bb=0,stable=0)
        for a,ctx in zip(u[0]['universe'],u[1]):
            cnt['total']+=1
            if a.get('isDelisted'):continue
            cnt['not_delisted']+=1
            lev=int(a.get('maxLeverage') or 0)
            if lev<C['lev_min']:continue
            cnt['lev']+=1;vol=float(ctx.get('dayNtlVlm') or 0);mk=float(ctx.get('markPx') or ctx.get('midPx') or 0);oi=float(ctx.get('openInterest') or 0)*mk
            if vol<C['vol_min']:continue
            cnt['vol']+=1
            if oi<C['oi_min']:continue
            cnt['oi']+=1;rows.append(dict(coin=a['name'],vol24h=vol,oi_usd=oi,max_lev=lev,mark=mk))
        out=[]
        for r in rows:
            c=r['coin'];last4=pd.Timestamp(vn()).floor('4h')            # cheap staleness key; refetch if a newer 4h bar may have closed
            try:
                k4=s.c4.get(c)
                if not k4 or k4['key']!=str(last4- pd.Timedelta(hours=0)) and pd.Timestamp(k4['bar'])+pd.Timedelta(hours=8)<=pd.Timestamp(vn()):
                    x=s._get(c,'4h',100);x=hl.bb(x);x=x[x.closed]
                    k4=dict(key=str(last4),bar=str(x.index[-1]),n=len(x),rank=bbrank.now(x) if len(x)>=60 else None,rank_s=bbrank.now_short(x) if len(x)>=60 else None,lo=float(x.lo.iloc[-1]),mid=float(x.mid.iloc[-1]),up=float(x.up.iloc[-1]))
                    s.c4[c]=k4
                k1=s.c1.get(c);today=str(vn()-dt.timedelta(hours=7))[:10]
                if not k1 or k1['key']!=today:
                    d=s._get(c,'1d',260);d=d[d.closed];z=bbrank.stab_frame(d).iloc[-1];ok,bad=bbrank.stab_eval(z)
                    tr=pd.concat([d.h-d.l,(d.h-d.c.shift()).abs(),(d.l-d.c.shift()).abs()],axis=1).max(axis=1)
                    atr=float(tr.rolling(14).mean().iloc[-1]/d.c.iloc[-1]*100) if len(d)>=15 else None
                    vol30=float(d.c.pct_change().iloc[-30:].std()*100) if len(d)>=31 else None
                    k1=dict(key=today,n=len(d),ok=ok,bad=bad,text=bbrank.stab_text(z,ok,bad),day=str(d.index[-1])[:10],atr_pct=round(atr,2) if atr is not None else None,vol30=round(vol30,2) if vol30 is not None else None,
                            **{kk:(round(float(z[kk]),3) if pd.notna(z[kk]) else None) for kk in('r30','r60','chg7','chg14','chg30','bwr','pb')})
                    s.c1[c]=k1
            except Exception as ex:
                r.update(err=str(ex)[:100]);out.append(r);continue
            R=k4['rank'] or {};RS=k4.get('rank_s') or {};hist=k4['n']>=C['min_4h_bars'] and k1['n']>=C['min_daily']
            r.update(bar=k4['bar'],n4h=k4['n'],ndaily=k1['n'],score=R.get('score'),rank=R,lo4h=k4['lo'],mid4h=k4['mid'],dist_lo_pct=round((r['mark']/k4['lo']-1)*100,2) if k4['lo'] else None,
                     stab_ok=k1['ok'],stab_bad=k1['bad'],stab_text=k1['text'],d1={kk:k1.get(kk) for kk in('day','r30','r60','chg7','chg14','chg30','bwr','pb','atr_pct','vol30')},
                     hist_ok=hist,pass_bb=hist and (R.get('score') or 0)>=s.bb_min)
            r['eligible']=bool(r['pass_bb'] and k1['ok'])
            r.update(score_s=RS.get('score'),rank_s=RS,up4h=k4.get('up'),dist_up_pct=round((k4['up']/r['mark']-1)*100,2) if k4.get('up') else None,
                     pass_bb_s=hist and (RS.get('score') or 0)>=s.bb_min)
            r['eligible_s']=bool(r['pass_bb_s'] and k1['ok']);cnt['bb_s']=cnt.get('bb_s',0)+r['pass_bb_s'];cnt['stable_s']=cnt.get('stable_s',0)+r['eligible_s']
            cnt['history']+=hist;cnt['bb']+=r['pass_bb'];cnt['stable']+=r['eligible'];out.append(r)
        el=sorted([r for r in out if r.get('eligible')],key=lambda r:-r['score'])
        return dict(time=str(vn()),trigger=why,counts=cnt,thresholds=dict(C,bb_min=s.bb_min,stab=dict(bbrank.ST)),rows=out,
                    shortlist=[r['coin'] for r in el[:C['max_size']]],secs=round(time.time()-t0,1))

# ---------------- per-group SELECTION RULES (Vien 11:05: "Short list chỉ cố định rule lựa chọn, ko cố định khoá tập token") ----------------
def feats(r,side='L'):
    R=(r.get('rank_s') if side=='S' else r.get('rank')) or {};d=r.get('d1') or {}
    return dict(vol24h=r.get('vol24h'),oi_usd=r.get('oi_usd'),oi_rank=r.get('oi_rank'),atr_pct=d.get('atr_pct'),vol30=d.get('vol30'),r30=d.get('r30'),
                raw_rev=R.get('raw_rev'),raw_fake=R.get('raw_fake'),swings30=R.get('swings30'),n_touch=R.get('n_touch'))
OPS={'_min':lambda v,t:v>=t,'_max':lambda v,t:v<=t}
LBL=dict(vol24h='KL 24h',oi_usd='OI',oi_rank='hạng OI',atr_pct='ATR% ngày',vol30='biến động 30 ngày %',r30='cao/thấp 30 ngày',raw_rev='tỉ lệ về dải giữa 2 ngày',raw_fake='tỉ lệ phá giả',swings30='nhịp dải/30 ngày',n_touch='số lần chạm/90 ngày')
def rule_check(r,rule,side='L'):
    """rule: dict like {'oi_usd_min':25e6,'atr_pct_max':6}. Returns (ok, list of failures in Vietnamese). Missing data = fail.
    side='S': BB-behaviour features (raw_rev/raw_fake/swings) come from the UPPER-band (mirrored) statistics."""
    f=feats(r,side);bad=[]
    for k,t in rule.items():
        if k.startswith('_') or not (k.endswith('_min') or k.endswith('_max')):continue
        base,op=k[:-4],k[-4:];v=f.get(base)
        if v is None or not OPS[op](v,t):bad.append(f"{LBL.get(base,base)} {('?' if v is None else (f'{v/1e6:.1f}M' if base in('vol24h','oi_usd') else round(v,2)))} {'<' if op=='_min' else '>'} {f'{t/1e6:.0f}M' if base in('vol24h','oi_usd') else t}")
    return not bad,bad
def group_lists(res,rules,max_size=15,side='L'):
    """res = Scanner.scan() result. Returns {group: [rows sorted by score]} of eligible coins (BB>=bb_min & 1D stable & group rule)."""
    sk='score_s' if side=='S' else 'score';ek='eligible_s' if side=='S' else 'eligible'
    rows=[r for r in res['rows'] if r.get(sk) is not None]
    oi=sorted(res['rows'],key=lambda r:-r['oi_usd'])
    for i,r in enumerate(oi,1):r['oi_rank']=i
    out={}
    for g,rule in rules.items():
        L=[]
        for r in rows:
            ok,bad=rule_check(r,rule,side)
            if ok and r.get(ek):L.append(r)
        out[g]=sorted(L,key=lambda r:-r[sk])[:max_size]
    return out
