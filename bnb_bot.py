"""LIVE trading bot (BINANCE USDT-M Futures build) – BASE_SL250 (x5, ladder 750/250/250/250 scaled to live equity) rule set, capital scaled to the live account. Strategy engine = strat.py (unchanged logic, 4-decimal money rounding).
DRY-RUN by default (env BNB_DRY_RUN=0 is required to send real orders). Execution layer: livecore.py + bnx.py (Binance USDT-M). Key: secrets\\binance_api_key.txt + binance_api_secret.txt (or env BNB_API_KEY/BNB_API_SECRET)."""
import winutf8  # Windows bundle: UTF-8 I/O
import json,math,time,pickle,os,traceback,datetime as dt,pandas as pd
import dynuni
import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt,numpy as np
plt.rcParams['text.parse_math']=False      # 10/10 06:53: '$' in reasons (e.g. '254.56$ … 5091.26$') was parsed as mathtext -> chart ParseException; draw all text literally
from hl import *;import strat,bbrank;from strat import CFG,Book,btc_regime
import sys,livecore
import tg_live
import step3
S3REF=[None]
import bnx as hlx
DRY=(os.environ.get('BNB_DRY_RUN','1')!='0') or ('--dry' in sys.argv)
LIVE_MODE=True
B=os.path.dirname(os.path.abspath(__file__))+os.sep
def grp(n): return n.replace('_X10','').rsplit('_',1)[0]
def now():return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)+dt.timedelta(hours=7)
EVF=B+'events_live.jsonl';AVF=B+'alerts_live.jsonl';TRF=B+'trades_live.csv';TSF=B+'data/trades_side_live.csv';STF=B+'state_live.json'
def jl(path,obj):
    with open(path,'a') as f:f.write(json.dumps(obj,ensure_ascii=False,default=str)+'\n')
BARS={}
_last_chart=[None]
M15={};M15_slot={}
def get15(c,force=False):
    """15m candles (closed only) + BB20/RSI14, refetched at most once per 15m slot per coin."""
    slot=int((time.time()-5)//900)
    if force or M15_slot.get(c)!=slot or c not in M15:
        try:
            x=candles(c,'15m',2);x=bb(x[x.closed]);x['rsi']=strat.rsi(x.c);M15[c]=x;M15_slot[c]=slot
        except Exception: pass
    return M15.get(c)
RANK={};RK_T={};D1_T={};D1={}
H1C={};H1T={};TREND={}     # G6_TREND1H: closed 1h BB bars per coin, fetch times, 4h trend (strat.trend4h)
M1={}
def get1m(c):
    """Last ~15 one-minute candles (incl. the one in progress) – used to replay intra-minute highs/lows to resting orders."""
    try: M1[c]=candles(c,'1m',0.011)
    except Exception: M1[c]=None
    return M1[c]
def path1m(r): return [r.o,r.l,r.h,r.c] if r.c>=r.o else [r.o,r.h,r.l,r.c]
FAST=5
RET_EV=[]
BTCX=dict(block=False,why=[],since=None,px=None,info={});BTCD={}
def btc_update(px=None,refetch=False):
    """BTC re-check state used by strat.btc_now() right before every entry/add (Vien 22:42)."""
    try:
        if refetch or 'h1' not in BTCD:
            BTCD['h1']=bb(candles('BTC','1h',3));BTCD['f15']=candles('BTC','15m',1)
        h1,f15=BTCD['h1'],BTCD['f15'];px=px if px is not None else float(h1.c.iloc[-1])
        r=strat.btc_recheck(h1,f15,px)
        if r['block'] and not BTCX['block']:BTCX['since']=str(now().replace(microsecond=0))
        if not r['block']:BTCX['since']=None
        if r['block_s'] and not BTCX.get('block_s'):BTCX['since_s']=str(now().replace(microsecond=0))
        if not r['block_s']:BTCX['since_s']=None
        BTCX.update(block=r['block'],why=r['why'],block_s=r['block_s'],why_s=r['why_s'],px=r['px'],info=r['info'],at=str(now().replace(microsecond=0)))
    except Exception as ex:
        BTCX.update(err=str(ex)[:120])      # data error -> keep last state
strat.BTC_GATE=lambda:BTCX
MOMX=float(((json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'config_live.json'),encoding='utf-8')).get('entry_filter') or {}).get('mom4h_pct')) or 0)
def _momf(c,t,px,L_):
    # Vien t546u (= BaseAster t544u): gia hien tai so voi gia dong nen 15m cuoi cung ket thuc <= t-4h; LONG bo neu giam >=X%, SHORT bo neu tang >=X%
    x=get15(c)
    if x is None or not len(x):return None
    t=pd.Timestamp(t);ref=x.c[x.index+pd.Timedelta('15min')<=t-pd.Timedelta('4h')]
    if not len(ref):return None
    m=(px/float(ref.iloc[-1])-1)*100
    r_=_momf0(c,t,px,L_,m)
    if r_ and S3REF[0] is not None:
        try:S3REF[0].note_block(c,t,L_)
        except Exception:pass
    return r_
def _momf0(c,t,px,L_,m):
    if L_ and m<=-MOMX:return f'gia giam manh vao dai: {m:.2f}% trong 4h (<= -{MOMX:g}%)'
    if not L_ and m>=MOMX:return f'gia tang manh vao dai: {m:+.2f}% trong 4h (>= +{MOMX:g}%)'
    return None
strat.MOMF=_momf if MOMX>0 else None
def _blocked15(s,kind,c,t,px,side,F,key):
    """Vien 03/11: same coin/side/reason block re-logged if >=15 min since the last logged one (was once per 4h bar). Logging only."""
    k=(kind,c,side,'|'.join(x.split(':')[0][:40] for x in F))
    d=s.__dict__.setdefault('blk_ts',{});now=time.time()
    if now-d.get(k,0)<900:return
    d[k]=now
    btc=all(x.split(' – ')[-1].startswith('BTC ') for x in F)
    ev=('ENTRY_BLOCKED_' if kind=='ENTRY' else 'ADD_BLOCKED_')+('BTC' if btc else 'RULES')
    s.ev(ev,c,t,px,f'Chặn {"vào lệnh" if kind=="ENTRY" else "nhồi"} {"LONG" if side!="S" else "SHORT"} {c}: '+'; '.join(F),side='LONG' if side!='S' else 'SHORT',fails=F)
strat.Book.blocked=_blocked15
def rank_update(c,force=False):
    """BB-respect score (90d of CLOSED 4h bars, recomputed at each new 4h close) + 1D stability (closed daily candles, hourly)."""
    try:
        lc=BARS[c][BARS[c].closed].index[-1]
        if force or RK_T.get(c)!=lc:
            x=bb(candles(c,'4h',100));x=x[x.closed];r=bbrank.now(x);RANK.setdefault(c,{})['short']=bbrank.now_short(x);time.sleep(0.15);RANK[c]=dict(RANK.get(c,{}),**r,bar=str(x.index[-1]));RK_T[c]=lc
        if force or time.time()-D1_T.get(c,0)>3600:
            d=candles(c,'1d',260);d=d[d.closed];time.sleep(0.15);z=bbrank.stab_frame(d).iloc[-1];ok,bad=bbrank.stab_eval(z)
            RANK[c]=dict(RANK.get(c,{}),stab_ok=ok,stab_bad=bad,stab_text=bbrank.stab_text(z,ok,bad),day=str(d.index[-1])[:10],**{k:(round(float(z[k]),3) if pd.notna(z[k]) else None) for k in('r30','r60','chg7','chg14','chg30','bwr','pb')})
            D1_T[c]=time.time()
    except Exception as ex: RANK.setdefault(c,{})['err']=str(ex)[:120]
    return RANK.get(c)
def chart(ev,book,tag=None,fills=None,m15=None):
    c=ev['coin'];x=BARS.get(c);SH=str(ev.get('side','')).upper()=='SHORT'
    em='v' if SH else '^';ec='red' if SH else 'lime'      # entries/adds: short = red down-triangle, long = green up-triangle
    if x is None:return
    if m15 is not None:
        fig,(ax,a2)=plt.subplots(2,1,figsize=(13,10),gridspec_kw=dict(height_ratios=[1.1,1]))
        y=m15.iloc[-96:];cy=np.where(y.c>=y.o,'#26a69a','#ef5350')
        a2.vlines(y.index,y.l,y.h,color=cy,lw=.7);a2.bar(y.index,(y.c-y.o).abs(),bottom=y[['o','c']].min(axis=1),width=pd.Timedelta(minutes=10),color=cy)
        a2.plot(y.index,y.up,'#555',ls='--',lw=.8);a2.plot(y.index,y.lo,'#555',ls='--',lw=.8);a2.plot(y.index,y.mid,'#ff7f0e',lw=1,label='Dải giữa BB 15m')
        for (t,px,n,k) in fills or []:
            t=pd.Timestamp(t)
            if t>=y.index[0]:a2.scatter(t,px,marker={'exit':'X','partial':'D'}.get(k,em),c={'exit':'gold','partial':'orange'}.get(k,ec),s=90,edgecolors='k',zorder=6)
        if ev.get('price'):a2.axhline(ev['price'],color='purple',lw=.8,ls=':')
        a2.set_title(f"Chart 15m (RSI15 hiện tại {y.rsi.iloc[-1]:.0f})",fontsize=9.5);a2.grid(alpha=.2);a2.legend(loc='upper left',fontsize=8)
    else: fig,ax=plt.subplots(figsize=(13,6))
    x=x.iloc[-60:];col=np.where(x.c>=x.o,'#26a69a','#ef5350')
    ax.vlines(x.index,x.l,x.h,color=col,lw=.8);ax.bar(x.index,(x.c-x.o).abs(),bottom=x[['o','c']].min(axis=1),width=pd.Timedelta(hours=2.8),color=col)
    ax.plot(x.index,x.up,'#555',ls='--',lw=.9);ax.plot(x.index,x.lo,'#555',ls='--',lw=.9);ax.plot(x.index,x.mid,'#1f77b4',lw=1.1)
    for i,(t,px,n,k) in enumerate(fills or []):
        t=pd.Timestamp(t);ax.scatter(t,px,marker={'exit':'X','partial':'D'}.get(k,em),c={'exit':'gold','partial':'orange'}.get(k,ec),s=110,edgecolors='k',zorder=6)
        ax.annotate({'entry':'Vào SHORT' if SH else 'Vào LONG','add':'Nhồi','exit':'Đóng','partial':'Chốt 50%'}[k]+f' {px:.5g}',(t,px),xytext=(10,20 if i%2==0 else -25),textcoords='offset points',fontsize=9,bbox=dict(fc='white',ec='#999'))
    import textwrap;ax.set_title(f"[{"DRY" if DRY else "LIVE"}{' '+tag if tag else ''}] {c} {('SHORT' if SH else 'LONG') if ev.get('side') else ''} – {ev['event']} {ev['time'][:16]} giờ VN\n"+textwrap.fill(ev['reason'],150),fontsize=9.5);ax.grid(alpha=.2);plt.tight_layout()
    fn=f"{B}charts/{ev['time'][:16].replace(':','').replace(' ','_')}_{c}{'_SHORT' if SH else ''}_{ev['event']}{'_'+tag if tag else ''}.png";plt.savefig(fn,dpi=100);plt.close();_last_chart[0]=fn
