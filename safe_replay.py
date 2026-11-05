"""Chạy lại LỊCH SỬ LỆNH THẬT của bot live qua luật SAFE để so sánh (chỉ đọc file, không gọi sàn).
Dùng: python safe_replay.py [trades.csv] [vốn_đầu_live] [vốn_safe] [risk_pct]
  mặc định: data/trades_side_live.csv (hoặc trades_live.csv) + data/step3_trades.csv, vốn live = start_capital trong config, vốn safe 8000, risk 3%.
Cách tính: lãi/lỗ % vốn của từng lệnh live (net / vốn trước lệnh) x k (= risk/25) áp lên vốn SAFE; lệnh nào rơi vào thời gian cầu dao
đang chặn (nghỉ sau SL, chuỗi SL, lỗ ngày, HALT sụt đỉnh) thì bỏ qua.
Giới hạn: không mô phỏng được lệnh MỚI có thể xuất hiện khi bỏ qua một lệnh (cần replay nến đầy đủ trên máy nghiên cứu)."""
import os,sys,csv,json
import pandas as pd
import safe
B=safe.B

def load(path=None):
    rows=[]
    cands=[path] if path else [B+'data/trades_side_live.csv',B+'trades_live.csv']
    for p in cands:
        if p and os.path.exists(p):
            for r in csv.DictReader(open(p,encoding='utf-8')):
                side=r.get('side') or ('SHORT' if str(r.get('reason','')).startswith('[SHORT]') else 'LONG')
                rows.append(dict(t0=r['t0'][:19],t1=r['t1'][:19],side='S' if side=='SHORT' else 'L',stop=str(r.get('stop')).lower()=='true',net=float(r['net']),src='BaseAster'))
            break
    p3=B+'data/step3_trades.csv'
    if not path and os.path.exists(p3):
        for r in csv.DictReader(open(p3,encoding='utf-8')):
            if r.get('net') in(None,''):continue
            rows.append(dict(t0=r['t0'][:19],t1=r['t1'][:19],side='S' if r['side']=='SHORT' else 'L',stop=str(r.get('why','')).startswith('SL'),net=float(r['net']),src='Step3'))
    return sorted(rows,key=lambda x:x['t0'])

def run(rows,eq_live0,eq_safe0,P):
    k=float(P['risk_pct'])/25.0;E=eq_live0;S=eq_safe0;peak=S;halt=False;kept=[];day=None;day0=S;out=[]
    for r in rows:
        r_live=r['net']/E if E>0 else 0.0;E+=r['net']
        t=pd.Timestamp(r['t0']);why=None
        if halt:why='HALT sụt đỉnh'
        if r['t0'][:10]!=day:day=r['t0'][:10];day0=S
        if not why and (S/day0-1)*100<=-P['daily_loss_pct']:why='lỗ ngày'
        if not why:
            n=0
            for x in reversed(kept):
                if not x['stop']:break
                n+=1
            if n>=P['max_consec_sl'] and (t-pd.Timestamp(kept[-1]['t1'])).total_seconds()/3600<P['pause_after_streak_h']:why=f'{n} SL liên tiếp'
        if not why:
            ss=[x for x in kept if x['stop'] and x['side']==r['side']]
            if ss and (t-pd.Timestamp(ss[-1]['t1'])).total_seconds()/3600<P['cooldown_after_sl_h']:why='nghỉ sau SL cùng chiều'
        if why:out.append(dict(r,skip=why,safe_eq=round(S,2),live_eq=round(E,2)));continue
        S*=1+k*r_live;peak=max(peak,S);kept.append(r)
        if (1-S/peak)*100>=P['max_dd_pct']:halt=True
        out.append(dict(r,skip='',safe_eq=round(S,2),live_eq=round(E,2)))
    return pd.DataFrame(out)

def mdd(x):
    x=pd.Series(list(x));return float(((x/x.cummax())-1).min()*100) if len(x) else 0.0

if __name__=='__main__':
    a=sys.argv[1:];path=a[0] if a and a[0] not in('-','') else None
    cfg=json.load(open(B+'config_live.json',encoding='utf-8'))
    eq_l=float(a[1]) if len(a)>1 else float(cfg.get('start_capital',1000));eq_s=float(a[2]) if len(a)>2 else 8000.0
    P=dict(safe.DEF,**(safe.load_profile(B) or {}));P['risk_pct']=float(a[3]) if len(a)>3 else float(P['risk_pct'])
    R=load(path)
    if not R:print('Không tìm thấy lịch sử lệnh (data/trades_side_live.csv / trades_live.csv).');sys.exit(1)
    D=run(R,eq_l,eq_s,P);K=D[D.skip=='']
    lv=[eq_l]+list(D.live_eq);sv=[eq_s]+list(K.safe_eq)
    print(f"Lệnh live: {len(D)} (SL {int(D.stop.sum())}) | lãi/lỗ live: {lv[-1]-eq_l:+.2f}$ ({(lv[-1]/eq_l-1)*100:+.1f}%), sụt tối đa {mdd(lv):.1f}%")
    print(f"SAFE risk {P['risk_pct']:g}%: giữ {len(K)} lệnh (SL {int(K.stop.sum())}), bỏ {len(D)-len(K)} | vốn {eq_s:.0f} -> {sv[-1]:.2f}$ ({(sv[-1]/eq_s-1)*100:+.1f}%), sụt tối đa {mdd(sv):.1f}%")
    if len(D)>len(K):print(D[D.skip!=''][['t0','src','side','stop','net','skip']].to_string(index=False))
    o=B+'data/safe_replay.csv';os.makedirs(os.path.dirname(o),exist_ok=True);D.to_csv(o,index=False);print('chi tiết:',o)
