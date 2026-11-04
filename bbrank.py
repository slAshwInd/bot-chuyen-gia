"""BB-respect score (Vien 2026-10-08 22:27): how well a token has historically RESPECTED its 4h Bollinger Bands
after touching the LOWER band. Point-in-time: at decision bar k only touch episodes that started inside the
look-back window AND were already resolved before bar k are used (no look-ahead). Shared by live bot + research.

Touch episode at closed 4h bar i: low[i] <= lower band of bar i-1 (= the band the live bot uses), and no touch in
the 2 previous bars. Entry reference e = lower band of bar i-1. Followed for N=12 bars (2 days):
  rev   : a high reaches the mid band (of the previous closed bar) within 12 bars
  t_mid : bars until the mid band
  mae   : deepest low below e (%) before the mid band / end of window   (how deep the adds go)
  deep  : mae >= DEEP% (6% ~ the SL250-350 zone with the ladders) before reaching mid
  break : some bar closes below its lower band (before mid); fake = that break closed back inside within 2 bars
  amp   : (mid - lo)/lo % at the touch (size of the move to the mid band)
Score (0..1) = 0.35*P(về dải giữa trong 2 ngày) + 0.25*(1-P(lún sâu >=6%)) + 0.20*P(phá giả) + 0.10*tốc độ + 0.10*nhịp dải-dải
  rates are shrunk toward a neutral prior with M pseudo-events (few events -> close to 0.5);
  tốc độ = 1 - median(t_mid)/12;  nhịp = min(lower->upper swings per 30d / 4, 1)."""
import numpy as np,pandas as pd
NF=12;DEEP=6.0;WIN=180;M=3.0;PRI=dict(rev=0.6,deep=0.25,fake=0.5,speed=0.5)
W=dict(rev=0.35,deep=0.25,fake=0.20,speed=0.10,swing=0.10)
def events(o,h,l,c,lo,mid,up):
    n=len(c);E=[]
    for i in range(3,n):
        if np.isnan(lo[i-1]) or not l[i]<=lo[i-1]:continue
        if (l[i-1]<=lo[i-2]) or (l[i-2]<=lo[i-3]):continue
        e=lo[i-1];mn=l[i];rev=0;tm=np.nan;brk=0;fake=0;res=None;bi=None
        for j in range(i,min(n,i+NF)):
            mn=min(mn,l[j])
            if j>i and h[j]>=mid[j-1]:rev=1;tm=j-i;res=j;break
            if bi is None and c[j]<lo[j]:bi=j;brk=1
            elif bi is not None and not fake and j<=bi+2 and c[j]>lo[j]:fake=1
        if res is None:
            if i+NF>n:continue          # not resolved yet (live: episode still open)
            res=i+NF-1
        if brk and not fake and bi+2>res:   # break close to the end: look at the 2 bars after the break (resolution moves there)
            if bi+2>=n:continue
            fake=int(c[bi+1]>lo[bi+1] or c[bi+2]>lo[bi+2]);res=bi+2
        mae=(e-mn)/abs(e)*100
        E.append(dict(i=i,res=res+1,rev=rev,t_mid=tm,mae=mae,deep=int(mae>=DEEP),brk=brk,fake=fake,amp=(mid[i-1]-lo[i-1])/abs(lo[i-1])*100))
    return E
def swings(h,l,lo,up,a,b):
    last=0;sw=0
    for i in range(a,b):
        if np.isnan(up[i]):continue
        if h[i]>=up[i] and last!=1:sw+=last==-1;last=1
        elif l[i]<=lo[i] and last!=-1:last=-1
    return sw
