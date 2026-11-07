"""Non-interactive service host. No order commands; preserve existing config."""
import ctypes, json, os, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
NAMES=('trade','telegram','monitor')
SCRIPTS=dict(trade='bnb_bot.py',telegram='tg_live_poller.py',monitor='monitor_bnb.py')
DELAYS=dict(trade=30,telegram=15,monitor=15)
LOGS=dict(trade='bot_live.log',telegram='tg_poller.log',monitor='logs/BaseAsterMonitor.log')
def trade_allowed(root=ROOT):
    return (root/'ALLOW_PC_RUN.flag').is_file() and not (root/'KILL').exists() and not (root/'SERVICE_MIGRATION_HOLD.flag').exists()
def hidden_flags(): return 0x08000000 if os.name=='nt' else 0
def log(message):
    try:
        with (ROOT/'logs/service_supervisor.log').open('a',encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ')+message+'\n')
    except OSError: pass
class Children:
    def __init__(self,root=ROOT,clock=time.monotonic,spawn=subprocess.Popen):
        self.root,self.clock,self.spawn=root,clock,spawn
        self.procs={}; self.next_start={n:0 for n in NAMES}
        self.python=str(root/'.venv/Scripts/python.exe')
    def tick(self):
        for name in NAMES:
            p=self.procs.get(name)
            if p is not None:
                if p.poll() is None: continue
                log(f'{name} exited code={p.returncode}; retry after {DELAYS[name]}s')
                self.procs.pop(name); self.next_start[name]=self.clock()+DELAYS[name]
            if name=='trade' and not trade_allowed(self.root): continue
            if self.clock()<self.next_start[name]: continue
            env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
            if name=='trade': env['BNB_DRY_RUN']='0'
            try:
                with (self.root/LOGS[name]).open('ab',buffering=0) as output:
                    p=self.spawn([self.python,'-u',str(self.root/SCRIPTS[name])],cwd=self.root,env=env,
                                 stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                                 creationflags=hidden_flags())
                self.procs[name]=p; log(f'{name} started pid={p.pid}')
                if name=='trade':
                    try: (self.root/'bot_bnb.pid').write_text(str(p.pid),encoding='ascii')
                    except OSError: log('Compatibility PID write failed; child still supervised')
            except OSError as ex:
                self.next_start[name]=self.clock()+DELAYS[name]
                log(f'{name} spawn failed type={type(ex).__name__}')
def main():
    if os.name!='nt': raise SystemExit('Windows service runtime only')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
    kernel.CreateMutexW.restype=ctypes.c_void_p
    mutex=kernel.CreateMutexW(None,False,'Global\\BaseAsterUnifiedSupervisor')
    if not mutex or ctypes.get_last_error()==183: raise SystemExit('Duplicate host or mutex unavailable')
    root=str(ROOT).replace("'","''")
    query=("$ErrorActionPreference='Stop';$r='"+root+"';"
           "$p=@(Get-CimInstance Win32_Process | Where-Object {"
           "$_.Name -match '^pythonw?\\.exe$' -and $_.CommandLine -and "
           "$_.CommandLine -match '(bnb_bot|tg_live_poller|monitor_bnb)\\.py' -and "
           "($_.ExecutablePath.StartsWith($r) -or $_.CommandLine.Contains($r))});$p.Count")
    check=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',query],capture_output=True,
                         text=True,timeout=40,creationflags=hidden_flags())
    if check.returncode or check.stdout.strip()!='0': raise SystemExit('Orphan child or duplicate check failed')
    (ROOT/'logs').mkdir(exist_ok=True)
    (ROOT/'run_bnb.pid').write_text(str(os.getpid()),encoding='ascii')
    log('host start; preserve config; no rescale; no Dashboard')
    children=Children()
    while True:
        children.tick()
        # Stale snapshot is NOT evidence Trade is hung: do not kill/restart on file lock.
        try:
            (ROOT/'logs/supervisor_heartbeat.json').write_text(json.dumps({
                'time':time.strftime('%Y-%m-%d %H:%M:%S'),
                'children':{k:{'pid':v.pid,'alive':v.poll() is None} for k,v in children.procs.items()},
                'trade_gate':trade_allowed()}),encoding='utf-8')
        except OSError: pass
        time.sleep(2)
if __name__=='__main__': main()