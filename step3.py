"""BaseAster STEP 3 (Vien 03/11/2026 t625u/t629u/t631u): trade ASTER after a pump (SHORT) / dump (LONG) that the 0.7% strong-run filter (step 2) blocked.
Detection at each closed 1h bar: A = ASTER % change vs close 8 bars ago, B = BTC same.
  pump -> SHORT: A>=5, A-B>=5, B<=+0.5 ; dump -> LONG: A<=-5, B-A>=5, B>=-0.5 ; same-direction detections >=48h apart.
Wait <=24 1h bars for the break (SHORT: close < previous bar low; LONG: close > previous bar high); peak/trough = extreme since detection (break bar excluded, as backtest).
Needs a step-2 (mom4h) block of the same side between detection_bar_open-8h and the break bar close. Entry market if break bar closed <=20 min ago and price within +-1% of its close.
SL = peak*1.01 / trough*0.99 (exchange STOP_MARKET closePosition), TP = entry*0.88 / *1.12 (LIMIT reduce-only), time exit 72h; skip if SL distance >15%.
Size: notional = risk 25% * USDT wallet / SL distance, cap 5x wallet, isolated x5. Priority over BaseAster (closes an ASTER BaseAster position first); while held, BaseAster opens nothing (position is 'foreign' to the BaseAster book)."""
import os,json,time,math,threading,datetime as dt
import pandas as pd
import bnx as hlx
from bnx import HLXError
COIN='ASTER'
DEF=dict(enabled=True,coin='ASTER',win=8,move_pct=5.0,rel_pct=5.0,btc_tol_pct=0.5,gap_h=48,wait_bars=24,sl_buf_pct=1.0,tp_pct=12.0,hold_h=72,risk_pct=25.0,max_notional_x=5.0,
         max_sl_dist_pct=15.0,entry_max_min=20,entry_max_dev_pct=1.0,lev=5,block_pre_h=8,require_block=True,entry_slip_pct=0.3)
def vn_now():return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)+dt.timedelta(hours=7)
H=pd.Timedelta('1h')
class Detector:
    """Pure bar logic (shared by live + offline replay test). Bars: DataFrame index = 1h bar OPEN time (VN), cols o h l c, closed only."""
    def __init__(s,C,st=None):
        s.C=C;st=st or {}
        s.last_det={k:(pd.Timestamp(v) if v else None) for k,v in (st.get('last_det') or {'S':None,'L':None}).items()}
        s.pend=[dict(p) for p in (st.get('pend') or [])];s.last_bar=pd.Timestamp(st['last_bar']) if st.get('last_bar') else None
    def dump(s):return dict(last_det={k:(str(v) if v is not None else None) for k,v in s.last_det.items()},pend=s.pend,last_bar=str(s.last_bar) if s.last_bar is not None else None)
    def step(s,A,Bc,j):
        """process closed bar position j of A. Returns list of events (dict kind=DETECT|BREAK|DROP)."""
        C=s.C;out=[];t=A.index[j];r=A.iloc[j]
        for p in list(s.pend):
            sh=p['side']=='S';prev=A.iloc[j-1]
            brk=(r.c<prev.l) if sh else (r.c>prev.h)
            if brk:
                s.pend.remove(p);out.append(dict(kind='BREAK',side=p['side'],det_bar=p['det_bar'],peak=p['peak'],t_close=str(t+H),close=float(r.c)));continue
            p['peak']=max(p['peak'],float(r.h)) if sh else min(p['peak'],float(r.l));p['n']+=1
            if p['n']>=C['wait_bars']:s.pend.remove(p);out.append(dict(kind='DROP',side=p['side'],det_bar=p['det_bar'],why=f"no break within {C['wait_bars']} 1h bars"))
        w=int(C['win'])
        if j>=w and t-pd.Timedelta(hours=w)==A.index[j-w]:
            a=(r.c/A.c.iloc[j-w]-1)*100;b0=Bc.get(A.index[j-w]);b1=Bc.get(t)
            if b0 and b1:
                b=(b1/b0-1)*100
                for sd,ok in (('S',a>=C['move_pct'] and a-b>=C['rel_pct'] and b<=C['btc_tol_pct']),('L',a<=-C['move_pct'] and b-a>=C['rel_pct'] and b>=-C['btc_tol_pct'])):
                    ld=s.last_det.get(sd)
                    if ok and (ld is None or t-ld>=pd.Timedelta(hours=C['gap_h'])):
                        s.last_det[sd]=t;pk=float(r.h) if sd=='S' else float(r.l)
                        s.pend=[p for p in s.pend if p['side']!=sd]+[dict(side=sd,det_bar=str(t),peak=pk,n=0,a=round(a,3),b=round(b,3))]
                        out.append(dict(kind='DETECT',side=sd,det_bar=str(t),t_close=str(t+H),a=round(a,3),b=round(b,3),peak=pk))
        s.last_bar=t;return out
