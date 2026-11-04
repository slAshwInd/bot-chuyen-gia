"""X5 (BASE_SL250, ladder 750/250/250/250 x equity/5000, x5) sizing + live add execution against the FAKE exchange (nothing is sent)."""
import os,sys,unittest
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import test_live as T
import json
from test_live import livecore,CFG,mk_bars,REG,RK,T0
CF=json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'config_live.json'),encoding='utf-8'))   # the REAL X5 config
_Base=T.FakeHX
class FX(_Base):
    def __init__(s,*a,**k):
        _Base.__init__(s,*a,**k);s.abstraction_='binanceUSDM'      # Binance build: account mode reported by bnx.user_state
    def ioc(s,c,is_buy,sz,px,reduce_only=False,label=''):
        x=s.pos.get(c);prev=dict(x) if x else None
        r=_Base.ioc(s,c,is_buy,sz,px,reduce_only,label)
        if prev and not reduce_only and 'filled' in str(r):
            fp=s.px*1.0003;q=prev['szi']+sz;s.pos[c]=dict(szi=q,entryPx=(prev['szi']*prev['entryPx']+sz*fp)/q)
        return r
def build(dry,eq):
    T.FakeHX=FX
    b,LV,hx,evs,ev=T.build(dry,eq,11.95,cf_base=CF)
    LV.lev=5;LV.max_notional=None          # X5: per-order guard derived from 0.30 x live equity x lev
    CFG['max_pos']=2;CFG['alert_warn']=-250*0.76;CFG['alert_crit']=-400*0.76;CFG['cat_stop']=-500*0.76;b.ladder=list(CF['groups']['BASE']['ladder']);b.lev=5.0;b.name='BASE_SL250'
    LV.sync(b,livecore.vn_now());return b,LV,hx,evs,ev
class T_x5(unittest.TestCase):
    def test_sizing_3800(self):
        b,LV,hx,evs,ev=build(True,3800.0)
        self.assertEqual([b.leg(k) for k in range(4)],[570.0,190.0,190.0,190.0])
        ok,bars=T.enter(b,LV,hx);p=b.pos['LINK'];print('\n3800: legs',[b.leg(k) for k in range(4)],'entry notional',round(p['cost'],2),'margin',round(p['margin'],2))
        self.assertAlmostEqual(p['cost'],2850,delta=15)
    def test_live_add(self):
        b,LV,hx,evs,ev=build(False,3800.0);ok,bars=T.enter(b,LV,hx);p=b.pos['LINK'];q0=p['q']
        px=11.95*0.982;hx.move(px)
        b.manage('LINK',T0,px,bars)
        p=b.pos['LINK'];adds=[e for e in evs if e['event']=='LIVE_ADD']
        print([ (e['event'],str(e.get('reason'))[:200]) for e in ev[-4:]],[e['event'] for e in evs[-5:]]);print('add events',len(adds),'k',p['k'],'q',q0,'->',p['q'],'exchange szi',hx.pos['LINK']['szi'],'stops',[(o['trigger'],o['sz']) for o in hx.orders])
        self.assertEqual(len(adds),1);self.assertEqual(p['k'],2);self.assertAlmostEqual(p['q'],hx.pos['LINK']['szi'],6)
        self.assertEqual(len(hx.orders),1);self.assertAlmostEqual(hx.orders[0]['sz'],p['q'],1)
class T_x5_small(unittest.TestCase):
    def test_tiny_equity_skips_cleanly(self):
        b,LV,hx,evs,ev=build(False,6.9973);self.assertIsNone(LV.min_equity)
        r=T.enter(b,LV,hx);self.assertNotIn('LINK',b.pos);self.assertFalse(any(x[0]=='ioc' for x in hx.sent))
        f=[e for e in evs if e['event']=='LIVE_ENTRY_FAILED'];self.assertEqual(len(f),1);self.assertIn('below exchange minimum',f[0]['info']['why'])
        T.enter(b,LV,hx);self.assertEqual(len([e for e in evs if e['event']=='LIVE_ENTRY_FAILED']),1)      # throttled: no log spam, still no order
        self.assertAlmostEqual(LV.max_notional,round(0.30*6.9973*5*1.03,4),6)
    def test_dynamic_cap_follows_equity(self):
        b,LV,hx,evs,ev=build(False,20000.0);self.assertAlmostEqual(LV.max_notional,0.30*20000*5*1.03,3)
if __name__=='__main__':unittest.main()
