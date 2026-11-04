"""Strategy v2 core (shared by backtest + paper bot). BB 4h mean reversion, LONG + mirrored SHORT (Vien 2026-10-09 17:52). PAPER ONLY.
Position dict p['side']: 'L' (default, missing = long) or 'S'. Every price rule of the long side is mirrored for shorts."""
import math,pandas as pd,numpy as np
CFG=dict(capital=5000,lev=5,ladder=[750,250,250,250],add_step=1.5,max_pos=2,daily_target_usd=200,partial_frac=0.5,be_buf=1.002,gate2_u1_min=0.0,gate2_slope_min=0.0,
         alert_warn=-250,alert_crit=-400,cat_stop=-500,cat_stop_enabled=False,
         stop_slip=0.0005,slope_min=-3.0,vol_mult=1.5,body_min=0.6,fee_taker=0.00045,fee_maker=0.00015,dd_alert_pct=25)
def btc_regime(d1,w1):
    """d1,w1: BTC daily/weekly frames with bb() columns, closed bars only."""
    a=d1.iloc[-1];b=w1.iloc[-1]
    allow= not (a.pb<0 and b.pb<0.5)          # block longs: daily closed below lower band while weekly in lower half
    fav= a.pb<=0.5 and b.pb>=0.4               # user's favourite: daily low/mid band, weekly at/above mid
    allow_s= not (a.pb>1 and b.pb>0.5)       # mirror for shorts: daily closed above upper band while weekly in upper half
    fav_s= a.pb>=0.5 and b.pb<=0.6             # mirror of fav (%B -> 1-%B)
    return dict(allow=bool(allow),fav=bool(fav),allow_s=bool(allow_s),fav_s=bool(fav_s),d1_pb=round(float(a.pb),4),w1_pb=round(float(b.pb),4))
# SHORT gate (Vien 2026-10-10 17:12 "Bổ sung thêm rule short"): extra gate on top of every existing short rule, all branches.
# Path A (range-bound coin, upper-band touch): upper-band respect score >= a_score, sustained-breakout rate (upper touches whose close above the band
# did NOT come back inside within 2 bars, / touches, 90d PIT) <= a_bo, overshoot >=6% beyond the band rate <= a_deep, 1D 30-day high/low <= a_r30;
# and never while breaking the upper edge: last closed 4h candle closed ABOVE its upper band -> blocked until a 4h candle closes back inside (reversal).
# Path B (confirmed 4h breakdown): last closed 4h candle closed BELOW its lower band -> continuation short (4h books: at market while price is still
# <= that lower band; 1h books G6/G7: on their 1h upper-band touch = retest); coin must be in the group's LONG or SHORT dynamic list (group rule +
# 1D stability), upper-band score not required; G7's %B4h>=0.5 is waived for path B. gate_adds: also block short ADDS while the last 4h closed above the upper band.
SGATE=dict(a_score=0.78,a_bo=0.10,a_deep=0.25,a_r30=1.5,gate_adds=False)
def short_range_eval(A):
    """A: dict(score,n_touch,n_break,raw_fake,raw_deep,r30) of the upper band (PIT). Returns (ok, fails, metrics)."""
    G=SGATE;F=[]
    if not A or not A.get('n_touch'):return False,['Path A: chưa có thống kê dải trên 90 ngày'],{}
    nb=A.get('n_break') or 0;fk=round((A.get('raw_fake') or 0)*nb);bo=(nb-fk)/A['n_touch'];dp=A.get('raw_deep');r30=A.get('r30');sc=A.get('score') or 0
    M=dict(score=round(sc,3),bo=round(bo,3),deep=dp,r30=r30,n_touch=A['n_touch'])
    if sc<G['a_score']:F.append(f'Path A: điểm tôn trọng dải trên {sc:.3f} < {G["a_score"]}')
    if bo>G['a_bo']:F.append(f'Path A: tỉ lệ phá dải trên thật {bo:.2f} > {G["a_bo"]}')
    if dp is None or dp>G['a_deep']:F.append(f'Path A: tỉ lệ vượt dải trên ≥6% {dp} > {G["a_deep"]}')
    if r30 is None or r30>G['a_r30']:F.append(f'Path A: biên độ 30 ngày 1D x{r30} > x{G["a_r30"]}')
    return not F,F,M
BTC_GATE=None     # live bot sets a callable returning the latest BTC re-check dict; research engines leave it None (= allowed)
def btc_now(): return BTC_GATE() if BTC_GATE else None
LEV_LEGACY=5.0     # positions without p['margin'] (opened before the per-branch leverage change 2026-10-15) were opened at the global x5: margin = cost/5
def pmargin(p): return p['margin'] if p.get('margin') is not None else p['cost']/LEV_LEGACY
BTC_RECHECK=dict(m15x4=-1.5,h1_now=-1.5,h1_closed=-1.5,vol_now=1.0,vol_closed=1.5)
def btc_recheck(h1,f15,px,P=None):
    """Short-term BTC dump check (Vien 22:42). h1/f15: BTC 1h (bb() applied) and 15m candles incl. the one in progress; px: BTC price now.
    Block if: net change over the last 4x15m (open of the 4th-last 15m candle -> now) <= -1.5%; or the current 1h candle <= -1.5%;
    or the last closed 1h candle <= -1.5%; or price below the 1h lower BB (last closed bar) with bands expanding (bw > 3 bars before)
    and a strong red candle (current 1h red with volume already >= 1.0x avg20, or last closed 1h red with volume >= 1.5x avg20)."""
    P=P or BTC_RECHECK;why=[]
    cl=h1[h1.closed];lc=cl.iloc[-1];cur=h1.iloc[-1] if not bool(h1.closed.iloc[-1]) else None
    f=f15.iloc[-4:];c4=(px/f.o.iloc[0]-1)*100
    if c4<=P['m15x4']:why.append(f'4 nến 15m gần nhất {c4:+.2f}% (≤{P["m15x4"]}%)')
    cn=(px/cur.o-1)*100 if cur is not None else 0.0
    if cur is not None and cn<=P['h1_now']:why.append(f'nến 1h đang chạy {cn:+.2f}% (≤{P["h1_now"]}%)')
    cc=(lc.c/lc.o-1)*100
    if cc<=P['h1_closed']:why.append(f'nến 1h vừa đóng {cc:+.2f}% (≤{P["h1_closed"]}%)')
    below=px<lc.lo;exp=lc.bw>cl.bw.iloc[-4]
    rv_now=cur is not None and px<cur.o and cur.v>=P['vol_now']*cur.vma
    rv_cl=lc.c<lc.o and lc.v>=P['vol_closed']*lc.vma
    if below and exp and (rv_now or rv_cl):why.append(f'BTC dưới dải dưới BB 1h ({lc.lo:.0f}), dải đang mở rộng, nến đỏ volume mạnh')
    # mirror for SHORT entries/adds (Vien 17:52): block on a sharp BTC pump
    ws=[]
    if c4>=-P['m15x4']:ws.append(f'4 nến 15m gần nhất {c4:+.2f}% (≥+{-P["m15x4"]}%)')
    if cur is not None and cn>=-P['h1_now']:ws.append(f'nến 1h đang chạy {cn:+.2f}% (≥+{-P["h1_now"]}%)')
    if cc>=-P['h1_closed']:ws.append(f'nến 1h vừa đóng {cc:+.2f}% (≥+{-P["h1_closed"]}%)')
    above=px>lc.up
    gv_now=cur is not None and px>cur.o and cur.v>=P['vol_now']*cur.vma
    gv_cl=lc.c>lc.o and lc.v>=P['vol_closed']*lc.vma
    if above and exp and (gv_now or gv_cl):ws.append(f'BTC trên dải trên BB 1h ({lc.up:.0f}), dải đang mở rộng, nến xanh volume mạnh')
    return dict(block=bool(why),why=why,block_s=bool(ws),why_s=ws,px=float(px),info=dict(m15x4=round(c4,4),h1_now=round(cn,4),h1_closed=round(cc,4),below_bb1h=bool(below),above_bb1h=bool(above),bw_expanding=bool(exp),redvol_now=bool(rv_now),redvol_closed=bool(rv_cl),greenvol_now=bool(gv_now),greenvol_closed=bool(gv_cl)))