def main():
    cfg=json.load(open(B+'config_live.json'));G=cfg['groups'];STOPS=cfg['stops'];DT=float(cfg.get('daily_target_usd',CFG['daily_target_usd']))
    CFG['partial_frac']=float(cfg.get('partial_frac',CFG['partial_frac']));g2=cfg.get('gate2',{})
    CFG['gate2_u1_min']=float(g2.get('u1_min',CFG['gate2_u1_min']));CFG['gate2_slope_min']=float(g2.get('slope_min',CFG['gate2_slope_min']))
    strat.BTC_RECHECK.update({k:v for k,v in cfg.get('btc_recheck',{}).items() if k in strat.BTC_RECHECK})
    strat.MIN_EQ=float(cfg.get('min_equity',0) or 0)
    strat.BTC_VOL.update({k:v for k,v in (cfg.get('btc_vol') or {}).items() if k in strat.BTC_VOL})      # zone 1 BTC_VOL (Vien 30/10, port từ BaseAster)
    ER=cfg.get('entry_rank',{});BB_MIN=float(ER.get('bb_min',bbrank.BB_MIN));bbrank.ST.update(ER.get('stab',{}));SKIP_ST=bool(ER.get('skip_stab_1d',False))
    BRC={f'{g}_{k}':dict(group=g,sl=v,**G[g]) for g in G for k,v in STOPS.items() if k in G[g].get('stops',STOPS)}   # a group may restrict its stop levels (G5_15M: SL500 only)
    for n_x,x_ in (cfg.get('extra_branches') or {}).items():      # clone branches with their own overrides (Vien 2026-10-15: BASE_SL250_X10 = BASE_SL250 rules, leverage x10, separate 5000$ account)
        g_=x_['group'];BRC[n_x]=dict(dict(group=g_,sl=STOPS[x_['stop']],**G[g_]),**{k_:v_ for k_,v_ in x_.items() if k_ not in('group','stop')})
    LIVE_BR=cfg['live']['branch'];BRC={n_:v_ for n_,v_ in BRC.items() if n_==LIVE_BR}      # LIVE: only the BASE_SL250_X10 branch is traded
    SGC=cfg.get('short_gate') or {};SG_ON=bool(SGC.get('enabled'))      # SHORT gate Path A/B (Vien 10/10 17:12)
    strat.SGATE.update({k:SGC[k] for k in strat.SGATE if k in SGC})
    DU=cfg.get('dynamic_universe') or {};DYN_ON=bool(DU.get('enabled'))
    MAXLEV={}      # coin -> exchange max leverage (from the shortlist scan rows); a branch with a leverage override (BASE_SL250_X10 = 10) only trades coins with max leverage >= its leverage
    try:
        _d=pd.read_csv(B+'data/shortlist_latest.csv');MAXLEV.update({r_.coin:int(r_.max_lev) for r_ in _d.itertuples() if r_.max_lev==r_.max_lev})
    except Exception as ex:print('maxlev init',ex,flush=True)
    LEV_T=[0.0]
    def lev_refresh(force=False):      # Vien 04/11: max leverage of forced coins straight from Binance (leverageBracket, cached 1h in bnx); CSV value kept as fallback
        if not cfg.get('force_universe') or (not force and time.time()-LEV_T[0]<3600):return
        LEV_T[0]=time.time()
        try:
            import hl as _hl
            ML=_hl.LEV_PROVIDER() if _hl.LEV_PROVIDER else {}
            for c_ in [str(x).upper() for x in cfg['force_universe']]:
                if ML.get(c_):MAXLEV[c_]=int(ML[c_])
            print('MAXLEV_BNX',{c_:MAXLEV.get(c_) for c_ in [str(x).upper() for x in cfg['force_universe']]},flush=True)
        except Exception as ex:LEV_T[0]=time.time()-3000;print('MAXLEV_BNX err (fallback CSV)',str(ex)[:120],flush=True)
    def ok_lev(c,lv):
        lev_refresh();return (not lv) or MAXLEV.get(c,0)>=lv
    EXCL=set(str(x).upper() for x in (cfg.get('excluded_coins') or []))      # Vien 15/10 22:5x: BTC/ETH are NOT trade candidates (no entry, no shortlist membership; both sides, both branches). BTC data for the market filter (btc_update/regime) is fetched separately and unaffected; open positions on excluded coins are managed normally (exits + adds)
    DYN={};DYN_S={};SLF=B+'state/shortlist.json';SL_META={};SHORT_ON=bool((cfg.get('short_side') or {}).get('enabled'))
    if DYN_ON:      # dynamic market-wide shortlist per group (Vien 09/10 11:04/11:05): groups keep a fixed SELECTION RULE, not a fixed coin list
        if os.path.exists(SLF):
            try:
                j=json.load(open(SLF));DYN.update({g:[c_ for c_ in v if c_ not in EXCL] for g,v in j.get('lists',{}).items() if g in G});SL_META.update(j.get('meta',{}))
                if SHORT_ON:DYN_S.update({g:[c_ for c_ in v if c_ not in EXCL] for g,v in (j.get('lists_short') or {}).items() if g in G})
            except Exception as ex:print('shortlist load',ex,flush=True)
        SCAN_ON=bool(cfg.get('shortlist_scan',not cfg.get('force_universe')))      # Vien 04/11: force_universe (ASTER) -> market-wide shortlist scan OFF
        if SCAN_ON:SCAN=dynuni.Scanner(DU.get('prefilter',{}),BB_MIN);SCAN.start();SCAN.request('khởi động bot')
        else:SCAN=None;print('SHORTLIST_SCAN_OFF force_universe=%s'%cfg.get('force_universe'),flush=True)
    else: SCAN=None
    # Shortlist guard (Vien 18:41 "shortlist rules must be guaranteed absolutely"): entries/adds ONLY from the current dynamic list of that side;
    # no legacy/hard-coded coin list is ever used (coins_legacy in config.json is documentation only); a list older than SL_MAX_AGE_H is not trusted.
    SL_MAX_AGE_H=float(DU.get('list_max_age_h',8))
    FORCE_U=[str(x).upper() for x in (cfg.get('force_universe') or [])]      # Vien 30/10: live = BaseAster, chỉ ASTER
    def guni(g):
        if FORCE_U:return list(FORCE_U)
        if G[g].get('universe_union'):return list(dict.fromkeys(c for x in G[g]['universe_union'] for c in guni(x)))   # G6_TREND1H: union of BASE+G1 dynamic lists
        return DYN.get(g,[]) if DYN_ON else []
    GR=[g for g in G if not G[g].get('universe_union')]      # groups with their own selection rule (scanner output)
    def sl_fresh():
        if FORCE_U:return True      # Vien 04/11: forced universe (ASTER) never depends on the shortlist scan age
        try:return DYN_ON and (now()-pd.Timestamp(SL_META['time']).to_pydatetime()).total_seconds()<=SL_MAX_AGE_H*3600
        except Exception:return False
    def guni_s(g):
        if FORCE_U:return list(FORCE_U) if SHORT_ON else []
        if G[g].get('universe_union'):return list(dict.fromkeys(c for x in G[g]['universe_union'] for c in guni_s(x)))
        return DYN_S.get(g,[]) if (DYN_ON and SHORT_ON) else []
    ALL=sorted(set(c for g in G for c in guni(g)+guni_s(g)))
    sp=B+'state/live_book.pkl';buf=[];CLOSE_G=set()
    def mklog(name):
        def log(ev):
            ev=dict(ev,branch=name)
            if ev['event'] in('EXIT','STOP','EXIT_RUNNER','EXIT_15M'):
                if SCAN is not None and name in BRC: SCAN.request(f"{name} đóng {ev.get('coin')}");CLOSE_G.add(BRC[name]['group'])
                tr=books[name].trades[-1];hdr=not os.path.exists(TRF)
                row={k:v for k,v in tr.items() if k!='fills'}|{'group':grp(name)}
                if hdr: pd.DataFrame([row]).to_csv(TRF,index=False)
                else:     # append-only: keep the existing header/column order; side shown as a reason prefix (full rows with a side column -> data/trades_side.csv)
                    cols=open(TRF).readline().strip().split(',')
                    r2=dict(row);r2['reason']=f"[{row.get('side','LONG')}] "+str(row.get('reason',''))
                    pd.DataFrame([{k:r2.get(k) for k in cols}]).to_csv(TRF,mode='a',header=False,index=False)
                pd.DataFrame([row]).to_csv(TSF,mode='a',header=not os.path.exists(TSF),index=False)
                ev['_fills']=tr['fills']
            buf.append(ev)
        return log
    def flush():
        # merge identical signals across branches -> one line with signal_id + branches list
        groups={}
        for ev in buf:
            sep=BRC[ev['branch']]['group'] if BRC.get(ev.get('branch'),{}).get('separate') else ''   # separate-account branch (G5_15M) never merged with others
            k=(ev['event'],ev['coin'],ev['time'],round(ev.get('price',0),8),ev['reason'] if ev['event'] in('ENTRY','TARGET_UP') else ev['event'],sep,ev.get('side'))
            groups.setdefault(k,[]).append(ev)
        for k,evs in groups.items():
            e0=evs[0];sid=f"{e0['event']}|{e0['coin']}|{e0['time']}"
            per={e['branch']:{kk:e[kk] for kk in('net','trade_net','remaining_notional','trail','unreal','notional','avg','adds','reason','group','stop','day','daily_realized','target','locked','peak','fails') if kk in e} for e in evs}
            out={kk:v for kk,v in e0.items() if kk not in('branch','net','trade_net','remaining_notional','trail','unreal','notional','avg','_fills','group','stop','daily_realized','locked','peak','fails')}
            out.update(signal_id=sid,branches=[e['branch'] for e in evs],per_branch=per)
            if e0['event'] in('ENTRY','ADD','EXIT','STOP','TP_PARTIAL','EXIT_RUNNER','EXIT_15M','TP_PARTIAL_15M'):
                try:
                    bk=books[e0['branch']];fills=e0.get('_fills') or (bk.pos[e0['coin']]['fills'] if e0['coin'] in bk.pos else [])
                    tag=None if len(evs)>1 else e0['branch']
                    if len(evs)>1: tag='+'.join(sorted(set(grp(e['branch']) for e in evs)))+f'({len(evs)})'
                    m15=get15(e0['coin']) if BRC.get(e0['branch'],{}).get('exit') else None
                    chart(dict(out,reason=out['reason']+f" | nhánh: {', '.join(out['branches'])}"),None,tag=tag,fills=fills,m15=m15);out['chart']=_last_chart[0]
                except Exception as ex: out['chart_err']=str(ex)
            jl(EVF,out)
            try: tg_live.notify(out)
            except Exception: pass
            if out['event'].startswith('ALERT') or out['event']=='STOP':jl(AVF,out)
        buf.clear()
    books=pickle.load(open(sp,'rb')) if os.path.exists(sp) else {}
    for old in list(books):             # migrate legacy names SL250 -> BASE_SL250
        if not old.startswith(('BASE_','G1_','G2_','G3_','G5_','G6_','G7_')):
            b=books.pop(old);b.name='BASE_'+old;books[b.name]=b
    NEWB=[];rs=B+'state/reset_G5_15M_SL500.flag';reset_ev=None;switched=[];init_peak=[];TH_CHG=[]
    renamed=[]
    for o,r in cfg.get('renamed_branches',{}).items():          # e.g. G5_15M_SL500 -> G5_15M_SL250 (Vien 06:58): same account/history/positions
        if o in books and r['to'] in BRC and r['to'] not in books:
            b=books.pop(o);b.name=r['to'];books[r['to']]=b;renamed.append((o,r['to'],b.sl,BRC[r['to']]['sl']))
    RET=cfg.get('retired_branches',{});retire=[n for n in RET if n in books and not getattr(books[n],'retired',None)]
    if os.path.exists(rs) and 'G5_15M_SL500' in books:   # one-shot void of G5 (Vien 22:29): not a realized loss/stop, fresh 5000$
        old=books.pop('G5_15M_SL500');why=open(rs).read().strip()
        reset_ev=dict(time=str(now().replace(microsecond=0)),event='RESET',coin=','.join(old.pos) or '*',price=0,signal_id='RESET|G5_15M_SL500',branches=['G5_15M_SL500'],
            reason=why or 'Xóa lệnh AVAX theo yêu cầu Vien vì vi phạm tiêu chí ổn định khung ngày; tài khoản G5 về 5000$ trống',
            voided_positions={c:dict(since=str(p['t0']),adds=p['k']-1,avg=round(p['cost']/p['q'],6),notional=round(p['cost'],4)) for c,p in old.pos.items()},voided_trades=len(old.trades),voided_realized=round(old.cash_pnl,4))
        os.replace(rs,rs+'.done')
    for name,c in BRC.items():
        if name not in books:
            books[name]=livecore.LiveBook(None,sl=c['sl'],name=name);books[name].peak=0.0;books[name].sgate_evt=True
            books[name].started=str(now().replace(microsecond=0));NEWB.append(name)
            books[name].scan15_on=books[name].compound_on=books[name].c2_on=True   # born with these features (no retroactive one-time CONFIG_CHANGE events)
            books[name].short_on=True;books[name].sl_guard=True      # born with SHORT + shortlist guard (no retroactive one-time CONFIG_CHANGE events)
            if cfg.get('equity_epoch'):books[name].epoch=cfg['equity_epoch']['time']     # new account: its own start is its epoch (no RESET_EPOCH)
        books[name].lev=c.get('lev')      # per-branch leverage override (None = global CFG['lev'] = 5)
        books[name].compound=bool((cfg.get('sizing') or {}).get('compound'))      # Vien 10/10 00:12
        if isinstance(books[name],strat.Book15):books[name].hold_mid=bool((cfg.get('exit_hold_mid4h') or {}).get('enabled'))   # C2 (Vien 21:05)
        if isinstance(books[name],strat.BookT1H):
            books[name].tp_opp1h=bool(c.get('tp_opp1h'))
            books[name].trend_allow={k:list(v) for k,v in (c.get('trend_allow') or type(books[name]).trend_allow).items()}
        if isinstance(books[name],strat.BookMTF):    # G7_MTF (Vien 10/10 16:19)
            books[name].no_btc=bool(c.get('no_btc',True));books[name].pb4_max=float(c.get('pb4h_max',0.5));books[name].sc15_min=int(c.get('sc15_min',0))
        if 'alerts' in c:books[name].alerts=[tuple(a) for a in c['alerts']]     # per-branch alert levels ([] = none, e.g. SL100)
        if str(c.get('exit','')).startswith('15m') and not isinstance(books[name],strat.Book15):   # switch exit rule in place (Vien 2026-10-09 06:56); positions/history kept
            books[name].__class__=strat.Book15;switched.append(name)
        books[name].ladder=c['ladder'];books[name].usd=None;books[name].sl=c['sl'];books[name].sgate_on=SG_ON
        if isinstance(books[name],strat.Book15):
            th_old=books[name].__dict__.get('th')
            for k in('eps','be_after','th','th_small','small'):
                if k+'_15m' in c: setattr(books[name],k,type(getattr(strat.Book15,k))(c[k+'_15m']))
            if th_old is not None and th_old!=books[name].th:      # exit threshold changed (e.g. Vien 13:28: -2 -> -3): re-evaluate the 'weak previous candle' flag with the new threshold
                for c_,p in books[name].pos.items():
                    o=p.get('outlook') or {}
                    if o.get('score') is not None:
                        thx=books[name].th_small if o.get('th')==books[name].th_small else books[name].th
                        p['weak_prev']=o['score']<=thx
                TH_CHG.append((name,th_old,books[name].th))
            T15=cfg.get('trail_15m')
            if T15: books[name].trail_cfg=dict(tiers=[tuple(map(float,x)) for x in T15['tiers']],mid_buf=T15.get('mid_buf'),log_min=float(T15.get('log_min',0.001)))
            for c_,p in books[name].pos.items():
                if 'peak' not in p: init_peak.append((name,c_))
        if not hasattr(books[name],'daily_hit'): books[name].daily_hit={}
        if getattr(books[name],'rlog',None) is None: books[name].rlog=[(str(tr['t1']),tr['net']) for tr in books[name].trades]
        for p in books[name].pos.values():
            for k,v in dict(held=False,trail=None,partial=None,real=0.0).items(): p.setdefault(k,v)
    for n,b in books.items(): b.log=mklog(n)
    for old_ in [n for n in books if n not in BRC]:books.pop(old_)
    LB=books[LIVE_BR];CFGL=cfg['live']
    CFG['max_pos']=int(CFGL['max_positions'])
    for k_ in('alert_warn','alert_crit','cat_stop'):CFG[k_]=CFG[k_]*float(cfg['scaling']['factor'])      # dollar params of strat.CFG scaled like config_live.json
    hx=hlx.HLX(CFGL.get('address'),dry_run=DRY,log=lambda e:jl(EVF,dict(dict(time=str(now())),**e)),max_open_notional=float(CFGL.get('max_notional') or 0.0),sign_in_dry=True,expect_agent=CFGL.get('agent_address'))
    LIVE=livecore.Live(B,cfg,hx,lambda e:jl(EVF,e),dry=hx.dry_run)
    for b in books.values():b.live=LIVE
    def save_books():
        logs={n:b.log for n,b in books.items()}
        for b in books.values(): b.log=None
        pickle.dump(books,open(sp+'.tmp','wb'));os.replace(sp+'.tmp',sp)
        for n,b in books.items(): b.log=logs[n]
    LIVE.save_cb=save_books
    S3=step3.Step3(B,cfg,LIVE,candles,tg_live.send,lambda e:jl(EVF,e));S3REF[0]=S3      # BƯỚC 3 (Vien 03/11 t631u)
    SG=None
    if cfg.get('safe'):      # BaseAster SAFE: cầu dao vào lệnh (BaseAster + Bước 3); size/ngưỡng $ đã nhân k trong config (safe.overlay)
        import safe as _safe
        SG=_safe.Guard(dict(cfg['safe'],**(_safe.load_profile(B) or {})),B,notify=tg_live.send,emit=lambda e:jl(EVF,e))
        SG.extra_trades=lambda:_safe.step3_trades(S3.trf)
        strat.MOMF=_safe.wrap_momf(strat.MOMF,SG,lambda:LB);S3.guard=SG
        for k_ in('alert_warn','alert_crit','cat_stop'):CFG[k_]=CFG[k_]*float(cfg['safe']['k'])
        print('SAFE_ON',json.dumps({k_:cfg['safe'].get(k_) for k_ in('risk_pct','k','notional_x','stop_dist_pct','cooldown_after_sl_h','max_consec_sl','pause_after_streak_h','daily_loss_pct','max_dd_pct')}),flush=True)
    def close_ba(c_,tn,px_,why_):
        try:LB.close(c_,tn,px_,why_,CFG['fee_taker'],kind='EXIT_15M')
        except livecore.VetoClose:return False
        except Exception as ex_:print('close_ba',repr(ex_)[:200],flush=True);return False
        flush();save_books();return c_ not in LB.pos
    def live_sync(tn,orders=False):
        try:LIVE.sync(LB,tn,orders=orders);LIVE.sync_ok=True
        except Exception as ex_:
            LIVE.sync_ok=False;print(str(now())[:19],'live sync error',repr(ex_)[:200],flush=True)
            if time.time()-getattr(LIVE,'_synclog',0)>120:LIVE._synclog=time.time();LIVE.ev('LIVE_SYNC_ERROR','*',f'exchange sync failed: {repr(ex_)[:200]}')
    def do_kill(why,tn):
        LIVE.ev('LIVE_KILL','*',f'KILL switch ({why}): flatten + stop')
        try:LIVE.flatten_all(why)
        except Exception as ex_:LIVE.ev('LIVE_CLOSE_FAILED','*',f'flatten error {repr(ex_)[:200]}')
        for c_ in list(LB.pos):
            try:LB.settle_external(c_,tn,f'Đóng theo KILL switch ({why})')
            except Exception as ex_:print('kill settle',c_,repr(ex_),flush=True)
        flush();save_books();sys.exit(0)
    EP=cfg.get('equity_epoch');ep_ev=None
    if EP:     # new measurement epoch (Vien 10/09 10:15): equity back to 5000, realized/counters only from trades closed after EP['time']; pre-epoch archived, open positions kept
        E=EP['time'];per={};mk=mids();t_e=now().replace(microsecond=0)
        for name in BRC:
            b=books[name]
            if getattr(b,'epoch',None)==E: continue
            pre=[tr for tr in b.trades if str(tr['t1'])<=E];post=[tr for tr in b.trades if str(tr['t1'])>E]
            rl=list(b.rlog or []);rpre=[x for x in rl if str(x[0])<=E];rpost=[x for x in rl if str(x[0])>E]
            eq0=b.equity(mk)
            prior=dict(epoch_ended=E,archived_at=str(t_e),equity_at_reset=round(eq0,4),realized_total=round(b.cash_pnl,4),realized_pre_epoch=round(sum(n for _,n in rpre),4),
                       n_trades=len(pre),n_wins=sum(1 for tr in pre if tr['net']>0),n_losses=sum(1 for tr in pre if tr['net']<=0),n_stops=sum(1 for tr in pre if tr.get('stop')),
                       peak=round(b.peak,4),daily_hit=dict(b.daily_hit),dd_alerted=bool(b.alerted.get('DD')))
            b.epochs=list(getattr(b,'epochs',None) or [])+[dict(prior=prior,trades_pre=pre,rlog_pre=rpre)]
            b.trades=post;b.rlog=rpost;b.cash_pnl=sum(n for _,n in rpost);b.n_stops=sum(1 for tr in post if tr.get('stop'))
            b.peak=CFG['capital'];b.daily_hit={};b.alerted.pop('DD',None);b.epoch=E
            per[name]=dict(prior_equity=prior['equity_at_reset'],prior_realized=prior['realized_total'],prior_trades=prior['n_trades'],prior_wins=prior['n_wins'],prior_stops=prior['n_stops'],
                           kept_trades=[dict(coin=tr['coin'],t1=tr['t1'],net=tr['net']) for tr in post],realized=round(b.cash_pnl,4),equity=round(b.equity(mk),4),
                           open_positions={c_:dict(since=str(p_['t0']),adds=p_['k']-1,avg=round(p_['cost']/p_['q'],6),notional=round(p_['cost'],4)) for c_,p_ in b.pos.items()})
        if per:
            ep_ev=dict(time=str(t_e),event='RESET_EPOCH',coin='*',price=0,signal_id=f'RESET_EPOCH|{E}',branches=list(per),epoch=E,
                reason=f'Đặt mốc đo lường mới theo yêu cầu Vien (09/10 10:15): mốc = {E} (lúc áp dụng trail 15m khóa lãi – thay đổi rule chốt cuối). Equity mỗi nhánh tính lại từ 5000$; PnL đã chốt chỉ gồm lệnh đóng sau mốc; số lệnh/thắng/thua, cắt lỗ, đỉnh/drawdown, mục tiêu ngày tính lại từ mốc. Vị thế đang mở giữ nguyên (tạm tính từ giá vào). Số liệu trước mốc được lưu trữ, lịch sử không xóa.',
                per_branch=per)
    PK={}
    for n,c_ in init_peak:     # open positions at switch-on: peak profit from 15m highs since entry, with the position size at each candle
        try:
            p=books[n].pos[c_];t0=pd.Timestamp(p['t0'])
            if c_ not in PK:PK[c_]=candles(c_,'15m',max(1,(now()-t0).days+2))
            x=PK[c_];x=x[x.index>=t0.floor('15min')];best=-1e18;bt=None
            for ts,r in x.iterrows():
                f=[z for z in p['fills'] if z[3] in('entry','add') and pd.Timestamp(z[0])<=ts+pd.Timedelta(minutes=15)]
                n_=sum(z[2] for z in f);q_=sum(z[2]/z[1] for z in f);u_=r.h*q_-n_-n_*CFG['fee_maker']
                if q_>0 and u_>best:best=u_;bt=ts
            if bt is not None:p['peak']=best;p['peak_at']=str(bt)+' (nến 15m, khởi tạo)'
        except Exception as ex: print('peak init',n,c_,ex,flush=True)
    LK=cfg.get('lock_milestones');LK_INIT=[]
    PCT_TH=cfg.get('pct_thresholds') if (cfg.get('pct_thresholds') or {}).get('enabled') else None
    if LK:      # Vien 2026-10-16 (t197u): peak-milestone profit lock (every step$ of peak net -> stop locks >= frac*M); all live Book15 branches
        m_lk=None
        for n,b in books.items():
            if n not in BRC or not isinstance(b,strat.Book15):continue
            b.lock_step=float(LK['step']);b.lock_frac=float(LK['frac'])
            for c_,p in b.pos.items():
                if 'lock30_floor' in p:continue
                M0=math.floor(p.get('peak',0.0)/b.lock_step+1e-9)*b.lock_step;p['lock30_floor']=0.0
                if M0>=b.lock_step:      # retroactive on stored peak ONLY if price has not already crossed that lock (never trigger an immediate exit)
                    if m_lk is None:m_lk=mids()
                    lv=b.lock_level(p,round(M0*b.lock_frac,6));px_=m_lk.get(c_,p['last']);g_=strat.sd(p)
                    crossed=g_*px_<=g_*lv;p['lock30_floor']=M0 if crossed else 0.0
                    LK_INIT.append(dict(branch=n,coin=c_,side=strat.SIDE(p),peak=p.get('peak'),milestone=M0,lock_target=round(M0*b.lock_frac,4),lock_price=round(lv,6),price=px_,u_net=round(b.u_net(p,px_),4),retroactive=not crossed,floor=p['lock30_floor']))
        print('lock30 init',json.dumps(LK_INIT),flush=True)
    if retire or renamed:
        m0=mids();t_r=now().replace(microsecond=0);ret_ev=[]
        for n in retire:     # close at current market (paper), then stop trading the branch; history kept
            b=books[n];closed={}
            for c in list(b.pos):
                tr=b.close(c,t_r,m0[c],f'Đóng theo giá thị trường: nhánh {n} bị bỏ ({RET[n].get("reason","")})',CFG['fee_taker'])
                closed[c]=dict(net=tr['net'],exit=round(m0[c],6),adds=tr['adds'])
            b.retired=str(t_r)
            ret_ev.append(dict(time=str(t_r),event='BRANCH_RETIRED',coin='*',price=0,signal_id=f'BRANCH_RETIRED|{n}',branches=[n],
                reason=f'Nhánh {n} ngừng giao dịch ({RET[n].get("reason","theo yêu cầu Vien")}). Đóng {len(closed)} vị thế theo giá thị trường: '+(', '.join(f"{c} {v['net']:+.2f}$" for c,v in closed.items()) or 'không có')+f'. Chốt cuối: equity {CFG["capital"]+b.cash_pnl:.2f}$, đã chốt lũy kế {b.cash_pnl:+.2f}$, {len(b.trades)} lệnh, {b.n_stops} lần cắt lỗ. Lịch sử được giữ để so sánh.',
                closed=closed,final_equity=round(CFG['capital']+b.cash_pnl,4),realized=round(b.cash_pnl,4),n_trades=len(b.trades),n_stops=b.n_stops))
        for o,r,s0,s1 in renamed:
            b=books[r];sp_=b.stop_px  # noqa
            ret_ev.append(dict(time=str(t_r),event='CONFIG_CHANGE',coin='*',price=0,signal_id=f'RENAME|{o}',branches=[r],
                reason=f'Đổi tên {o} → {r} và cắt lỗ {s0}$ → {s1}$ theo yêu cầu Vien (09/10 06:58); giữ tài khoản riêng 5000$, lịch sử và vị thế đang mở: '+(', '.join(f"{c} (giá vốn {p['cost']/p['q']:.6g}, mức cắt lỗ mới {(-s1+p['cost']+p['fee'])/(p['q']*(1-CFG['fee_taker'])):.6g}"+(f", SL hòa vốn đang giữ {p['trail']:.6g}" if p.get('trail') else '')+")" for c,p in b.pos.items()) or 'không có')))
        RET_EV[:]=ret_ev
    lastclosed={};reg=None;reg_t=0;bars_t=0
    B15=[n for n in BRC if BRC[n].get('exit')]
    if reset_ev: jl(EVF,reset_ev)
    if RET_EV: flush()           # market closes of retired branches first (EXIT lines), then BRANCH_RETIRED / rename notes
    for e in RET_EV: jl(EVF,e)
    if ep_ev: jl(EVF,ep_ev)
    if SHORT_ON:
        newly=[n for n in BRC if not getattr(books[n],'short_on',False)]
        for n in BRC:books[n].short_on=True
        if newly:
            jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id='CONFIG_CHANGE|short_side',branches=newly,
                reason='Thêm chiều SHORT theo yêu cầu Vien (09/10 17:52–17:54): đối xứng hoàn toàn với LONG cho cả 9 nhánh (cùng tài khoản/equity/mốc đo, SL 250/350, thang nhồi, x5, ký quỹ ≤30%/vị thế, tối đa 2 vị thế tổng hai chiều, không mở long+short cùng coin). Vào short khi giá ≥ dải trên BB 4h của nến đóng gần nhất; chạm dải chỉ là tín hiệu – mọi lệnh vào và lệnh nhồi (cả hai chiều) phải qua toàn bộ bộ rule: BTC 1D/1W theo chiều, BTC ngắn hạn (short bị chặn khi BTC tăng mạnh), điểm tôn trọng dải tương ứng ≥0.75, ổn định 1D, độ dốc MA20 4h theo chiều, ký quỹ 30%, cổng lệnh 2. Thoát: điểm triển vọng 15m đảo chiều ≤-3 hai nến (≤-4 khi lãi <60$), hòa vốn ở +40$, trail 15m khóa lãi đối xứng. Lệnh bị chặn ghi ENTRY_BLOCKED_*/ADD_BLOCKED_* kèm chiều và lý do.',
                short_side=cfg.get('short_side')))
    if TH_CHG:
        jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id='CONFIG_CHANGE|exit15m_th',branches=[n for n,_,_ in TH_CHG],
            reason='Nâng ngưỡng xấu chốt theo triển vọng 15m theo yêu cầu Vien (09/10 13:28): '+', '.join(sorted(set(f'{a}→{b}' for _,a,b in TH_CHG)))+' – đóng toàn bộ khi điểm 15m ≤ '+str(TH_CHG[0][2])+' hai nến 15m đóng liên tiếp (giữ ≤-4 khi lãi <60$, SL hòa vốn khi lãi ≥40$, trail 15m khóa lãi không đổi). Vị thế đang mở áp dụng ngay; cờ "nến trước yếu" tính lại theo ngưỡng mới.',
            old_th={n:a for n,a,_ in TH_CHG},new_th={n:b for n,_,b in TH_CHG},open_positions={n:list(books[n].pos) for n,_,_ in TH_CHG}))
    if switched:
        op={n:{c:dict(adds=p['k']-1,avg=round(p['cost']/p['q'],6),notional=round(p['cost'],4),partial_done=bool(p.get('partial'))) for c,p in books[n].pos.items()} for n in switched}
        jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id='CONFIG_CHANGE|exit15m',branches=switched,
            reason='Đổi cách chốt lệnh theo yêu cầu Vien (09/10 06:56): '+', '.join(switched)+' chuyển từ kiểu B (50% dải giữa 4h + trail) sang chốt theo triển vọng chart 15m giống G5_15M (lãi ≥40$ → SL hòa vốn; điểm 15m ≤-2, hoặc ≤-4 khi lãi <60$, hai nến liên tiếp → đóng toàn bộ). Áp dụng cả cho vị thế đang mở (không vị thế nào đã chốt 50%). Giữ nguyên coin, xếp hạng, lọc 1D, thang nhồi, cắt lỗ 250/350/500, cổng lệnh 2, kiểm tra BTC, mục tiêu 200$/ngày.',
            open_positions=op))
    jl(EVF,dict(time=str(now()),event='START',signal_id='START',branches=list(BRC),reason=f'Bot {'DRY-RUN' if DRY else 'LIVE (lệnh thật)'} {len(BRC)} nhánh khởi động (tất cả nhánh chốt theo triển vọng chart 15m (từ 09/10 06:56); lệnh 2 cần lệnh 1 ≥0$ và MA20 không giảm; nhánh tài khoản riêng: {",".join(n for n in BRC if BRC[n].get("separate")) or "-"}; vào lệnh: xếp hạng điểm tôn trọng BB 4h, chỉ vào khi điểm ≥{BB_MIN} và nến ngày ổn định; kiểm tra lại BTC ngắn hạn trước mỗi lệnh/nhồi; không giới hạn vào lại coin vừa đóng; cắt lỗ/dời SL = lệnh stop chờ sẵn (kiểm tra mỗi {FAST}s + nến 1m); mục tiêu ngày {DT:.0f}$/nhánh, không trần); vị thế đang mở: '+str({n:list(b.pos) for n,b in books.items() if b.pos})))
    ALL[:]=sorted(set(ALL)|set(c for n,b in books.items() if n in BRC for c in b.pos))   # open positions are always managed, even before the first scan
    for n in []:
        if BRC[n].get('lev') and not isinstance(books[n],strat.BookT1H):
            c=BRC[n];jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id=f'CONFIG_CHANGE|new_branch|{n}',branches=[n],
                reason=f'Mở nhánh mới {n} theo yêu cầu Vien (15/10 ~13:50): nhân bản đúng luật BASE_SL250 (nhóm {c["group"]}, cắt lỗ {c["sl"]}$ ròng, thang ký quỹ {"/".join(map(str,c["ladder"]))}, cùng shortlist/cổng/thoát/trail/hòa vốn, lãi kép, trần ký quỹ 30% tính trên ký quỹ) nhưng ĐÒN BẨY x{c["lev"]:g} (khối lượng = ký quỹ bậc thang × {c["lev"]:g}); tài khoản riêng 5000$, không có vị thế lúc khởi động, mốc đo/started = {books[n].started}, mục tiêu ngày {DT:.0f}$. BASE_SL250 giữ nguyên x5 và vị thế đang mở.',
                group=c['group'],sl=c['sl'],ladder=c['ladder'],lev=c['lev'],started=books[n].started))
        elif isinstance(books[n],strat.BookMTF):
            c=BRC[n];jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id=f'CONFIG_CHANGE|new_branch|{n}',branches=[n],
                reason=f'Mở nhánh chính thức mới {n} theo yêu cầu Vien (10/10 16:19: "Lập thêm 1 nhánh chính thức. Chỉ quan tâm động thái 4h, 1h và 15 phút của 1 coin. Rule lọc token ko đổi. Ko cần quan tâm điều kiện bit. Sl 500."): tài khoản riêng 5000$, mốc đo = lúc khởi động {books[n].started}, PnL báo cáo riêng, không cộng gộp với nhánh khác. Coin: {c.get("rule_label","")}; nhồi kiểm tra lại shortlist hiện tại; shortlist cũ → không vào/nhồi. Vào lệnh: {c.get("entry_label","")} Nhồi: {c.get("adds_label","")} Cắt lỗ chờ sẵn {c["sl"]}$ ròng sau phí (tính lại sau mỗi lần nhồi, tự động khớp). Thoát giống mọi nhánh: hòa vốn +40$, điểm 15m ≤-3 hai nến (≤-4 khi lãi <60$) chỉ sau khi chạm dải giữa 4h (C2), trail 15m khóa lãi, cộng chốt tại dải đối diện BB 1h (như G6) – cái nào đến trước. Lệnh 2: lệnh 1 ≥0$ + MA20 4h không ngược chiều (không xét BTC). {c.get("alerts_label","")}',
                group=BRC[n]['group'],sl=c['sl'],ladder=c['ladder'],trend_allow=books[n].trend_allow,no_btc=True,pb4h_max=books[n].pb4_max,universe_union=c.get('universe_union'),started=books[n].started))
        elif isinstance(books[n],strat.BookT1H):
            c=BRC[n];jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id=f'CONFIG_CHANGE|new_branch|{n}',branches=[n],
                reason=f'Mở nhánh mới {n} theo yêu cầu Vien (09/10 20:08/20:09; SL60/SL70, không nhồi, chốt dải đối diện 1h: 21:05): tài khoản riêng 5000$, mốc đo = lúc khởi động {books[n].started}; cắt lỗ {c["sl"]}$/lệnh (lệnh stop chờ sẵn, lỗ ròng sau phí = {c["sl"]}$), thang {"/".join(map(str,c["ladder"]))}, x5, ký quỹ ≤30%, tối đa 2 vị thế, cổng lệnh 2. Coin = shortlist động BASE ∪ G1 theo từng chiều (điểm tôn trọng dải tương ứng ≥0.75, ổn định 1D, rule nhóm; không danh sách cũ; nhồi kiểm tra lại shortlist; shortlist cũ >8h → không vào/nhồi). MỚI: xu hướng 4h của token UP/DOWN/SIDEWAYS ({c.get("trend_label","")}); vào lệnh khi giá chạm dải BB(20,2) 1h của nến 1h đóng gần nhất: LONG khi xu hướng {"/".join(books[n].trend_allow["L"])} + chạm dải dưới 1h, SHORT khi xu hướng {"/".join(books[n].trend_allow["S"])} + chạm dải trên 1h; không bao giờ ngược xu hướng. '+('Nhồi: cùng thang/khoảng cách tối thiểu 1.5% nhưng phải chạm dải 1h cùng chiều, kiểm tra lại toàn bộ rule (kể cả xu hướng).' if len(c['ladder'])>1 else 'Không nhồi (1 lệnh 250$ ký quỹ x5).')+(' Chốt lời tại dải ĐỐI DIỆN BB 1h (long: dải trên 1h, short: dải dưới 1h; lệnh giới hạn chờ sẵn, chỉ khi có lãi so với giá vốn), song song với điểm 15m (chỉ sau khi chạm dải giữa 4h), hòa vốn +40$, trail 15m – cái nào đến trước.' if c.get('tp_opp1h') else '')+f' Giữ mọi rule cũ: BTC 1D/1W + BTC ngắn hạn theo chiều, dốc MA20 4h, thoát 15m ≤-3 (≤-4 khi lãi <60$), hòa vốn +40$, trail 15m. Không có cảnh báo WARN/CRIT (cắt lỗ 100$ đến trước).',
                group=BRC[n]['group'],sl=c['sl'],ladder=c['ladder'],trend_allow=books[n].trend_allow,started=books[n].started))
    if SCAN is not None and not all(getattr(books[n],'scan15_on',False) for n in BRC):
        nb=[n for n in BRC if not getattr(books[n],'scan15_on',False)]
        for n in BRC:books[n].scan15_on=True
        jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id='CONFIG_CHANGE|shortlist_scan_15m',branches=nb,
            reason=f'Quét lại shortlist toàn thị trường mỗi {SCAN.C["periodic_min"]} phút theo yêu cầu Vien (10/10 02:00), ~{SCAN.C["offset_s"]}s sau mỗi nến 15m đóng, tất cả nhóm, cả LONG lẫn SHORT – ngoài quét sau mỗi lệnh đóng và lúc khởi động. Rule chọn coin giữ NGUYÊN. Chạy ở luồng nền, không bao giờ chồng lần quét (lượt đến hạn khi lần trước chưa xong thì bỏ qua); dùng lại nến 4h/1D đã đóng trong bộ nhớ, chỉ tải phần nến mới (cửa sổ dữ liệu giống hệt tải đầy đủ). Chặn vào lệnh/nhồi khi shortlist cũ hơn {SL_MAX_AGE_H:g}h (trước: 8h). Sự kiện SHORTLIST_UPDATE chỉ ghi khi danh sách thay đổi; nhịp quét (heartbeat) ghi trong state.json mục scanner.'))
    if (cfg.get('sizing') or {}).get('compound') and not all(getattr(books[n],'compound_on',False) for n in BRC):
        nb=[n for n in BRC if not getattr(books[n],'compound_on',False)]
        for n in BRC:books[n].compound_on=True
        jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id='CONFIG_CHANGE|compound_sizing',branches=nb,
            reason='Tính khối lượng lệnh theo vốn lãi kép theo yêu cầu Vien (10/10 00:12: "Vốn tính lãi/lỗ nhập gốc làm cơ sở tính volume lệnh"): vốn tính khối lượng mỗi nhánh = 5000$ + PnL ĐÃ CHỐT kể từ mốc đo (không tính tạm tính); mỗi bậc thang (ký quỹ) nhân với vốn/5000; trần ký quỹ 30% = 30% của vốn đó. Mức cắt lỗ $ giữ nguyên (250/350/60/70). Vị thế đang mở giữ khối lượng cũ, lệnh nhồi tiếp theo dùng bậc thang mới. Hiện tại: '+'; '.join(f'{n} vốn {books[n].base():.2f}$ (x{books[n].scale():.4f}), lệnh đầu ký quỹ {books[n].leg(0):.2f}$' for n in BRC),
            sizing={n:dict(base=round(books[n].base(),4),scale=round(books[n].scale(),4),legs=[books[n].leg(k) for k in range(len(books[n].L))]) for n in BRC},open_positions={n:list(books[n].pos) for n in BRC if books[n].pos}))
    if (cfg.get('exit_hold_mid4h') or {}).get('enabled') and not all(getattr(books[n],'c2_on',False) for n in BRC):
        nb=[n for n in BRC if not getattr(books[n],'c2_on',False)]
        for n in BRC:books[n].c2_on=True
        jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id='CONFIG_CHANGE|exit_hold_mid4h',branches=nb,
            reason='Nâng cấp chốt lãi C2 cho mọi nhánh theo yêu cầu Vien (09/10 21:05): lệnh chốt theo điểm triển vọng 15m (≤-3 hai nến, ≤-4 khi lãi <60$) CHỈ được phép sau khi giá đã chạm dải giữa BB 4h kể từ lúc vào lệnh (long: giá ≥ dải giữa 4h của nến 4h đóng gần nhất; short: giá ≤ dải giữa). Cắt lỗ chờ sẵn, hòa vốn +40$, trail 15m giữ nguyên; G6 giữ thêm chốt tại dải đối diện 1h (cái nào đến trước). Áp dụng cả vị thế đang mở: '+(str({n:list(books[n].pos) for n in BRC if books[n].pos}) or 'không có')+'. Backtest PIT 19/08–09/10 (research/lab*.py): tốt hơn ở G1/G2/G5/G6 cả 2 nửa, hơi kém ở BASE/G3.',
            open_positions={n:list(books[n].pos) for n in BRC if books[n].pos}))
    if SG_ON and not all(getattr(books[n],'sgate_evt',False) for n in BRC):
        nb=[n for n in BRC if not getattr(books[n],'sgate_evt',False)]
        for n in BRC:books[n].sgate_evt=True
        G_=strat.SGATE
        jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id='CONFIG_CHANGE|short_gate',branches=nb,
            reason=f'Bổ sung rule SHORT theo yêu cầu Vien (10/10 17:12: "Chỉ short khi (1) Token là loại có quá khứ thể hiện đi trong dải bol ổn định, hoặc (2) Short khi confirm break down bol 4h (đóng nến dưới đường bol downline). Ko short khi break cạnh trên. Nếu có dấu hiệu quay đầu thì short."). Cổng bổ sung cho MỌI lệnh vào SHORT của {len(nb)} nhánh, mọi rule short cũ giữ nguyên. '
                   f'Path A (coin đi trong dải ổn định, khi chạm dải trên): điểm tôn trọng dải trên ≥{G_["a_score"]}, tỉ lệ phá dải trên thật (đóng trên dải mà 2 nến sau không quay lại) ≤{G_["a_bo"]:.0%}, tỉ lệ vượt dải trên ≥6% ≤{G_["a_deep"]:.0%} (90 ngày, chỉ dữ liệu quá khứ), biên độ 30 ngày 1D ≤x{G_["a_r30"]}; KHÔNG short khi nến 4h đóng gần nhất đóng TRÊN dải trên (đang phá cạnh trên) – chỉ short khi đã có nến 4h đóng lại trong dải (dấu hiệu quay đầu). '
                   f'Path B (phá vỡ xuống 4h đã xác nhận): nến 4h đóng gần nhất đóng DƯỚI dải dưới → được short tiếp diễn (nhánh 4h: vào theo giá thị trường khi giá còn ≤ dải dưới đó; G6/G7: khi giá chạm dải trên BB 1h = hồi lại), coin phải thuộc shortlist động LONG hoặc SHORT của nhóm (rule nhóm + ổn định 1D), không cần điểm dải trên; G7 bỏ %B4h≥0.5 cho Path B. '
                   +('Nhồi short cũng bị chặn khi nến 4h đóng trên dải trên. ' if G_.get('gate_adds') else 'Nhồi lệnh không đổi. ')+'Cắt lỗ/thang/thoát lệnh không đổi. Lệnh short bị chặn ghi ENTRY_BLOCKED_RULES kèm lý do. Vị thế đang mở giữ nguyên.',
            short_gate=dict(cfg.get('short_gate') or {}),open_positions={n:list(books[n].pos) for n in BRC if books[n].pos}))
    if not all(getattr(books[n],'sl_guard',False) for n in BRC):
        for n in BRC:books[n].sl_guard=True
        jl(EVF,dict(time=str(now().replace(microsecond=0)),event='CONFIG_CHANGE',coin='*',price=0,signal_id='CONFIG_CHANGE|shortlist_guard',branches=list(BRC),
            reason=f'Siết bảo đảm shortlist theo yêu cầu Vien (09/10 18:41 "shortlist rules must be guaranteed absolutely"): mọi lệnh vào VÀ lệnh nhồi, cả LONG lẫn SHORT, chỉ được dùng coin đang nằm trong shortlist động hiện tại của đúng chiều đó (điểm tôn trọng dải tương ứng ≥{BB_MIN}, ổn định 1D, rule nhóm); lệnh nhồi trên coin đã rời shortlist chiều đó bị chặn (ADD_BLOCKED_RULES), vị thế vẫn được quản lý/thoát bình thường; không bao giờ dùng danh sách coin cũ/cố định; shortlist cũ hơn {SL_MAX_AGE_H:.0f}h (quét lỗi) → không vào/nhồi mới đến khi quét lại thành công; thiếu dữ liệu ổn định 1D → không đạt (trước đây mặc định đạt).'))
    CYCLE_S=float(CFG.get('cycle_s',15)); RKCSV_T=[0.0]; print('CYCLE_S',CYCLE_S,flush=True)      # Vien 04/11 t689u: main cycle 60s -> 15s (configurable)
    while True:
        t0=time.time()
        if time.time()-reg_t>3600 or reg is None:
            d1=bb(candles('BTC','1d',120));w1=bb(candles('BTC','1w',800))
            reg=btc_regime(d1[d1.closed],w1[w1.closed]);reg['fng']=fng();reg_t=time.time()
        if True:      # Vien 04/11: ALL/BARS upkeep runs even with the scan OFF
            while SCAN is not None and not SCAN.out.empty():
                res=SCAN.out.get();GL=dynuni.group_lists(res,{g:G[g].get('rule',{}) for g in GR},int(DU.get('prefilter',{}).get('max_size',15)))
                GLS=dynuni.group_lists(res,{g:G[g].get('rule',{}) for g in GR},int(DU.get('prefilter',{}).get('max_size',15)),side='S') if SHORT_ON else {g:[] for g in G}
                GL={g:[r_ for r_ in v if r_['coin'] not in EXCL] for g,v in GL.items()};GLS={g:[r_ for r_ in v if r_['coin'] not in EXCL] for g,v in GLS.items()}      # excluded coins never enter the shortlist (scan rows/rank data are kept)
                try:      # [monitor patch] one log line per completed scan (read by monitor_live.py)
                    print('SCAN_DONE',res.get('time'),'secs=%s'%res.get('secs'),'trigger=%s'%res.get('trigger'),'counts='+json.dumps(res.get('counts')),'LONG='+','.join(f"{r_['coin']}:{r_.get('score',0):.2f}" for v_ in GL.values() for r_ in v_),'SHORT='+','.join(f"{r_['coin']}:{(r_.get('score_upper') or r_.get('score') or 0):.2f}" for v_ in GLS.values() for r_ in v_),flush=True)
                except Exception as _ex:print('SCAN_DONE log err',_ex,flush=True)
                MAXLEV.update({r_['coin']:int(r_['max_lev']) for r_ in res['rows'] if r_.get('max_lev')})
                for r in res['rows']:
                    if r.get('score') is None:continue
                    c=r['coin'];RANK[c]=dict(RANK.get(c,{}),**r['rank'],bar=r['bar'],stab_ok=r['stab_ok'],stab_bad=r['stab_bad'],stab_text=r['stab_text'],**{k:v for k,v in r['d1'].items()})
                    RANK[c]['short']=r.get('rank_s') or {}
                    RK_T[c]=pd.Timestamp(r['bar']);D1_T[c]=time.time()
                tE=str(now().replace(microsecond=0));evs=[]
                for g in GR:
                    news=[r['coin'] for r in GLS[g]];olds=DYN_S.get(g) or [];adds_=[c for c in news if c not in olds];rems_=[c for c in olds if c not in news]
                    new=[r['coin'] for r in GL[g]];old=DYN.get(g);add=[c for c in new if c not in (old or [])];rem=[c for c in (old or []) if c not in new]
                    periodic=not any(' đóng ' in x for x in res['trigger'].split('; '))
                    if add or rem or old is None or adds_ or rems_:      # Vien 10/10 02:00: log only when the LIST CONTENT changes (scans every 15 min); heartbeat in state.json
                        tops=[dict(coin=r['coin'],score_upper=r['score_s'],stable_1d=r['stab_ok'],r30=r['d1'].get('r30'),chg14=r['d1'].get('chg14'),bwr=r['d1'].get('bwr'),atr_pct=r['d1'].get('atr_pct'),dist_up4h_pct=r['dist_up_pct'],vol24h_m=round(r['vol24h']/1e6,1),oi_m=round(r['oi_usd']/1e6,1)) for r in GLS[g]]
                        top=[dict(coin=r['coin'],score=r['score'],stable_1d=r['stab_ok'],r30=r['d1'].get('r30'),chg14=r['d1'].get('chg14'),bwr=r['d1'].get('bwr'),atr_pct=r['d1'].get('atr_pct'),dist_lo4h_pct=r['dist_lo_pct'],vol24h_m=round(r['vol24h']/1e6,1),oi_m=round(r['oi_usd']/1e6,1)) for r in GL[g]]
                        evs.append(dict(time=tE,event='SHORTLIST_UPDATE',coin=','.join(new) or '-',price=0,signal_id=f'SHORTLIST_UPDATE|{g}|{tE}',group=g,branches=[n for n in BRC if BRC[n]['group']==g],
                            trigger=res['trigger'],rule=G[g].get('rule_label',''),shortlist=top,added=add,removed=rem,shortlist_short=tops,added_short=adds_,removed_short=rems_,counts=res['counts'],scan_secs=res['secs'],
                            reason=f"Cập nhật shortlist nhóm {g} trên toàn thị trường ({res['trigger']}): quét {res['counts']['total']} perp → thanh khoản {res['counts']['oi']} → đủ lịch sử {res['counts']['history']} → điểm BB ≥{BB_MIN} {res['counts']['bb']} → ổn định 1D {res['counts']['stable']} → theo rule nhóm {len(new)}. Danh sách: "+(', '.join(f"{r['coin']} {r['score']:.2f}" for r in GL[g]) or 'trống')+(f"; thêm {','.join(add)}" if add else '')+(f"; bỏ {','.join(rem)}" if rem else '')
                            +(f". SHORT (điểm tôn trọng dải TRÊN ≥{BB_MIN} + ổn định 1D + rule nhóm): "+(', '.join(f"{r['coin']} {r['score_s']:.2f}" for r in GLS[g]) or 'trống')+(f"; thêm {','.join(adds_)}" if adds_ else '')+(f"; bỏ {','.join(rems_)}" if rems_ else '') if SHORT_ON else '')))
                    DYN[g]=new;DYN_S[g]=news
                CLOSE_G.clear();SL_META.update(time=res['time'],trigger=res['trigger'],counts=res['counts'],secs=res['secs'],api=res.get('api'),changed=[e['group'] for e in evs])
                for e in evs: jl(EVF,e)
                try:
                    json.dump(dict(meta=SL_META,lists=DYN,lists_short=DYN_S,thresholds=res['thresholds']),open(SLF+'.tmp','w'),ensure_ascii=False,indent=1,default=str);os.replace(SLF+'.tmp',SLF)
                    rows=[]
                    for r in sorted(res['rows'],key=lambda r:-(r.get('score') or 0)):
                        x=dict(updated=res['time'],coin=r['coin'],vol24h_m=round(r['vol24h']/1e6,1),oi_m=round(r['oi_usd']/1e6,1),oi_rank=r.get('oi_rank'),max_lev=r['max_lev'],n4h=r.get('n4h'),ndaily=r.get('ndaily'),bb_score=r.get('score'),bb_score_upper=r.get('score_s'),dist_up4h_pct=r.get('dist_up_pct'),
                               raw_rev=(r.get('rank') or {}).get('raw_rev'),raw_fake=(r.get('rank') or {}).get('raw_fake'),swings30=(r.get('rank') or {}).get('swings30'),stable_1d=r.get('stab_ok'),why_unstable='; '.join(r.get('stab_bad') or []),dist_lo4h_pct=r.get('dist_lo_pct'),err=r.get('err'))
                        x.update({k:v for k,v in (r.get('d1') or {}).items() if k!='day'})
                        for g in GR:
                            ok,bad=dynuni.rule_check(r,G[g].get('rule',{}));x[f'in_{g}']=r['coin'] in DYN[g];x[f'rule_{g}']='ok' if ok else '; '.join(bad)
                            x[f'in_short_{g}']=r['coin'] in DYN_S.get(g,[])
                        rows.append(x)
                    pd.DataFrame(rows).to_csv(B+'data/shortlist_latest.csv',index=False)
                except Exception as ex:print('shortlist save',ex,flush=True)
            ALL[:]=sorted(set(c for g in G for c in guni(g)+guni_s(g))|set(c for n,b in books.items() if n in BRC for c in b.pos))
            for c in ALL:
                if c not in BARS:
                    try:BARS[c]=bb(candles(c,'4h',15));time.sleep(0.2)
                    except Exception as ex:print('bars',c,ex,flush=True)
            ALL[:]=[c for c in ALL if c in BARS]
        if time.time()-bars_t>300:
            for c in ALL:
                BARS[c]=bb(candles(c,'4h',15));time.sleep(0.2)
            bars_t=time.time()
        m=mids();tnow=now().replace(microsecond=0);cb={}
        btc_update(m.get('BTC'),refetch=True)
        if LIVE.kill_requested():do_kill('KILL file/env',tnow)
        live_sync(tnow,orders=True)
        if SG is not None and LIVE.sync_ok:
            try:SG.update(LB.base(),tnow)
            except Exception as ex_:print('safe update',repr(ex_)[:200],flush=True)
        if LIVE.sync_ok:S3.tick(LB,tnow,m,close_ba)
        for c in ALL:
            cl=BARS[c][BARS[c].closed]
            newbar=cl.index[-1]!=lastclosed.get(c);cb[c]=cl.iloc[-1] if (newbar and c in lastclosed) else None;lastclosed[c]=cl.index[-1]
        for c in ALL: rank_update(c)
        for c in sorted(set(c for b in books.values() for c in b.pos)): get1m(c);time.sleep(0.1)
        st=dict(updated=str(tnow),mode='DRY-RUN' if DRY else 'LIVE',btc=reg,btc_recheck={k:BTCX.get(k) for k in('block','why','since','block_s','why_s','since_s','px','info','at')},branches={},watch={},filtered={})
        sc=lambda c:(RANK.get(c) or {}).get('score',0.0) or 0.0
        def sc_side(c,sd_):
            R=RANK.get(c) or {};return ((R.get('short') or {}).get('score') if sd_=='S' else R.get('score')) or 0.0
        def rk_side(c,sd_,r=None,n=None,U=None,g=None):
            """Rule inputs for one side: BB-respect score of the touched band (lower for LONG, upper for SHORT) >= BB_MIN + 1D stability."""
            R=RANK.get(c) or {};RS=(R.get('short') or {}) if sd_=='S' else R;scv=sc_side(c,sd_);ok_bb=scv>=BB_MIN;ok_st=(R.get('stab_ok') is True) or SKIP_ST
            why=('; '.join(([f'điểm tôn trọng dải {"trên" if sd_=="S" else "dưới"} BB 4h {scv:.2f} < {BB_MIN}'] if not ok_bb else [])+((['không ổn định khung ngày: '+'; '.join(R.get('stab_bad',[]))] if R.get('stab_ok') is False else ['chưa có dữ liệu ổn định khung ngày']) if not ok_st else [])))
            sg={}
            if SG_ON and sd_=='S':      # SHORT gate inputs: Path A range stats (upper band, PIT 90d) + Path B eligibility (group LONG/SHORT list + 1D stability)
                inu=bool(g) and sl_fresh() and (c in guni(g) or c in guni_s(g))
                sg=dict(sA=dict({k:RS.get(k) for k in('score','n_touch','n_break','raw_fake','raw_deep')},r30=R.get('r30')),pathB_ok=bool(ok_st and inu),
                        pathB_why='; '.join(([] if inu else [f'Path B: {c} không thuộc shortlist động LONG/SHORT hiện tại của nhóm {g}'])+([] if ok_st else ['Path B: không ổn định khung ngày'])))
            if r is None:return dict(ok=ok_bb and ok_st,score=round(scv,3),why=why,trend=TREND.get(c),**sg)
            txt=((bbrank.reason_vi_short(RS,r,n) if sd_=='S' else bbrank.reason_vi(R,r,n)) if 'n_touch' in RS else f'Xếp hạng {r}/{n}, chưa có điểm BB')+f' (nhóm {g}); '+R.get('stab_text','chưa có dữ liệu nến ngày')
            return dict(ok=ok_bb and ok_st,score=round(scv,3),rank=r,n_cand=n,text=txt,why=why,trend=TREND.get(c),**sg)
        def rk_add(c,sd_,g,pth=None,lv=None):
            r=rk_side(c,sd_,g=g)
            if not ok_lev(c,lv):return dict(r,ok=False,why=f'{c} đòn bẩy tối đa {MAXLEV.get(c,"?")}x < đòn bẩy nhánh x{lv:g}')
            lst=(guni_s(g) if sd_=='S' else guni(g)) if sl_fresh() else []
            if pth=='B' and SG_ON:       # Path B short: coin must still be in the group's LONG or SHORT list + 1D stable (upper-band score not required)
                if r.get('pathB_ok'):return dict(r,ok=True,why='')
                return dict(r,ok=False,why=r.get('pathB_why') or 'Path B: không còn đạt')
            if c in lst:return r
            if c in EXCL and sl_fresh() and pth!='B':return r      # excluded coin with an OPEN position: adds still re-check BB score >=0.75 + 1D stability (+ BTC/MA20/30% cap in strat.rules) but no shortlist membership needed
            msg=f'{c} không nằm trong shortlist {"SHORT" if sd_=="S" else "LONG"} hiện tại của nhóm {g}'+('' if sl_fresh() else f' (shortlist cũ hơn {SL_MAX_AGE_H:.0f}h, chờ quét lại)')
            return dict(r,ok=False,why='; '.join(x for x in (r.get('why'),msg) if x))
        for n in B15:     # 15m candles only for coins with an open position in a 15m branch
            for c in list(books[n].pos): get15(c);time.sleep(0.1)
            books[n].m15=M15
        G6U=sorted(set(c for n_,b_ in books.items() if n_ in BRC and isinstance(b_,strat.BookT1H) for c in guni(BRC[n_]['group'])+guni_s(BRC[n_]['group'])+list(b_.pos)))
        for c in G6U:      # G6_TREND1H: closed 1h BB(20,2) bars (refetch when a newer 1h bar may have closed, or every 5 min) + 4h trend
            try:
                x=H1C.get(c)
                if x is None or time.time()-H1T.get(c,0)>300 or pd.Timestamp(tnow)>=x.index[-1]+pd.Timedelta(hours=2):
                    y=bb(candles(c,'1h',4));H1C[c]=y[y.closed];H1T[c]=time.time();time.sleep(0.15)
                if c in BARS:TREND[c]=strat.trend4h(BARS[c][BARS[c].closed])
            except Exception as ex:print('1h',c,ex,flush=True)
        for name,book in books.items():
            if name not in BRC:continue
            T1=isinstance(book,strat.BookT1H)
            if T1:book.h1=H1C
            gname=BRC[name]['group'];FR=sl_fresh();UL=[c for c in guni(gname) if ok_lev(c,book.lev) and c not in EXCL] if FR else [];US=[c for c in guni_s(gname) if ok_lev(c,book.lev) and c not in EXCL] if FR else []
            U=list(dict.fromkeys(UL+US+list(book.pos)));cand=[]
            book.reg=reg;book.rk_fn=lambda c,sd_,pth=None,_g=gname,_lv=book.lev:rk_add(c,sd_,_g,pth,_lv)        # adds re-check the full rule set (Vien 17:54) + current shortlist of that side (18:41)
            for c in U:
                if c not in BARS:continue
                cl=BARS[c][BARS[c].closed];px=m.get(c)
                if px is None or len(cl)<31:continue
                if c in book.pos:
                    p=book.pos[c];m1=M1.get(c);t0m=str(pd.Timestamp(tnow).floor('min'))
                    if 'm1' not in p: p['m1']=t0m      # positions found at (re)start: no retroactive replay
                    done=False;first=True
                    if m1 is not None and len(m1):
                        for ts,r in m1[m1.index>pd.Timestamp(p['m1'])].iterrows():   # minutes after the last fully processed one
                            book.walk(c,ts if r.closed else tnow,path1m(r),cl,cb[c] if first else None);first=False
                            if c not in book.pos:done=True;break
                            if r.closed:book.pos[c]['m1']=str(ts)
                    if not done and c in book.pos: book.walk(c,tnow,[px],cl,cb[c] if first else None)
                bnd=(H1C[c].iloc[-1] if (c in H1C and len(H1C[c])) else None) if T1 else cl.iloc[-1]     # trigger band: 1h for G6_TREND1H, else 4h
                if bnd is None:continue
                _m1=(M1.get(c) if c in book.pos else (get1m(c) if (c in UL or c in US) else None));_hi=px;_lo=px
                try:
                    if _m1 is not None and len(_m1):_hi=max(px,float(_m1['high'].iloc[-2:].max()));_lo=min(px,float(_m1['low'].iloc[-2:].min()))      # Vien 03/11 t659u: step-1 touch uses current 1m high/low
                except Exception:pass
                if c in book.pos:   # opposite-band touch on a coin we already hold the other way -> logged as blocked (no long+short on one coin)
                    sd0=book.pos[c].get('side','L')
                    if sd0=='L' and c in US and _hi>=bnd.up:cand.append((c,'S'))
                    if sd0=='S' and c in UL and _lo<=bnd.lo:cand.append((c,'L'))
                    continue
                if c in UL and _lo<=bnd.lo: cand.append((c,'L'))
                b4_=cl.iloc[-1];brkB=False   # Path B DISABLED (Vien 22/10)     # SHORT Path B: last closed 4h closed below its lower band
                if (c in US or (brkB and T1)) and _hi>=bnd.up: cand.append((c,'S'))
                elif brkB and not T1 and px<=float(b4_.lo): cand.append((c,'S'))
            # entries: band touch = trigger only; ranked by the touched band's BB-respect score; full rule set checked in try_entry
            if PCT_TH and not book.pos and isinstance(book,strat.Book15):      # Vien 03/11 (t641u): BE/LOCK/TRAIL/15m-exit $ = % of current equity (ref 1000$); recomputed only when flat -> fixed for the life of a position
                try:
                    eq_p=float(book.equity(m));P_=PCT_TH['pct']
                    if eq_p>0:
                        newv=dict(eps=round(P_['eps_15m']/100*eq_p,6),be_after=round(P_['be_after_15m']/100*eq_p,6),small=round(P_['small_15m']/100*eq_p,6),lock_step=round(P_['lock_step']/100*eq_p,6),tiers=[(round(a_/100*eq_p,6),float(f_)) for a_,f_ in P_['trail_tiers']])
                        oldv=dict(eps=getattr(book,'eps',None),be_after=getattr(book,'be_after',None),small=getattr(book,'small',None),lock_step=getattr(book,'lock_step',None),tiers=list((getattr(book,'trail_cfg',None) or {}).get('tiers') or []))
                        if any(abs((oldv[k_] or 0)-newv[k_])>1e-4 for k_ in('eps','be_after','small','lock_step')) or [t_[0] for t_ in oldv['tiers']]!=[t_[0] for t_ in newv['tiers']]:
                            book.eps=newv['eps'];book.be_after=newv['be_after'];book.small=newv['small']
                            if hasattr(book,'lock_step'):book.lock_step=newv['lock_step']
                            if getattr(book,'trail_cfg',None):book.trail_cfg['tiers']=newv['tiers']
                            print('PCT_TH',str(tnow),'equity=%.4f'%eq_p,' '.join(f"{k_}={newv[k_]:.4f}$({P_[k2_]:g}%)" for k_,k2_ in(('be_after','be_after_15m'),('lock_step','lock_step'),('eps','eps_15m'),('small','small_15m'))),'trail='+'/'.join(f"{t_[0]:.4f}$({p_:g}%)x{t_[1]:g}" for t_,(p_,_) in zip(newv['tiers'],P_['trail_tiers'])),flush=True)
                except Exception as ex_:print('PCT_TH err',ex_,flush=True)
            seed_bo={}
            for (c_s,sd_s,b2_,z_) in LIVE.seed_candidates(book,m,UL,BARS,tnow):
                if (c_s,sd_s) not in cand and c_s not in book.pos:cand.append((c_s,sd_s));seed_bo[(c_s,sd_s)]=b2_
            cand.sort(key=lambda x:-sc_side(*x))
            for r,(c,sd_) in enumerate(cand,1):
                rk=rk_side(c,sd_,r,len(cand),U,gname)
                if not rk['ok']:
                    st['filtered'].setdefault(f'{c}|{sd_}',dict(time=str(tnow),side='SHORT' if sd_=='S' else 'LONG',why=rk['why'],branches=[]))['branches'].append(name)
                cl=BARS[c][BARS[c].closed]
                if isinstance(book,strat.BookMTF):get15(c);book.m15=M15      # G7_MTF: 15m confirmation needs the coin's closed 15m candles
                book.try_entry(c,tnow,m[c],cl,reg,rk=rk,side=sd_,**({'band':H1C[c].iloc[-1],'blbl':'1h'} if T1 else ({'band':seed_bo[(c,sd_)]} if (c,sd_) in seed_bo else {})))
                if c in book.pos: book.pos[c].setdefault('m1',str(pd.Timestamp(tnow).floor('min')))
            for c_ in list(book.pos):LIVE.stop_sync(book,c_,tnow)
            eq=book.equity(m);book.peak=max(book.peak,eq);dd=((eq/book.peak-1)*100 if book.peak>0 else 0.0)      # BNB build: avoid crash-loop at 0 USDT equity (display/alert only)
            if dd<=-CFG['dd_alert_pct'] and not book.alerted.get('DD'):
                book.alerted['DD']=1;buf.append(dict(time=str(tnow),event='ALERT_DD',branch=name,coin='*',price=0,reason=f'Tài khoản paper giảm {dd:.1f}% từ đỉnh'))
            ur=sum(book.unreal(c,m.get(c,p['last'])) for c,p in book.pos.items())
            day=str(tnow)[:10];dr=book.day_realized(day);hit=dr>=DT
            if hit and day not in book.daily_hit:   # first cross of the day -> one event; trading continues (no ceiling)
                book.daily_hit[day]=str(tnow)
                buf.append(dict(time=str(tnow),event='DAILY_TARGET_HIT',branch=name,coin='*',price=0,group=BRC[name]['group'],stop=f'SL{book.sl}',day=day,daily_realized=round(dr,4),target=DT,
                    reason=f'Nhánh {name} đạt mục tiêu ngày: đã chốt {dr:.2f}$ ≥ {DT:.0f}$ (ngày {day} giờ VN) – bot vẫn tiếp tục giao dịch, không giới hạn trần'))
            st['branches'][name]=dict(group=BRC[name]['group'],sl=book.sl,ladder=book.L,sizing=dict(compound=bool(getattr(book,'compound',False)),base=round(book.base(),4),scale=round(book.scale(),6),legs=[book.leg(k) for k in range(len(book.L))]),exit_rule=BRC[name].get('exit_label','B: 50% ở dải giữa BB 4h, phần còn lại trail hòa vốn→dải giữa, chốt ở dải trên'),separate_account=bool(BRC[name].get('separate')),lev=book.LV(),daily_target=DT,day=day,today_realized=round(dr,4),today_trades=sum(1 for tr in book.trades if str(tr['t1'])[:10]==day),daily_target_hit=bool(hit),daily_target_hit_at=book.daily_hit.get(day),equity=round(eq,4),realized=round(book.cash_pnl,4),unrealized=round(ur,4),peak=round(book.peak,4),dd_pct=round(dd,4),n_trades=len(book.trades),n_wins=sum(1 for tr in book.trades if tr['net']>0),n_stops=book.n_stops,
                positions={c:dict(side=strat.SIDE(p),since=str(p['t0']),adds=p['k']-1,avg=round(p['cost']/p['q'],6),notional=round(p['cost'],4),margin=round(strat.pmargin(p),4),price=m.get(c),unreal=round(book.unreal(c,m.get(c,p['last'])),2),target=p['target'],adds_paused=p['paused'],partial_done=bool(p.get('partial')),partial=p.get('partial'),trail=round(p['trail'],6) if p.get('trail') else None,held=p.get('held',False),armed_15m=p.get('armed'),outlook_15m=p.get('outlook'),mid4h_hit=p.get('mid4h_hit'),be_at=p.get('be_at'),stop_px=round(book.stop_px(p),6) if book.stop_px(p) else None,lock30=p.get('lock30'),lock30_floor=p.get('lock30_floor'),peak=round(p['peak'],4) if p.get('peak') is not None else None,peak_at=p.get('peak_at'),trail_kind=p.get('trail_kind'),lock=p.get('lock'),u_net=round(book.u_net(p,m.get(c,p['last'])),2),stop_usd=-book.sl if book.sl else None) for c,p in book.pos.items()})
        flush()
        st['watch']={c:dict(price=m.get(c),lo=round(float(BARS[c][BARS[c].closed].lo.iloc[-1]),6),up=round(float(BARS[c][BARS[c].closed].up.iloc[-1]),6),mid=round(float(BARS[c][BARS[c].closed].mid.iloc[-1]),6)) for c in ALL}
        if SCAN is not None:
            st['scanner']=dict(last_scan=SL_META.get('time'),trigger=SL_META.get('trigger'),secs=SL_META.get('secs'),api_calls=SL_META.get('api'),changed_groups=SL_META.get('changed'),
                               busy=SCAN.busy,running_since=SCAN.started_at if SCAN.busy else None,next_due=SCAN.next_due(),n_scans=SCAN.n_scans,skipped_slots=SCAN.skipped,err=SCAN.err,
                               every_min=SCAN.C['periodic_min'],offset_s=SCAN.C['offset_s'],max_age_h=SL_MAX_AGE_H,fresh=sl_fresh(),alive=SCAN.is_alive())
        st['epoch']=dict(time=EP['time'],rule=EP.get('rule')) if EP else None
        st['trend1h']={c:dict(price=m.get(c),trend=(TREND.get(c) or {}).get('trend'),why=(TREND.get(c) or {}).get('why'),lo1h=round(float(H1C[c].lo.iloc[-1]),6),up1h=round(float(H1C[c].up.iloc[-1]),6),bar1h=str(H1C[c].index[-1]),
            dist_lo1h_pct=round((m[c]/H1C[c].lo.iloc[-1]-1)*100,4) if m.get(c) else None,dist_up1h_pct=round((H1C[c].up.iloc[-1]/m[c]-1)*100,4) if m.get(c) else None,
            in_long=c in guni('G6_TREND1H'),in_short=c in guni_s('G6_TREND1H')) for c in G6U if c in H1C and len(H1C[c])} if 'G6_TREND1H' in G else {}
        for n_ in BRC:
            if n_ in st['branches'] and getattr(books[n_],'started',None):st['branches'][n_]['started']=books[n_].started
        for name in BRC:
            if name in st['branches'] and getattr(books[name],'epochs',None):st['branches'][name]['pre_epoch']=books[name].epochs[-1]['prior']
        st['retired']={n:dict(retired_at=getattr(b,'retired',None),final_equity=round(CFG['capital']+b.cash_pnl,4),realized=round(b.cash_pnl,4),n_trades=len(b.trades),n_wins=sum(1 for tr in b.trades if tr['net']>0),n_stops=b.n_stops,
            today_realized=round(b.day_realized(str(tnow)[:10]),2)) for n,b in books.items() if getattr(b,'retired',None)}
        st['ranking']={'bb_min':BB_MIN,'stab_rule':dict(bbrank.ST),'coins':{c:{k:v for k,v in (RANK.get(c) or {}).items()} for c in ALL},
            'universes':{g:[c for c in sorted(guni(g),key=lambda c:-sc(c))] for g in G}}
        st['live']=LIVE.status(LB)
        st['step3']=S3.status()
        if SG is not None:st['safe']=SG.status()
        if SCAN is not None: st['shortlist']=dict(SL_META,busy=SCAN.busy,err=SCAN.err,lists=DYN,lists_short=DYN_S,short_on=SHORT_ON,rules={g:G[g].get('rule_label','') for g in G})
        if time.time()-RKCSV_T[0]>=300 or not os.path.exists(B+'data/bb_rank_latest.csv'):
            RKCSV_T[0]=time.time()
            try:
                rows=[]
                for g in G:
                    for i,c in enumerate(st['ranking']['universes'][g],1):
                        R=RANK.get(c) or {};rows.append(dict(updated=str(tnow),universe=g,rank=i,coin=c,bb_score=R.get('score'),pass_bb=(R.get('score') or 0)>=BB_MIN,stable_1d=R.get('stab_ok'),eligible=((R.get('score') or 0)>=BB_MIN) and bool(R.get('stab_ok',True)),
                            p_mid_2d=R.get('raw_rev'),p_fake=R.get('raw_fake'),p_deep6=R.get('raw_deep'),mae_med_pct=R.get('mae_med'),t_mid_med_bars=R.get('t_mid_med'),swings30=R.get('swings30'),touches_90d=R.get('n_touch'),
                            r30_1d=R.get('r30'),r60_1d=R.get('r60'),chg7_1d=R.get('chg7'),chg14_1d=R.get('chg14'),chg30_1d=R.get('chg30'),bw_ratio_1d=R.get('bwr'),pb_1d=R.get('pb'),why_unstable='; '.join(R.get('stab_bad',[]))))
                pd.DataFrame(rows).to_csv(B+'data/bb_rank_latest.csv',index=False)
            except Exception as ex: st['rank_csv_err']=str(ex)
        json.dump(st,open(STF+'.tmp','w'),ensure_ascii=False,indent=1,default=str);os.replace(STF+'.tmp',STF)
        save_books()
        try:      # [monitor patch] one heartbeat/decision line per 60s cycle
            _lv=st.get('live') or {};print('LOOP',str(tnow),'sync_ok=%s'%_lv.get('sync_ok'),'halted=%s'%_lv.get('halted'),'equity=%s'%_lv.get('equity'),'pos=%s'%sorted(_lv.get('exchange_positions') or {}),'stops=%s'%sorted(_lv.get('stops') or {}),'btc_allow=%s/%s'%((st.get('btc') or {}).get('allow'),(st.get('btc') or {}).get('allow_s')),'btc_block=%s/%s'%((st.get('btc_recheck') or {}).get('block'),(st.get('btc_recheck') or {}).get('block_s')),'step3=%s'%(('POS_'+S3.pos['side']) if S3.pos else ('WAIT_'+'/'.join(p_['side'] for p_ in S3.D.pend) if S3.D.pend else 'idle')),'watch='+','.join(f"{k_}:{v_['price']}(lo{(v_['price']/v_['lo']-1)*100:+.2f}%/up{(v_['up']/v_['price']-1)*100:.2f}%)" for k_,v_ in (st.get('watch') or {}).items() if v_.get('lo') and v_.get('up')),flush=True)
        except Exception as _ex:print('LOOP log err',_ex,flush=True)
        # fast loop until the next CYCLE_S cycle: every ~5s, allMids -> resting stops / trails / adds / TPs for open positions only
        while time.time()-t0<CYCLE_S-0.5:      # sleep in FAST steps (last step trimmed) so a full cycle = CYCLE_S
            time.sleep(max(0.2,min(FAST,CYCLE_S-(time.time()-t0))))
            if LIVE.kill_requested():do_kill('KILL file/env',now().replace(microsecond=0))
            if not any(b.pos for b in books.values()):continue
            try:
                mm=mids();tf=now().replace(microsecond=0);btc_update(mm.get('BTC'));live_sync(tf)
                for name,book in books.items():
                    if name not in BRC:continue
                    for c in list(book.pos):
                        if c in mm and c in BARS:
                            book.walk(c,tf,[mm[c]],BARS[c][BARS[c].closed]);LIVE.stop_sync(book,c,tf)
                if buf:
                    flush()
                    for b in books.values(): b.log=None
                    pickle.dump(books,open(sp+'.tmp','wb'));os.replace(sp+'.tmp',sp)
                    for n,b in books.items(): b.log=mklog(n)
            except Exception as ex:
                print(str(now())[:19],'fast-loop error',repr(ex)[:200],flush=True)
if __name__=='__main__':main()
