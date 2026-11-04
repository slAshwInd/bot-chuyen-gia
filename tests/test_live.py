"""Tests of the live execution layer against a FAKE exchange (no network, no keys, nothing is sent anywhere).
Covers: dry-run payloads, 30%-of-equity sizing, stop placement/updates (BE, lock 2/3, trail), SL execution by the exchange stop (veto + settle), backstop,
15m-style immediate close, kill/flatten, guards (max notional, foreign position, min equity), seeds (tolerance/skip)."""
import os,sys,json,math,time,unittest,copy
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np,pandas as pd
import requests
_o=requests.Session.post
def _g(self,url,*a,**k):
    if str(url).endswith('/exchange'):raise AssertionError('TEST TRIED TO POST TO /exchange')
    return _o(self,url,*a,**k)
requests.Session.post=_g
import bnx as hlx,strat,livecore
from strat import CFG
# Fixture: these engine tests were written for the 7.2$ X10 one-leg config (min_equity 4, max_notional 25, x10, LINK seed).
# The real X5 config (min_equity 1900, x5, 4-leg ladder) is covered by tests/test_x5.py.
CF=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'fixture_x10_config_live.json'),encoding='utf-8'))
ADDR=CF['live']['address']
class FakeHX:
    """Minimal exchange simulator with the same interface as hlx.HLX that Live uses."""
    def __init__(s,dry,equity=7.2,px=11.95):
        s.dry_run=dry;s.logs=[];s.n_sent=0;s.px=px;s.usdc=equity;s.pos={};s.orders=[];s.fills=[];s.oid=100;s.sent=[];s.max_open_notional=25.0
        s.assets={'LINK':dict(asset=18,szDecimals=1,maxLeverage=10,onlyIsolated=False,marginMode=None),'ZZZ':dict(asset=99,szDecimals=0,maxLeverage=3,onlyIsolated=False,marginMode=None)}
        s.lev=dict(type='cross',value=20);s.abstraction_='unifiedAccount';s.fail_stop=0;s.fee_rate=0.00045;s.now_ms=lambda:int(time.time()*1000)
    def log(s,e):s.logs.append(e)
    def load_meta(s,force=False):return s.assets
    def asset(s,c):
        if c not in s.assets:raise hlx.HLXError('unknown coin')
        return s.assets[c]
    def mids(s):return {'LINK':s.px,'BTC':80000.0}
    def book(s,c):return (s.px*0.9999,s.px*1.0001)
    def user_state(s):
        pos={};up=0
        for c,x in s.pos.items():
            u=x['szi']*(s.px-x['entryPx']);up+=u;pos[c]=dict(szi=x['szi'],entryPx=x['entryPx'],upnl=u,marginUsed=abs(x['szi'])*x['entryPx']/10,positionValue=abs(x['szi'])*s.px,liqPx=x['entryPx']*0.95,lev=dict(type='isolated',value=10))
        hold=sum(x['marginUsed'] for x in pos.values())
        return dict(abstraction=s.abstraction_,usdc_total=s.usdc,usdc_hold=hold,perps_account_value=0.0,base=s.usdc,upnl=up,equity=s.usdc+up,positions=pos,time=0)
    def open_orders(s):return [dict(coin='LINK',oid=o['oid'],isTrigger=True,reduceOnly=True,orderType='Stop Market',triggerPx=str(o['trigger']),sz=str(o['sz']),side='A') for o in s.orders]
    def asset_data(s,c):return dict(leverage=dict(s.lev),availableToTrade=[str(s.usdc),str(s.usdc)],maxTradeSzs=['6','6'],markPx=str(s.px))
    def min_notional(s,c):return 10.0
    def fills_since(s,ms,coin=None):return [f for f in s.fills if f['time']>=ms]
    def set_leverage(s,c,lev,cross=False,label=''):s.sent.append(('lev',c,lev,cross));s.lev=dict(type='cross' if cross else 'isolated',value=lev);return {'status':'ok','response':{'type':'default'}}
    def _fill(s,c,szi_delta,px,closing):
        fee=abs(szi_delta)*px*s.fee_rate;cp=0.0;x=s.pos.get(c)
        if closing:cp=-szi_delta*0+ (x['szi']*0)  # replaced below
        s.oid+=1;return fee
    def ioc(s,c,is_buy,sz,px,reduce_only=False,label=''):
        s.sent.append(('ioc',c,is_buy,sz,px,reduce_only));s.n_sent+=0 if s.dry_run else 1
        if not reduce_only and sz*px>s.max_open_notional*1.02:raise hlx.HLXError('GUARD')
        x=s.pos.get(c);fp=s.px*(1.0003 if is_buy else 0.9997)
        if is_buy and fp>px or (not is_buy and fp<px):return {'status':'ok','response':{'type':'order','data':{'statuses':[{'error':'Order could not immediately match'}]}}}
        s.oid+=1;fee=sz*fp*s.fee_rate
        if reduce_only:
            if not x:return {'status':'ok','response':{'type':'order','data':{'statuses':[{'error':'Reduce only order would increase position'}]}}}
            sz=min(sz,abs(x['szi']));d=sz if x['szi']<0 else -sz;cp=-d*0
            cp=(fp-x['entryPx'])*(sz if x['szi']>0 else -sz);s.usdc+=cp-fee;x['szi']+=d
            s.fills.append(dict(coin=c,px=str(fp),sz=str(sz),side='A' if d<0 else 'B',time=int(time.time()*1000),dir='Close Long' if d<0 else 'Close Short',closedPnl=str(cp),fee=str(fee),oid=s.oid))
            if abs(x['szi'])<1e-9:del s.pos[c]
        else:
            d=sz if is_buy else -sz;s.pos[c]=dict(szi=d,entryPx=fp);s.usdc-=fee
            s.fills.append(dict(coin=c,px=str(fp),sz=str(sz),side='B' if is_buy else 'A',time=int(time.time()*1000),dir='Open Long' if is_buy else 'Open Short',closedPnl='0.0',fee=str(fee),oid=s.oid))
        return {'status':'ok','response':{'type':'order','data':{'statuses':[{'filled':{'totalSz':str(sz),'avgPx':str(fp),'oid':s.oid}}]}}}
    def stop(s,c,close_is_buy,sz,trigger,limit,label=''):
        s.sent.append(('stop',c,close_is_buy,sz,trigger,limit));s.n_sent+=0 if s.dry_run else 1
        if s.fail_stop>0:s.fail_stop-=1;return {'status':'ok','response':{'type':'order','data':{'statuses':[{'error':'Invalid TP/SL price'}]}}}
        s.oid+=1;s.orders.append(dict(oid=s.oid,trigger=trigger,sz=sz,buy=close_is_buy,limit=limit,coin=c));return {'status':'ok','response':{'type':'order','data':{'statuses':[{'resting':{'oid':s.oid}}]}}}
    def cancel(s,c,oid,label=''):
        s.sent.append(('cancel',c,oid));s.orders=[o for o in s.orders if o['oid']!=oid];return {'status':'ok','response':{'type':'cancel','data':{'statuses':['success']}}}
    def move(s,px):
        """Move the market; fire stop orders whose trigger is crossed (as the exchange would, on mark price)."""
        s.px=px
        for o in list(s.orders):
            x=s.pos.get(o['coin'])
            if x is None:s.orders.remove(o);continue
            if (x['szi']>0 and px<=o['trigger']) or (x['szi']<0 and px>=o['trigger']):
                s.orders.remove(o);fp=o['trigger']*(0.9995 if x['szi']>0 else 1.0005);sz=abs(x['szi']);fee=sz*fp*s.fee_rate
                cp=(fp-x['entryPx'])*x['szi'];s.usdc+=cp-fee;s.oid+=1
                s.fills.append(dict(coin=o['coin'],px=str(fp),sz=str(sz),side='A',time=int(time.time()*1000),dir='Close Long' if x['szi']>0 else 'Close Short',closedPnl=str(cp),fee=str(fee),oid=s.oid));del s.pos[o['coin']]