# BTC_VOL (Vien 30/10 ~09:57, zone 1): block NEW entries + adds on BOTH sides while BTC is volatile (research/btcvol). Cooldown = last K closed 1h bars.
MIN_EQ=0.0
MOMF=None   # Vien t546u: loc gia chay manh vao dai 4h (hook do bnb_bot.py gan)
BTC_VOL=dict(enabled=False,m15=1.0,h1=1.0,rng=1.5,k=2,cool_h=2.0,dump_exit=True);STAB={}
_btc_recheck_base=btc_recheck
def btc_recheck(h1,f15,px,P=None):
    r=_btc_recheck_base(h1,f15,px,P)
    V=BTC_VOL
    if not V.get('enabled'):return r
    w=[];cl=h1[h1.closed];cur=h1.iloc[-1] if not bool(h1.closed.iloc[-1]) else None
    c4=(px/f15.iloc[-4:].o.iloc[0]-1)*100
    if abs(c4)>=V['m15']:w.append(f'BTC_VOL: 4 nến 15m {c4:+.2f}% (|.|≥{V["m15"]}%)')
    if cur is not None:
        cn=(px/cur.o-1)*100;rg=(max(cur.h,px)-min(cur.l,px))/cur.o*100
        if abs(cn)>=V['h1']:w.append(f'BTC_VOL: nến 1h đang chạy {cn:+.2f}% (|.|≥{V["h1"]}%)')
        if rg>=V['rng']:w.append(f'BTC_VOL: biên độ nến 1h đang chạy {rg:.2f}% (≥{V["rng"]}%)')
    for t_,b in cl.iloc[-int(V['k']):].iterrows():
        bc=(b.c/b.o-1)*100;br=(b.h-b.l)/b.o*100
        if abs(bc)>=V['h1'] or br>=V['rng']:w.append(f'BTC_VOL: nến 1h {str(t_)[5:16]} thân {bc:+.2f}% biên {br:.2f}% (nguội {int(V["k"])}h)');break
    r=dict(r);r['dump']=bool(r['block']);r['pump']=bool(r['block_s'])      # raw one-sided recheck (sharp BTC dump / pump)
    # Vien 30/10 10:05-10:06: dump/pump also blocks BOTH sides; "stable" = no trigger for cool_h hours (stateful, calm minutes mid-storm do not re-open)
    if r['dump'] or r['pump']:w=w+['BTC '+('sập' if r['dump'] else 'bơm')+' mạnh: '+'; '.join(r['why'] if r['dump'] else r['why_s'])]
    import time as _t
    if w:STAB['last']=_t.time();STAB['last_why']=w
    elif STAB.get('last') and _t.time()-STAB['last']<V.get('cool_h',2.0)*3600:
        w=[f'BTC_VOL: chờ thị trường ổn định – còn {(V.get("cool_h",2.0)*3600-(_t.time()-STAB["last"]))/60:.0f} phút nguội (lần biến động cuối: '+'; '.join(STAB.get('last_why',[]))[:160]+')']
    if w:
        r['block']=True;r['block_s']=True;r['why']=w;r['why_s']=w;r['btc_vol']=w
    return r
def sd(p): return -1 if p.get('side')=='S' else 1
def SIDE(p): return 'SHORT' if p.get('side')=='S' else 'LONG'
def strong_break(bar):
    rng=bar.h-bar.l
    return bool(bar.o<bar.mid<bar.c and bar.v>=CFG['vol_mult']*bar.vma and rng>0 and (bar.c-bar.o)/rng>=CFG['body_min'])
