"""BNB X5 (Binance) - LIVE DASHBOARD (READ-ONLY). Status + PNL + shortlist + activity in one console window.
Never reads/prints API secrets for display, never places orders, never touches KILL/flag/task. GET account + log/state files only.
Run: dashboard_bnb.bat  |  once: python dashboard.py --once [--plain]"""
import os,sys,re,json,time,subprocess,unicodedata,datetime as dt
B=os.path.dirname(os.path.abspath(__file__))+os.sep
os.environ['BNB_DRY_RUN']='1'
sys.path.insert(0,B)
import winutf8
import bnx as hlx
from pnl_monitor import trades_stats
REFRESH=12;W=108;MAXL=38
PLAIN='--plain' in sys.argv
TRADE_EV=('ENTRY','ADD','EXIT','STOP','EXIT_15M','EXIT_RUNNER','LIVE_STOP_SET','LIVE_ENTRY_FAILED','LIVE_STOP_FAIL','LIVE_STOP_BACKSTOP','LIVE_KILL','LIVE_FLATTEN','LIVE_SEED_ENTERED','LIVE_ADOPT','LIVE_CLOSE_FAILED','LIVE_FOREIGN_POSITION','LIVE_SYNC_ERROR')
_hx=None
def C(code,s):return str(s) if PLAIN else f"\x1b[{code}m{s}\x1b[0m"
G=lambda s:C('92',s);R=lambda s:C('91',s);Y=lambda s:C('93',s);D=lambda s:C('90',s);BO=lambda s:C('1;96',s);WH=lambda s:C('1;97',s)
def badge(txt,col):return C(f'1;30;{col}',f' {txt} ') if not PLAIN else f'[{txt}]'
ANSI=re.compile(r'\x1b\[[0-9;]*m')
def dw(s):
    n=0
    for ch in ANSI.sub('',str(s)):
        if unicodedata.combining(ch):continue
        n+=2 if unicodedata.east_asian_width(ch) in 'WF' else 1
    return n
def pad(s,w,right=False):
    s=str(s);f=' '*max(0,w-dw(s));return f+s if right else s+f
def sgn(x,fmt='{:+.2f}'):
    t=fmt.format(x);return G(t) if x>0 else R(t) if x<0 else D(t)
def now():return dt.datetime.now()
def wrap(s,w):
    out=[];s=str(s)
    while dw(s)>w:
        cut=s.rfind(' ',0,w);cut=w if cut<=0 else cut
        out.append(s[:cut]);s=s[cut:].lstrip()
    out.append(s);return out
def box(title,lines):
    inner=W-4
    top='╭─ '+title+' '+'─'*max(0,W-5-dw(title))+'╮'
    o=[BO(top)]
    for l in lines:
        for p in (wrap(l,inner) if '\x1b' not in l else [l]):
            o.append(BO('│')+' '+pad(p,inner)+' '+BO('│'))
    o.append(BO('╰'+'─'*(W-2)+'╯'));return o
def tail(path,nbytes=400000):
    try:
        with open(path,'rb') as f:
            f.seek(0,2);sz=f.tell();f.seek(max(0,sz-nbytes));return f.read().decode('utf-8','replace').splitlines()
    except Exception:return []
_np=None
def nproc():
    global _np
    if _np is not None:return _np
    try:
        c='(Get-CimInstance Win32_Process -Filter "Name like \'python%\'" | ?{$_.CommandLine -like \'*'+os.path.dirname(os.path.abspath(__file__))+'*bnb_bot.py*\'} | Measure-Object).Count'
        # A desktop user cannot read SYSTEM process command lines; use the service PID file.
        q=lambda p:"'"+p.replace("'","''")+"'"
        c=("$pf="+q(B+'bot_bnb.pid')+"; if ((Get-Service BaseAsterTrade -ErrorAction SilentlyContinue).Status -eq 'Running') { "
           "$bid=0; [int]::TryParse(((Get-Content -LiteralPath $pf -ErrorAction SilentlyContinue) -join ''),[ref]$bid) | Out-Null; "
           "if ($bid -gt 0 -and (Get-Process -Id $bid -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath "+q(B+'KILL')+")) { 1 } else { 0 } } else { "+c+" }")
        r=subprocess.run(['powershell','-NoProfile','-Command',c],capture_output=True,text=True,timeout=15,creationflags=0x08000000)
        _np=int((r.stdout or '0').strip() or 0)
    except Exception:_np=-1
    return _np
TS=re.compile(r'^(?:[A-Z_]+ )?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)')
def pts(s):
    try:return dt.datetime.strptime(s,'%Y-%m-%d %H:%M:%S')
    except Exception:return None
def bot_start(L):
    for l in reversed(L):
        if 'run_bnb.ps1: starting' in l:
            m=TS.match(l);return pts(m.group(1)) if m else None
    return None
