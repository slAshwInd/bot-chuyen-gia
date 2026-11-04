"""Binance adapter tests (no network, no keys; nothing is sent). Filters = real exchangeInfo sample taken on DESKTOP-PJNO57A 2026-10-22."""
import os,sys,json,unittest
D=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,os.path.dirname(D))
os.environ['BNB_DRY_RUN']='1'
import bnx,hl
EI={x['symbol']:x for x in json.load(open(os.path.join(D,'fixture_ei_sample.json'),encoding='utf-8-sig'))}
SP={hl.coin_of(k):bnx.spec_from_symbol(v) for k,v in EI.items()}
class T(unittest.TestCase):
    def test_specs(self):
        self.assertEqual(SP['SOL'].min_notional,5.0);self.assertEqual(SP['LINK'].min_notional,20.0);self.assertEqual(float(SP['DOGE'].step),1.0)
    def test_px_round(self):
        self.assertEqual(bnx.px_round(123.4567,SP['SOL'],'up'),123.46);self.assertEqual(bnx.px_round(123.4567,SP['SOL'],'down'),123.45)
        self.assertAlmostEqual(bnx.px_round(0.123456,SP['DOGE'],'nearest'),0.12346)
        for c,sp in SP.items():
            for px in(0.0123456,1.23456,17.7777,987.654):
                if px<float(sp.tick)*10:continue
                q=bnx.px_round(px,sp,'down');n=float(bnx.Decimal(repr(q))/sp.tick)
                self.assertAlmostEqual(n,round(n),6)
    def test_sz_round_down(self):
        self.assertEqual(bnx.sz_round(12.999,SP['SOL']),12.99);self.assertEqual(bnx.sz_round(99.9,SP['DOGE']),99.0);self.assertEqual(bnx.sz_round(0.0999,SP['XRP']),0.0)
    def test_fmt(self):
        self.assertEqual(bnx.fmt(0.12,bnx.Decimal('0.000010')),'0.12000');self.assertEqual(bnx.fmt(3.0,bnx.Decimal('1')),'3')
    def test_plan_ok(self):
        sz,n,note=bnx.plan_size(57.0,180.0,SP['SOL'],100.0)
        self.assertEqual(sz,0.31);self.assertLessEqual(n,57.0)
    def test_skip_below_min_notional_no_upsize(self):
        sz,n,note=bnx.plan_size(4.9,180.0,SP['SOL'],100.0);self.assertEqual(sz,0.0);self.assertIn('SKIP',note)
        sz,n,note=bnx.plan_size(15.0,15.0,SP['LINK'],100.0);self.assertEqual(sz,0.0);self.assertIn('minimum 20',note)     # LINK minimum is 20$
    def test_skip_below_min_qty(self):
        sz,n,note=bnx.plan_size(6.0,10.0,SP['AVAX'],100.0);self.assertEqual(sz,0.0);self.assertIn('SKIP',note)          # 0.6 AVAX < step 1
    def test_cap(self):
        sz,n,note=bnx.plan_size(500.0,1.0,SP['DOGE'],100.0);self.assertEqual(sz,100.0);self.assertIn('capped',note)
    def _client(self):
        c=bnx.BNX(dry_run=True,key=('x'*64,'y'*64),max_open_notional=100.0);sent=[]
        c.log=lambda e:sent.append(e)
        c.assets={k:dict(asset=k+'USDT',szDecimals=v,spec=v,maxLeverage=20) for k,v in SP.items()};c.meta_t=1e18
        def boom(*a,**k):raise AssertionError('network/sign call in dry run')
        c.signed=boom;return c,sent
    def test_stop_payload(self):
        c,sent=self._client();r=c.stop('SOL',False,0.31,171.234,None,'STOP SL SOL')
        self.assertTrue(r['dry']);p=sent[-1]['payload']
        self.assertEqual(p['path'],'/fapi/v1/algoOrder');q=p['params']
        self.assertEqual((q['algoType'],q['type'],q['side'],q['closePosition'],q['workingType'],q['triggerPrice']),('CONDITIONAL','STOP_MARKET','SELL','true','MARK_PRICE','171.23'))
        self.assertNotIn('quantity',q)
        c.stop('SOL',True,0.31,190.0,None);self.assertEqual(sent[-1]['payload']['params']['side'],'BUY')
    def test_ioc_payload_and_guard(self):
        c,sent=self._client();c.ioc('DOGE',True,300,0.12,False,'ENTRY')
        q=sent[-1]['payload']['params'];self.assertEqual((q['type'],q['timeInForce'],q['quantity'],q['price']),('LIMIT','IOC','300','0.12000'));self.assertNotIn('reduceOnly',q)
        with self.assertRaises(bnx.HLXError):c.ioc('DOGE',True,1000,0.12,False)          # 120$ > max_open_notional 100
        c.ioc('DOGE',False,5000,0.12,True,'CLOSE');self.assertEqual(sent[-1]['payload']['params']['reduceOnly'],'true')   # reduce-only not guarded
    def test_leverage_isolated(self):
        c,sent=self._client();r=c.set_leverage('SOL',5,cross=False)
        self.assertEqual(r['status'],'ok');ps=[e['payload'] for e in sent]
        self.assertEqual(ps[0]['params'],dict(symbol='SOLUSDT',marginType='ISOLATED'));self.assertEqual(ps[1]['params'],dict(symbol='SOLUSDT',leverage=5))
    def test_cancel_algo_vs_regular(self):
        c,sent=self._client();c.cancel('SOL','algo:123');self.assertEqual(sent[-1]['payload']['path'],'/fapi/v1/algoOrder')
        c.cancel('SOL',456);self.assertEqual(sent[-1]['payload']['path'],'/fapi/v1/order')
    def test_order_result(self):
        st=bnx.order_result_to_status(dict(orderId=9,status='FILLED',executedQty='0.31',avgPrice='180.1'))
        self.assertEqual(bnx.parse_statuses(bnx._ok([st]))[0],dict(kind='filled',oid=9,avgPx=180.1,totalSz=0.31))
        st=bnx.order_result_to_status(dict(orderId=9,status='EXPIRED',executedQty='0',avgPrice='0'));self.assertEqual(bnx.parse_statuses(bnx._ok([st]))[0]['kind'],'error')
    def test_live_needs_env(self):
        os.environ['BNB_DRY_RUN']='1';c=bnx.BNX(dry_run=False,key=('x'*64,'y'*64));self.assertTrue(c.dry_run)
    def test_key_loading_no_print(self):
        import tempfile
        d=tempfile.mkdtemp();os.environ['BNB_KEY_DIR']=d
        for k in('BNB_API_KEY','BNB_API_SECRET'):os.environ.pop(k,None)
        with self.assertRaises(bnx.HLXError):bnx.load_key()
        open(os.path.join(d,'binance_api_key.txt'),'w').write('A'*64);open(os.path.join(d,'binance_api_secret.txt'),'w').write('S'*64)
        k,s=bnx.load_key();self.assertEqual((len(k),len(s)),(64,64))
        open(os.path.join(d,'binance_api_secret.txt'),'w').write('short')
        try:bnx.load_key();self.fail()
        except bnx.HLXError as ex:self.assertNotIn('short',str(ex));self.assertNotIn('AAAA',str(ex))
        os.environ.pop('BNB_KEY_DIR')
    def test_nokey_user_state_halts(self):
        c=bnx.BNX(dry_run=True,key=('',''));c._k=None;st=c.user_state();self.assertEqual(st['abstraction'],'nokey');self.assertEqual(st['base'],0.0)
if __name__=='__main__':unittest.main()