class Book:
    def __getstate__(s):
        d=dict(s.__dict__);d.pop('rk_fn',None);return d   # closure set by bot.py each loop; not picklable
    def __init__(s,log,sl=None,name='main'):
        s.cash_pnl=0.0;s.pos={};s.trades=[];s.log=log;s.peak=CFG['capital'];s.alerted={}
        s.sl=sl;s.name=name;s.cool={};s.n_stops=0;s.ladder=None;s.usd=None;s.rlog=[]
    @property
    def L(s): return getattr(s,'ladder',None) or CFG['ladder']
    # Compounding sizing (Vien 2026-10-10 00:12 "Vốn tính lãi/lỗ nhập gốc làm cơ sở tính volume lệnh"): sizing base = 5000 + realized PnL
    # since the epoch (closed trades only, never unrealized); every ladder leg (margin) x base/5000; 30% margin cap = 30% of base.
    def base(s): return CFG['capital']+s.cash_pnl if getattr(s,'compound',False) else CFG['capital']
    def scale(s): return max(s.base(),0.0)/CFG['capital']
    def leg(s,k): return round(s.L[k]*s.scale(),4)
    def LV(s): return float(getattr(s,'lev',None) or CFG['lev'])      # per-branch leverage override (BASE_SL250_X10 = 10); default = global CFG['lev'] (5)
    # sl: hard stop on unrealized loss per position (USD); ladder: per-branch override
    def day_realized(s,day):
        """Realized net PnL of trades closed on VN calendar day 'YYYY-MM-DD' (times are VN)."""
        # rlog = every realized leg (partial 50% + final close), so a partial counts on the day it happens
        rl=getattr(s,'rlog',None)
        if rl is None: rl=s.rlog=[(str(tr['t1']),tr['net']) for tr in s.trades]
        return sum(n for t,n in rl if str(t)[:10]==day)
    def stop_px(s,p):
        """Exact price at which net PnL (entry fees + taker exit fee) = -SL. Recomputed from the current size after every add."""
        if not getattr(s,'sl',None):return None
        if sd(p)<0: return (s.sl+p['cost']-p['fee'])/(p['q']*(1+CFG['fee_taker']))     # short: cost - px*q*(1+fee) - entry fee = -SL
        return (-s.sl+p['cost']+p['fee'])/(p['q']*(1-CFG['fee_taker']))
    def walk(s,c,t,prices,bars,closed_bar=None,live=True):
        """Feed a price path (e.g. 1m candle o/h/l/c, or successive 5s mids) to manage(). Pending add levels crossed on the way
        down are visited in order, so resting adds / stops / trails trigger in the sequence a real order book would see them."""
        res=None;first=True
        for px in prices:
            if c not in s.pos:break
            p=s.pos[c];last=p.get('last',px);lv=[];g=sd(p)
            if g*px<g*last:     # moving against the position (down for longs, up for shorts): visit add levels in order
                k=p['k']
                while k<len(s.L) and not p['paused']:
                    a=s.add_level(c,p,p['first']*(1-g*CFG['add_step']*k/100))
                    if a is not None and g*px<g*a<g*last:lv.append(a)
                    k+=1
            for x in sorted(lv,reverse=(g>0))+[px]:
                if c not in s.pos:break
                r=s.manage(c,t,x,bars,closed_bar if first else None,False,live);first=False
                if r is not None:res=r
        return res
    def add_level(s,c,p,lv): return lv
    def add_note(s,c,p): return ''      # hook: G6_TREND1H also requires the 1h band (BookT1H)
    def u_net(s,p,px): return sd(p)*(px*p['q']-p['cost'])-p['fee']
    def unreal(s,c,px):
        p=s.pos[c];return sd(p)*(px*p['q']-p['cost'])
    def equity(s,prices):
        return CFG['capital']+s.cash_pnl+sum(s.unreal(c,prices.get(c,s.pos[c]['last'])) for c in s.pos)
    def ev(s,kind,c,t,px,reason,**kw):
        if 'side' not in kw and c in getattr(s,'pos',{}): kw['side']=SIDE(s.pos[c])
        s.log(dict(time=str(t),event=kind,coin=c,price=round(px,6),reason=reason,**kw))
    def rules(s,c,side,bars,reg,rk,kind='entry'):
        """FULL rule set (Vien 17:54: 'chạm trên hay dưới đều phải view các tiêu chí của toàn bộ bộ rule trước khi vào lệnh').
        A band touch is only a trigger; every ENTRY and every ADD, both sides, must pass all of these. Returns (fails, info)."""
        L_=side!='S';b=bars.iloc[-1];F=[]
        NB=getattr(s,'no_btc',False)
        if NB:reg=None          # G7_MTF (Vien 10/10 16:19 "Ko cần quan tâm điều kiện bit"): no BTC 1D/1W, no BTC re-check, no BTC in the 2nd-position gate
        sl=(b.mid/bars.mid.iloc[-11]-1)*100 if len(bars)>=11 else float('nan')
        # 1) BTC regime 1D/1W per side
        if reg is not None:
            if L_ and not reg.get('allow',True):F.append(f'BTC 1D/1W không cho LONG (1D %B {reg.get("d1_pb")}, 1W %B {reg.get("w1_pb")})')
            if not L_ and not reg.get('allow_s',True):F.append(f'BTC 1D/1W không cho SHORT (1D %B {reg.get("d1_pb")}, 1W %B {reg.get("w1_pb")})')
        # 2) short-term BTC re-check, mirrored per side
        g=None if NB else btc_now()
        if g and (g['block'] if L_ else g.get('block_s')):F.append('BTC '+('giảm' if L_ else 'tăng')+' mạnh ngắn hạn: '+'; '.join(g['why'] if L_ else g.get('why_s',[])))
        # 3+4) BB-respect score of the touched band + 1D stability (rk computed by the bot from the side's score)
        if rk is not None and not rk.get('ok',True):F.append(rk.get('why') or 'không đạt điểm tôn trọng BB / ổn định khung ngày')
        # 4b) G6_TREND1H (Vien 20:08): never counter-trend – side must be allowed by the token's 4h trend
        TA=getattr(s,'trend_allow',None)
        if TA is not None:
            tr=(rk or {}).get('trend')
            if not tr:F.append('chưa xác định được xu hướng 4h')
            elif tr['trend'] not in TA.get('S' if not L_ else 'L',[]):F.append(f'xu hướng 4h {tr["trend"]} – không vào {"LONG" if L_ else "SHORT"} ngược xu hướng ({tr["why"]})')
        # 5) 4h MA20 slope gate per side
        if sl==sl:
            if L_ and sl<CFG['slope_min']:F.append(f'MA20 4h giảm {sl:.1f}%/10 nến (< {CFG["slope_min"]}%)')
            if not L_ and sl>-CFG['slope_min']:F.append(f'MA20 4h tăng {sl:+.1f}%/10 nến (> +{-CFG["slope_min"]}%)')
        # 5b) SHORT gate on adds (optional): never add to a short while the last closed 4h candle closed above the upper band
        if kind=='add' and not L_ and getattr(s,'sgate_on',False) and SGATE.get('gate_adds') and float(b.c)>float(b.up):F.append(f'đang phá cạnh trên: nến 4h đóng {float(b.c):.6g} > dải trên {float(b.up):.6g} – không nhồi short')
        # 6) margin cap 30% per position
        p=s.pos.get(c);cur=pmargin(p) if (p and kind=='add') else 0.0;k=p['k'] if (p and kind=='add') else 0      # cur = margin already used (per-position, legacy x5 positions = cost/5)
        if kind=='entry' and MIN_EQ>0 and s.base()<MIN_EQ:F.append(f'vốn {s.base():.2f}$ < tối thiểu {MIN_EQ:.0f}$')
        if k<len(s.L) and cur+s.leg(k)>1.001*s.base()+0.01:F.append(f'ký quỹ vượt 30% vốn {s.base():.4f}$ ({cur+s.leg(k):.4f}$)')
        if kind=='entry':
            # 7) one side per coin per branch; max 2 positions in total
            if c in s.pos:F.append(f'đang có vị thế {SIDE(s.pos[c])} {c} trong nhánh (không mở ngược chiều)')
            if len(s.pos)>=CFG['max_pos']:F.append(f'đã đủ {CFG["max_pos"]} vị thế')
            # 8) careful 2nd-position gate (mirrored)
            elif len(s.pos)==1 and c not in s.pos:
                (c1,p1),=s.pos.items();u1=s.u_net(p1,p1.get('last',p1['first']))
                if reg is not None and not reg.get('fav' if L_ else 'fav_s',False):F.append('lệnh 2: BTC 1D/1W chưa thuận lợi cho '+('LONG' if L_ else 'SHORT'))
                if u1<CFG['gate2_u1_min']:F.append(f'lệnh 2: lệnh 1 {c1} đang {u1:+.4f}$ (<0)')
                if sl==sl and ((L_ and sl<CFG['gate2_slope_min']) or (not L_ and sl>-CFG['gate2_slope_min'])):F.append(f'lệnh 2: MA20 4h {"đang giảm" if L_ else "đang tăng"} ({sl:+.1f}%/10 nến)')
        fng=(reg or {}).get('fng')
        return F,dict(slope=round(sl,4) if sl==sl else None,fng=fng,g=g)
    def blocked(s,kind,c,t,px,side,F,key):
        """Log a rule block once per (kind, coin, side, reasons, key)."""
        k=(kind,c,side,'|'.join(x.split(':')[0][:40] for x in F),key)
        if k in s.__dict__.setdefault('blk_log',set()):return
        s.blk_log.add(k)
        btc=all(x.split(' – ')[-1].startswith('BTC ') for x in F)
        ev=('ENTRY_BLOCKED_' if kind=='ENTRY' else 'ADD_BLOCKED_')+('BTC' if btc else 'RULES')
        s.ev(ev,c,t,px,f'Chặn {"vào lệnh" if kind=="ENTRY" else "nhồi"} {"LONG" if side!="S" else "SHORT"} {c}: '+'; '.join(F),side='LONG' if side!='S' else 'SHORT',fails=F)
    def sgate(s,c,px,bars,b,rk,T1):
        """SHORT gate (Vien 17:12). Returns (path|None, fails, rk). path None = no short trigger at this price."""
        b4=bars.iloc[-1];brk=float(b4.c)<float(b4.lo);rk=dict(rk or {})
        if False and brk and ((T1 and px>=b.up) or (not T1 and px<=b4.lo)):   # Path B DISABLED (Vien 22/10)
            F=[] if rk.get('pathB_ok') else [rk.get('pathB_why') or 'Path B: coin không thuộc shortlist động (LONG/SHORT) của nhóm hoặc không ổn định 1D']
            rk.update(ok=True,why='');rk['path']='B';return 'B',F,rk
        if px>=b.up:
            ok,F,M=short_range_eval(rk.get('sA'));rk['path']='A';rk['sA_m']=M
            if float(b4.c)>float(b4.up):F.append(f'đang phá cạnh trên: nến 4h {str(getattr(b4,"name",""))[5:16]} đóng {float(b4.c):.6g} > dải trên {float(b4.up):.6g} – chờ nến 4h đóng lại trong dải (dấu hiệu quay đầu)')
            return 'A',F,rk
        return None,[],rk
    def try_entry(s,c,t,px,bars,reg,rk=None,side='L',band=None,blbl='4h'):
        """Trigger: LONG = price <= lower band of the last CLOSED 4h bar; SHORT (mirror, Vien 17:52) = price >= upper band.
        The touch is only the trigger: the full rule set (s.rules) must pass, otherwise ENTRY_BLOCKED_* (side + reasons) is logged."""
        L_=side!='S';b=band if band is not None else bars.iloc[-1];pth=None;F0=[]
        if not L_ and getattr(s,'sgate_on',False):
            if c in s.pos and s.pos[c].get('side','L')=='S':return
            pth,F0,rk=s.sgate(c,px,bars,b,rk,band is not None)
            if pth is None:return
        elif (L_ and not px<=b.lo) or (not L_ and not px>=b.up):return
        if c in s.pos and s.pos[c].get('side','L')==('L' if L_ else 'S'):return      # already in this trade (adds are handled in manage)
        F,I=s.rules(c,side,bars,reg,rk,'entry');F=F0+F
        if MOMF is not None:
            try:_m=MOMF(c,t,px,L_)
            except Exception:_m=None
            if _m:F.append(_m)
        if F:
            s.blocked('ENTRY',c,t,px,side,F,str(bars.index[-1]) if hasattr(bars,'index') else '');return
        g2=''
        if len(s.pos)==1:
            (c1,p1),=s.pos.items();g2=f'; LỆNH 2: BTC thuận lợi cho {"LONG" if L_ else "SHORT"}, lệnh 1 {c1} đang {s.u_net(p1,p1.get("last",p1["first"])):+.4f}$ (≥0), MA20 4h {"không giảm" if L_ else "không tăng"} ({I["slope"]:+.1f}%/10 nến)'
        mkt=pth=='B' and band is None      # path B on 4h books = market short after the 4h close below the lower band
        fill=px if mkt else (min(px,b.lo) if L_ else max(px,b.up));n=s.leg(0)*s.LV()
        s.pos[c]=dict(first=fill,q=n/fill,cost=n,fee=n*CFG['fee_taker' if mkt else 'fee_maker'],k=1,t0=t,target='mid',paused=False,last=fill,fills=[(str(t),fill,n,'entry')],held=False,trail=None,partial=None,real=0.0,margin=s.leg(0),lev=s.LV())
        if not L_:s.pos[c]['side']='S'
        if pth:s.pos[c]['path']=pth
        b4=bars.iloc[-1];ptxt=(f'; SHORT Path B (phá vỡ xuống 4h): nến 4h đóng {float(b4.c):.6g} < dải dưới {float(b4.lo):.6g}'+(', vào theo giá thị trường' if mkt else ', chạm lại dải trên 1h') if pth=='B' else (f'; SHORT Path A (coin đi trong dải ổn định: điểm dải trên {rk["sA_m"].get("score")}, phá thật {rk["sA_m"].get("bo")}, vượt ≥6% {rk["sA_m"].get("deep")}, 30 ngày x{rk["sA_m"].get("r30")}; nến 4h gần nhất đóng trong dải)' if pth=='A' else ''))
        fg=I.get('fng');fgt=f'; Fear&Greed {fg[0]} ({fg[1]})' if fg and fg[0] is not None else ''
        s.ev('ENTRY',c,t,fill,(f'LONG: giá chạm dải dưới BB {blbl} ({b.lo:.6g})' if L_ else (f'SHORT: nến 4h đóng dưới dải dưới, giá {fill:.6g} ≤ dải dưới 4h ({b4.lo:.6g})' if mkt else f'SHORT: giá chạm dải trên BB {blbl} ({b.up:.6g})'))+ptxt+(f'; xu hướng 4h {rk["trend"]["trend"]} ({rk["trend"]["why"]})' if rk and rk.get('trend') else '')+f' – đã kiểm tra đủ bộ rule: BTC 1D %B {reg["d1_pb"]}, 1W %B {reg["w1_pb"]} cho phép {"LONG" if L_ else "SHORT"}; BTC ngắn hạn không {"giảm" if L_ else "tăng"} mạnh; MA20 4h dốc {I["slope"]:+.1f}%/10 nến; ký quỹ {n/s.LV():.2f}$ (≤30%){f"; vốn tính khối lượng {s.base():.2f}$ (x{s.scale():.4f})" if getattr(s,"compound",False) else ""}{fgt}{g2}'+(f'; {rk["text"]}' if rk else ''),notional=n,side='LONG' if L_ else 'SHORT',trigger_tf=blbl,**({'trend_4h':rk['trend']['trend']} if rk and rk.get('trend') else {}),**({'bb_score':rk.get('score'),'rank':rk.get('rank'),'n_cand':rk.get('n_cand')} if rk else {}),**({'short_path':pth} if pth else {}))
        return True
    def manage(s,c,t,px,bars,closed_bar=None,gap=False,live=False):
        """Exit policy B (from 2026-10-08 13:06, Vien): 50% at 4h BB mid band, runner trails (breakeven -> mid band), runner TP at upper band."""
        p=s.pos[c];p['last']=px;b=bars.iloc[-1]
        p.setdefault('held',False);p.setdefault('trail',None);p.setdefault('partial',None);p.setdefault('real',0.0)
        avg=p['cost']/p['q'];g=sd(p);BE=avg*CFG['be_buf'] if g>0 else avg*(2-CFG['be_buf'])
        if closed_bar is not None and g>0:      # 4h policy-B logic (long-only, not used by the 15m branches)
            if p['target']=='mid' and strong_break(closed_bar):
                p['target']='up';p['held']=True;p['trail']=max(p['trail'] or 0,BE)
                s.ev('TARGET_UP',c,t,px,f'Nến 4h vượt dải giữa với volume mạnh → giữ toàn bộ lên dải trên, dời cắt lỗ về hòa vốn {BE:.6g} rồi theo dải giữa')
            if p['trail'] is not None: p['trail']=max(p['trail'],BE,float(b.mid))   # trail ratchets up to the mid band of each closed 4h bar
        u=s.u_net(p,px)
        pre='Đóng phần còn lại' if p['partial'] else 'Đóng lệnh'
        if p['trail'] is not None and g*px<=g*p['trail']:
            # resting stop order (Vien 22:39): triggers when price touches the level, fills at the level with adverse slippage (sell lower / buy higher); true gap -> market
            fill=px if gap else p['trail']*(1-g*CFG['stop_slip'])
            if p.get('trail_kind')=='lock30':
                L=p.get('lock30',{})
                return s.close(c,t,fill,f'LOCK_30_2/3: giá chạm mức khóa lãi {p["trail"]:.6g} (mốc lãi đỉnh {L.get("milestone",0):.4f}$ → khóa ≥ {L.get("target",0):.4f}$ ròng = 2/3 mốc; lãi đỉnh {p.get("peak",0):.4f}$), khớp {fill:.6g} (lệnh stop chờ sẵn, trượt giá giả định {CFG["stop_slip"]*100:.2f}%)',CFG['fee_taker'],extra=dict(lock_reason='LOCK_30_2/3',milestone=L.get('milestone'),locked=L.get('target'),peak=round(p.get('peak',0.0),4)))
            if p.get('trail_kind')=='lock15':
                L=p.get('lock',{})
                return s.close(c,t,fill,f'trail 15m: giá chạm mức khóa lãi {p["trail"]:.6g} (khóa {L.get("frac",0)*100:.0f}% lãi đỉnh {L.get("peak",0):.4f}$), khớp {fill:.6g} (lệnh stop chờ sẵn, trượt giá giả định {CFG["stop_slip"]*100:.2f}%)',CFG['fee_taker'])
            return s.close(c,t,fill,f'{pre}: giá chạm mức dời cắt lỗ {p["trail"]:.6g} (hòa vốn/dải giữa BB 4h), khớp {fill:.6g} (lệnh stop chờ sẵn, trượt giá giả định {CFG["stop_slip"]*100:.2f}%)',CFG['fee_taker'])
        if g>0:
            done,res=s.tp_logic(c,t,px,b,p,avg,BE,gap,pre)
            if done: return res
        # alerts (alert-first, no auto close)
        for lvl,name in(getattr(s,'alerts',None) if getattr(s,'alerts',None) is not None else [(CFG['alert_warn'],'WARN'),(CFG['alert_crit'],'CRIT')]):
            if u<=lvl and not s.alerted.get((c,p['t0'],name)):
                s.alerted[(c,p['t0'],name)]=1;p['paused']=True
                s.ev('ALERT_'+name,c,t,px,f'Lỗ tạm tính {u:.4f}$ ≤ {lvl}$ → tạm dừng nhồi, chờ Vien quyết định',unreal=round(u,4))
        ps=s.stop_px(p)
        if ps is not None and g*px<=g*ps:
            # resting stop order at the exact price where the loss (after entry+exit fees) = -SL; fill = stop price with adverse slippage; true gap -> market
            fill=px if gap else ps*(1-g*CFG['stop_slip'])
            s.cool[c]=t;s.n_stops+=1
            return s.close(c,t,fill,f'Cắt lỗ nhánh SL{s.sl}: giá chạm mức cắt lỗ {ps:.6g} (tương ứng -{s.sl}$ sau phí), khớp {fill:.6g} (lệnh stop chờ sẵn, trượt giá giả định {CFG["stop_slip"]*100:.2f}%)',CFG['fee_taker'],stop=True)
        if u<=CFG['cat_stop'] and g>0:
            if CFG['cat_stop_enabled']:
                ps=(CFG['cat_stop']+p['cost']+p['fee'])/(p['q']*(1-CFG['fee_taker']))   # stop-market trigger level
                return s.close(c,t,px if gap else min(ps,max(px,ps)),f'Cắt lỗ thảm họa {CFG["cat_stop"]}$ (đã được duyệt)',CFG['fee_taker'])
            if not s.alerted.get((c,p['t0'],'CAT')):
                s.alerted[(c,p['t0'],'CAT')]=1;s.ev('ALERT_CAT',c,t,px,f'Lỗ {u:.4f}$ chạm ngưỡng cắt lỗ thảm họa đề xuất {CFG["cat_stop"]}$ (chưa bật) – cần Vien can thiệp',unreal=round(u,4))
        # adds
        while p['k']<len(s.L) and not p['paused']:
            lv=s.add_level(c,p,p['first']*(1-g*CFG['add_step']*p['k']/100))
            if lv is None:break
            if g*px<=g*lv:
                rkf=getattr(s,'rk_fn',None);rk=((rkf(c,p.get('side','L'),p['path']) if p.get('path') else rkf(c,p.get('side','L'))) if rkf else None)
                F,_=s.rules(c,p.get('side','L'),bars,getattr(s,'reg',None),rk,'add')
                if F:      # full rule set also for adds (Vien 17:54); postponed, filled at market later if rules pass and price still beyond the level
                    s.blocked('ADD',c,t,px,p.get('side','L'),[f'nhồi lần {p["k"]} tại {lv:.6g} – '+F[0]]+F[1:],str(p['t0'])+f'|{p["k"]}')
                    p['add_blk']=True
                    break
                if p.get('add_blk'):lv=(min(lv,px) if g>0 else max(lv,px));p['add_blk']=None     # delayed add fills at market, never at a better-than-market level
                n=s.leg(p['k'])*s.LV();p['margin']=pmargin(p)+s.leg(p['k']);p['q']+=n/lv;p['cost']+=n;p['fee']+=n*CFG['fee_maker'];p['k']+=1;p['fills'].append((str(t),lv,n,'add'))
                s.ev('ADD',c,t,lv,f'Nhồi {SIDE(p)} lần {p["k"]-1}: giá cách lệnh đầu {(lv/p["first"]-1)*100:+.1f}% (thang tối thiểu {"-" if g>0 else "+"}{CFG["add_step"]*(p["k"]-1):.1f}%)'+s.add_note(c,p),notional=n,avg=round(p['cost']/p['q'],6))
            else:break
    def tp_logic(s,c,t,px,b,p,avg,BE,gap,pre):
        """Policy B take-profit. Returns (handled, result)."""
        tgt=b.mid if p['target']=='mid' else b.up
        if px>=tgt and px>avg*1.003:
            fill=px if gap else tgt   # resting limit fills at its level, not at the tick high
            if p['target']=='mid' and not p['held']:
                p['held']=True;p['target']='up';p['paused']=True;p['trail']=BE
                return True,s.close(c,t,fill,f'Chốt {CFG["partial_frac"]*100:.0f}% tại dải giữa BB 4h; phần còn lại dời cắt lỗ về hòa vốn {BE:.6g}, nâng dần theo dải giữa, mục tiêu dải trên',CFG['fee_maker'],frac=CFG['partial_frac'])
            return True,s.close(c,t,fill,f'{pre}: chạm dải {"giữa" if p["target"]=="mid" else "trên"} BB 4h',CFG['fee_maker'])
        return False,None
    def close(s,c,t,px,why,fee,stop=False,frac=1.0,kind=None,extra=None):
        p=s.pos[c];q=p['q']*frac;cost=p['cost']*frac;f0=p['fee']*frac
        f=f0+px*q*fee;net=sd(p)*(px*q-cost)-f;s.cash_pnl+=net;p['real']=p.get('real',0.0)+net;p['fee_paid']=p.get('fee_paid',0.0)+f
        if getattr(s,'rlog',None) is None: s.day_realized('')
        s.rlog.append((str(t),net))
        if frac<1.0:
            p['q']-=q;p['cost']-=cost;p['fee']-=f0;p['fills'].append((str(t),px,cost,'partial'))
            p['partial']=dict(time=str(t),price=px,net=round(net,4),notional=round(cost,4))
            s.ev('TP_PARTIAL',c,t,px,why,net=round(net,4),remaining_notional=round(p['cost'],4),trail=round(p['trail'],6) if p['trail'] else None,adds=p['k']-1)
            return None
        p=s.pos.pop(c);p['fills'].append((str(t),px,0,'exit'));pt=p.get('partial')
        tr=dict(branch=getattr(s,'name','main'),side=SIDE(p),stop=stop,coin=c,t0=str(p['t0']),t1=str(t),adds=p['k']-1,avg=p['cost']/p['q'],exit=px,notional=p['cost']+(pt['notional'] if pt else 0),
                net=round(p['real'],6),fee=round(p['fee_paid'],6),partial_net=pt['net'] if pt else None,reason=why,fills=p['fills'])
        s.trades.append(tr)
        kind='STOP' if stop else (kind or ('EXIT_RUNNER' if pt else 'EXIT'))
        s.ev(kind,c,t,px,why,net=round(net,4),trade_net=round(p['real'],4),adds=p['k']-1,side=SIDE(p),**(extra or {}));return tr