def agg(ev,sw30):
    n=len(ev);rv=sum(e['rev'] for e in ev);dp=sum(e['deep'] for e in ev);nb=sum(e['brk'] for e in ev);fk=sum(e['fake'] for e in ev)
    tm=[e['t_mid'] for e in ev if e['rev']]
    p_rev=(rv+M*PRI['rev'])/(n+M);p_deep=(dp+M*PRI['deep'])/(n+M);p_fake=(fk+M*PRI['fake'])/(nb+M)
    sp=(1-np.median(tm)/NF) if tm else PRI['speed'];sp=(sp*len(tm)+M*PRI['speed'])/(len(tm)+M)
    swn=min(sw30/4.0,1.0)
    sc=W['rev']*p_rev+W['deep']*(1-p_deep)+W['fake']*p_fake+W['speed']*sp+W['swing']*swn
    return dict(score=round(float(sc),3),n_touch=n,p_rev=round(p_rev,3),raw_rev=round(rv/n,3) if n else None,p_deep=round(p_deep,3),raw_deep=round(dp/n,3) if n else None,
                n_break=nb,p_fake=round(p_fake,3),raw_fake=round(fk/nb,3) if nb else None,t_mid_med=float(np.median(tm)) if tm else None,
                mae_med=round(float(np.median([e['mae'] for e in ev])),2) if n else None,amp_med=round(float(np.median([e['amp'] for e in ev])),2) if n else None,swings30=round(sw30,2))
def series(x,win=WIN):
    """Per-bar point-in-time score for a 4h frame with bb() columns (index = bar open). Value at row k uses only
    bars < k (decision made during bar k, after bar k-1 closed)."""
    o,h,l,c,lo,mid,up=[x[k].to_numpy(float) for k in('o','h','l','c','lo','mid','up')];n=len(c)
    E=events(o,h,l,c,lo,mid,up);out=np.full(n,np.nan);out_rev=np.full(n,np.nan)
    # swings: cumulative count of lower->upper alternations, per bar
    last=0;cs=np.zeros(n)
    for i in range(n):
        s=0
        if not np.isnan(up[i]):
            if h[i]>=up[i] and last!=1:s=last==-1;last=1
            elif l[i]<=lo[i] and last!=-1:last=-1
        cs[i]=(cs[i-1] if i else 0)+s
    for k in range(30,n):
        a=max(0,k-win);ev=[e for e in E if e['i']>=a and e['res']<=k]
        nb=k-max(a,20);sw30=(cs[k-1]-cs[max(a,20)-1])/max(nb,1)*180   # 180 bars = 30 days
        out[k]=agg(ev,sw30)['score']
    return out
def now(x,win=WIN):
    """Live: x = CLOSED 4h bars with bb(). Score for a decision taken now (after the last closed bar)."""
    o,h,l,c,lo,mid,up=[x[k].to_numpy(float) for k in('o','h','l','c','lo','mid','up')];n=len(c)
    E=events(o,h,l,c,lo,mid,up);a=max(0,n-win);ev=[e for e in E if e['i']>=a and e['res']<=n]
    s0=max(a,20);sw30=swings(h,l,lo,up,s0,n)/max(n-s0,1)*180
    return agg(ev,sw30)
def reason_vi(r,rank,ncand):
    f=lambda v:f"{v*100:.0f}%" if v is not None else 'chưa đủ dữ liệu'
    return (f"Xếp hạng {rank}/{ncand}, điểm tôn trọng BB {r['score']:.2f}: {f(r['raw_rev'])} lần chạm dải dưới về được dải giữa trong 2 ngày, "
            f"phá giả {f(r['raw_fake'])} ({r['n_break']} lần đóng dưới dải), lún sâu ≥6% {f(r['raw_deep'])}, MAE trung vị {r['mae_med']}%, "
            f"{r['n_touch']} lần chạm/30 ngày")

# ---------------- 1D stability (Vien 22:29: "chọn những token có xu hướng ổn định ... nhìn nến ngày") ----------------
# On CLOSED daily candles (UTC day = 07:00 VN):  r30 = max(high)/min(low) of the last 30 days (x2 = 2.0);
# chg14 = |close change over 14 days| %;  bwr = 1D BB width now / its own median over the last 180 days.
ST=dict(r30=1.8,r60=1.8,chg14=30.0,bwr=2.0);BB_MIN=0.75     # chosen variant (research/rank_grid3.py, IS-selected)
def daily_from_4h(x4):
    y=x4[['o','h','l','c','v']].copy();y.index=y.index-pd.Timedelta(hours=7)      # VN -> UTC day boundaries
    d=y.resample('1D').agg(dict(o='first',h='max',l='min',c='last',v='sum')).dropna();d.index=d.index+pd.Timedelta(hours=7)
    d['nb']=y.c.resample('1D').count().reindex(d.index-pd.Timedelta(hours=7)).to_numpy()
    return d
