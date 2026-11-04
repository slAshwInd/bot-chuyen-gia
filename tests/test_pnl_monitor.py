import os,sys,unittest,tempfile
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pnl_monitor as M
class T(unittest.TestCase):
    def test_snapshot(self):
        d=tempfile.mkdtemp();p=os.path.join(d,'t.csv')
        open(p,'w',encoding='utf-8').write('branch,net,reason\nA,1.5,"x, y"\nA,-0.5,z\nA,0.25,q\n')
        s=M.snapshot(lambda:dict(equity=101.2,positions=[dict(coin='SOL',side='LONG',upnl=-2.5)]),p)
        self.assertEqual(s['row'][1:6],['3','2','1','+1.2500','101.2000']);self.assertEqual(s['row'][6],'SOL LONG -2.5000');self.assertEqual(s['row'][7],'-')
        self.assertEqual(s['sign'][6],-2.5)
    def test_empty_and_error(self):
        s=M.snapshot(lambda:(_ for _ in ()).throw(RuntimeError('net')),'/nonexistent.csv')
        self.assertEqual(s['row'][1:6],['0','0','0','+0.0000','?']);self.assertIn('net',s['error'])