def rsi(c,n=14):
    d=c.diff();u=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean();dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+u/dn)
def prep15(x):
    """15m frame (closed candles) -> BB(20,2) already present; add RSI14, ATR14, MA80 (~20h)."""
    x=x.copy();x['rsi']=rsi(x.c)
    x['atr']=pd.concat([x.h-x.l,(x.h-x.c.shift()).abs(),(x.l-x.c.shift()).abs()],axis=1).max(axis=1).rolling(14).mean();x['ma80']=x.c.rolling(80).mean()
    return x
def score15(x,side='L'):
    """Transparent 15m outlook score on the LAST closed 15m candle: sum of 8 factors (+1 good / 0 / -1 bad). Same as research/bt15.score15(room=True).
    side='S': every factor mirrored (good for a short = bearish 15m outlook)."""
    if side=='S':return score15_short(x)
    z=x.iloc[-1];z1=x.iloc[-2];o,h,l,c,v=z.o,z.h,z.l,z.c,z.v;rng=max(h-l,1e-12);F=[]
    F.append(('%B',1 if z.pb>=0.8 else (-1 if z.pb<0.5 else 0)))
    F.append(('dải giữa 15m',1 if z.mid>x.mid.iloc[-5] else -1))
    bwx=z.bw>x.bw.iloc[-4];F.append(('độ rộng dải',1 if (bwx and c>z.mid) else (-1 if (bwx and c<z.mid) else 0)))
    r0,r1=z1.rsi,z.rsi;F.append(('RSI15',1 if (r1>=55 and r1>r0 and r1<75) else (-1 if (r1<50 or (r0>70 and r1<r0)) else 0)))
    F.append(('volume',(1 if c>=o else -1) if v>=z.vma else 0))
    up_w=h-max(o,c);F.append(('nến',-1 if (c<o and (c<z1.l or (o-c)/rng>=0.6)) or up_w/rng>=0.5 else (1 if c>o and (c-o)/rng>=0.6 else 0)))
    F.append(('MA80 (~20h)',1 if c>z.ma80 else -1))
    rm=(z.up-c)/z.atr if z.atr>0 else 0;F.append(('dư địa tới dải trên',1 if rm>=1.0 else (-1 if rm<0 else 0)))
    return int(sum(f for _,f in F)),F,dict(pb=round(float(z.pb),4),rsi=round(float(r1),1),room_atr=round(float(rm),4))
