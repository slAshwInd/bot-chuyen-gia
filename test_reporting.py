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
        self.assertIn('CHUA DOI SOAT',txt)
    def test_missing_deposit_is_unknown_not_hardcoded(self):
        self.state()
        self.assertIn('CHUA CAU HINH',R.pnl_text(self.root))

    def test_config_capital_precedence(self):
        self.state(); self.write('config_live.json',{'start_capital':1200})
        self.assertIn('1,200.00',R.pnl_text(self.root))
    def test_missing_csv_is_unknown_not_zero_or_paper(self):
        self.state()
        self.assertIn('Net CSV: KHONG XAC DINH',R.pnl_text(self.root))
    def test_invalid_net_not_counted_as_zero(self):
        self.state()
        (self.root/'trades_live.csv').write_text('net\nNaN\nbad\n0\n',encoding='utf-8')
        text=R.pnl_text(self.root)
        self.assertIn('CSV: 1 ban ghi',text)
        self.assertIn('2 dong net khong hop le',text)
    def test_zero_balance_preserved(self):
        self.write('state_live.json',{'live':{'usdc_total':0,'equity':0,'base':1000,'upnl':0}})
        self.assertIn('Balance: 0.00',R.pnl_text(self.root))
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
        self.assertIn('BTC chan: 1',text)
        self.assertLess(len(text),1801)
    def test_empty_window_does_not_invent_reason(self):
        t=dt.datetime(2026,11,7)
        text,r=R.summary(self.root,t,t+dt.timedelta(hours=2),M.parse_loop,M.classify)
        self.assertEqual(r['loops'],0)
        self.assertIn('khong co ly do duoc ghi',text)
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
        self.assertIn('BNB SHORT',R.pnl_text(self.root))
    def test_multiple_bot_processes_are_abnormal(self):
        lp={'ts':dt.datetime.now(),'sync_ok':'True','halted':'None','equity':1100,'pos':'[]'}
        with patch.object(M,'last_loop',return_value=lp),patch.object(M,'proc_task',return_value=('Running',2)),patch.object(M,'cfg',return_value={'stale_loop_s':120}):
            self.assertIn('BAT THUONG',M.status_text())
    def test_null_command_line_can_use_service_descendant(self):
        class Result:
            stdout='Running|1\n'; returncode=0
        with patch.object(M.subprocess,'run',return_value=Result()) as run:
            M.proc_task()
            ps=run.call_args.args[0][-1]
            self.assertIn('($_.ProcessId -in $ids -or ($_.CommandLine -and',ps)
    def test_valid_json_non_objects_do_not_crash(self):
        self.write('state_live.json',[])
        self.assertIn('khong doc duoc',R.pnl_text(self.root))
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
            self.assertIn('BAT THUONG',M.status_text())
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

if __name__=='__main__': unittest.main(verbosity=2)
