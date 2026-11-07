import tempfile, unittest
from pathlib import Path
import service_supervisor as S
class P:
    count=100
    def __init__(self):
        P.count+=1; self.pid=P.count; self.returncode=None
    def poll(self): return self.returncode
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.r=Path(self.tmp.name); (self.r/'logs').mkdir()
        self.calls=[]; self.t=100
        def spawn(args,**kw): self.calls.append((args,kw)); return P()
        self.h=S.Children(self.r,lambda:self.t,spawn)
    def tearDown(self): self.tmp.cleanup()
    def enable(self): (self.r/'ALLOW_PC_RUN.flag').touch()
    def test_gate(self):
        self.h.tick(); self.assertEqual(set(self.h.procs),{'telegram','monitor'})
    def test_kill(self):
        self.enable(); (self.r/'KILL').touch(); self.h.tick(); self.assertNotIn('trade',self.h.procs)
    def test_migration_hold(self):
        self.enable(); (self.r/'SERVICE_MIGRATION_HOLD.flag').touch(); self.h.tick()
        self.assertNotIn('trade',self.h.procs); self.assertEqual(set(self.h.procs),{'telegram','monitor'})
    def test_three(self):
        self.enable(); self.h.tick(); self.assertEqual(set(self.h.procs),set(S.NAMES))
        self.assertEqual(self.calls[0][1]['env']['BNB_DRY_RUN'],'0')
        self.assertFalse(any('rescale' in str(a) or 'dashboard' in str(a) for a,k in self.calls))
    def test_isolation(self):
        self.enable(); self.h.tick(); trade=self.h.procs['trade']; mon=self.h.procs['monitor']
        self.h.procs['telegram'].returncode=1; self.h.tick(); self.assertEqual(len(self.calls),3)
        self.t+=14; self.h.tick(); self.assertEqual(len(self.calls),3)
        self.t+=1; self.h.tick(); self.assertEqual(len(self.calls),4)
        self.assertIs(self.h.procs['trade'],trade); self.assertIs(self.h.procs['monitor'],mon)
    def test_duplicate(self):
        self.enable(); self.h.tick(); self.h.tick(); self.assertEqual(len(self.calls),3)
    def test_active_kill(self):
        self.enable(); self.h.tick(); p=self.h.procs['trade']; (self.r/'KILL').touch(); self.h.tick()
        self.assertIs(self.h.procs['trade'],p)
        p.returncode=0; self.h.tick(); self.t+=60; self.h.tick(); self.assertNotIn('trade',self.h.procs)
    def test_delay(self):
        self.enable(); self.h.tick(); self.h.procs['trade'].returncode=1; self.h.tick()
        self.t+=29; self.h.tick(); self.assertEqual(len(self.calls),3)
        self.t+=1; self.h.tick(); self.assertEqual(len(self.calls),4)
    def test_spawn_failure(self):
        self.enable(); old=self.h.spawn
        def spawn(args,**kw):
            if 'bnb_bot.py' in args[-1]: raise PermissionError('simulated')
            return old(args,**kw)
        self.h.spawn=spawn; self.h.tick(); self.assertEqual(set(self.h.procs),{'telegram','monitor'})
        self.assertEqual(self.h.next_start['trade'],130)
if __name__=='__main__': unittest.main()