def score15_short(x):
    """Mirror of score15 for SHORT positions (price -> -price: %B -> 1-%B, up <-> lo, RSI -> 100-RSI, green <-> red)."""
    z=x.iloc[-1];z1=x.iloc[-2];o,h,l,c,v=z.o,z.h,z.l,z.c,z.v;rng=max(h-l,1e-12);F=[]
    F.append(('%B',1 if z.pb<=0.2 else (-1 if z.pb>0.5 else 0)))
    F.append(('dải giữa 15m',1 if z.mid<x.mid.iloc[-5] else -1))
    bwx=z.bw>x.bw.iloc[-4];F.append(('độ rộng dải',1 if (bwx and c<z.mid) else (-1 if (bwx and c>z.mid) else 0)))
    r0,r1=z1.rsi,z.rsi;F.append(('RSI15',1 if (r1<=45 and r1<r0 and r1>25) else (-1 if (r1>50 or (r0<30 and r1>r0)) else 0)))
    F.append(('volume',(1 if c<=o else -1) if v>=z.vma else 0))
    lo_w=min(o,c)-l;F.append(('nến',-1 if (c>o and (c>z1.h or (c-o)/rng>=0.6)) or lo_w/rng>=0.5 else (1 if c<o and (o-c)/rng>=0.6 else 0)))
    F.append(('MA80 (~20h)',1 if c<z.ma80 else -1))
    rm=(c-z.lo)/z.atr if z.atr>0 else 0;F.append(('dư địa tới dải dưới',1 if rm>=1.0 else (-1 if rm<0 else 0)))
    return int(sum(f for _,f in F)),F,dict(pb=round(float(z.pb),4),rsi=round(float(r1),1),room_atr=round(float(rm),4))