def mk_bars(n=70,end=12.5):
    idx=pd.date_range('2026-10-10',periods=n,freq='4h');c=np.linspace(end*1.02,end,n)+np.sin(np.arange(n))*0.05
    x=pd.DataFrame(dict(o=c*1.001,h=c*1.004,l=c*0.996,c=c,v=1000.0),index=idx);x['closed']=True
    m=x.c.rolling(20).mean();sd=x.c.rolling(20).std(ddof=0);x['mid']=m;x['up']=m+2*sd;x['lo']=m-2*sd;x['pb']=(x.c-x.lo)/(x.up-x.lo);x['bw']=(x.up-x.lo)/m*100;x['vma']=x.v.rolling(20).mean().shift()
    return x
REG=dict(allow=True,fav=True,allow_s=True,fav_s=False,d1_pb=0.15,w1_pb=0.83,fng=(55,'Greed'))
RK=dict(ok=True,score=0.8,why='',text='rk',rank=1,n_cand=1)
def build(dry,equity=7.2,px=11.95,cfg_over=None,cf_base=None):
    cf=copy.deepcopy(cf_base or CF)
    if cfg_over:cf['live'].update(cfg_over)
    hx=FakeHX(dry,equity,px);evs=[];B=os.path.join('/tmp','hl_test_%d/'%os.getpid());os.makedirs(B+'state',exist_ok=True)
    for f in('KILL',):
        try:os.unlink(B+f)
        except OSError:pass
    for f in os.listdir(B+'state'):os.unlink(B+'state/'+f)
    LV=livecore.Live(B,cf,hx,evs.append,dry=dry);LV.sync_ok=True;LV.settle_grace_s=0.0
    CFG['max_pos']=1;CFG['alert_warn']=-250*0.00144;CFG['alert_crit']=-400*0.00144;CFG['cat_stop']=-500*0.00144
    b=livecore.LiveBook(None,sl=cf['stops']['SL250'],name='BASE_SL250_X10');b.peak=0.0;b.ladder=[1500];b.lev=10.0;b.usd=None;b.sgate_on=True;b.hold_mid=False
    b.eps=cf['groups']['BASE']['eps_15m'];b.be_after=cf['groups']['BASE']['be_after_15m'];b.small=cf['groups']['BASE']['small_15m'];b.th=-3;b.th_small=-4
    b.lock_step=cf['lock_milestones']['step'];b.lock_frac=cf['lock_milestones']['frac'];b.trail_cfg=dict(tiers=[tuple(x) for x in cf['trail_15m']['tiers']],mid_buf=None,log_min=0.001)
    b.live=LV;ev=[];b.log=ev.append;b.rlog=[];b.m15={};b.reg=REG;b.rk_fn=lambda c,sd,pth=None:RK
    LV.sync(b,livecore.vn_now());return b,LV,hx,evs,ev