def blocked_in(blocks,side,det_bar,t_close,pre_h):
    lo=pd.Timestamp(det_bar)-pd.Timedelta(hours=pre_h);hi=pd.Timestamp(t_close)
    return [b for b in blocks if b[1]==side and lo<=pd.Timestamp(b[0])<=hi]
class Step3:
    def __init__(s,B,cfg,LIVE,candles,notify,emit):
        s.B=B;s.C=dict(DEF,**(cfg.get('step3') or {}));s.on=bool(s.C.get('enabled'));s.L=LIVE;s.hx=LIVE.hx;s.candles=candles;s.notify=notify;s.emit=emit
        s.path=B+'state/step3.json';s.trf=B+'data/step3_trades.csv';s.lk=threading.Lock()
        try:st=json.load(open(s.path,encoding='utf-8'))
        except Exception:st={}
        s.D=Detector(s.C,st.get('det'));s.blocks=[tuple(x) for x in st.get('blocks',[])];s.pos=st.get('pos');s.breaks=st.get('breaks',[]);s.hist=st.get('hist',[])
        s.A=None;s.Bc=None;s.fetch_t=0;s.err=None;s.last_chk=0;s.init_done=bool(st)
        if not st:s._seed_blocks()
        if s.pos:s.L.foreign.add(s.C['coin'])
    # ---------------- persistence / io
    def save(s):
        d=dict(det=s.D.dump(),blocks=[list(b) for b in s.blocks],pos=s.pos,breaks=s.breaks[-20:],hist=s.hist[-50:],updated=str(vn_now().replace(microsecond=0)),cfg=s.C)
        try:
            json.dump(d,open(s.path+'.tmp','w',encoding='utf-8'),ensure_ascii=False,indent=1,default=str);os.replace(s.path+'.tmp',s.path)
        except Exception as ex:print('step3 save',ex,flush=True)
    def ev(s,event,reason,tg=True,**kw):
        e=s.L.ev(event,s.C['coin'],reason,**kw)
        if tg:
            try:s.notify('[LIVE] '+reason)
            except Exception:pass
        return e
    def _seed_blocks(s):
        """first start: step-2 (mom4h) blocks of the last 3 days from events_live.jsonl"""
        try:
            lim=str(vn_now()-dt.timedelta(days=3))
            with open(s.B+'events_live.jsonl',encoding='utf-8',errors='replace') as f:
                for ln in f:
                    if 'manh vao dai' not in ln or '"ENTRY_BLOCKED' not in ln:continue
                    try:e=json.loads(ln)
                    except Exception:continue
                    if e.get('coin')==s.C['coin'] and str(e.get('time'))>=lim:s.blocks.append((str(e['time']),'S' if e.get('side')=='SHORT' else 'L'))
        except Exception as ex:print('step3 seed',ex,flush=True)
    def note_block(s,c,t,L_):
        """hook from the mom4h filter (step 2 failed)"""
        if not s.on or c!=s.C['coin']:return
        k=(str(pd.Timestamp(t).replace(microsecond=0)),'L' if L_ else 'S')
        with s.lk:
            if not s.blocks or s.blocks[-1]!=k:s.blocks.append(k)
    # ---------------- data
    def _bars(s,force=False):
        now=pd.Timestamp(vn_now());need=now.floor('h')-H
        if not force and s.A is not None and len(s.A) and s.A.index[-1]>=need:return True
        if not force and time.time()-s.fetch_t<20:return s.A is not None
        s.fetch_t=time.time()
        try:
            a=s.candles(s.C['coin'],'1h',4);a=a[a.closed];b=s.candles('BTC','1h',4);b=b[b.closed]
            s.A=a;s.Bc=b.c.to_dict();s.err=None;return True
        except Exception as ex:s.err=f'data: {ex}'[:150];return s.A is not None
    # ---------------- main tick (called once per 60s loop, after live sync)
    def tick(s,LB,tnow,m,close_ba):
        if not s.on:return
        try:
            if s.pos:s._manage(tnow)
            if not s._bars():return
            A=s.A
            if s.D.last_bar is None:            # first run: replay the last ~60h so pending waits / 48h spacing are reconstructed (no stale entries: 20-min rule)
                s.D.last_bar=A.index[max(0,len(A)-61)]
            new=[j for j in range(len(A)) if A.index[j]>s.D.last_bar and j>=1]
            for j in new:
                for e in s.D.step(A,s.Bc,j):s._on_event(e,LB,tnow,m,close_ba)
            cut=str(vn_now()-dt.timedelta(hours=60));s.blocks=[b for b in s.blocks if b[0]>=cut]
            for b in list(s.breaks):      # retry a fresh break (e.g. a failed close of BaseAster) until the 20-min window ends
                if b.get('status')=='retry':s._try_enter(b,LB,tnow,m,close_ba)
            s.save()
        except Exception as ex:
            import traceback;s.err=repr(ex)[:200];print('step3 tick error',traceback.format_exc()[-600:],flush=True)
    def _on_event(s,e,LB,tnow,m,close_ba):
        sd='SHORT' if e['side']=='S' else 'LONG';fresh=(pd.Timestamp(tnow)-pd.Timestamp(e.get('t_close',tnow))).total_seconds()<=s.C['entry_max_min']*60
        if e['kind']=='DETECT':
            s.hist.append(dict(e,time=str(tnow)))
            s.ev('STEP3_DETECT',f"BƯỚC 3 – phát hiện {'đợt bơm' if e['side']=='S' else 'đợt sập'} ASTER (nến 1h {e['det_bar'][:16]} đóng {e['t_close'][11:16]}): ASTER {e['a']:+.2f}% / BTC {e['b']:+.2f}% trong 8h. Chờ tối đa {s.C['wait_bars']} nến 1h cho nến gãy để vào {sd}.",tg=fresh,**{k:e[k] for k in('side','a','b','peak','det_bar')})
        elif e['kind']=='DROP':
            s.ev('STEP3_DROP',f"BƯỚC 3 – bỏ {sd} (phát hiện {e['det_bar'][:16]}): {e['why']}",tg=False)
        elif e['kind']=='BREAK':
            b=dict(e);b['status']='new';s.breaks.append(b);s._try_enter(b,LB,tnow,m,close_ba)
    def _try_enter(s,b,LB,tnow,m,close_ba):
        C=s.C;c=C['coin'];sh=b['side']=='S';sd='SHORT' if sh else 'LONG'
        age=(pd.Timestamp(tnow)-pd.Timestamp(b['t_close'])).total_seconds()
        sl=b['peak']*(1+C['sl_buf_pct']/100) if sh else b['peak']*(1-C['sl_buf_pct']/100);dist=abs(sl/b['close']-1)
        def skip(why,tg=True):
            b['status']='skip:'+why;s.ev('STEP3_SKIP',f"BƯỚC 3 – không vào {sd} ASTER (nến gãy đóng {b['t_close'][11:16]} giá {b['close']:.5g}): {why}",tg=tg and age<=3600)
        blk=blocked_in(s.blocks,b['side'],b['det_bar'],b['t_close'],C['block_pre_h'])
        if C.get('require_block',True) and not blk:return skip('bước 2 (lọc 0,7%) không chặn tín hiệu BaseAster cùng chiều trong cửa sổ → không áp dụng bước 3',tg=False)
        if dist>C['max_sl_dist_pct']/100:return skip(f'khoảng cắt lỗ {dist*100:.2f}% > {C["max_sl_dist_pct"]:g}%')
        if age>C['entry_max_min']*60:return skip(f'nến gãy đã đóng {age/60:.0f} phút (> {C["entry_max_min"]} phút)',tg=b['status']=='retry')
        if s.pos:return skip('đang giữ lệnh bước 3 khác')
        g=getattr(s,'guard',None)      # BaseAster SAFE: cầu dao (nghỉ sau SL / lỗ ngày / sụt từ đỉnh)
        if g is not None:
            why=g.check(LB,tnow,b['side'])
            if why:return skip(why)
        px=m.get(c)
        if px is None:b['status']='retry';return
        if abs(px/b['close']-1)>C['entry_max_dev_pct']/100:return skip(f'giá {px:.5g} lệch {(px/b["close"]-1)*100:+.2f}% so với giá đóng nến gãy (> ±{C["entry_max_dev_pct"]:g}%)')
        if s.L.kill_requested():return skip('KILL switch')
        if s.L.halted:return skip('bot halted: '+str(s.L.halted))
        if s.L.dry:return skip('DRY-RUN (không gửi lệnh)')
        if c in LB.pos:                   # priority: close the BaseAster position on ASTER first
            ok=close_ba(c,tnow,px,f'Đóng theo giá thị trường để ưu tiên BƯỚC 3 ({sd} sau {"đợt bơm" if sh else "đợt sập"})')
            if not ok or c in LB.pos:b['status']='retry';s.ev('STEP3_WAIT','BƯỚC 3 – chưa đóng được lệnh BaseAster ASTER, thử lại vòng sau',tg=False);return
        others=list(s.L.refresh(0.0)['positions'])
        if others:b['status']='retry';s.ev('STEP3_WAIT',f'BƯỚC 3 – sàn còn vị thế {others}, chờ phẳng rồi vào',tg=False);return
        r=s._open(b,sl,dist,px,tnow)
        b['status']='entered' if r else 'retry'
    def _open(s,b,sl,dist,px,tnow):
        C=s.C;c=C['coin'];hx=s.hx;sh=b['side']=='S';is_buy=not sh;sd='SHORT' if sh else 'LONG'
        try:
            st=s.L.refresh(0.0);base=float(st['base']);avail=float(st.get('available') or base)
            notional=min(C['risk_pct']/100*base/dist,C['max_notional_x']*base,avail*C['lev']*0.97)
            A=hx.asset(c);sp=A['spec'];bid,ask=hx.book(c)
            if not bid or not ask:raise HLXError('no two-sided book')
            if ask/bid-1>s.L.max_spread:raise HLXError(f'spread {(ask/bid-1)*100:.3f}% > max')
            ref=ask if is_buy else bid;sz=hlx.sz_round(notional/ref,sp,'down')
            if sz<=0 or sz*ref<hx.min_notional(c):raise HLXError(f'size {sz} too small (notional {sz*ref:.2f}$)')
            r=hx.set_leverage(c,C['lev'],cross=False,label='STEP3 leverage')
            if r.get('status')!='ok':raise HLXError(f'set leverage failed {r}')
            lim=hlx.px_round(ref*(1+C['entry_slip_pct']/100) if is_buy else ref*(1-C['entry_slip_pct']/100),sp,'up' if is_buy else 'down')
            hx.max_open_notional=sz*lim*1.01
            t_ms=int(time.time()*1000)
            r=hx.ioc(c,is_buy,sz,lim,False,label=f'STEP3 ENTRY {sd} {c}')
            stt=hlx.parse_statuses(r)
            if not stt or stt[0]['kind']!='filled':raise HLXError(f'entry not filled: {stt[:1] or r}')
            avg,fsz=float(stt[0]['avgPx']),float(stt[0]['totalSz'])
            try:avg,fsz,fee=s.L.fill_of_oid(c,stt[0]['oid'],avg,fsz,avg*fsz*0.0005)
            except Exception:fee=avg*fsz*0.0005
        except Exception as ex:
            s.ev('STEP3_ENTRY_FAILED',f'BƯỚC 3 – vào {sd} ASTER thất bại: {ex}'[:400],tg=True);return False
        tp=avg*(1-C['tp_pct']/100) if sh else avg*(1+C['tp_pct']/100)
        s.pos=dict(side=b['side'],entry=avg,q=fsz,notional=round(avg*fsz,4),fee_in=fee,t0=str(tnow),t_entry_ms=t_ms,sl=sl,tp=tp,peak=b['peak'],det_bar=b['det_bar'],break_close=b['t_close'],
                   dist=dist,base=base,until=str(pd.Timestamp(tnow)+pd.Timedelta(hours=C['hold_h'])),sl_oid=None,tp_oid=None)
        s.L.foreign.add(c);s.save()
        ok=s._place_orders()
        if not ok:
            s.ev('STEP3_ABORT','BƯỚC 3 – không đặt được lệnh cắt lỗ trên sàn: đóng vị thế ngay',tg=True)
            s._close_market(tnow,'không đặt được SL');return True
        s.ev('STEP3_ENTRY',f"BƯỚC 3 – VÀO {sd} ASTER {fsz:g} @ {avg:.5g} (notional {avg*fsz:.2f}$, ký quỹ x{C['lev']} isolated {avg*fsz/C['lev']:.2f}$). Phát hiện {b['det_bar'][:16]}, {'đỉnh' if sh else 'đáy'} {b['peak']:.5g}. SL {sl:.5g} ({dist*100:.2f}%, rủi ro ~{C['risk_pct']:g}% vốn {base:.2f}$), TP {tp:.5g} ({'-' if sh else '+'}{C['tp_pct']:g}%), đóng sau {C['hold_h']}h ({s.pos['until'][:16]}).",
             price=avg,side=sd,size=fsz,sl=sl,tp=tp)
        s.save();return True
    def _place_orders(s):
        C=s.C;c=C['coin'];p=s.pos;hx=s.hx;sh=p['side']=='S';sp=hx.asset(c)['spec'];ok=True
        if not p.get('sl_oid'):
            ok=False
            for i in range(3):
                try:
                    trig=hlx.px_round(p['sl'],sp,'down' if sh else 'up')
                    r=hx.stop(c,sh,p['q'],trig,None,label=f'STEP3 SL {c} {trig}');stt=hlx.parse_statuses(r)
                    if stt and stt[0]['kind'] in('resting','ok'):p['sl_oid']=stt[0].get('oid');p['sl_trig']=trig;ok=True;break
                    s.L.ev('LIVE_WARN',c,f'STEP3 SL rejected {r}'[:300])
                except Exception as ex:s.L.ev('LIVE_WARN',c,f'STEP3 SL error {ex}'[:300])
                time.sleep(1)
        if ok and not p.get('tp_oid'):
            try:
                tpx=hlx.px_round(p['tp'],sp,'up' if sh else 'down')
                r=hx.limit_reduce(c,sh,p['q'],tpx,label=f'STEP3 TP {c} {tpx}');stt=hlx.parse_statuses(r)
                if stt and stt[0]['kind'] in('resting','filled','ok'):p['tp_oid']=stt[0].get('oid');p['tp_px']=tpx
                else:s.L.ev('LIVE_WARN',c,f'STEP3 TP rejected {r}'[:300])
            except Exception as ex:s.L.ev('LIVE_WARN',c,f'STEP3 TP error {ex}'[:300])
        s.save();return ok
    def _manage(s,tnow):
        C=s.C;c=C['coin'];p=s.pos
        st=s.L.st or s.L.refresh(0.0);x=st['positions'].get(c)
        if x is None:
            if time.time()*1000-p['t_entry_ms']<15000:return
            return s._settle(tnow,'exchange')
        if (x['szi']>0)!=(p['side']=='L'):s.L.ev('LIVE_WARN',c,'STEP3 exchange side differs from state');return
        q=abs(x['szi'])
        if abs(q-p['q'])/p['q']>0.02:p['q']=q;p['tp_oid']=None if p.get('tp_oid') is None else p['tp_oid']
        if time.time()-s.last_chk>120:      # re-check resting SL / TP on the exchange
            s.last_chk=time.time()
            try:
                oo=[o for o in s.hx.open_orders() if o.get('coin')==c]
                ids={str(o['oid']) for o in oo}
                if p.get('sl_oid') is not None and str(p['sl_oid'])!='unknown' and str(p['sl_oid']) not in ids:p['sl_oid']=None;s.L.ev('LIVE_STOP_MISSING',c,'STEP3 SL not on exchange: re-placing')
                if p.get('tp_oid') is not None and str(p['tp_oid']) not in ids:p['tp_oid']=None;s.L.ev('LIVE_WARN',c,'STEP3 TP not on exchange: re-placing')
                if not p.get('sl_oid') or not p.get('tp_oid'):
                    if not s._place_orders() and not p.get('sl_oid'):s._close_market(tnow,'SL mất và không đặt lại được')
            except HLXError as ex:s.L.ev('LIVE_WARN',c,f'STEP3 orders check {ex}'[:200])
        if pd.Timestamp(tnow)>=pd.Timestamp(p['until']):s._close_market(tnow,f'hết {C["hold_h"]}h')
    def _close_market(s,tnow,why):
        c=s.C['coin'];s.pos['close_why']=why
        flat=s.L.market_close(c,None,'STEP3 '+why)
        if flat:s._settle(tnow,why)
        else:s.L.ev('LIVE_CLOSE_FAILED',c,'STEP3 market close failed, retry next loop')
    def _settle(s,tnow,src):
        c=s.C['coin'];p=s.pos
        s.L.cancel_coin_orders(c)
        res=None
        try:res=s.L.collect(c,dict(live=dict(t_entry_ms=p['t_entry_ms'])))
        except Exception as ex:print('step3 collect',ex,flush=True)
        ex_px=res['px'] if res else None;net=res['net'] if res else None
        why=p.get('close_why')
        if not why and ex_px is not None:
            sh=p['side']=='S'
            why='TP 12%' if ((ex_px<=p['tp']*1.002) if sh else (ex_px>=p['tp']*0.998)) else ('SL' if ((ex_px>=p['sl']*0.99) if sh else (ex_px<=p['sl']*1.01)) else 'đóng trên sàn')
        why=why or 'đóng trên sàn (không tìm thấy fill)'
        sd='SHORT' if p['side']=='S' else 'LONG'
        row=dict(side=sd,det_bar=p['det_bar'],t0=p['t0'],entry=p['entry'],q=p['q'],notional=p['notional'],sl=p['sl'],tp=p['tp'],t1=str(tnow),exit=ex_px,why=why,net=net,fee=res['fee'] if res else None)
        try:pd.DataFrame([row]).to_csv(s.trf,mode='a',header=not os.path.exists(s.trf),index=False)
        except Exception as e_:print('step3 csv',e_,flush=True)
        s.pos=None;s.L.foreign.discard(c);s.save()
        s.ev('STEP3_EXIT',f"BƯỚC 3 – ĐÓNG {sd} ASTER: {why}; giá ra {ex_px if ex_px is None else f'{ex_px:.5g}'} (vào {p['entry']:.5g}), lãi/lỗ ròng {('%+.2f$'%net) if net is not None else '?'}.",price=ex_px,side=sd,net=net,why=why)
    def status(s):
        return dict(enabled=s.on,pos=s.pos,pending=s.D.pend,last_det=s.D.dump()['last_det'],last_bar=str(s.D.last_bar) if s.D.last_bar is not None else None,
                    blocks_recent=s.blocks[-6:],last_breaks=s.breaks[-3:],err=s.err)
