import sys;sys.argv=['x','ASTER']
import pandas as pd,numpy as np
from hl import *;import backtest as BT
S=pd.read_csv('data/screen_v2_raw.csv')
cand=S[(S.aster_score>=0.6)&(S.vol24h_musd>=3)&(S.lo_revert>=0.75)].sort_values('aster_score',ascending=False).coin.tolist()
cut=pd.Timestamp.now()+pd.Timedelta(hours=7)-pd.Timedelta(days=90);D={}
for c in cand:
    x=bb(candles(c,'4h',130));x=x[x.closed];D[c]=x[x.index>=cut-pd.Timedelta(days=12)]
L={'hiện tại 250/300/400/550':[250,300,400,550],'đều 375x4':[375,375,375,375],'nặng đầu 500/350/325/325':[500,350,325,325]}
R=[]
for c in cand:
    for k,lad in L.items():
        BT.SL=[c];BT.D={c:D[c]};T,E,A,bk=BT.run(dict(cat_stop_enabled=True,cat_stop=-500,ladder=lad))
        r=T.reason;R.append(dict(coin=c,ladder=k,n=len(T),p_tp120=round(r.str.contains('120').mean(),2),p_stop=round(r.str.contains('Cắt').mean(),2),avg_net=round(T.net.mean(),1),net=round(T.net.sum()),worst=round(T.net.min())))
R=pd.DataFrame(R);R.to_csv('data/ladder_cmp.csv',index=False)
print(R.groupby('ladder')[['n','p_tp120','p_stop','avg_net','net']].agg({'n':'sum','p_tp120':'mean','p_stop':'mean','avg_net':'mean','net':'sum'}).round(2))
print(cand)