def status(L):
    n=nproc();o=[]
    run=badge('RUNNING','42') if n>0 else (badge('UNKNOWN','43') if n<0 else badge('STOPPED','41'))
    mode=sync=halt='?';age=None
    try:
        s=json.load(open(B+'state_live.json',encoding='utf-8'));age=time.time()-os.path.getmtime(B+'state_live.json')
        mode=s.get('mode') or '?';lv=s.get('live') or {};sync=lv.get('sync_ok','?');halt=lv.get('halted')
    except Exception:pass
    for l in reversed(L[-200:]):
        if l.startswith('LOOP '):
            m=re.search(r'sync_ok=(\S+) halted=(\S+)',l)
            if m and sync=='?':sync,halt=m.group(1),m.group(2)
            break
    kill=os.path.exists(B+'KILL');flag=os.path.exists(B+'ALLOW_PC_RUN.flag')
    st=bot_start(L)
    o.append(f"{run} {badge('LIVE','45')}  {D('processes')} {n}   {D('mode')} {mode}   {D('KILL')} {R('ON') if kill else G('off')}   {D('flag')} {G('yes') if flag else Y('NO')}   {D('started')} {st:%H:%M %d/%m}" if st else f"{run} {badge('LIVE','45')}  {D('processes')} {n}")
    hb=(f"{age:.0f}s ago" if age is not None else '?')
    o.append(f"{D('sync_ok')} {G(sync) if str(sync)=='True' else Y(sync)}   {D('halted')} {G(halt) if str(halt) in ('None','False') else R(halt)}   {D('state updated')} {R(hb+'  !!! BOT MAY BE HUNG') if age and age>180 else hb}")
    a=None;err=None
    try:
        global _hx
        if _hx is None:_hx=hlx.HLX(dry_run=True)
        if not _hx.has_key:raise RuntimeError('API key not set (use BNB X5 - NHAP API KEY)')
        a=_hx.user_state();oo=_hx.open_orders()
    except Exception as ex:err=f'{type(ex).__name__}: {ex}'[:90]
    if a:
        o.append(f"{D('Equity')} {WH('%.4f USDT'%a['equity'])}   {D('Unrealized')} {sgn(a.get('upnl',0),'{:+.4f}')}   {D('Open orders')} {len(oo)}")
        for c,x in a['positions'].items():
            sd=G('LONG ') if x['szi']>0 else R('SHORT');stp=[str(q.get('triggerPx')) for q in oo if q.get('coin')==c]
            o.append(f"  {sd} {pad(c,8)} {abs(x['szi'])} @ {x['entryPx']}  {D('PnL')} {sgn(x['upnl'],'{:+.4f}')}  {D('liq')} {x['liqPx']}  {D('stop')} {', '.join(stp) if stp else R('!!! NONE')}")
        if not a['positions']:o.append(D('  No open positions.'))
        if a['equity']<=0:o.append(Y('  ! Futures wallet is empty - bot scans but cannot open trades until funded.'))
    else:o.append(R(f"Exchange read error: {err}"))
    return o,a
def pnl(a):
    t=trades_stats()
    pos=sorted((a or {}).get('positions',{}).items(),key=lambda kv:-abs(kv[1]['positionValue']))
    def pc(i):
        if len(pos)<=i:return D('-')
        c,x=pos[i];return f"{c} {'L' if x['szi']>0 else 'S'} "+sgn(x['upnl'])
    h=['Date','Total trades','Wins','Losses','Cumulative PNL','Equity','Position 1','Position 2']
    w=[11,12,4,6,14,8,13,13];ra=[0,1,1,1,1,1,1,1]
    v=[now().strftime('%d/%m %H:%M'),str(t['n']),G(t['win']),R(t['loss']) if t['loss'] else D(0),sgn(t['pnl'],'{:+.4f}'),f"{a['equity']:.2f}" if a else Y('?'),pc(0),pc(1)]
    row=lambda r:' │ '.join(pad(x,k,b) for x,k,b in zip(r,w,ra))
    sep='─┼─'.join('─'*k for k in w)
    return [WH(row(h)),D(sep),row(v)]
