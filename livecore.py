"""livecore.py - LIVE execution layer (BINANCE USDT-M build: exchange client bnx.py; identical rules to the Hyperliquid build) (BASE_SL250 (x5) rule set, one branch, ladder adds executed live).

The strategy engine (strat.py: entries, rules, BE, lock 2/3, trail 15m, 15m outlook exit, SL) is UNCHANGED and keeps deciding. LiveBook turns its decisions
into real orders through Live (hlx.HLX) and keeps the exchange as the source of truth (position, equity, realized PnL, fees).
DRY-RUN (default): every payload is logged ("DRY_RUN_WOULD_SEND") and a fill is simulated at the reference price; nothing is sent.
"""
import os,json,time,math,datetime as dt
import pandas as pd
import strat
from strat import Book15,Book,CFG,sd,SIDE
import bnx as hlx
from bnx import HLXError,px_round,sz_round,plan_size

class VetoClose(Exception):
    """Raised inside the paper engine's manage() to cancel a resting-stop style close that the exchange stop order should execute itself."""

def vn_now():return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)+dt.timedelta(hours=7)

class Live:
    def __init__(s,B,cfg,hx,emit,dry=True):
        L=cfg['live'];s.B=B;s.cfg=cfg;s.L=L;s.hx=hx;s.emit=emit;s.dry=dry
        s.lev=int(L['leverage']);s.margin_mode=L.get('margin_mode','isolated')
        s.max_notional_fixed=(float(L['max_notional']) if L.get('max_notional') is not None else None);s.max_positions=int(L['max_positions'])
        s.entry_slip=float(L['entry_slip_pct'])/100;s.stop_limit_slip=float(L['stop_limit_slip_pct'])/100;s.close_slip=float(L['close_slip_pct'])/100
        s.max_spread=float(L['max_spread_pct'])/100;s.max_px_dev=float(L['max_px_dev_pct'])/100
        s.min_equity=(float(L['min_equity_to_trade']) if L.get('min_equity_to_trade') is not None else None);s.veto_grace=float(L.get('stop_backstop_s',25))
        s.stop_min_gap=float(L.get('stop_update_min_s',8));s.allowed_abs=set(L.get('allowed_abstractions',['unifiedAccount']))
        s.st=None;s.st_t=0;s.sync_ok=False;s.settle_grace_s=15.0;s.halted=None;s.foreign=set();s.veto_t={};s.veto_log={};s.stop_fail={};s.orders_t=0;s.blocked={};s.seeds=[];s.save_cb=None
        s.journal_path=B+'state/live_journal.json';s.kill_path=B+'KILL';s.t_start=time.time();s.dry_seq=0
        s.seed_path=B+'state/live_seeds.json'
        try:
            with open(s.seed_path) as f:done=json.load(f)
        except Exception:done={}
        for sdz in L.get('seeds',[]):
            k=f"{sdz['coin']}|{sdz['since']}";s.seeds.append(dict(sdz,key=k,status=done.get(k,'pending')))
    # ------------------------------------------------------------ small helpers
    def ev(s,event,coin='*',reason='',**kw):
        e=dict(time=str(vn_now().replace(microsecond=0)),event=event,coin=coin,reason=reason,dry_run=s.dry,**kw);s.emit(e);return e
    def kill_requested(s):
        return os.path.exists(s.kill_path) or os.environ.get('BNB_KILL','').strip() not in('','0')
    def refresh(s,max_age=2.0):
        if s.st is None or time.time()-s.st_t>max_age:
            s.st=s.hx.user_state();s.st_t=time.time()
        return s.st
    def tick_info(s,coin):
        a=s.hx.asset(coin);return a['szDecimals'],a['asset']
    def fee_taker(s):return CFG['fee_taker']
    # ------------------------------------------------------------ sync with exchange (source of truth)
    def sync(s,book,tnow,orders=False):
        st=s.refresh(0.0);book.live_base=st['base']
        if st['abstraction'] not in s.allowed_abs:
            s.halted=f'account abstraction {st["abstraction"]} not in allowed {sorted(s.allowed_abs)}'
        elif s.halted and s.halted.startswith('account abstraction'):s.halted=None
        if s.dry:return st
        ex=st['positions']
        for c in list(book.pos):
            if c not in ex:
                if time.time()*1000-book.pos[c].get('live',{}).get('t_entry_ms',0)>s.settle_grace_s*1000:book.settle_external(c,tnow)
            else:
                p=book.pos[c];q=abs(ex[c]['szi'])
                if p['q'] and abs(q-p['q'])/p['q']>0.02:
                    s.ev('LIVE_SIZE_DRIFT',c,f'exchange size {q} vs book {p["q"]}: book follows the exchange');avg_=p['cost']/p['q'];p['q']=q;p['cost']=q*float(ex[c].get('entryPx') or avg_)
                else:
                    ep=float(ex[c].get('entryPx') or 0)
                    if ep>0 and p['q'] and abs(p['cost']/p['q']/ep-1)>2e-4:      # FIX 2026-10-30: book avg = exchange entry price (real fills)
                        s.ev('LIVE_ENTRY_FIX',c,f'book avg {p["cost"]/p["q"]:.6g} -> exchange entryPx {ep:.6g}',old_avg=p['cost']/p['q'],new_avg=ep)
                        p['cost']=p['q']*ep;p['margin']=p['cost']/s.lev
                        if len(p.get('fills') or [])==1:f0=list(p['fills'][0]);f0[1]=ep;f0[2]=p['cost'];p['fills'][0]=type(p['fills'][0])(f0);p['first']=ep
        jr=s.read_journal()
        for c,x in ex.items():
            if c in book.pos:continue
            if jr and jr.get('coin')==c and time.time()-jr.get('t',0)<900:
                s.adopt(book,c,x,tnow,'journal');continue
            if c not in s.foreign:s.ev('LIVE_FOREIGN_POSITION',c,f'position {x["szi"]} {c} on the exchange is not managed by the bot: left untouched, new entries are blocked while it exists')
            s.foreign.add(c)
        s.foreign={c for c in s.foreign if c in ex and c not in book.pos}
        if orders or time.time()-s.orders_t>30:s.refresh_orders(book)
        return st
    def refresh_orders(s,book):
        if s.dry:return
        try:oo=s.hx.open_orders()
        except HLXError as ex:s.ev('LIVE_WARN','*',f'open orders refresh failed: {ex}');return
        s.orders_t=time.time()
        for c,p in book.pos.items():
            g=sd(p);stops=[o for o in oo if o.get('coin')==c and o.get('isTrigger') and o.get('reduceOnly') and 'Take Profit' not in str(o.get('orderType',''))]
            lv=p.setdefault('live',{})
            if not stops:
                if lv.get('stop') and lv['stop'].get('oid') is not None:s.ev('LIVE_STOP_MISSING',c,'resting stop not found on the exchange: will be re-placed');lv['stop']=None
                continue
            stops.sort(key=lambda o:float(o['triggerPx']),reverse=(g>0))      # most protective first (long: highest trigger)
            keep=stops[0]
            for o in stops[1:]:
                try:s.hx.cancel(c,o['oid'],'cancel duplicate stop')
                except HLXError as ex:s.ev('LIVE_WARN',c,f'cancel duplicate stop failed: {ex}')
            cur=lv.get('stop') or {}
            if cur.get('oid')!=keep['oid']:lv['stop']=dict(oid=keep['oid'],trigger=float(keep['triggerPx']),kind=cur.get('kind','?'),t=time.time())
    # ------------------------------------------------------------ journal (crash safety between fill and book save)
    def write_journal(s,d):
        try:
            with open(s.journal_path+'.tmp','w') as f:json.dump(d,f)
            os.replace(s.journal_path+'.tmp',s.journal_path)
        except OSError:pass
    def read_journal(s):
        try:
            with open(s.journal_path) as f:return json.load(f)
        except Exception:return None
    # ------------------------------------------------------------ entries
    def entry_veto(s,book,c,side,px):
        why=None
        if s.kill_requested():why='KILL switch active'
        elif not s.sync_ok or book.live_base is None:why='exchange state not synced'
        elif s.halted:why='halted: '+s.halted
        elif s.foreign:why='unmanaged position(s) on exchange: '+','.join(sorted(s.foreign))
        elif s.min_equity is not None and s.st is not None and s.st['base']<s.min_equity:why=f'account equity {s.st["base"]:.2f}$ < min_equity_to_trade {s.min_equity}$'
        elif c in s.blocked:
            if px<=s.blocked[c]:why='seed skipped: coin blocked until price leaves the 4h lower band and a new touch occurs'
            else:s.blocked.pop(c,None)
        elif len(book.pos)>=s.max_positions:why=f'max_positions {s.max_positions} reached'
        if why and time.time()-s.veto_log.get((c,why),0)>300:
            s.veto_log[(c,why)]=time.time();s.ev('LIVE_ENTRY_VETO',c,f'{side} {c}: {why}')
        return why
    @property
    def max_notional(s):
        """Per-order notional guard. X5 config: none fixed -> derived from the paper 30% margin cap: 0.30 x live equity x leverage (+3% rounding slack)."""
        if s.max_notional_fixed is not None:return s.max_notional_fixed
        base=float(s.st['base']) if s.st else 0.0
        return round(1.00*base*s.lev*1.03,4)
    @max_notional.setter
    def max_notional(s,v):s.max_notional_fixed=None if v is None else float(v)
    def new_pos(s,book,c,side,avg,sz,fee,t,margin):
        n=avg*sz
        p=dict(first=avg,q=sz,cost=n,fee=fee,k=1,t0=t,target='mid',paused=False,last=avg,fills=[(str(t),avg,n,'entry')],held=False,trail=None,partial=None,real=0.0,margin=margin,lev=s.lev)
        if side=='S':p['side']='S'
        p['live']=dict(t_entry_ms=int(time.time()*1000),stop=None,src='bot')
        return p
    def open(s,book,c,p,px,t):
        """Execute the entry the paper engine just decided. Mutates p to the REAL fill. Returns (ok, info dict)."""
        side='S' if p.get('side')=='S' else 'L';is_buy=side=='L';info={}
        try:
            szdec,asset=s.tick_info(c);A=s.hx.asset(c)
            if A['maxLeverage']<s.lev:return False,dict(why=f'{c} max leverage {A["maxLeverage"]} < {s.lev}')
            bid,ask=s.hx.book(c)
            if not bid or not ask:return False,dict(why='no two-sided book')
            ref=ask if is_buy else bid;spread=ask/bid-1
            if spread>s.max_spread:return False,dict(why=f'spread {spread*100:.3f}% > max {s.max_spread*100:.3f}%')
            if abs(ref/px-1)>s.max_px_dev:return False,dict(why=f'book price {ref} deviates {abs(ref/px-1)*100:.2f}% from signal price {px}')
            target=p['cost']                                                    # = leg(0)*lev  (30% of live equity x10)
            cap_n=s.max_notional;s.hx.max_open_notional=cap_n
            sz,notional,note=plan_size(target,ref,szdec,cap_n,s.hx.min_notional(c))
            info.update(target_notional=round(target,4),size=sz,notional=round(notional,4),plan_note=note,ref=ref,spread_pct=round(spread*100,4),szDecimals=szdec)
            if sz<=0:return False,dict(info,why=note)
            margin=notional/s.lev;need=margin+notional*s.fee_taker()+0.01
            if not s.dry:
                ad=s.hx.asset_data(c);avail=float(ad['availableToTrade'][0 if is_buy else 1])
                if avail<need:return False,dict(info,why=f'availableToTrade {avail} < needed {need:.3f}')
                lvl=ad.get('leverage',{});want='isolated' if s.margin_mode=='isolated' else 'cross'
                if lvl.get('type')!=want or int(lvl.get('value',0))!=s.lev:
                    r=s.hx.set_leverage(c,s.lev,cross=(want=='cross'),label=f'leverage {c} x{s.lev} {want}')
                    if r.get('status')!='ok':return False,dict(info,why=f'updateLeverage failed: {str(r)[:200]}')
                    lvl=s.hx.asset_data(c).get('leverage',{})
                    if lvl.get('type')!=want or int(lvl.get('value',0))!=s.lev:return False,dict(info,why=f'leverage not applied: {lvl}')
            else:
                s.hx.set_leverage(c,s.lev,cross=(s.margin_mode!='isolated'),label=f'(dry) leverage {c} x{s.lev} {s.margin_mode} - only sent live if activeAssetData differs')
            lim=px_round(ref*(1+s.entry_slip) if is_buy else ref*(1-s.entry_slip),szdec,'up' if is_buy else 'down')
            s.write_journal(dict(coin=c,side=side,sz=sz,t=time.time(),ref=ref))
            r=s.hx.ioc(c,is_buy,sz,lim,False,label=f'ENTRY {SIDE(p)} {c} IOC')
            if s.dry:
                avg=ref;fsz=sz;fee=avg*fsz*s.fee_taker();oid='dry';info.update(simulated=True)
            else:
                stt=hlx.parse_statuses(r)
                if not stt or stt[0]['kind']!='filled':
                    s.write_journal({});return False,dict(info,why=f'entry not filled: {stt[:1]}')
                avg=stt[0]['avgPx'];fsz=stt[0]['totalSz'];oid=stt[0]['oid'];avg,fsz,fee=s.fill_of_oid(c,oid,avg,fsz,avg*fsz*s.fee_taker())
            info.update(avg=avg,filled=fsz,fee=round(fee,6),limit_px=lim,oid=oid,slip_vs_signal_pct=round((avg/px-1)*100*(1 if is_buy else -1),4))
        except HLXError as ex:
            s.write_journal({});return False,dict(info,why=f'HLXError {ex}')
        np_=s.new_pos(book,c,side,avg,fsz,fee,t,avg*fsz/s.lev);p.clear();p.update(np_)
        return True,info
    def fills_of_oid(s,c,oid,avg,fsz,fee_est):
        """Alias used by step3/batday."""
        return s.fill_of_oid(c,oid,avg,fsz,fee_est)
    def fill_of_oid(s,c,oid,avg,fsz,fee_est):
        """FIX 2026-10-30: entry/add price, qty and fee from the REAL Binance fills (userTrades) of this order, not the order response / limit / signal price."""
        for i in range(5):
            try:
                fl=[f for f in s.hx.fills_since(int(time.time()*1000)-180000,c) if str(f.get('oid'))==str(oid)]
                if fl:
                    q=sum(float(f['sz']) for f in fl)
                    if q>0:
                        a=sum(float(f['px'])*float(f['sz']) for f in fl)/q
                        if abs(a/avg-1)>1e-5 or abs(q-fsz)>1e-9:s.ev('LIVE_FILL_FIX',c,f'order {oid}: response {fsz} @ {avg} -> real fills {q} @ {a:.6g}',resp_avg=avg,real_avg=a,resp_sz=fsz,real_sz=q)
                        return a,q,sum(float(f['fee']) for f in fl)
            except HLXError:pass
            time.sleep(1)
        return avg,fsz,fee_est
    def fee_of_oid(s,c,oid,est):
        for i in range(4):
            try:
                fl=[f for f in s.hx.fills_since(int(time.time()*1000)-120000,c) if f.get('oid')==oid]
                if fl:return sum(float(f['fee']) for f in fl)
            except HLXError:pass
            time.sleep(1)
        return est
    def adopt(s,book,c,x,tnow,src):
        side='L' if x['szi']>0 else 'S';q=abs(x['szi']);n=q*x['entryPx']
        p=s.new_pos(book,c,side,x['entryPx'],q,n*s.fee_taker(),tnow,x['marginUsed']);p['live']['src']='adopted:'+src;p['live']['t_entry_ms']=int(time.time()*1000)-3600000
        book.pos[c]=p;s.write_journal({})
        s.ev('LIVE_ADOPT',c,f'adopted {SIDE(p)} {c} {q} @ {x["entryPx"]} from the exchange ({src}); stop will be (re)placed',size=q,entryPx=x['entryPx'])
    # ------------------------------------------------------------ protective stop on the exchange
    def desired_stop(s,book,p):
        g=sd(p);cand=[]
        if p.get('trail') is not None:cand.append((float(p['trail']),p.get('trail_kind') or 'BE'))
        ps=book.stop_px(p)
        if ps:cand.append((float(ps),'SL'))
        if not cand:return None
        return max(cand) if g>0 else min(cand)
    def ensure_stop(s,book,c,tnow=None,force=False):
        """Make the exchange's resting stop-market (reduce-only) match the engine's effective stop. Place NEW first, then cancel old. Returns 'ok'|'close_now'|'fail'."""
        p=book.pos[c];g=sd(p);d=s.desired_stop(book,p)
        if d is None:return 'ok'
        lvl,kind=d;szdec,_=s.tick_info(c)
        trig=px_round(lvl,szdec,'up' if g>0 else 'down')       # round toward the entry price: loss never larger than the SL level
        lv=p.setdefault('live',{});cur=lv.get('stop')
        if cur and cur.get('trigger')==trig and cur.get('oid') is not None:return 'ok'
        if cur and cur.get('oid') is not None and not force and time.time()-cur.get('t',0)<s.stop_min_gap:return 'ok'
        sz=sz_round(p['q'],szdec,'down') if s.dry else sz_round(abs(s.refresh(1.0)['positions'].get(c,{'szi':p['q']})['szi']),szdec,'down')
        lim=px_round(trig*(1-g*s.stop_limit_slip),szdec,'down' if g>0 else 'up')
        try:
            r=s.hx.stop(c,g<0,sz,trig,lim,label=f'STOP {kind} {c} trigger {trig}')
            if s.dry:oid=9000000000+s.dry_seq;s.dry_seq+=1
            else:
                stt=hlx.parse_statuses(r)
                if not stt or stt[0]['kind'] not in('resting','ok'):raise HLXError(f'stop rejected: {stt[:1]}')
                oid=stt[0].get('oid')
                if oid is None:      # 'waitingForTrigger'-style acknowledgement without an oid: look it up
                    oid='unknown'
                    try:
                        for o in s.hx.open_orders():
                            if o.get('coin')==c and o.get('isTrigger') and abs(float(o['triggerPx'])-trig)<1e-9:oid=o['oid']
                    except HLXError:pass
        except HLXError as ex:
            n=s.stop_fail[c]=s.stop_fail.get(c,0)+1
            s.ev('LIVE_STOP_FAIL',c,f'stop {kind} {trig} failed ({n}): {ex}')
            try:pxn=s.hx.mids().get(c)
            except HLXError:pxn=None
            if pxn is not None and g*(pxn-trig)<=0:      # the new stop level is already crossed (exchange refuses a trigger on the wrong side of the mark): exit now
                s.ev('LIVE_STOP_CROSSED',c,f'stop level {trig} already crossed by price {pxn}: closing at market now');return 'close_now'
            if cur and cur.get('oid') is not None:return 'ok'      # an older (looser) stop is still resting
            return 'close_now' if n>=3 else 'fail'
        s.stop_fail[c]=0
        if cur and cur.get('oid') is not None:
            try:s.hx.cancel(c,cur['oid'],f'cancel old stop {c}')
            except HLXError as ex:s.ev('LIVE_WARN',c,f'cancel old stop failed (duplicate cleanup next sync): {ex}')
        lv['stop']=dict(oid=oid,trigger=trig,kind=kind,t=time.time(),limit=lim,sz=sz)
        s.ev('LIVE_STOP_SET',c,f'{SIDE(p)} {c}: exchange stop-market {kind} trigger {trig} (limit {lim}, size {sz}, reduce-only)',trigger=trig,kind=kind,size=sz,stop_usd_at_trigger=round(sd(p)*(trig*p['q']-p['cost'])-p['fee']-trig*p['q']*s.fee_taker(),4))
        return 'ok'
    # ------------------------------------------------------------ exits
    def collect(s,c,p):
        """Realized result of this trade from the exchange fills (entry+exit): exit avg px, fees, closedPnl, net."""
        t0=p.get('live',{}).get('t_entry_ms',0)-5000;fl=[];cl=[]
        for i in range(4):
            try:fl=[f for f in s.hx.fills_since(max(t0,0),c) if f.get('coin')==c]
            except HLXError:fl=[]
            cl=[f for f in fl if 'Close' in str(f.get('dir',''))]
            if cl:break
            time.sleep(1.0)
        if not cl:return None
        fee=sum(float(f['fee']) for f in fl);cp=sum(float(f['closedPnl']) for f in fl)
        sz=sum(float(f['sz']) for f in cl);px=sum(float(f['px'])*float(f['sz']) for f in cl)/sz
        op=[f for f in fl if 'Close' not in str(f.get('dir',''))];eq=sum(float(f['sz']) for f in op)
        return dict(entry_avg=(sum(float(f['px'])*float(f['sz']) for f in op)/eq if eq>0 else None),px=px,fee=fee,closed_pnl=cp,net=cp-fee,n=len(fl),exit_fee=sum(float(f['fee']) for f in cl),t_last=max(int(f['time']) for f in cl))
    def market_close(s,c,p,reason='',szi=None):
        """Reduce-only IOC close, widening slippage up to 3 times; then cancel the coin's resting orders. Returns True when flat."""
        szdec,_=s.tick_info(c)
        for i,slip in enumerate((s.close_slip,s.close_slip*2,s.close_slip*5)):
            if s.dry:x_szi=szi if szi is not None else (sd(p)*p['q'] if p else None)
            else:
                x=s.refresh(0.0)['positions'].get(c);x_szi=x['szi'] if x else None
            if x_szi is None:break
            bid,ask=s.hx.book(c);is_buy=x_szi<0;ref=ask if is_buy else bid
            if not ref:ref=s.hx.mids().get(c)
            lim=px_round(ref*(1+slip) if is_buy else ref*(1-slip),szdec,'up' if is_buy else 'down')
            s.hx.ioc(c,is_buy,sz_round(abs(x_szi),szdec,'down') or abs(x_szi),lim,True,label=f'CLOSE {c} reduce-only IOC attempt {i+1} {reason[:40]}')
            if s.dry:return True
            time.sleep(0.8)
        flat=c not in s.refresh(0.0)['positions']
        if flat:s.cancel_coin_orders(c)
        return flat
    def cancel_coin_orders(s,c):
        if s.dry:return
        try:
            for o in s.hx.open_orders():
                if o.get('coin')==c:
                    try:s.hx.cancel(c,o['oid'],f'cancel resting order {c}')
                    except HLXError as ex:s.ev('LIVE_WARN',c,f'cancel failed {ex}')
        except HLXError as ex:s.ev('LIVE_WARN',c,f'open orders failed {ex}')
    def close(s,book,c,p,px,why,stop,kind,tnow):
        """Called from LiveBook.close(). Returns dict(px, fee_rate, net) with the REAL result, or raises VetoClose."""
        resting=(kind!='EXIT_15M')
        if s.dry:
            s.market_close(c,p,why);return dict(px=px,simulated=True)
        st=s.refresh(0.0);x=st['positions'].get(c)
        if x is None:      # exchange stop already executed
            res=s.collect(c,p);s.veto_t.pop(c,None);return res
        if resting:        # let the resting exchange stop do the job; backstop after `stop_backstop_s` if it did not fire
            now_=time.time();t0,last=s.veto_t.get(c,(now_,now_))
            if now_-last>60:t0=now_                      # an earlier brief touch is forgotten: grace counts from the current continuous crossing
            s.veto_t[c]=(t0,now_)
            if now_-t0<s.veto_grace:raise VetoClose()
            s.ev('LIVE_STOP_BACKSTOP',c,f'engine stop level crossed {s.veto_grace:.0f}s ago but the exchange stop has not executed: closing at market (reduce-only)')
        flat=s.market_close(c,p,why);s.veto_t.pop(c,None)
        if not flat:
            s.ev('LIVE_CLOSE_FAILED',c,'could not flatten the position: will retry next tick (stop order stays resting)')
            raise VetoClose()
        return s.collect(c,p)
    def flatten_all(s,reason):
        """Emergency: market-close every position and cancel every resting order (works without any book state)."""
        out=[]
        st=s.refresh(0.0)
        for c,x in st['positions'].items():
            ok=s.market_close(c,{},reason,szi=x['szi']);out.append((c,x['szi'],ok))
        if not s.dry:
            try:
                for o in s.hx.open_orders():
                    try:s.hx.cancel(o['coin'],o['oid'],'cancel all')
                    except HLXError as ex:s.ev('LIVE_WARN',o.get('coin'),f'cancel failed {ex}')
            except HLXError as ex:s.ev('LIVE_WARN','*',f'open orders failed {ex}')
        else:s.hx.log(dict(event='DRY_RUN_WOULD_SEND',label='cancel all open orders (frontendOpenOrders -> cancel each)'))
        s.ev('LIVE_FLATTEN',',' .join(c for c,_,_ in out) or '-',f'flatten_all ({reason}): '+(', '.join(f'{c} {z} -> {"flat" if ok else "NOT FLAT"}' for c,z,ok in out) or 'no open positions'))
        return out
    def stop_sync(s,book,c,tnow):
        if c not in book.pos:return
        r=s.ensure_stop(book,c,tnow)
        if r=='close_now':
            s.ev('LIVE_STOP_FAIL_CLOSE',c,'stop could not be placed 3 times: closing the position at market')
            flat=s.market_close(c,book.pos[c],'stop failed')
            if flat or s.dry:book.settle_external(c,tnow,'Đóng ngay vì không đặt được lệnh stop trên sàn')
    def status(s,book):
        st=s.st or {}
        return dict(dry_run=s.dry,sync_ok=s.sync_ok,halted=s.halted,foreign=sorted(s.foreign),kill_file=os.path.exists(s.kill_path),abstraction=st.get('abstraction'),
                    usdc_total=st.get('usdc_total'),base=st.get('base'),equity=st.get('equity'),upnl=st.get('upnl'),exchange_positions={c:dict(szi=x['szi'],entryPx=x['entryPx'],upnl=x['upnl'],liqPx=x['liqPx']) for c,x in (st.get('positions') or {}).items()},
                    stops={c:(p.get('live') or {}).get('stop') for c,p in book.pos.items()},seeds=[dict(coin=z['coin'],status=z['status'],ref_px=z['ref_px']) for z in s.seeds],
                    max_notional=s.max_notional,leverage=s.lev,margin_mode=s.margin_mode,n_orders_sent=getattr(s.hx,'n_sent',0))
    # ------------------------------------------------------------ seeds (take the paper LINK signal if still valid)
    def seed_candidates(s,book,m,UL,BARS,tnow):
        out=[]
        for z in s.seeds:
            if z['status']!='pending':continue
            c=z['coin'];side=z.get('side','L');px=m.get(c)
            exp=pd.Timestamp(z['expires']) if z.get('expires') else None
            if (exp is not None and pd.Timestamp(tnow)>exp) or time.time()-s.t_start>float(z.get('window_min',30))*60:
                z['status']='expired';s.save_seeds();s.ev('LIVE_SEED_EXPIRED',c,f'seed {side} {c} (paper entry {z["ref_px"]} {z.get("since","")}) not taken within its window');continue
            if c in book.pos:z['status']='entered';continue
            if px is None or c not in BARS:continue
            cl=BARS[c][BARS[c].closed];b=cl.iloc[-1];dev=(px/float(z['ref_px'])-1)*100;tol=float(z.get('tol_pct',0.3))
            if abs(dev)>tol:
                z['status']='skipped';s.save_seeds();s.blocked[c]=float(b.lo)
                s.ev('LIVE_SEED_SKIPPED',c,f'seed {side} {c}: price {px} is {dev:+.2f}% from the paper entry {z["ref_px"]} (tolerance ±{tol}%) -> NOT entering; {c} stays blocked until price leaves the 4h lower band {float(b.lo):.6g} and touches it again',price=px,dev_pct=round(dev,3));continue
            if c not in UL:
                s.ev('LIVE_SEED_WAIT',c,f'seed {side} {c}: not in the current LONG shortlist / max leverage < {s.lev}x -> waiting (rules)') if not z.get('waitlog') else None;z['waitlog']=1;continue
            b2=b.copy();b2['lo']=max(float(b.lo),px) if side=='L' else b.lo     # seed: the paper band touch already happened; trigger condition satisfied at the current price
            out.append((c,side,b2,z))
        return out
    def save_seeds(s):
        if s.dry:return
        try:
            with open(s.seed_path,'w') as f:json.dump({z['key']:z['status'] for z in s.seeds},f)
        except OSError:pass
    def seed_entered(s,book,c,bars,tnow):
        for z in s.seeds:
            if z['status']=='pending' and z['coin']==c and c in book.pos:
                z['status']='entered';s.save_seeds();p=book.pos[c];g=sd(p)
                try:      # carry the paper position's progress that is price-based: has the 4h mid band been touched since the paper entry?
                    from hl import candles
                    x=candles(c,'15m',0.2);t0=pd.Timestamp(z['since']);x=x[x.index>=t0.floor('15min')]
                    mid=float(bars.iloc[-1].mid)
                    if len(x) and ((x.h.max()>=mid) if g>0 else (x.l.min()<=mid)):p['mid4h_hit']=str(tnow);p['mid4h_lv']=round(mid,6)
                except Exception as ex:s.ev('LIVE_WARN',c,f'seed state carry-over failed: {ex}')
                s.ev('LIVE_SEED_ENTERED',c,f'seed {c}: entered from the paper signal of {z.get("since")} (paper entry {z["ref_px"]}); management = engine rules on the live position (paper had no BE/lock/trail yet; mid4h_hit={p.get("mid4h_hit")})')

