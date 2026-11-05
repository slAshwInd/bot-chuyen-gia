"""BaseAster SAFE – bản an toàn cho vốn lớn (mặc định 8000$). Mọi rule vào/ra lệnh GIỐNG bot live; chỉ khác 2 lớp:

1) KHỐI LƯỢNG THEO RỦI RO (overlay lên config_live.json do make_live_config.py tạo):
   Bot live: ký quỹ 98% vốn x5 -> notional ~4.9x vốn, SL = 25% vốn (khoảng cách giá ~5%).
   SAFE: mọi tham số $ (thang ký quỹ, SL, hòa vốn, khóa lãi, trail, ngưỡng chốt 15m, cảnh báo, bước 3) nhân hệ số
   k = risk_pct / 25. Vì size và ngưỡng $ cùng nhân k, MỌI MỨC GIÁ (giá SL, giá hòa vốn, giá khóa lãi, trail) giữ nguyên
   như bot live; chỉ số tiền mất mỗi lần SL giảm từ 25% vốn xuống risk_pct% vốn.
   Đồng thời sửa công thức pct_thresholds (bản live chỉ đúng khi vốn ~1000$; ở 8000$ ngưỡng hòa vốn/khóa lãi bị phóng 8 lần).

2) CẦU DAO (Guard) – chặn LỆNH VÀO MỚI (BaseAster + Bước 3), không đụng vị thế đang mở (SL/BE/LOCK/trail vẫn chạy):
   - nghỉ cùng chiều N giờ sau mỗi lần SL (bot live: vào lại ngay)
   - thua SL liên tiếp >= max_consec_sl -> nghỉ cả 2 chiều pause_after_streak_h giờ
   - lỗ trong ngày (giờ VN) >= daily_loss_pct% vốn đầu ngày -> nghỉ tới hết ngày
   - sụt từ đỉnh >= max_dd_pct% -> tạo SAFE_HALT.flag, dừng vào lệnh tới khi người xóa file (python safe.py reset)

Config: khóa "safe" trong ref/config_paper.json hoặc file safe_profile.json cạnh bot (ưu tiên safe_profile.json)."""
import os,json,copy,time,datetime as dt

DEF=dict(enabled=True,risk_pct=3.0,cooldown_after_sl_h=12.0,max_consec_sl=2,pause_after_streak_h=48.0,
         daily_loss_pct=6.0,max_dd_pct=15.0,halt_file='SAFE_HALT.flag',state_file='state/safe_guard.json')
B=os.path.dirname(os.path.abspath(__file__))+os.sep

def load_profile(base_dir=B,paper=None):
    """safe_profile.json (nếu có) > paper['safe'] > None. Trả về dict đã gộp DEF, hoặc None nếu tắt."""
    p=None
    f=os.path.join(base_dir,'safe_profile.json')
    if os.path.exists(f):p=json.load(open(f,encoding='utf-8'))
    elif paper and paper.get('safe'):p=paper['safe']
    if not p or not p.get('enabled',True):return None
    return dict(DEF,**{k:v for k,v in p.items() if not str(k).startswith('_')})

def live_sl_pct(cfg):
    """% vốn mất khi dính SL ở bot live = SL$ (tại vốn chuẩn 5000) / 5000."""
    base=float((cfg.get('scaling') or {}).get('base_usd',5000.0))
    return float(cfg['stops']['SL250'])/base*100

def overlay(cfg,prof):
    """cfg = output của make_live_config (bản live). Trả về cfg SAFE: mọi tham số $ x k, giữ nguyên mọi mức giá."""
    o=copy.deepcopy(cfg);r0=live_sl_pct(cfg);k=float(prof['risk_pct'])/r0
    if not 0<k<=1:raise ValueError(f'risk_pct {prof["risk_pct"]} phải trong (0, {r0:g}] (SL live = {r0:g}% vốn)')
    F=float(cfg['scaling']['factor']);BASE=float(cfg['scaling'].get('base_usd',5000.0))
    G=o['groups']['BASE']
    G['ladder']=[round(x*k,6) for x in G['ladder']]
    for kk in('eps_15m','be_after_15m','small_15m'):G[kk]=round(G[kk]*k,8)
    o['stops']={n:round(v*k,6) for n,v in cfg['stops'].items()}
    o['daily_target_usd']=round(cfg['daily_target_usd']*k,8)
    o['trail_15m']['tiers']=[[round(a*k,8),f] for a,f in cfg['trail_15m']['tiers']]
    o['lock_milestones']['step']=round(cfg['lock_milestones']['step']*k,8)
    o['alerts_scaled']={n:round(v*k,8) for n,v in cfg['alerts_scaled'].items()}
    # pct_thresholds: % của vốn hiện tại. Live: pct = (paper$ x F)/10 -> chỉ đúng khi CAP=1000. Đúng: paper$/BASE*100, rồi x k.
    pc=lambda v:round(v/F/BASE*100*k,6)
    if o.get('pct_thresholds'):
        o['pct_thresholds']['pct']=dict(be_after_15m=pc(cfg['groups']['BASE']['be_after_15m']),eps_15m=pc(cfg['groups']['BASE']['eps_15m']),
            small_15m=pc(cfg['groups']['BASE']['small_15m']),lock_step=pc(cfg['lock_milestones']['step']),
            trail_tiers=[[pc(a),f] for a,f in cfg['trail_15m']['tiers']])
        o['pct_thresholds']['note']='SAFE: pct = paper$/5000*100 x k (không phụ thuộc vốn); BE/LOCK/TRAIL/15m-exit $ = pct% x vốn hiện tại.'
    import step3 as _s3      # Bước 3: rủi ro 25% vốn / trần 5x vốn của live -> x k (khóa step3 có thể không có trong config: bot dùng step3.DEF)
    s3=dict(o.get('step3') or {});d=dict(_s3.DEF,**s3);s3['risk_pct']=round(float(d['risk_pct'])*k,6);s3['max_notional_x']=round(float(d['max_notional_x'])*k,6);o['step3']=s3
    lev=float(cfg['live']['leverage']);m=sum(G['ladder'])/BASE
    o['safe']=dict(prof,k=round(k,6),live_sl_pct=round(r0,4),notional_x=round(m*lev,4),margin_pct=round(m*100,4),
        stop_dist_pct=round(float(cfg['stops']['SL250'])/(sum(cfg['groups']['BASE']['ladder'])*lev)*100,4),
        note=f'SAFE: SL = {prof["risk_pct"]:g}% vốn (live {r0:g}%), notional {m*lev:.2f}x vốn (live {sum(cfg["groups"]["BASE"]["ladder"])/BASE*lev:.2f}x), mọi mức giá giữ nguyên.')
    o['branch_display_name']=str(cfg.get('branch_display_name','BaseAster'))+' SAFE'
    return o