T0=pd.Timestamp('2026-10-17 23:00:00')
def enter(b,LV,hx,px=None,bars=None):
    bars=bars if bars is not None else mk_bars();px=px or hx.px
    b2=bars.iloc[-1].copy();b2['lo']=max(float(b2.lo),px)
    return b.try_entry('LINK',T0,px,bars,REG,rk=RK,side='L',band=b2),bars

class T_dry(unittest.TestCase):
    def test_entry_payloads_and_sizing(self):
        b,LV,hx,evs,ev=build(True);ok,bars=enter(b,LV,hx)
        self.assertIn('LINK',b.pos);p=b.pos['LINK']
        self.assertAlmostEqual(b.leg(0),2.16,6)                              # 30% of 7.2
        self.assertAlmostEqual(p['margin'],p['cost']/10,3);self.assertLessEqual(p['cost'],21.6+1e-9);self.assertGreater(p['cost'],21.3)
        W=[l for l in hx.logs if l['event']=='DRY_RUN_WOULD_SEND'] if False else None
        self.assertEqual(hx.n_sent,0);kinds=[x[0] for x in hx.sent]
        self.assertEqual(kinds[:1],['lev']) ;self.assertIn('ioc',kinds);self.assertIn('stop',kinds)
        ioc=[x for x in hx.sent if x[0]=='ioc'][0];self.assertFalse(ioc[5]);self.assertEqual(ioc[3],1.8)
        stp=[x for x in hx.sent if x[0]=='stop'][0];self.assertTrue(stp[3]==1.8 and stp[2] is False)         # sell-to-close, reduce-only
        self.assertLess(stp[4],p['first']);self.assertAlmostEqual(stp[4],b.stop_px(p),1)
        self.assertTrue(any(e['event']=='LIVE_STOP_SET' for e in evs));self.assertEqual(hx.n_sent,0)
    def test_stop_moves_and_cancel(self):
        b,LV,hx,evs,ev=build(True);enter(b,LV,hx);p=b.pos['LINK'];bars=mk_bars();t=T0
        first=p['live']['stop']['trigger'];trig=[first]
        for px in(12.00,12.04,12.10,12.20,12.15,12.30,12.45):     # up: BE at +0.0576, lock milestones 0.0432*k
            t=t+pd.Timedelta(seconds=30);LV.stop_min_gap=0
            b.walk('LINK',t,[px],bars);LV.stop_sync(b,'LINK',t)
            if 'LINK' not in b.pos:break
            trig.append(b.pos['LINK']['live']['stop']['trigger'])
        self.assertTrue(all(y>=x for x,y in zip(trig,trig[1:])),trig)         # stop only ratchets up for a long
        self.assertGreater(trig[-1],first);self.assertTrue(any(x[0]=='cancel' for x in hx.sent))
        self.assertIn(b.pos['LINK'].get('trail_kind'),('lock30',None,'lock15'))
    def test_sl_dry_close(self):
        b,LV,hx,evs,ev=build(True);enter(b,LV,hx);p=b.pos['LINK'];bars=mk_bars();ps=b.stop_px(p)
        b.walk('LINK',T0+pd.Timedelta(minutes=1),[ps*0.999],bars)
        self.assertNotIn('LINK',b.pos);self.assertTrue(any(e['event']=='STOP' for e in ev));self.assertTrue(any(x[0]=='ioc' and x[5] for x in hx.sent))     # reduce-only close
        tr=b.trades[-1];self.assertLess(tr['net'],0);self.assertGreater(tr['net'],-0.45);self.assertEqual(b.n_stops,1)
    def test_kill_flatten_dry(self):
        b,LV,hx,evs,ev=build(True);hx.pos['LINK']=dict(szi=1.8,entryPx=11.9);out=LV.flatten_all('test')
        self.assertEqual(out[0][0],'LINK');self.assertTrue(any(x[0]=='ioc' and x[5] and x[2] is False for x in hx.sent));self.assertEqual(hx.n_sent,0)