class LiveBook(Book15):
    """Book15 (BASE_SL250_X10 engine) + real execution. live: Live instance (set by the bot), live_base: realized equity from the exchange."""
    live=None;live_base=None
    def __getstate__(s):
        d=Book15.__getstate__(s);d['live']=None;return d
    def base(s):return float(s.live_base) if s.live_base is not None else 0.0
    def scale(s):return max(s.base(),0.0)/CFG['capital']
    def leg(s,k):return math.floor(s.L[k]*s.scale()*1e4+1e-9)/1e4          # round DOWN to 4 decimals: never above the 30% margin cap
    def equity(s,prices):return s.base()+sum(s.unreal(c,prices.get(c,s.pos[c]['last'])) for c in s.pos)
    def try_entry(s,c,t,px,bars,reg,rk=None,side='L',band=None,blbl='4h'):
        if s.live is None:raise RuntimeError('LiveBook without executor')
        if s.live.entry_veto(s,c,side,px):return
        had=c in s.pos;cap=[];old=s.log;s.log=cap.append
        try:r=Book15.try_entry(s,c,t,px,bars,reg,rk=rk,side=side,band=band,blbl=blbl)
        finally:s.log=old
        if c in s.pos and not had:
            p=s.pos[c];ok,info=s.live.open(s,c,p,px,t)
            for e in cap:
                if e['event']=='ENTRY':
                    if ok:
                        e.update(price=round(info['avg'],6),notional=round(info['avg']*info['filled'],4),live=info)
                        e['reason']+=f' | LIVE: khớp {info["filled"]} @ {info["avg"]} (notional {info["avg"]*info["filled"]:.2f}$, ký quỹ x{s.live.lev} {s.live.margin_mode} {info["avg"]*info["filled"]/s.live.lev:.3f}$, phí vào {info["fee"]}$, lệch giá so với tín hiệu {info["slip_vs_signal_pct"]}%)'+(' [DRY-RUN: khớp giả lập]' if s.live.dry else '')
                    else:e=None
                if e is not None:old(e)
            if not ok:
                del s.pos[c]
                if not s.live.dry:s.live.write_journal({})
                kf=(c,str(info.get('why'))[:50])
                if time.time()-s.live.veto_log.get(kf,0)>300:
                    s.live.veto_log[kf]=time.time();s.live.ev('LIVE_ENTRY_FAILED',c,f'{SIDE(p)} {c}: không vào được lệnh thật: {info.get("why")}',info=info)
                return
            s.live.write_journal({}) if not s.live.dry else None
            res='fail'
            for _i in range(3):
                res=s.live.ensure_stop(s,c,t,force=True)
                if res=='ok':break
                if not s.live.dry:time.sleep(1.0)
            if res!='ok':
                s.live.ev('LIVE_ENTRY_ABORTED',c,'stop could not be placed after the fill: closing the position immediately')
                try:s.live.market_close(c,s.pos[c],'no stop')
                except Exception as ex:s.live.ev('LIVE_CLOSE_FAILED',c,str(ex))
                s.settle_external(c,t,'Đóng ngay vì không đặt được lệnh stop trên sàn')
                return
            s.live.seed_entered(s,c,bars,t)
            if s.live.save_cb:s.live.save_cb()
            return True
        for e in cap:old(e)
        return r
    def manage(s,c,t,px,bars,closed_bar=None,gap=False,live=False):
        ns=s.n_stops;p0=s.pos.get(c);snap=None
        if p0 is not None:snap={k:(list(p0[k]) if k=='fills' else p0.get(k)) for k in('k','q','cost','fee','margin','fills','add_blk')}
        try:r=Book15.manage(s,c,t,px,bars,closed_bar,gap,live)
        except VetoClose:
            s.n_stops=ns;return None
        p=s.pos.get(c)
        if snap is not None and p is not None and p['k']>snap['k']:s._live_add(c,p,snap,t)      # X5: the engine decided one or more ladder adds -> execute them for real
        return r
    def _live_add(s,c,p,snap,t):
        """Execute the add(s) the paper engine just booked (notional = leg(k)*lev each, same 30% cap as the engine). On failure revert the book to the pre-add snapshot."""
        L=s.live;n_add=p['cost']-snap['cost'];side='S' if p.get('side')=='S' else 'L';is_buy=side=='L';why=None;info={}
        def revert(w):
            for k,v in snap.items():p[k]=v
            kf=(c,'add',str(w)[:50])
            if time.time()-L.veto_log.get(kf,0)>300:L.veto_log[kf]=time.time();L.ev('LIVE_ADD_FAILED',c,f'{SIDE(p)} {c}: nhồi lệnh thật không thực hiện được: {w}',info=info)
        if L.kill_requested():return revert('KILL switch active')
        if L.halted:return revert('halted: '+L.halted)
        try:
            szdec,_=L.tick_info(c);bid,ask=L.hx.book(c)
            if not bid or not ask:return revert('no two-sided book')
            ref=ask if is_buy else bid
            if ask/bid-1>L.max_spread:return revert(f'spread {(ask/bid-1)*100:.3f}% > max')
            sz=hlx.sz_round(min(n_add,L.max_notional)/ref,szdec,'down');L.hx.max_open_notional=L.max_notional
            mn_=L.hx.min_notional(c)
            if sz*ref<mn_:return revert(f'add notional {sz*ref:.2f}$ < exchange minimum {mn_}$')
            if not L.dry:
                ad=L.hx.asset_data(c);avail=float(ad['availableToTrade'][0 if is_buy else 1]);need=sz*ref/L.lev*1.01
                if avail<need:return revert(f'availableToTrade {avail} < needed {need:.3f}')
            lim=hlx.px_round(ref*(1+L.entry_slip) if is_buy else ref*(1-L.entry_slip),szdec,'up' if is_buy else 'down')
            r=L.hx.ioc(c,is_buy,sz,lim,False,label=f'ADD {SIDE(p)} {c} IOC (k={p["k"]-1})')
            if L.dry:avg=ref;fsz=sz;fee=avg*fsz*L.fee_taker()
            else:
                stt=hlx.parse_statuses(r)
                if not stt or stt[0]['kind']!='filled':return revert(f'add not filled: {stt[:1]}')
                avg=stt[0]['avgPx'];fsz=stt[0]['totalSz'];avg,fsz,fee=L.fill_of_oid(c,stt[0]['oid'],avg,fsz,avg*fsz*L.fee_taker())
        except HLXError as ex:return revert(f'HLXError {ex}')
        n=avg*fsz;p['q']=snap['q']+fsz;p['cost']=snap['cost']+n;p['fee']=snap['fee']+fee;p['margin']=(snap['margin'] or 0)+n/L.lev
        p['fills']=list(snap['fills'])+[(str(t),avg,n,'add')]
        info.update(avg=avg,filled=fsz,notional=round(n,4),fee=round(fee,6),limit_px=lim,simulated=L.dry)
        L.ev('LIVE_ADD',c,f'{SIDE(p)} {c}: nhồi thật {fsz} @ {avg} (notional {n:.2f}$, ký quỹ x{L.lev} {n/L.lev:.2f}$); giá vốn mới {p["cost"]/p["q"]:.6g}'+(' [DRY-RUN: khớp giả lập]' if L.dry else ''),info=info)
        L.ensure_stop(s,c,t,force=True)
        if L.save_cb:L.save_cb()
    def close(s,c,t,px,why,fee,stop=False,frac=1.0,kind=None,extra=None):
        p=s.pos[c];res=s.live.close(s,c,p,px,why,stop,kind,t)       # may raise VetoClose
        return s._finish(c,t,px,why,fee,stop,frac,kind,extra,res)
    def _finish(s,c,t,px,why,fee,stop,frac,kind,extra,res):
        p=s.pos[c]
        if res and res.get('px'):
            px=res['px']
            if res.get('exit_fee') is not None and px*p['q']>0:fee=res['exit_fee']/(px*p['q'])
            why=why+f' | LIVE: khớp thật {px:.6g}'
        cap=[];_old=s.log;s.log=cap.append      # FIX 2026-10-30: hold the EXIT log (-> trades_live.csv row) until the exchange net is in
        try:tr=Book.close(s,c,t,px,why,fee,stop,frac,kind,extra)
        finally:s.log=_old
        if res and res.get('net') is not None and not s.live.dry:        # exchange numbers (closedPnl - fees, entry+exit) replace the model's
            d=res['net']-tr.get('net',0.0);s.cash_pnl+=d;tr['net_model']=tr['net'];tr['net']=round(res['net'],6);tr['fee']=round(res['fee'],6)
            if res.get('entry_avg'):tr['avg']=round(res['entry_avg'],6)
            if s.rlog:s.rlog[-1]=(s.rlog[-1][0],res['net'])
            for e in cap:
                if 'net' in e:e['net']=tr['net']
        for e in cap:_old(e)
        s.live.write_journal({}) if not s.live.dry else None
        return tr
    def settle_external(s,c,t,why=None):
        """The exchange closed the position by itself (resting stop executed / liquidation / manual): book the real result."""
        p=s.pos[c];lv=p.get('live',{});stp=lv.get('stop') or {};k=stp.get('kind','?')
        res=s.live.collect(c,p) if not s.live.dry else None
        px=res['px'] if res else p['last']
        sl=(k=='SL') and why is None
        if sl:s.n_stops+=1
        why=why or (f'Lệnh stop chờ sẵn trên sàn đã khớp ({"cắt lỗ SL" if sl else "dời SL/khóa lãi: "+str(k)}) trigger {stp.get("trigger")}, khớp thật {px:.6g}' if res else f'Vị thế đã đóng trên sàn (không tìm thấy fill), dùng giá cuối {px:.6g}')
        s.live.veto_t.pop(c,None)
        tr=s._finish(c,t,px,why,s.live.fee_taker(),sl,1.0,'STOP' if sl else None,None,res)
        return tr