class Book15(Book):
    """G5_15M branch. Entries/adds/stops/BTC filter/2nd-position gate = v2 rules on the 4h chart (unchanged). NO 4h take-profit.
    Take-profit decided by a transparent 15m outlook score (Vien 2026-10-08 22:03; research/bt15.py, variant
    'S<=-2 thích ứng+xác nhận+dư địa, BE40'):
      - profit protection: once unrealized (after entry fee) >= +40$, stop moves to breakeven (avg +0.2%);
      - at each closed 15m candle where unrealized > +8$ (covers exit fee/slippage): score = sum of 8 factors (see score15);
        threshold = -2, or -4 while unrealized < 60$ (a small profit needs a clearly weak outlook before giving it up);
        if the score is <= threshold on this AND the previous evaluated candle -> close 100% at market (EXIT_15M).
      - otherwise hold (no fixed $ target)."""
    eps=8.0;be_after=40.0;th=-3;th_small=-4;small=60.0   # th -2 -> -3 (Vien 2026-10-09 13:28)
    trail_cfg=dict(tiers=[(80.0,0.3),(150.0,0.5)],mid_buf=None,log_min=0.001)   # profit lock (Vien 2026-10-09 08:29; research/trail15_grid3.py)
    lock_step=0.0;lock_frac=2/3     # Vien 2026-10-16 (t197u): milestone lock: every `lock_step`$ of PEAK net profit M -> stop locks >= lock_frac*M net (0 = off; set from config.json lock_milestones)
    def lock_level(s,p,T):
        """Stop (trigger) price whose FILL (trigger -/+ stop_slip) nets exactly +T$ after entry fees AND the taker exit fee (same formula as close())."""
        fee=CFG['fee_taker'];sl=CFG['stop_slip']
        if sd(p)>0: return ((T+p['cost']+p['fee'])/(p['q']*(1-fee)))/(1-sl)
        return ((p['cost']-p['fee']-T)/(p['q']*(1+fee)))/(1+sl)
    def lock30_update(s,c,t,p,px):
        """Per tick (like the BE stop): milestone M = floor(peak/step)*step (peak = max net profit after entry fees); raise the resting stop so that
        a stop fill nets >= lock_frac*M. Ratchets only (LONG up / SHORT down), only if more protective than the current trail (BE / lock15).
        Milestones <= p['lock30_floor'] are ignored (positions whose stored-peak lock was already crossed by price when the rule was enabled)."""
        if not s.lock_step:return
        M=math.floor(p.get('peak',0.0)/s.lock_step+1e-9)*s.lock_step
        if M<s.lock_step or M<=p.get('lock30_floor',0.0):return
        g=sd(p);T=round(M*s.lock_frac,6);lv=s.lock_level(p,T)
        if p.get('trail') is not None and g*lv<=g*p['trail']:return
        fx=lv*(1-g*CFG['stop_slip']);locked=g*(fx*p['q']-p['cost'])-fx*p['q']*CFG['fee_taker']-p['fee']
        p['trail']=lv;p['trail_kind']='lock30'
        p['lock30']=dict(milestone=M,target=T,level=round(lv,6),locked_usd=round(locked,4),peak=round(p.get('peak',0.0),4),at=str(t))
        if M>p.get('lock30_logged',0.0):
            p['lock30_logged']=M
            s.ev('TRAIL_15M',c,t,px,f'LOCK_30_2/3 {SIDE(p)}: lãi đỉnh {p.get("peak",0.0):.4f}$ chạm mốc {M:.4f}$ → {"nâng" if g>0 else "hạ"} mức cắt lỗ chờ sẵn {"lên" if g>0 else "xuống"} {lv:.6g}; nếu chạm sẽ chốt ≥ {locked:+.1f}$ ròng sau phí vào/ra và trượt giá (đích {T:.4f}$ = 2/3 mốc)',trail=round(lv,6),locked=round(locked,4),peak=round(p.get('peak',0.0),4),lock_reason='LOCK_30_2/3',milestone=M)
    def lock_update(s,c,t,p,z,px):
        """At a closed 15m candle z: raise the resting stop to lock a fraction of PEAK profit (tiers), optionally also to the 15m BB mid
        minus a buffer; only levels below the candle close; never lowered. Logs TRAIL_15M when it moves >= log_min."""
        T=s.trail_cfg;pk=p.get('peak',0.0);lk=max([f for a,f in T['tiers'] if pk>=a] or [0]);cand=[];g=sd(p)
        if g>0:
            if lk>0:cand.append(((lk*pk+p['cost']+p['fee'])/p['q'],f'khóa {lk*100:.0f}% lãi đỉnh {pk:.4f}$'))
            if T.get('mid_buf') is not None and pk>=T['tiers'][0][0] and pd.notna(z.mid):cand.append((z.mid*(1-T['mid_buf']),f'dải giữa BB 15m {z.mid:.6g} -{T["mid_buf"]*100:.1f}%'))
            cand=[x for x in cand if x[0]<z.c]
            if not cand:return
            lv,why=max(cand)
            if lv<=(p.get('trail') or 0):return
        else:          # SHORT mirror: stop ABOVE price, only lowered
            if lk>0:cand.append(((p['cost']-p['fee']-lk*pk)/p['q'],f'khóa {lk*100:.0f}% lãi đỉnh {pk:.4f}$'))
            if T.get('mid_buf') is not None and pk>=T['tiers'][0][0] and pd.notna(z.mid):cand.append((z.mid*(1+T['mid_buf']),f'dải giữa BB 15m {z.mid:.6g} +{T["mid_buf"]*100:.1f}%'))
            cand=[x for x in cand if x[0]>z.c]
            if not cand:return
            lv,why=min(cand)
            if p.get('trail') is not None and lv>=p['trail']:return
        p['trail']=lv;p['trail_kind']='lock15'
        fx=lv*(1-g*CFG['stop_slip']);locked=g*(fx*p['q']-p['cost'])-fx*p['q']*CFG['fee_taker']-p['fee']
        p['lock']=dict(level=round(lv,6),frac=lk,peak=round(pk,4),locked_usd=round(locked,4),at=str(t),why=why)
        lg=p.get('trail_logged')
        if not lg or g*(lv/lg-1)>=T.get('log_min',0.001):
            p['trail_logged']=lv
            s.ev('TRAIL_15M',c,t,px,f'trail 15m {SIDE(p)}: {"nâng" if g>0 else "hạ"} mức cắt lỗ chờ sẵn {"lên" if g>0 else "xuống"} {lv:.6g} ({why}; nến 15m {str(z.name)[11:16]} đóng {z.c:.6g}); nếu chạm sẽ chốt khoảng {locked:+.4f}$ sau phí/trượt giá',trail=round(lv,6),locked=round(locked,4),peak=round(pk,4))
    def __getstate__(s):
        d=dict(s.__dict__);d['m15']=None;d.pop('rk_fn',None);return d
    def f15(s,c):
        x=(getattr(s,'m15',None) or {}).get(c)
        return x if x is not None and len(x)>=90 else None
    def tp_logic(s,c,t,px,b,p,avg,BE,gap,pre):
        return False,None          # no 4h take-profit in this branch
    def manage(s,c,t,px,bars,closed_bar=None,gap=False,live=False):
        p=s.pos[c];p['last']=px;avg=p['cost']/p['q'];g=sd(p);BE=avg*CFG['be_buf'] if g>0 else avg*(2-CFG['be_buf'])
        u=s.u_net(p,px)
        if u>p.get('peak',-1e18):p['peak']=u;p['peak_at']=str(t)
        if u>=s.be_after and not p.get('be_at'):      # breakeven (+40$): effective stop = most protective of BE / lock30 / lock15
            p['be_at']=str(t)
            if p.get('trail') is None or g*BE>g*p['trail']: p['trail']=BE;p['trail_kind']=None
        s.lock30_update(c,t,p,px)
        x=s.f15(c)
        # C2 (Vien 2026-10-09 21:05): the 15m outlook-score exit is only allowed after price has touched the 4h mid band since entry
        # (long: price >= mid of the last closed 4h bar; short mirrored). Stops, BE, trail_15m unchanged.
        try:mid4=float(bars.iloc[-1].mid)
        except Exception:mid4=float('nan')
        if not p.get('mid4h_hit') and mid4==mid4 and g*(px-mid4)>=0:p['mid4h_hit']=str(t);p['mid4h_lv']=round(mid4,6)
        gate=getattr(s,'hold_mid',False) and not p.get('mid4h_hit')
        if x is not None and str(x.index[-1])!=p.get('last15'):
            x=prep15(x);p['last15']=str(x.index[-1]);z=x.iloc[-1]
            sc,F,info=score15(x,p.get('side','L'));uz=s.u_net(p,z.c)
            th=s.th if uz>=s.small else s.th_small
            p['outlook']=dict(candle=str(x.index[-1])[11:16],score=sc,th=th,factors={k:v for k,v in F},**info,**({'gate':f'chờ chạm dải giữa 4h ({mid4:.6g})'} if gate else {}))
            s.lock_update(c,t,p,z,px)
            if uz>s.eps and not gate:
                weak=sc<=th;prev=p.get('weak_prev',False);p['weak_prev']=weak
                if weak and prev and u>0:
                    fs=', '.join(f'{k} {v:+d}' for k,v in F if v)
                    return s.close(c,t,px,f'Chốt {SIDE(p)} theo triển vọng chart 15m'+(' (điểm đảo chiều cho short)' if g<0 else '')+f': điểm {sc} ≤ {th} hai nến 15m liên tiếp (nến {str(x.index[-1])[11:16]}); yếu tố: {fs}; %B {info["pb"]}, RSI15 {info["rsi"]}, dư địa tới dải {"trên" if g>0 else "dưới"} {info["room_atr"]} ATR',CFG['fee_taker'],kind='EXIT_15M')
        return super().manage(c,t,px,bars,None,gap,live)   # closed_bar=None: no 4h strong-break / 4h-mid trail here

