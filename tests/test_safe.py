"""BaseAster SAFE: overlay (size x k, mọi mức giá giữ nguyên) + cầu dao vào lệnh. Không cần mạng."""
import os,sys,json,shutil,subprocess,tempfile,unittest
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R)
import safe

def build(cap,profile=None):
    d=tempfile.mkdtemp()
    for f in os.listdir(R):
        if f.endswith('.py'):shutil.copy(os.path.join(R,f),d)
    shutil.copytree(os.path.join(R,'ref'),os.path.join(d,'ref'))
    if profile is not None:json.dump(profile,open(os.path.join(d,'safe_profile.json'),'w'))
    subprocess.run([sys.executable,'make_live_config.py','ref/config_paper.json',str(cap)],cwd=d,check=True,capture_output=True)
    c=json.load(open(os.path.join(d,'config_live.json'),encoding='utf-8'));shutil.rmtree(d);return c

def levels(c,eq):
    """Các mức giá (% di chuyển giá so với giá vào) mà bot dùng, tại vốn eq."""
    n=sum(c['groups']['BASE']['ladder'])/5000*eq*c['live']['leverage'];P=c['pct_thresholds']['pct']
    return dict(notional=n,sl_usd=c['stops']['SL250']/5000*eq,stop=c['stops']['SL250']/5000*eq/n*100,be=P['be_after_15m']/100*eq/n*100,
                lock=P['lock_step']/100*eq/n*100,eps=P['eps_15m']/100*eq/n*100,small=P['small_15m']/100*eq/n*100,trail=[a/100*eq/n*100 for a,_ in P['trail_tiers']])

class TestOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live1000=build(1000);cls.safe8000=build(8000,dict(enabled=True,risk_pct=3.0));cls.live8000=build(8000)
    def test_no_profile_is_live_unchanged(self):
        self.assertNotIn('safe',self.live1000);self.assertEqual(self.live1000['groups']['BASE']['ladder'],[4900])
    def test_risk_and_size(self):
        L=levels(self.safe8000,8000)
        self.assertAlmostEqual(self.safe8000['safe']['k'],0.12)
        self.assertAlmostEqual(L['sl_usd'],240.0,places=4)                    # 3% của 8000
        self.assertAlmostEqual(L['notional'],4704.0,places=2)                  # 0.588x vốn
    def test_price_levels_equal_live_rules(self):
        """Mọi mức giá của SAFE ở 8000$ = bot live ở vốn chuẩn 1000$ (nơi công thức pct của live đúng)."""
        S=levels(self.safe8000,8000);Lv=levels(self.live1000,1000)
        for k in('stop','be','lock','eps','small'):self.assertAlmostEqual(S[k],Lv[k],places=6,msg=k)
        for a,b in zip(S['trail'],Lv['trail']):self.assertAlmostEqual(a,b,places=6)
    def test_live_pct_bug_documented(self):
        """Bản live ở 8000$ phóng ngưỡng hòa vốn lên 8 lần (bug pct_thresholds) – SAFE sửa."""
        self.assertAlmostEqual(levels(self.live8000,8000)['be']/levels(self.live1000,1000)['be'],8.0,places=6)
    def test_step3_scaled(self):
        s3=self.safe8000['step3'];self.assertAlmostEqual(s3['risk_pct'],3.0);self.assertAlmostEqual(s3['max_notional_x'],0.6)
    def test_bad_risk(self):
        with self.assertRaises(ValueError):safe.overlay(self.live1000,dict(safe.DEF,risk_pct=30))

class FakeBook:
    def __init__(s,eq,trades=()):s.eq=eq;s.trades=list(trades)
    def base(s):return s.eq

def tr(t1,side='LONG',stop=True,net=-240):return dict(t1=t1,side=side,stop=stop,net=net)

