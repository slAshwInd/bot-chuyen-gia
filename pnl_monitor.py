"""HLBot X5 - cua so PNL (CHI DOC). Khong dung khoa API, khong dat lenh, khong dong vao KILL/khoa.
Nguon: trades_live.csv (lenh da dong, net sau phi) + GET tai khoan Binance (chi doc, khoa API chi doc so du/vi the).
Chay: pythonw pnl_monitor.py   |  kiem tra khong giao dien: python pnl_monitor.py --once"""
import os,sys,csv,json,datetime as dt
B=os.path.dirname(os.path.abspath(__file__))+os.sep
os.environ['BNB_DRY_RUN']='1'
sys.path.insert(0,B)
import bnx
COLS=['Ngày','Tổng số lệnh lũy kế','Số lệnh thắng lũy kế','Số lệnh thua lũy kế','PNL tích lũy','Vốn','PNL tạm tính vị thế 1','PNL tạm tính vị thế 2']
REFRESH_MS=12000
_hx=None
def vn_now():return dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=7)
def trades_stats(path=None):
    path=path or B+'trades_live.csv';n=w=l=0;pnl=0.0
    if os.path.exists(path):
        with open(path,encoding='utf-8',newline='') as f:
            for r in csv.DictReader(f):
                try:x=float(r.get('net') or 0)
                except ValueError:continue
                n+=1;pnl+=x;w+=x>0;l+=x<=0
    return dict(n=n,win=w,loss=l,pnl=pnl)
def account():
    global _hx
    if _hx is None:
        _hx=bnx.BNX(dry_run=True)          # dry_run: chi GET (tai khoan), khong bao gio gui lenh
    st=_hx.user_state();pos=[]
    for c,p in sorted(st['positions'].items(),key=lambda kv:-abs(kv[1]['positionValue'])):
        pos.append(dict(coin=c,side='LONG' if p['szi']>0 else 'SHORT',upnl=p['upnl']))
    return dict(equity=st['equity'],positions=pos)
def snapshot(acct_fn=account,trades_path=None):
    t=trades_stats(trades_path);err=None
    try:a=acct_fn()
    except Exception as ex:a=None;err=f'{type(ex).__name__}: {ex}'[:120]
    def pc(i):
        if not a or len(a['positions'])<=i:return ('-',0)
        p=a['positions'][i];return (f"{p['coin']} {p['side']} {p['upnl']:+.4f}",p['upnl'])
    p1,p2=pc(0),pc(1)
    row=[vn_now().strftime('%Y-%m-%d %H:%M:%S'),str(t['n']),str(t['win']),str(t['loss']),f"{t['pnl']:+.4f}",f"{a['equity']:.4f}" if a else '?',p1[0],p2[0]]
    sign=[0,0,0,0,t['pnl'],0,p1[1],p2[1]]
    return dict(row=row,sign=sign,error=err)
def gui():
    import tkinter as tk
    root=tk.Tk();root.title('BNB X5 - PNL (chỉ đọc, giờ VN)');root.configure(bg='#111')
    f=tk.Frame(root,bg='#111',padx=10,pady=10);f.pack()
    cells=[]
    for j,h in enumerate(COLS):
        tk.Label(f,text=h,bg='#333',fg='white',font=('Segoe UI',10,'bold'),padx=8,pady=6,relief='ridge',bd=1).grid(row=0,column=j,sticky='nsew')
        v=tk.Label(f,text='…',bg='#222',fg='white',font=('Consolas',12,'bold'),padx=8,pady=8,relief='ridge',bd=1);v.grid(row=1,column=j,sticky='nsew');cells.append(v)
    st=tk.Label(root,text='',bg='#111',fg='#aaa',font=('Segoe UI',9));st.pack(anchor='w',padx=10,pady=(0,8))
    def tick():
        s=snapshot()
        for j,(v,sg) in enumerate(zip(s['row'],s['sign'])):
            cells[j].config(text=v,fg='#3ddc84' if sg>0 else '#ff5c5c' if sg<0 else 'white')
        st.config(text=('Lỗi đọc sàn: '+s['error']) if s['error'] else f'Tự làm mới mỗi {REFRESH_MS//1000}s. Chỉ đọc – không đặt lệnh.')
        root.after(REFRESH_MS,tick)
    root.after(100,tick);root.mainloop()
if __name__=='__main__':
    if '--once' in sys.argv:
        s=snapshot();print(json.dumps(s,ensure_ascii=False))
    else:gui()