class T_live(unittest.TestCase):
    def test_full_cycle_sl_by_exchange_stop(self):
        b,LV,hx,evs,ev=build(False);enter(b,LV,hx);self.assertIn('LINK',b.pos);p=b.pos['LINK'];bars=mk_bars()
        self.assertEqual(len(hx.orders),1);self.assertEqual(hx.lev['type'],'isolated');self.assertEqual(hx.lev['value'],10)
        self.assertTrue(abs(p['q']-hx.pos['LINK']['szi'])<1e-9);self.assertAlmostEqual(p['first'],hx.pos['LINK']['entryPx'],6)
        ps=b.stop_px(p);u0=hx.usdc
        # engine sees the level crossed first -> VETO (the exchange stop must do it)
        hx.px=ps*0.9995;b.walk('LINK',T0+pd.Timedelta(minutes=1),[hx.px],bars);self.assertIn('LINK',b.pos)
        hx.move(ps*0.999)                                           # exchange fires the stop
        LV.sync(b,T0+pd.Timedelta(minutes=2));self.assertNotIn('LINK',b.pos)
        tr=b.trades[-1];real=hx.usdc-7.2
        self.assertAlmostEqual(tr['net'],real,5);self.assertTrue(tr['stop']);self.assertEqual(hx.orders,[]);self.assertLess(real,0)
        self.assertTrue(any(e['event']=='STOP' for e in ev))
    def test_backstop_when_exchange_stop_does_not_fire(self):
        b,LV,hx,evs,ev=build(False);LV.veto_grace=0.0;enter(b,LV,hx);p=b.pos['LINK'];bars=mk_bars();ps=b.stop_px(p)
        hx.px=ps*0.999;hx.orders.clear()                            # stop vanished/failed: engine must close at market
        for i in range(3):
            b.walk('LINK',T0+pd.Timedelta(minutes=1+i),[hx.px],bars)
            if 'LINK' not in b.pos:break
        self.assertNotIn('LINK',b.pos);self.assertTrue(any(e['event']=='LIVE_STOP_BACKSTOP' for e in evs));self.assertNotIn('LINK',hx.pos)
    def test_stop_level_already_crossed_closes_now(self):
        b,LV,hx,evs,ev=build(False);enter(b,LV,hx);p=b.pos['LINK'];p['trail']=hx.px*1.01;p['trail_kind']='lock30'   # trail above the market: exchange would refuse the trigger
        hx.fail_stop=5;LV.stop_min_gap=0
        r=LV.ensure_stop(b,'LINK',force=True);self.assertEqual(r,'close_now');self.assertTrue(any(e['event']=='LIVE_STOP_CROSSED' for e in evs))
    def test_15m_exit_immediate_market_close(self):
        b,LV,hx,evs,ev=build(False);enter(b,LV,hx);hx.move(12.10)
        b.close('LINK',T0+pd.Timedelta(minutes=20),12.10,'Chốt theo triển vọng chart 15m: test',CFG['fee_taker'],kind='EXIT_15M')
        self.assertNotIn('LINK',b.pos);self.assertNotIn('LINK',hx.pos);self.assertEqual(hx.orders,[]);tr=b.trades[-1];self.assertAlmostEqual(tr['net'],hx.usdc-7.2,5);self.assertGreater(tr['net'],-0.02)
    def test_stop_failure_aborts_entry(self):
        b,LV,hx,evs,ev=build(False);hx.fail_stop=5;enter(b,LV,hx)
        self.assertNotIn('LINK',b.pos);self.assertNotIn('LINK',hx.pos);self.assertTrue(any(e['event']=='LIVE_ENTRY_ABORTED' for e in evs))
    def test_foreign_position_blocks_and_not_touched(self):
        b,LV,hx,evs,ev=build(False);hx.pos['LINK']=dict(szi=2.0,entryPx=11.0);LV.sync(b,T0);self.assertIn('LINK',LV.foreign)
        enter(b,LV,hx);self.assertNotIn('LINK',b.pos);self.assertEqual(hx.pos['LINK']['szi'],2.0);self.assertFalse(any(x[0]=='ioc' for x in hx.sent))
    def test_journal_adopts_orphan(self):
        b,LV,hx,evs,ev=build(False);hx.pos['LINK']=dict(szi=1.8,entryPx=11.9);LV.write_journal(dict(coin='LINK',side='L',sz=1.8,t=time.time()));LV.sync(b,T0)
        self.assertIn('LINK',b.pos);self.assertEqual(LV.foreign,set());self.assertEqual(LV.ensure_stop(b,'LINK',T0),'ok');self.assertEqual(len(hx.orders),1)
    def test_sizing_follows_live_equity(self):
        b,LV,hx,evs,ev=build(False,equity=6.0);self.assertAlmostEqual(b.leg(0),1.8,6);enter(b,LV,hx,px=11.95);p=b.pos['LINK'];self.assertAlmostEqual(p['cost']/10,1.8,1)
        b2,LV2,hx2,_,_=build(False,equity=3.0);enter(b2,LV2,hx2);self.assertNotIn('LINK',b2.pos)      # 30% of 3$ x10 = 9$ < 10$ exchange minimum -> skipped
    def test_max_notional_cap(self):
        b,LV,hx,evs,ev=build(False,equity=20.0);enter(b,LV,hx);p=b.pos['LINK'];self.assertLessEqual(p['cost'],25.0*1.0001);self.assertGreater(p['cost'],23.0)    # 60$ target capped to 25$ (2.0 LINK = 23.9$ after lot rounding down)
    def test_min_order_bump_and_leverage_guard(self):
        b,LV,hx,evs,ev=build(False);hx.assets['LINK']['maxLeverage']=5;enter(b,LV,hx);self.assertNotIn('LINK',b.pos);self.assertTrue(any(e['event']=='LIVE_ENTRY_FAILED' and 'max leverage' in e['info'].get('why','') for e in evs))
    def test_abstraction_guard(self):
        b,LV,hx,evs,ev=build(False);hx.abstraction_='default';LV.sync(b,T0);self.assertIsNotNone(LV.halted);enter(b,LV,hx);self.assertNotIn('LINK',b.pos)
    def test_kill_file(self):
        b,LV,hx,evs,ev=build(False);open(LV.kill_path,'w').close();self.assertTrue(LV.kill_requested());enter(b,LV,hx);self.assertNotIn('LINK',b.pos);os.unlink(LV.kill_path)
        b,LV,hx,evs,ev=build(False);enter(b,LV,hx);out=LV.flatten_all('kill');self.assertNotIn('LINK',hx.pos);self.assertEqual(hx.orders,[]);self.assertTrue(out[0][2])
