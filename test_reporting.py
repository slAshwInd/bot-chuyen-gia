import ast
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import reporting as R
import mon_common as M

class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
    def write(self,name,obj):
        (self.root/name).write_text(json.dumps(obj),encoding='utf-8')
    def state(self):
        self.write('state_live.json',{'updated':'2026-11-07 14:00:00','live':{'equity':2200.0,'usdc_total':2200.0,'upnl':0}})
    def test_local_deposit_not_1000(self):
        self.state()
        self.write('monitor_config.json', {'initial_deposit':2000})
        (self.root/'trades_live.csv').write_text('net\n50.0\n',encoding='utf-8')
        txt=R.pnl_text(self.root)
        self.assertIn('2,000.00',txt)
        self.assertIn('+200.00 (+10.00%)',txt)
        self.assertIn('+150.00 USDT',txt)
        self.assertIn('+2.50%',txt)
        self.assertIn('chưa đối soát',txt)
    def test_missing_deposit_is_unknown_not_hardcoded(self):
        self.state()
        self.assertIn('CHƯA CẤU HÌNH',R.pnl_text(self.root))

    def test_config_capital_precedence(self):
        self.state(); self.write('config_live.json',{'start_capital':1200})
        self.assertIn('1,200.00',R.pnl_text(self.root))
    def test_missing_csv_is_unknown_not_zero_or_paper(self):
        self.state()
        self.assertIn('Lãi/lỗ ròng CSV: CHƯA XÁC ĐỊNH',R.pnl_text(self.root))
    def test_invalid_net_not_counted_as_zero(self):
        self.state()
        (self.root/'trades_live.csv').write_text('net\nNaN\nbad\n0\n',encoding='utf-8')
        text=R.pnl_text(self.root)
        self.assertIn('CSV: 1 bản ghi',text)
        self.assertIn('2 dòng lãi/lỗ không hợp lệ',text)
    def test_zero_balance_preserved(self):
        self.write('state_live.json',{'live':{'usdc_total':0,'equity':0,'base':1000,'upnl':0}})
        self.assertIn('Số dư: 0.00',R.pnl_text(self.root))
    def test_interval_boundaries_reasons_and_no_entry(self):
        t0=dt.datetime(2026,11,7,12)
        t1=t0+dt.timedelta(hours=2)
        (self.root/'bot_live.log').write_text(''.join(f'LOOP 2026-11-07 {t} sync_ok=True halted=None equity=1100\n' for t in ['12:00:00','12:01:00','12:02:00','14:00:00']),encoding='utf-8')
        events=[{'time':'2026-11-07 12:00:30','event':'ENTRY','coin':'BNB'},
                {'time':'2026-11-07 12:01:30','event':'ENTRY_BLOCKED_BTC'},
                {'time':'2026-11-07 14:00:00','event':'ENTRY'}]
        (self.root/'events_live.jsonl').write_text('\n'.join(json.dumps(e) for e in events+[events[0]])+'\n',encoding='utf-8')
        text,r=R.summary(self.root,t0,t1,M.parse_loop,M.classify)
        self.assertEqual((r['loops'],r['entries'],r['no_entry']),(3,1,2))
        self.assertEqual(r['blocked'],{'ENTRY_BLOCKED_BTC':1})
        self.assertIn('BTC chặn: 1',text)
        self.assertLess(len(text),1801)
    def test_empty_window_does_not_invent_reason(self):
        t=dt.datetime(2026,11,7)
        text,r=R.summary(self.root,t,t+dt.timedelta(hours=2),M.parse_loop,M.classify,detail=True)
        self.assertEqual(r['loops'],0)
        self.assertIn('không có lý do được ghi',text)
    def test_service_authoritative_query(self):
        class Result:
            stdout='Running|1\n'; returncode=0
        with patch.object(M.subprocess,'run',return_value=Result()) as run:
            self.assertEqual(M.proc_task(),('Running',1))
            ps=run.call_args.args[0][-1]
            self.assertIn('BaseAsterTrade',ps)
            self.assertIn('else{$state=',ps)
            self.assertIn(r'^python(w)?\.exe$',ps)
            self.assertIn(r'\bbnb_bot\.py\b',ps)
    def test_positions_not_lost_in_pnl(self):
        self.write('state_live.json',{'live':{'exchange_positions':{'BNB':{'szi':-2,'entryPx':500,'upnl':3}}}})
        self.assertIn('BNB Bán',R.pnl_text(self.root))
    def test_multiple_bot_processes_are_abnormal(self):
        lp={'ts':dt.datetime.now(),'sync_ok':'True','halted':'None','equity':1100,'pos':'[]'}
        with patch.object(M,'last_loop',return_value=lp),patch.object(M,'proc_task',return_value=('Running',2)),patch.object(M,'cfg',return_value={'stale_loop_s':120}):
            self.assertIn('BẤT THƯỜNG',M.status_text())
    def test_null_command_line_can_use_service_descendant(self):
        class Result:
            stdout='Running|1\n'; returncode=0
        with patch.object(M.subprocess,'run',return_value=Result()) as run:
            M.proc_task()
            ps=run.call_args.args[0][-1]
            self.assertIn('($_.ProcessId -in $ids -or ($_.CommandLine -and',ps)
    def test_valid_json_non_objects_do_not_crash(self):
        self.write('state_live.json',[])
        self.assertIn('không đọc được',R.pnl_text(self.root))
        (self.root/'events_live.jsonl').write_text('[]\nnull\n',encoding='utf-8')
        now=dt.datetime(2026,11,7)
        self.assertEqual(R.summary(self.root,now,now+dt.timedelta(hours=2),M.parse_loop,M.classify)[1]['entries'],0)
    def test_venv_python_shim_is_not_a_duplicate_bot(self):
        class Result:
            stdout='Running|1\n'; returncode=0
        with patch.object(M.subprocess,'run',return_value=Result()) as run:
            M.proc_task()
            self.assertIn('$_.ProcessId -notin $py.ParentProcessId',run.call_args.args[0][-1])
    def test_service_query_denied_is_unknown(self):
        class Result:
            stdout=''; returncode=1
        with patch.object(M.subprocess,'run',return_value=Result()):
            self.assertEqual(M.proc_task(),(None,None))
    def test_status_stale_even_if_service_running(self):
        lp={'ts':dt.datetime.now()-dt.timedelta(minutes=10),'sync_ok':'True','halted':'None','equity':1100,'pos':'[]'}
        with patch.object(M,'last_loop',return_value=lp),patch.object(M,'proc_task',return_value=('Running',1)),patch.object(M,'cfg',return_value={'stale_loop_s':120}):
            self.assertIn('BẤT THƯỜNG',M.status_text())
    def test_status_concise_vietnamese(self):
        lp={'ts':dt.datetime.now(),'sync_ok':'True','halted':'None','equity':2200,'pos':'[]'}
        with patch.object(M,'last_loop',return_value=lp),patch.object(M,'proc_task',return_value=('Running',1)),patch.object(M,'cfg',return_value={'stale_loop_s':120}),patch.object(M,'last_errors',return_value=['2026-11-07 14:20:18 restarting in 30s']):
            text=M.status_text()
        self.assertIn('ĐANG HOẠT ĐỘNG',text)
        self.assertIn('Đồng bộ: ổn | Tạm dừng: không',text)
        self.assertIn('Vị thế: không',text)
        self.assertIn('Sự cố gần nhất (lịch sử): 2026-11-07 14:20:18 · /log',text)
        self.assertNotIn('halted=None',text)
        self.assertNotIn('snapshot',text)
        self.assertLessEqual(len(text.splitlines()),7)
        self.assertLess(len(text),450)
    def test_status_missing_and_halted(self):
        with patch.object(M,'last_loop',return_value=None),patch.object(M,'proc_task',return_value=(None,None)),patch.object(M,'cfg',return_value={'stale_loop_s':120}),patch.object(M,'last_errors',return_value=[]):
            self.assertIn('CHƯA XÁC MINH DỊCH VỤ',M.status_text())
        lp={'ts':dt.datetime.now(),'sync_ok':'False','halted':'RISK_GATE','equity':2200,'pos':'[ASTER]'}
        with patch.object(M,'last_loop',return_value=lp),patch.object(M,'proc_task',return_value=('Running',1)),patch.object(M,'cfg',return_value={'stale_loop_s':120}),patch.object(M,'last_errors',return_value=[]):
            self.assertIn('Đồng bộ: lỗi | Tạm dừng: có (xem /log)',M.status_text())
    def test_120m_schedule_and_restart(self):
        start=dt.datetime(2026,11,7,12)
        state={'next_sum':'legacy'}
        self.assertIsNone(R.due_window(state,start))
        self.assertIsNone(R.due_window(state,start+dt.timedelta(minutes=119)))
        self.assertEqual(R.due_window(state,start+dt.timedelta(minutes=120)),(start,start+dt.timedelta(minutes=120)))
        restored=json.loads(json.dumps(state))
        self.assertIsNone(R.due_window(restored,start+dt.timedelta(minutes=121)))
        self.assertEqual(R.due_window(restored,start+dt.timedelta(minutes=240)),(start+dt.timedelta(minutes=120),start+dt.timedelta(minutes=240)))
    def test_missed_windows_are_combined_not_dropped(self):
        t=dt.datetime(2026,11,7,12); state={}
        R.due_window(state,t)
        self.assertEqual(R.due_window(state,t+dt.timedelta(minutes=361)),(t,t+dt.timedelta(minutes=360)))
        self.assertEqual(state['next_sum'],(t+dt.timedelta(minutes=480)).isoformat())
    def test_malformed_schedule_recovers(self):
        state={'summary_schema':2,'next_sum':'bad'}
        self.assertIsNone(R.due_window(state,dt.datetime(2026,11,7)))
        self.assertIn('summary_start',state)
    def test_all_python_syntax(self):
        for f in Path(__file__).parent.glob('*.py'):
            ast.parse(f.read_text(encoding='utf-8-sig'),filename=f.name)

    def current(self, **kw):
        d={'updated':'2026-11-07 15:30:00','live':{'sync_ok':True,'halted':None,'exchange_positions':{}},'watch':{'ASTER':{'price':10,'lo':9,'up':12,'mid':10.5}},'btc':{'allow':True,'allow_s':False,'d1_pb':0.2,'w1_pb':0.6},'btc_recheck':{'block':False,'block_s':True,'why_s':['tăng mạnh'],'info':{'m15x4':2}},'ranking':{'bb_min':0.5,'coins':{'ASTER':{'score':0.7,'short':{'score':0.8},'stab_ok':True}}}}
        d.update(kw);self.write('state_live.json',d)
        return d
    def test_decision_concrete_bands_btc_and_unknown_rules(self):
        self.current()
        text=R.decision_text(self.root,dt.datetime(2026,11,7,15,30,30))
        for x in ['Mua: chờ giá ≤ 9','Bán hồi: chờ giá ≥ 12','cho mua có, bán không','chặn mua không, bán có','tăng mạnh','MA20','không đồng nghĩa sẽ vào lệnh']:
            self.assertIn(x,text)
    def test_decision_stale_and_missing(self):
        self.assertIn('thiếu dữ liệu',R.decision_text(self.root))
        self.current()
        self.assertIn('CŨ/CHƯA XÁC MINH',R.decision_text(self.root,dt.datetime(2026,11,7,16)))
    def test_unknown_exchange_is_not_flat(self):
        self.current(live={})
        text=R.decision_text(self.root)
        self.assertIn('không mặc định là rỗng',text)
        self.assertNotIn('Chưa có vị thế theo',text)
        self.assertNotIn('Mua: chờ',text)
    def test_h1_trigger_never_uses_4h_band(self):
        self.current(trend1h={'ASTER':{'lo1h':8,'up1h':13}})
        text=R.decision_text(self.root)
        self.assertIn('dải 1 giờ',text)
        self.assertIn('Mua: chờ giá ≤ 8',text)
        self.assertNotIn('Mua: chờ giá ≤ 9',text)
    def test_open_position_next_action_and_active_branch_only(self):
        pos={'ASTER':{'stop_px':9.1,'trail':9.3,'outlook_15m':{'score':-2,'th':-3,'candle':'15:15'},'mid4h_hit':None}}
        self.current(live={'exchange_positions':{'ASTER':{'szi':1,'entryPx':10}}},branches={'LIVE':{'positions':pos,'exit_rule':'15m'},'PAPER':{'positions':{'ASTER':{'stop_px':999}},'exit_rule':'15m'}})
        self.write('config_live.json',{'live':{'branch':'LIVE'},'exit_hold_mid4h':{'enabled':True}})
        text=R.decision_text(self.root)
        for x in ['dừng bảo vệ 9.1','khóa lãi 9.3','Điểm 15 phút: -2 / ngưỡng -3','dải giữa 4 giờ: 10.5','Không có giá chốt cố định']:
            self.assertIn(x,text)
        self.assertNotIn('999',text)
        self.assertNotIn('Mua: chờ',text)
    def test_position_unmatched_is_unknown(self):
        self.current(live={'exchange_positions':{'ASTER':{'szi':-1,'entryPx':10}}})
        self.assertIn('chưa xác định mốc tiếp theo',R.decision_text(self.root))
    def test_summary_keeps_metrics_and_adds_current_section(self):
        self.current()
        t=dt.datetime(2026,11,7,15,31)
        text,stats=R.summary(self.root,t-dt.timedelta(hours=1),t,M.parse_loop,M.classify)
        self.assertEqual(stats['entries'],0)
        self.assertIn('Hiện tại — dữ liệu 2026-11-07 15:30:00',text)
        self.assertLess(len(text),3901)
    def test_decision_does_not_write_or_import_trading(self):
        self.current()
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        R.decision_text(self.root)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        tree=ast.parse(Path(R.__file__).read_text(encoding='utf-8'))
        imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]+[a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
        self.assertFalse(set(imports)&{'strat','bnb_bot','livecore','bnx','tg_notify'})
    def test_action_vietnamese_preserves_reason_and_net(self):
        txt=R.action_text({'event':'EXIT','side':'SHORT','coin':'ASTER','reason':'Lý do gốc','per_branch':{'X':{'net':1.5}}})
        for x in ['Đóng lệnh Bán ASTER','Lý do gốc','lãi/lỗ ròng +1.50']:self.assertIn(x,txt)
    def test_utf8_and_menu_description_lengths(self):
        for command, description in M.COMMANDS:
            self.assertTrue(1<=len(description)<=256)
            self.assertNotIn('\ufffd',description)
        for file in ['reporting.py','mon_common.py','tg_live_poller.py']:
            self.assertNotIn('\ufffd',Path(R.__file__).with_name(file).read_text(encoding='utf-8'))

class BriefTests(unittest.TestCase):
    setUp=ReportingTests.setUp
    write=ReportingTests.write
    current=ReportingTests.current
    def test_brief_is_short_and_uncertain(self):
        self.current(btc={'allow':True,'allow_s':True},btc_recheck={'block':False,'block_s':False})
        text=R.decision_brief(self.root,dt.datetime(2026,11,7,15,30,30))
        for x in ['mua ≤ 9','bán hồi ≥ 12','BTC chưa ghi nhận chặn ngắn hạn','không đảm bảo vào lệnh']:
            self.assertIn(x,text)
        self.assertNotIn('MA20',text)
        self.assertNotIn('CŨ/CHƯA XÁC MINH',text)
        self.assertLess(len(text),650)
    def test_brief_btc_blocks_and_unknown_exchange(self):
        self.current()
        self.assertIn('BTC chặn bán',R.decision_brief(self.root))
        self.current(live={})
        self.assertNotIn('Chưa vào lệnh',R.decision_brief(self.root))
        self.assertIn('không mặc định là rỗng',R.decision_brief(self.root))
    def test_brief_h1_missing_and_touched(self):
        self.current(trend1h={'ASTER':{'lo1h':8,'up1h':13}})
        self.assertIn('mua ≤ 8',R.decision_brief(self.root))
        self.current(watch={})
        self.assertIn('chưa đủ',R.decision_brief(self.root))
        self.current(watch={'ASTER':{'price':8,'lo':9,'up':12}})
        self.assertNotIn('mua ≤ 9',R.decision_brief(self.root))
    def test_compact_and_detail_keep_identical_metrics(self):
        self.current()
        t=dt.datetime(2026,11,7,15,31)
        a,sa=R.summary(self.root,t-dt.timedelta(hours=1),t,M.parse_loop,M.classify)
        b,sb=R.summary(self.root,t-dt.timedelta(hours=1),t,M.parse_loop,M.classify,detail=True)
        self.assertEqual(sa,sb);self.assertLess(len(a),1000);self.assertGreater(len(b),len(a))
        self.assertIn('/summary detail',a)
    def test_freshness_is_not_historical_window_end(self):
        self.current()
        t=dt.datetime(2026,11,7,15,29)
        with patch.object(R,'decision_brief',return_value='brief') as brief:
            R.summary(self.root,t-dt.timedelta(hours=1),t,M.parse_loop,M.classify)
            brief.assert_called_once_with(self.root)
    def test_brief_active_position_never_paper(self):
        self.current(live={'exchange_positions':{'ASTER':{'szi':1,'entryPx':10}}},branches={'LIVE':{'positions':{'ASTER':{'stop_px':9,'trail':9.5}},'exit_rule':'15m'},'PAPER':{'positions':{'ASTER':{'stop_px':999}},'exit_rule':'15m'}})
        self.write('config_live.json',{'live':{'branch':'LIVE'}})
        text=R.decision_brief(self.root)
        self.assertIn('Bảo vệ 9',text);self.assertNotIn('999',text);self.assertIn('nến 15 phút',text)
    def test_brief_stale_missing_halted_no_writes(self):
        self.assertIn('thiếu dữ liệu',R.decision_brief(self.root))
        self.current(live={'sync_ok':False,'halted':'RISK','exchange_positions':{}})
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        text=R.decision_brief(self.root,dt.datetime(2026,11,7,16))
        self.assertIn('CŨ/CHƯA XÁC MINH',text);self.assertIn('tạm dừng',text)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})

if __name__=='__main__': unittest.main(verbosity=2)