def summary(cfg,equity):
    """Bảng số liệu cho người đọc: tiền mất/lãi tại một mức vốn."""
    S=cfg['safe'];lev=float(cfg['live']['leverage']);BASE=float(cfg['scaling'].get('base_usd',5000.0))
    margin=sum(cfg['groups']['BASE']['ladder'])/BASE*equity;notional=margin*lev;sl=float(cfg['stops']['SL250'])/BASE*equity
    return dict(equity=equity,margin=round(margin,2),notional=round(notional,2),notional_x=round(notional/equity,3),sl_usd=round(sl,2),sl_pct=round(sl/equity*100,3),
        stop_dist_pct=S['stop_dist_pct'],be_usd=round(cfg['pct_thresholds']['pct']['be_after_15m']/100*equity,2),
        consec_sl_to_minus_30pct=_n_to(0.70,sl/equity),liq_dist_pct=round(100/lev,1))

def _n_to(level,r):
    n,e=0,1.0
    while e>level and n<1000:e*=1-r;n+=1
    return n

def vn_now():return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)+dt.timedelta(hours=7)

class Guard:
    """Cầu dao vào lệnh. check() trả về lý do chặn (str) hoặc None. Chỉ chặn LỆNH VÀO MỚI."""
    def __init__(s,prof,base_dir=B,notify=None,emit=None,clock=time.time):
        s.P=dict(DEF,**prof);s.B=base_dir;s.notify=notify;s.emit=emit;s.clock=clock
        s.sf=os.path.join(base_dir,s.P['state_file']);s.hf=os.path.join(base_dir,s.P['halt_file'])
        try:s.st=json.load(open(s.sf,encoding='utf-8'))
        except Exception:s.st={}
        s.st.setdefault('peak',None);s.st.setdefault('day',None);s.st.setdefault('day_start',None);s.st.setdefault('said',{})
        s.extra_trades=lambda:[]          # callable -> [(t1 str VN, side 'L'|'S', is_stop bool)] (Bước 3)
    def save(s):
        os.makedirs(os.path.dirname(s.sf),exist_ok=True)
        json.dump(s.st,open(s.sf+'.tmp','w',encoding='utf-8'),ensure_ascii=False,indent=1);os.replace(s.sf+'.tmp',s.sf)
    def _say(s,key,msg,every_s=3600):
        last=s.st['said'].get(key,0)
        if s.clock()-last<every_s:return
        s.st['said'][key]=s.clock()
        if s.emit:
            try:s.emit(dict(time=str(vn_now().replace(microsecond=0)),event='SAFE_GUARD',coin='*',price=0,reason=msg))
            except Exception:pass
        if s.notify:
            try:s.notify('🛡 '+msg)
            except Exception:pass
    def halted(s):return os.path.exists(s.hf)
    def update(s,equity,tnow):
        """Gọi mỗi vòng với vốn đã chốt (không tính lãi/lỗ đang mở). Cập nhật đỉnh, vốn đầu ngày, kiểm tra sụt giảm."""
        if not equity or equity<=0:return
        day=str(tnow)[:10]
        if s.st['day']!=day:s.st['day']=day;s.st['day_start']=float(equity)
        if s.st['peak'] is None or equity>s.st['peak']:s.st['peak']=float(equity)
        dd=(1-equity/s.st['peak'])*100
        if dd>=float(s.P['max_dd_pct']) and not s.halted():
            why=f'SAFE HALT: vốn {equity:.2f}$ sụt {dd:.1f}% từ đỉnh {s.st["peak"]:.2f}$ (>= {s.P["max_dd_pct"]:g}%). Dừng mọi lệnh vào mới; vị thế đang mở vẫn giữ SL/chốt lời. Xem lại rồi chạy: python safe.py reset'
            os.makedirs(os.path.dirname(s.hf) or '.',exist_ok=True)
            open(s.hf,'w',encoding='utf-8').write(f'{tnow} {why}\n')
            s._say('halt',why,every_s=0)
        s.save()
    def trades(s,book):
        T=[(str(tr['t1']),'S' if tr.get('side')=='SHORT' else 'L',bool(tr.get('stop'))) for tr in (getattr(book,'trades',None) or [])]
        try:T+=list(s.extra_trades() or [])
        except Exception:pass
        return sorted(T)
    def check(s,book,tnow,side,equity=None,src='BaseAster'):
        P=s.P;t=_ts(tnow)
        if s.halted():
            try:why=open(s.hf,encoding='utf-8').read().strip()[:200]
            except Exception:why=''
            return f'SAFE: đang HALT ({why or s.P["halt_file"]}) – xóa bằng python safe.py reset'
        eq=equity if equity is not None else (book.base() if book is not None else None)
        if eq and s.st.get('day_start') and s.st.get('day')==str(t)[:10]:
            dl=(eq/s.st['day_start']-1)*100
            if dl<=-float(P['daily_loss_pct']):
                m=f'SAFE: lỗ trong ngày {dl:.2f}% (<= -{P["daily_loss_pct"]:g}% vốn đầu ngày {s.st["day_start"]:.2f}$) – nghỉ vào lệnh tới hết ngày'
                s._say('day|'+str(t)[:10],m,every_s=10**9);return m
        T=s.trades(book)
        n=0
        for t1,_,stp in reversed(T):
            if not stp:break
            n+=1
        if n>=int(P['max_consec_sl']) and T:
            t_last=_ts(T[-1][0]);h=(t-t_last).total_seconds()/3600
            if h<float(P['pause_after_streak_h']):
                m=f'SAFE: {n} lần SL liên tiếp (lần cuối {T[-1][0][:16]}) – nghỉ cả 2 chiều {P["pause_after_streak_h"]:g}h (còn {P["pause_after_streak_h"]-h:.1f}h)'
                s._say('streak|'+T[-1][0],m,every_s=10**9);return m
        st_side=[x for x in T if x[2] and x[1]==side]
        if st_side:
            h=(t-_ts(st_side[-1][0])).total_seconds()/3600
            if h<float(P['cooldown_after_sl_h']):
                return f'SAFE: vừa SL {"LONG" if side=="L" else "SHORT"} lúc {st_side[-1][0][:16]} – nghỉ cùng chiều {P["cooldown_after_sl_h"]:g}h (còn {P["cooldown_after_sl_h"]-h:.1f}h)'
        return None
    def status(s):
        return dict(halted=s.halted(),peak=s.st.get('peak'),day=s.st.get('day'),day_start=s.st.get('day_start'),risk_pct=s.P['risk_pct'])