class TestGuard(unittest.TestCase):
    def setUp(self):
        self.d=tempfile.mkdtemp();self.msgs=[]
        self.g=safe.Guard(dict(safe.DEF),self.d,notify=self.msgs.append)
    def tearDown(self):shutil.rmtree(self.d)
    def test_clean(self):
        self.g.update(8000,'2026-11-05 10:00:00');self.assertIsNone(self.g.check(FakeBook(8000),'2026-11-05 10:00:00','L'))
    def test_cooldown_same_side_only(self):
        b=FakeBook(7760,[tr('2026-11-05 08:00:00')]);t='2026-11-05 14:00:00'
        self.assertIn('nghỉ cùng chiều',self.g.check(b,t,'L'))
        self.assertIsNone(self.g.check(b,t,'S'))
        self.assertIsNone(self.g.check(b,'2026-11-05 20:00:01','L'))       # hết 12h
    def test_win_resets_cooldown_logic(self):
        b=FakeBook(8000,[tr('2026-11-05 08:00:00',stop=False,net=50)]);self.assertIsNone(self.g.check(b,'2026-11-05 08:30:00','L'))
    def test_streak_pause_both_sides(self):
        b=FakeBook(7500,[tr('2026-11-04 08:00:00'),tr('2026-11-05 08:00:00','SHORT')])
        self.assertIn('liên tiếp',self.g.check(b,'2026-11-06 07:00:00','L'))
        self.assertIsNone(self.g.check(b,'2026-11-07 08:00:01','L'))       # hết 48h
    def test_streak_broken_by_win(self):
        b=FakeBook(7500,[tr('2026-11-04 08:00:00'),tr('2026-11-04 12:00:00',stop=False,net=30),tr('2026-11-05 08:00:00','SHORT')])
        self.assertIsNone(self.g.check(b,'2026-11-05 09:00:00','L'))
    def test_step3_trades_count(self):
        self.g.extra_trades=lambda:[('2026-11-05 08:00:00','S',True)]
        b=FakeBook(7500,[tr('2026-11-05 06:00:00')])
        self.assertIn('liên tiếp',self.g.check(b,'2026-11-05 09:00:00','L'))
    def test_daily_loss(self):
        self.g.update(8000,'2026-11-05 00:05:00');self.g.update(7500,'2026-11-05 12:00:00')
        self.assertIn('lỗ trong ngày',self.g.check(FakeBook(7500),'2026-11-05 12:00:00','S'))
        self.g.update(7500,'2026-11-06 00:01:00');self.assertIsNone(self.g.check(FakeBook(7500),'2026-11-06 00:01:00','S'))
    def test_drawdown_halt_needs_manual_reset(self):
        self.g.update(8000,'2026-11-01 10:00:00');self.g.update(9000,'2026-11-02 10:00:00')
        self.g.update(7600,'2026-11-05 10:00:00')                           # -15.6% từ đỉnh 9000
        self.assertTrue(self.g.halted());self.assertTrue(self.msgs)
        self.g.update(9500,'2026-11-06 10:00:00');self.assertIn('HALT',self.g.check(FakeBook(9500),'2026-11-06 10:00:00','L'))
        os.remove(os.path.join(self.d,'SAFE_HALT.flag'));self.assertIsNone(self.g.check(FakeBook(9500),'2026-11-06 10:00:00','L'))
    def test_state_persists(self):
        self.g.update(9000,'2026-11-02 10:00:00');g2=safe.Guard(dict(safe.DEF),self.d);self.assertEqual(g2.st['peak'],9000)
    def test_wrap_momf(self):
        b=FakeBook(7760,[tr('2026-11-05 08:00:00')])
        f=safe.wrap_momf(lambda c,t,px,L_:'mom' if L_ else None,self.g,lambda:b)
        r=f('ASTER','2026-11-05 09:00:00',1.0,True);self.assertTrue(r.startswith('mom; SAFE'))
        self.assertIsNone(f('ASTER','2026-11-05 09:00:00',1.0,False))
        self.assertIn('SAFE',safe.wrap_momf(None,self.g,lambda:b)('ASTER','2026-11-05 09:00:00',1.0,True))

if __name__=='__main__':unittest.main()