# ---------------- G6_TREND1H (Vien 2026-10-09 20:08): 4h trend classifier + 1h BB trigger ----------------
TREND_TS=1.0      # MA20(4h) slope threshold, % over 10 closed 4h bars
def pivots(h,l,k=2):
    """Swing highs/lows: bar i is a pivot high if h[i] is the max of h[i-k..i+k] (needs k bars after -> confirmed, no look-ahead)."""
    H=[];L=[]
    for i in range(k,len(h)-k):
        if h[i]==max(h[i-k:i+k+1]):H.append(h[i])
        if l[i]==min(l[i-k:i+k+1]):L.append(l[i])
    return H,L
def trend4h(x,ts=None):
    """Token 4h trend from CLOSED 4h bars (>=60). 4 votes:
      (1) MA20 slope over 10 bars >= +ts% (up) / <= -ts% (down)
      (2) last close above / below MA20
      (3) MA20 above / below MA50
      (4) structure of the last 2 confirmed swing highs+lows in the last 60 bars: HH+HL (up) / LH+LL (down)
    UP = >=3 up-votes and slope > 0; DOWN = >=3 down-votes and slope < 0; otherwise SIDEWAYS."""
    ts=TREND_TS if ts is None else ts
    if x is None or len(x)<60:return None
    c=x.c.to_numpy(float);m20=x.c.rolling(20).mean().to_numpy();m50=x.c.rolling(50).mean().to_numpy()
    sl=(m20[-1]/m20[-11]-1)*100;w=x.iloc[-60:];H,L=pivots(w.h.to_numpy(float),w.l.to_numpy(float))
    st='?'
    if len(H)>=2 and len(L)>=2:
        hh,hl=H[-1]>H[-2],L[-1]>L[-2]
        st='HH/HL' if (hh and hl) else ('LH/LL' if (not hh and not hl) else 'hỗn hợp')
    up=[sl>=ts,c[-1]>m20[-1],m20[-1]>m50[-1],st=='HH/HL'];dn=[sl<=-ts,c[-1]<m20[-1],m20[-1]<m50[-1],st=='LH/LL']
    tr='UP' if (sum(up)>=3 and sl>0) else ('DOWN' if (sum(dn)>=3 and sl<0) else 'SIDEWAYS')
    why=f'MA20 4h {sl:+.1f}%/10 nến, giá {"trên" if c[-1]>m20[-1] else "dưới"} MA20, MA20 {"trên" if m20[-1]>m50[-1] else "dưới"} MA50, cấu trúc đỉnh/đáy {st}; phiếu tăng {sum(up)}/4, giảm {sum(dn)}/4'
    return dict(trend=tr,slope=round(float(sl),4),c=float(c[-1]),ma20=round(float(m20[-1]),6),ma50=round(float(m50[-1]),6),struct=st,up_votes=int(sum(up)),down_votes=int(sum(dn)),why=why,bar=str(x.index[-1]))
class BookT1H(Book15):
    """G6_TREND1H: same accounts/ladder/stops/15m exits/BE/trail_15m/full rule set as Book15, plus
    (a) 4h trend filter (trend_allow) checked in rules() for entries AND adds, (b) entry trigger = last CLOSED 1h BB(20,2) band,
    (c) adds = ladder level (first -/+ 1.5%*k) AND the last closed 1h band in the same direction (level = the further of the two)."""
    trend_allow={'L':['UP','SIDEWAYS'],'S':['DOWN']}
    def __getstate__(s):
        d=Book15.__getstate__(s);d['h1']=None;return d
    def b1h(s,c):
        x=(getattr(s,'h1',None) or {}).get(c)
        return x.iloc[-1] if x is not None and len(x) else None
    def add_level(s,c,p,lv):
        b=s.b1h(c)
        if b is None or b.lo!=b.lo:return None
        return min(lv,float(b.lo)) if sd(p)>0 else max(lv,float(b.up))
    def manage(s,c,t,px,bars,closed_bar=None,gap=False,live=False):
        # G6 (Vien 21:05): take-profit at the OPPOSITE 1h band of the last closed 1h candle (long: upper, short: lower), resting limit,
        # only when that level is in profit vs the average price; checked before stop/BE/trail_15m/15m-score (whichever comes first)
        if getattr(s,'tp_opp1h',False):
            p=s.pos[c];g=sd(p);b=s.b1h(c);avg=p['cost']/p['q']
            if b is not None:
                tg=float(b.up if g>0 else b.lo)
                if tg==tg and g*(px-tg)>=0 and g*(tg-avg)>0:
                    fill=px if gap else tg
                    return s.close(c,t,fill,f'Chốt {SIDE(p)} tại dải {"trên" if g>0 else "dưới"} BB 1h {tg:.6g} (nến 1h {str(b.name)[11:16]} đóng) – mục tiêu dải đối diện 1h của G6, khớp lệnh giới hạn chờ sẵn {fill:.6g}',CFG['fee_maker'])
        return super().manage(c,t,px,bars,closed_bar,gap,live)
    def add_note(s,c,p):
        b=s.b1h(c);return '' if b is None else (f'; điều kiện 1h: giá ≤ dải dưới BB 1h {b.lo:.6g}' if sd(p)>0 else f'; điều kiện 1h: giá ≥ dải trên BB 1h {b.up:.6g}')