def stab_frame(d):
    """d: daily OHLC (closed bars). Columns r30/chg7/chg14/chg30/bw/bwr/pb as of each daily CLOSE."""
    from hl import bb
    z=bb(d);z['r30']=z.h.rolling(30,min_periods=20).max()/z.l.rolling(30,min_periods=20).min()
    for n in(7,14,30):z[f'chg{n}']=(z.c/z.c.shift(n)-1)*100
    z['r60']=z.h.rolling(60,min_periods=40).max()/z.l.rolling(60,min_periods=40).min()
    z['bwr']=z.bw/z.bw.rolling(180,min_periods=60).median()
    return z
def stab_eval(row,th=None):
    th=th or ST;bad=[]
    if any(pd.isna(row.get(k)) for k in('r30','r60','chg14','bwr')):return True,[]   # not enough daily history: no penalty
    if row['r30']>=th['r30']:bad.append(f"biên độ 30 ngày cao/thấp x{row['r30']:.2f} (≥x{th['r30']})")
    if abs(row['chg14'])>=th['chg14']:bad.append(f"14 ngày {row['chg14']:+.0f}% (vượt ±{th['chg14']:.0f}%)")
    if th.get('r60') and row['r60']>=th['r60']:bad.append(f"60 ngày cao/thấp x{row['r60']:.2f} (≥x{th['r60']}, {'gần gấp đôi' if row['r60']<2.1 else 'hơn gấp đôi'})" if row['r60']>=1.9 else f"60 ngày cao/thấp x{row['r60']:.2f} (≥x{th['r60']})")
    if row['bwr']>=th['bwr']:bad.append(f"độ rộng BB 1D gấp {row['bwr']:.1f} lần trung vị 180 ngày (≥{th['bwr']})")
    return len(bad)==0,bad
def stab_text(row,ok,bad):
    base=f"nến ngày: 30/60 ngày cao/thấp x{row['r30']:.2f}/x{row['r60']:.2f}, 7/14/30 ngày {row['chg7']:+.0f}%/{row['chg14']:+.0f}%/{row['chg30']:+.0f}%, độ rộng BB 1D x{row['bwr']:.1f} trung vị, %B 1D {row['pb']:.2f}"
    return ('ổn định khung ngày (' if ok else 'KHÔNG ổn định khung ngày: '+'; '.join(bad)+' (')+base+')'

# ---------------- SHORT side (Vien 2026-10-09 17:52): respect of the UPPER band = the same statistics on the mirrored chart ----------------
# price -> -price: high<->low, upper band <-> lower band, mid stays the mid. A "touch" becomes high >= upper band of the previous bar,
# "reach mid" = a low reaches the mid band, "break" = close above the upper band, "fake" = closes back inside within 2 bars,
# deep = MAE >= 6% ABOVE the upper band before reaching mid. Denominators use |e| so the formulas are identical.
def mirror(x):
    y=x.copy()
    y['o'],y['c'],y['h'],y['l']=-x.o,-x.c,-x.l,-x.h
    y['mid'],y['up'],y['lo']=-x.mid,-x.lo,-x.up
    return y
def now_short(x,win=WIN):
    """Upper-band respect score (for SHORT candidates) on CLOSED 4h bars with bb()."""
    return now(mirror(x),win)
def series_short(x,win=WIN):
    return series(mirror(x),win)
def reason_vi_short(r,rank,ncand):
    f=lambda v:f"{v*100:.0f}%" if v is not None else 'chưa đủ dữ liệu'
    return (f"Xếp hạng short {rank}/{ncand}, điểm tôn trọng dải TRÊN BB {r['score']:.2f}: {f(r['raw_rev'])} lần chạm dải trên về được dải giữa trong 2 ngày, "
            f"phá giả {f(r['raw_fake'])} ({r['n_break']} lần đóng trên dải), vượt sâu ≥6% {f(r['raw_deep'])}, MAE trung vị {r['mae_med']}%, "
            f"{r['n_touch']} lần chạm dải trên/30 ngày")