def cols(items,colw,width):
    per=max(1,width//colw);o=[]
    for i in range(0,len(items),per):o.append(''.join(pad(x,colw) for x in items[i:i+per]).rstrip())
    return o
def shortlist(L):
    Ls=Ss=None;ts=None
    for l in reversed(L):
        if l.startswith('SCAN_DONE'):
            m=TS.match(l);ts=pts(m.group(1)) if m else None
            lg=re.search(r'LONG=(\S*)',l);sh=re.search(r'SHORT=(\S*)',l)
            Ls=[x for x in (lg.group(1).split(',') if lg else []) if x];Ss=[x for x in (sh.group(1).split(',') if sh else []) if x];break
    run=nproc()>0;st=bot_start(L)
    if Ls is None or (run and st and ts and ts<st):
        if run and st:return [Y(f"First scan in progress (started {st:%H:%M}, ~15 min)...")]
        if Ls is None:return [D('Not scanned yet (bot not running).')]
    nm=lambda lst:', '.join((x.split(':')[0]+(' '+x.split(':')[1] if ':' in x and x.split(':')[1] else '')) for x in lst) or '(empty)'
    line=(f"Last scan {ts:%H:%M}" if ts else 'Last scan ?')+(' (bot stopped)' if not run else '')+f" | LONG ({len(Ls)}, coin + lower-band respect score): {nm(Ls)} | SHORT ({len(Ss)}, coin + upper-band respect score): {nm(Ss)}"
    return wrap(line,W-4)
def activity(L):
    loops=[];errs={};restarts=[];last=None;lim=now()-dt.timedelta(minutes=30)
    for l in L:
        m=TS.match(l)
        if m:last=pts(m.group(1)) or last
        if l.startswith('LOOP '):
            p=re.search(r'equity=([\d.]+)',l);ps=re.search(r'pos=(\[.*?\])',l);w=re.search(r'watch=(\S*)',l);ws=[]
            for it in (w.group(1).split('),') if w and w.group(1) else []):
                mm=re.match(r'(\w+):[\d.]+\(lo\+?(-?[\d.]+)%/up(-?[\d.]+)%',it)
                if mm:lo,up=float(mm.group(2)),float(mm.group(3));ws.append((min(lo,up),f"{mm.group(1)} {'lower' if lo<=up else 'upper'} {min(lo,up):.1f}%"))
            loops.append((last,float(p.group(1)) if p else 0,ps.group(1) if ps else '?',', '.join(x[1] for x in sorted(ws)[:3]) or '-'))
        elif 'bot exited' in l:restarts.append(last)
        elif last and last>=lim and re.search(r'(Error|error|fail|FAIL)',l) and not l.startswith(('  ','Traceback','SCAN_DONE')):
            k='Binance API connection error' if ('API fail' in l or 'failed:' in l or 'Connection' in l) else l.split(' ',2)[-1][:60]
            c,_=errs.get(k,(0,None));errs[k]=(c+1,last)
    o=[WH(f"{pad('Time',9)}│ {pad('Equity',10,True)} │ {pad('Positions',16)} │ Nearest band")]
    o.append(D('─'*9+'┼'+'─'*12+'┼'+'─'*18+'┼'+'─'*(W-4-42)))
    for t,e,ps,ws in loops[-8:]:o.append(f"{pad(t.strftime('%H:%M:%S') if t else '?',9)}│ {pad('%.2f'%e,10,True)} │ {pad(ps if ps!='[]' else D('none'),16)} │ {ws if ws!='-' else D('-')}")
    if not loops:o.append(D('(no loops yet)'))
    rs=[r for r in restarts if r and r>=now()-dt.timedelta(hours=24)]
    if rs:o.append(Y(f"Bot auto-restarted {len(rs)}x in 24h, last at {rs[-1]:%H:%M}"))
    er=[R(f"{k}  x{c}, last {t:%H:%M}") for k,(c,t) in sorted(errs.items(),key=lambda kv:kv[1][1])][-3:]
    return o,er or [G('None')]
def trades():
    ev=[]
    for l in tail(B+'events_live.jsonl')+tail(B+'alerts_live.jsonl'):
        try:e=json.loads(l)
        except Exception:continue
        x=str(e.get('event',''))
        if x.startswith('ENTRY_BLOCKED'):continue
        if x in TRADE_EV or x.startswith(('ENTRY','ADD','EXIT','STOP')):
            col=R if ('FAIL' in x or 'KILL' in x or x=='STOP') else G if x.startswith('ENTRY') else Y
            ev.append(f"{D(str(e.get('time'))[5:16])}  {col(pad(x,18))} {pad(e.get('coin',''),9)} {pad(e.get('side',''),6)} {str(e.get('reason',''))[:48]}")
    return ev[-4:] or [D('(no trades yet)')]
def render():
    global _np;_np=None
    L=tail(B+'bot_live.log')
    s,a=status(L);act,er=activity(L)
    hdr=f"{WH('BNB X5 · BINANCE FUTURES')}  {D('read-only dashboard')}   {now():%Y-%m-%d %H:%M:%S} (UTC+7)   {D('refresh %ds'%REFRESH)}"
    def fix(l,n):return (l+['']*n)[:max(n,len(l))]
    sl=shortlist(L)
    # one cell per box = latest info only; fixed heights so nothing shifts (STATUS last, variable)
    loops=[x for x in act[2:] if x[2:3]==':']
    la=act[:2]+(loops[-1:] or [act[2] if len(act)>2 else D('(no loops yet)')])
    out=[hdr]+box('PNL',pnl(a))+box('SHORTLIST (current)',fix(sl,3))+box('ACTIVITY (latest loop)',la)+box('ERRORS (last 30 min) - latest',[er[-1]])+box('LAST TRADE',[trades()[-1]])+box('STATUS',s)
    return '\n'.join(out)
if __name__=='__main__':
    if '--once' in sys.argv:print(render());sys.exit(0)
    os.system('');sys.stdout.write('\x1b[?25l\x1b[2J')
    while True:
        try:txt=render()
        except Exception as ex:txt=R(f'Display error: {type(ex).__name__}: {ex}')
        sys.stdout.write('\x1b[H'+txt.replace('\n','\x1b[K\n')+'\x1b[K\x1b[J');sys.stdout.flush();time.sleep(REFRESH)