# ---------------- G7_MTF (Vien 2026-10-10 16:19): coin-only multi-timeframe 4h/1h/15m, NO BTC conditions ----------------
class BookMTF(BookT1H):
    """G7_MTF: "Chỉ quan tâm động thái 4h, 1h và 15 phút của 1 coin. Rule lọc token ko đổi. Ko cần quan tâm điều kiện bit. Sl 500."
    Inherits BookT1H (1h-band trigger, adds = ladder level AND same-side 1h band, TP at the opposite 1h band) and Book15 (BE +40$, 15m score
    exit <=-3 / <=-4 under 60$ gated by C2, trail_15m, resting SL). Differences:
      - no_btc: rules() ignores BTC 1D/1W, the BTC short-term re-check and the BTC part of the 2nd-position gate (gate keeps: 1st position
        u_net >= 0 and the new coin's 4h MA20 not against the side). All other rules kept (shortlist, BB-respect score, 1D stability,
        MA20 slope gate, 30% margin, max 2 positions, one side per coin).
      - 4h trend (trend4h) LONG: UP/SIDEWAYS, SHORT: DOWN/SIDEWAYS (trend_allow, entries AND adds) – never against the 4h trend.
      - ENTRY only (adds use the 1h band + 1.5% spacing + full rule re-check): price in the lower half of the last closed 4h BB
        (%B4h <= pb4_max, short >= 1-pb4_max) AND 15m confirmation on the last CLOSED 15m candles: outlook score15 >= 0, or a closed
        15m candle back above the 15m lower band after a touch (one of the last 2 closed candles had low <= its 15m lower band and the
        last closed candle closed above its 15m lower band); short mirrored (upper band)."""
    trend_allow={'L':['UP','SIDEWAYS'],'S':['DOWN','SIDEWAYS']}
    no_btc=True;pb4_max=0.5;sc15_min=0
    def mtf_check(s,c,side,bars,px,pathB=False):
        L_=side!='S';F=[];I={}
        b=bars.iloc[-1];w=float(b.up)-float(b.lo)
        pb=(px-float(b.lo))/w if w>0 else float('nan');I['pb4h']=round(pb,3) if pb==pb else None
        if pathB:pass          # SHORT Path B (Vien 17:12): 4h closed below the lower band -> %B4h>=0.5 waived
        elif pb!=pb:F.append('chưa có dải BB 4h')
        elif L_ and pb>s.pb4_max:F.append(f'giá ở nửa trên BB 4h (%B4h {pb:.2f} > {s.pb4_max})')
        elif not L_ and pb<1-s.pb4_max:F.append(f'giá ở nửa dưới BB 4h (%B4h {pb:.2f} < {1-s.pb4_max})')
        x=s.f15(c)
        if x is None:F.append('chưa có dữ liệu nến 15m');return F,I
        x=prep15(x);sc,FF,info=score15(x,side);z=x.iloc[-1];z1=x.iloc[-2]
        if L_:rec=bool(((z.l<=z.lo) or (z1.l<=z1.lo)) and z.c>z.lo)
        else:rec=bool(((z.h>=z.up) or (z1.h>=z1.up)) and z.c<z.up)
        I.update(score15=int(sc),rec15=rec,c15=str(x.index[-1])[11:16])
        if not (sc>=s.sc15_min or rec):
            F.append(f'15m chưa xác nhận: điểm 15m {sc} < {s.sc15_min} và chưa có nến 15m đóng quay lại {"trên dải dưới" if L_ else "dưới dải trên"} BB 15m sau khi chạm (nến {I["c15"]})')
        I['conf']=(f'điểm 15m {sc} ≥ {s.sc15_min}' if sc>=s.sc15_min else '')+(' + ' if (sc>=s.sc15_min and rec) else '')+((f'nến 15m {I["c15"]} đóng quay lại {"trên dải dưới" if L_ else "dưới dải trên"} BB 15m sau khi chạm') if rec else '')
        return F,I
    def try_entry(s,c,t,px,bars,reg,rk=None,side='L',band=None,blbl='1h'):
        L_=side!='S';b=band;pth=None;F0=[]
        if b is None or b.lo!=b.lo:return
        if (L_ and not px<=b.lo) or (not L_ and not px>=b.up):return
        if c in s.pos and s.pos[c].get('side','L')==('L' if L_ else 'S'):return
        if not L_ and getattr(s,'sgate_on',False):
            pth,F0,rk=s.sgate(c,px,bars,b,rk,True)
            if pth is None:return
        F,I=s.rules(c,side,bars,None,rk,'entry');F2,I2=s.mtf_check(c,side,bars,px,pth=='B');F=F0+F+F2
        if F:
            s.blocked('ENTRY',c,t,px,side,F,str(getattr(b,'name','')));return
        g2=''
        if len(s.pos)==1:
            (c1,p1),=s.pos.items();g2=f'; LỆNH 2 (không xét BTC): lệnh 1 {c1} đang {s.u_net(p1,p1.get("last",p1["first"])):+.4f}$ (≥0), MA20 4h {"không giảm" if L_ else "không tăng"} ({I["slope"]:+.1f}%/10 nến)'
        fill=min(px,b.lo) if L_ else max(px,b.up);n=s.leg(0)*s.LV()
        s.pos[c]=dict(first=fill,q=n/fill,cost=n,fee=n*CFG['fee_maker'],k=1,t0=t,target='mid',paused=False,last=fill,fills=[(str(t),fill,n,'entry')],held=False,trail=None,partial=None,real=0.0,margin=s.leg(0),lev=s.LV())
        if not L_:s.pos[c]['side']='S'
        if pth:s.pos[c]['path']=pth
        b4=bars.iloc[-1];ptxt=(f'; SHORT Path B (phá vỡ xuống 4h): nến 4h đóng {float(b4.c):.6g} < dải dưới {float(b4.lo):.6g}, chạm lại dải trên 1h; bỏ điều kiện %B4h≥0.5' if pth=='B' else (f'; SHORT Path A (coin đi trong dải ổn định: điểm dải trên {rk["sA_m"].get("score")}, phá thật {rk["sA_m"].get("bo")}, vượt ≥6% {rk["sA_m"].get("deep")}, 30 ngày x{rk["sA_m"].get("r30")}; nến 4h gần nhất đóng trong dải)' if pth=='A' else ''))
        tr=(rk or {}).get('trend') or {}
        s.ev('ENTRY',c,t,fill,(f'LONG: giá chạm dải dưới BB 1h ({b.lo:.6g})' if L_ else f'SHORT: giá chạm dải trên BB 1h ({b.up:.6g})')+f'; xu hướng 4h {tr.get("trend")} ({tr.get("why")}); vị trí trong BB 4h %B {I2.get("pb4h")} ({"≤" if L_ else "≥"}{s.pb4_max if L_ else 1-s.pb4_max}); xác nhận 15m: {I2.get("conf")}'+ptxt
              +f' – đã kiểm tra đủ bộ rule G7 (KHÔNG xét BTC theo yêu cầu Vien): shortlist động, điểm tôn trọng dải, ổn định 1D, MA20 4h dốc {I["slope"]:+.1f}%/10 nến; ký quỹ {n/s.LV():.2f}$ (≤30%)'+(f'; vốn tính khối lượng {s.base():.2f}$ (x{s.scale():.4f})' if getattr(s,'compound',False) else '')+g2+(f'; {rk["text"]}' if rk and rk.get('text') else ''),
              notional=n,side='LONG' if L_ else 'SHORT',trigger_tf='1h',trend_4h=tr.get('trend'),pb4h=I2.get('pb4h'),score15=I2.get('score15'),rec15=I2.get('rec15'),**({'short_path':pth} if pth else {}),**({'bb_score':rk.get('score'),'rank':rk.get('rank'),'n_cand':rk.get('n_cand')} if rk else {}))
        return True


SL_SCALE=True   # Vien t539u: SL = 25% ky quy, scale theo von
Book.sl=property(lambda s:s.__dict__.get('_slb',0)*(s.scale() if SL_SCALE else 1),lambda s,v:s.__dict__.__setitem__('_slb',v))