def _ts(x):
    import pandas as pd
    return pd.Timestamp(str(x)[:19])

def step3_trades(path):
    """Đọc data/step3_trades.csv -> [(t1, side, is_stop)]."""
    import csv
    out=[]
    try:
        for r in csv.DictReader(open(path,encoding='utf-8')):
            out.append((str(r.get('t1'))[:19],'S' if r.get('side')=='SHORT' else 'L',str(r.get('why','')).startswith('SL')))
    except FileNotFoundError:pass
    return out

def wrap_momf(orig,guard,get_book):
    """Nối cầu dao vào hook lọc vào lệnh của strat (strat.MOMF). Lọc gốc chạy trước (giữ nguyên ghi nhận chặn cho Bước 3)."""
    def f(c,t,px,L_):
        r=orig(c,t,px,L_) if orig else None
        g=guard.check(get_book(),t,'L' if L_ else 'S')
        if r and g:return r+'; '+g
        return r or g
    return f

if __name__=='__main__':
    import sys
    a=sys.argv[1:] or ['show']
    if a[0]=='reset':
        P=dict(DEF);hf=os.path.join(B,P['halt_file']);sf=os.path.join(B,P['state_file'])
        if os.path.exists(hf):os.remove(hf);print('đã xóa',P['halt_file'])
        if os.path.exists(sf):
            st=json.load(open(sf,encoding='utf-8'));st['peak']=None;st['said']={};json.dump(st,open(sf,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
            print('đỉnh vốn đặt lại (lấy theo vốn hiện tại ở vòng tiếp theo)')
    else:
        c=json.load(open(os.path.join(B,'config_live.json'),encoding='utf-8'))
        if not c.get('safe'):print('config_live.json chưa có khối "safe" (chưa bật bản an toàn)');sys.exit(1)
        eq=float(a[1]) if len(a)>1 else float(c['scaling']['capital_usd'])
        print(json.dumps(dict(safe=c['safe'],at_equity=summary(c,eq)),ensure_ascii=False,indent=1))