class T_seed(unittest.TestCase):
    def _bars_map(self):return {'LINK':mk_bars()}
    def test_seed_in_tolerance_enters_even_above_band(self):
        b,LV,hx,evs,ev=build(True,px=11.95);bars=self._bars_map();m={'LINK':11.95}
        c=LV.seed_candidates(b,m,['LINK'],bars,T0);self.assertEqual(len(c),1);cc,sd,b2,z=c[0];self.assertGreaterEqual(b2.lo,11.95)
        b.try_entry('LINK',T0,11.95,bars['LINK'],REG,rk=RK,side='L',band=b2);self.assertIn('LINK',b.pos);self.assertEqual(z['status'],'entered')
    def test_seed_out_of_tolerance_skips_and_blocks(self):
        b,LV,hx,evs,ev=build(True,px=12.10);bars=self._bars_map()
        c=LV.seed_candidates(b,{'LINK':12.10},['LINK'],bars,T0);self.assertEqual(c,[]);self.assertEqual(LV.seeds[0]['status'],'skipped');self.assertTrue(any(e['event']=='LIVE_SEED_SKIPPED' for e in evs))
        b.try_entry('LINK',T0,12.10,bars['LINK'],REG,rk=RK,side='L',band=bars['LINK'].iloc[-1].copy().set_axis(bars['LINK'].columns) if False else None);self.assertNotIn('LINK',b.pos)
    def test_seed_requires_shortlist(self):
        b,LV,hx,evs,ev=build(True);c=LV.seed_candidates(b,{'LINK':11.95},[],self._bars_map(),T0);self.assertEqual(c,[]);self.assertEqual(LV.seeds[0]['status'],'pending')
if __name__=='__main__':unittest.main()
