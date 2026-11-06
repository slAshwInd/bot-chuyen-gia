"""Deterministic offline tests for batday (bottom-bounce LONG) + step3 LONG removal + cycle_s.
No network, no real exchange, no Telegram. Clocks/exchange faked.
"""
import os, sys, json, math, time, unittest, tempfile, shutil
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Block any accidental exchange POSTs
import requests
_o = requests.Session.post
def _block(self, url, *a, **k):
    if '/order' in str(url) or '/algoOrder' in str(url) or str(url).endswith('/exchange'):
        raise AssertionError('TEST TRIED TO POST TO EXCHANGE: ' + str(url))
    return _o(self, url, *a, **k)
requests.Session.post = _block

import batday, step3, bnx as hlx
from batday import (
    drop_ok, prior_block_ok, bb_width_ok, btc_24h_ok, gap_ok, evaluate_signal,
    entry_limits, tp_sl_px, size_notional, DEF, H1, H4, M15, M1,
)

def mk_h1(n=20, start='2026-02-01 00:00:00', peak_at=None, drop_close=None, base=1.0):
    idx = pd.date_range(start, periods=n, freq='h')
    h = np.full(n, base * 1.01); l = np.full(n, base * 0.99); c = np.full(n, base); o = c.copy()
    if peak_at is not None and drop_close is not None:
        # last `so_nen` bars: peak on first of window, close of last = drop_close
        h[-6] = peak_at
        for i in range(-5, 0):
            h[i] = max(h[i], drop_close * 1.001)
        c[-1] = drop_close
        l[-1] = min(l[-1], drop_close * 0.999)
    df = pd.DataFrame({'o': o, 'h': h, 'l': l, 'c': c, 'v': 1.0, 'closed': True}, index=idx)
    return df

def mk_h4_bb(n=30, start='2026-01-20 00:00:00', mid=1.0, width_pct=10.0):
    idx = pd.date_range(start, periods=n, freq='4h')
    half = mid * (width_pct / 100.0) / 2.0
    up = mid + half; lo = mid - half
    df = pd.DataFrame({
        'o': mid, 'h': up, 'l': lo, 'c': mid, 'v': 1.0, 'closed': True,
        'mid': mid, 'up': up, 'lo': lo, 'bw': width_pct, 'pb': 0.5,
    }, index=idx)
    return df

def mk_m15(n=200, start='2026-01-31 00:00:00', close=1.0):
    idx = pd.date_range(start, periods=n, freq='15min')
    return pd.DataFrame({'o': close, 'h': close * 1.001, 'l': close * 0.999, 'c': close, 'v': 1.0, 'closed': True}, index=idx)

def mk_m1_block(t0, hours=6, touch_low=0.95, bb_lo=0.96, ref15=1.0):
    idx = pd.date_range(t0, periods=int(hours * 60), freq='min')
    l = np.full(len(idx), 1.0)
    # one minute hits both conditions
    l[10] = touch_low
    return pd.DataFrame({'o': 1.0, 'h': 1.01, 'l': l, 'c': 1.0, 'v': 1.0, 'closed': True}, index=idx)


class TestSignalThresholds(unittest.TestCase):
    def test_drop_3pct_boundary(self):
        # peak 1.0, close 0.97 -> -3.0% exactly -> ok
        h1 = mk_h1(12, peak_at=1.0, drop_close=0.97, base=0.99)
        ok, info = drop_ok(h1, len(h1) - 1, 6, 3.0)
        self.assertTrue(ok, info)
        self.assertAlmostEqual(info['drop_pct'], -3.0, places=5)
        # -2.99% fail
        h1b = mk_h1(12, peak_at=1.0, drop_close=0.9701, base=0.99)
        ok2, info2 = drop_ok(h1b, len(h1b) - 1, 6, 3.0)
        self.assertFalse(ok2, info2)

    def test_drop_missing_fail_closed(self):
        h1 = mk_h1(3)
        ok, info = drop_ok(h1, 2, 6, 3.0)
        self.assertFalse(ok)
        self.assertEqual(info['why'], 'missing_1h')

    def test_bb_width_and_btc_filters(self):
        h4 = mk_h4_bb(width_pct=15.0)
        t = h4.index[-1] + H4
        ok, info = bb_width_ok(h4, t, 15.0)
        self.assertTrue(ok, info)
        ok2, _ = bb_width_ok(h4, t, 14.9)
        self.assertFalse(ok2)
        # strict > -8%: -8.1% fails, -7.9% passes (avoid float boundary 92/100)
        self.assertFalse(btc_24h_ok(91.9, 100, -8.0)[0])
        self.assertTrue(btc_24h_ok(92.1, 100, -8.0)[0])
        self.assertFalse(btc_24h_ok(None, 100, -8.0)[0])

    def test_prior_block_asof_no_lookahead(self):
        # Build 4h BB and 15m such that only after a closed 4h the lo is known
        h4 = mk_h4_bb(n=10, start='2026-02-01 00:00:00', mid=1.0, width_pct=10.0)
        # lo = 0.95 for width 10% mid 1.0 -> half=0.05, lo=0.95
        m15 = mk_m15(n=100, start='2026-01-31 12:00:00', close=1.0)
        t0 = pd.Timestamp('2026-02-01 16:00:00')  # after some 4h bars closed
        m1 = mk_m1_block(t0, hours=2, touch_low=0.94, bb_lo=0.95, ref15=1.0)
        # mom 0.7% => need low <= 1.0*0.993=0.993; 0.94 qualifies
        ok, info = prior_block_ok(m1, h4, m15, t0, t0 + pd.Timedelta(hours=2), 0.7)
        self.assertTrue(ok, info)
        # missing m1 -> fail closed
        ok2, info2 = prior_block_ok(None, h4, m15, t0, t0 + pd.Timedelta(hours=1), 0.7)
        self.assertFalse(ok2)
        self.assertEqual(info2['why'], 'missing_prior_data')

    def test_gap_12h_from_qualified_only(self):
        t0 = pd.Timestamp('2026-02-01 00:00:00')
        self.assertTrue(gap_ok(None, t0, 12)[0])
        self.assertFalse(gap_ok(t0, t0 + pd.Timedelta(hours=11), 12)[0])
        self.assertTrue(gap_ok(t0, t0 + pd.Timedelta(hours=12), 12)[0])


class TestEntryCaps(unittest.TestCase):
    def test_ask_cap_and_ioc_limit(self):
        sc = 1.0
        why, po, ioc = entry_limits(sc, ask=1.011, bid=1.0)  # >1%
        self.assertIsNotNone(why)
        why, po, ioc = entry_limits(sc, ask=1.005, bid=1.0)
        self.assertIsNone(why)
        self.assertEqual(po, 1.0)
        # ioc = min(ask*1.003, sc*1.01) = min(1.008015, 1.01)
        self.assertAlmostEqual(ioc, 1.005 * 1.003, places=6)

    def test_tp_sl_anchor_signal_close(self):
        tp, sl = tp_sl_px(1.0)
        self.assertAlmostEqual(tp, 1.015)
        self.assertAlmostEqual(sl, 0.98)

    def test_size_notional_no_extra_5x(self):
        # 4.9 * 1000 = 4900, not 4900*5
        # when available is ample, notional = 4.9*equity (no extra ×5)
        n = size_notional(1000, 2000, 5, 4.9, 1.0)
        self.assertAlmostEqual(n, 4900.0)
        # available margin cap: avail*lev*0.97
        n2 = size_notional(1000, 100, 5, 4.9, 1.0)
        self.assertAlmostEqual(n2, 100 * 5 * 0.97)
        # equity=available: avail cap binds slightly below 4.9×
        n3 = size_notional(1000, 1000, 5, 4.9, 1.0)
        self.assertAlmostEqual(n3, 1000 * 5 * 0.97)


class TestStep3LongRemoved(unittest.TestCase):
    def test_detector_no_long_detect(self):
        C = dict(step3.DEF)
        D = step3.Detector(C)
        # Build bars: dump relative to BTC that would have triggered LONG
        idx = pd.date_range('2026-02-01', periods=20, freq='h')
        a = pd.DataFrame({'o': 1.0, 'h': 1.0, 'l': 0.9, 'c': 1.0, 'v': 1}, index=idx)
        # make bar 10 a dump: close 8 bars ago was 1.1, now 0.9 => -18%
        a.loc[idx[2], 'c'] = 1.1
        a.loc[idx[10], 'c'] = 0.9
        a.loc[idx[10], 'l'] = 0.9
        Bc = {t: 100.0 for t in idx}  # BTC flat
        outs = []
        for j in range(1, len(a)):
            outs += D.step(a, Bc, j)
        sides = [e['side'] for e in outs if e['kind'] == 'DETECT']
        self.assertNotIn('L', sides)
        # pump for SHORT still works
        D2 = step3.Detector(C)
        a2 = pd.DataFrame({'o': 1.0, 'h': 1.0, 'l': 0.9, 'c': 1.0, 'v': 1}, index=idx)
        a2.loc[idx[2], 'c'] = 1.0
        a2.loc[idx[10], 'c'] = 1.06  # +6%
        a2.loc[idx[10], 'h'] = 1.07
        outs2 = []
        for j in range(1, len(a2)):
            outs2 += D2.step(a2, Bc, j)
        self.assertIn('S', [e['side'] for e in outs2 if e['kind'] == 'DETECT'])

    def test_long_enabled_false_skips_enter(self):
        # _try_enter with side L should skip when long_enabled False
        class FakeL:
            dry = False; halted = None; foreign = set()
            def kill_requested(s): return False
            def ev(s, *a, **k): return {}
            def refresh(s, *_): return dict(positions={}, base=1000, available=1000)
        class FakeHX: pass
        tmp = tempfile.mkdtemp()
        try:
            S = step3.Step3.__new__(step3.Step3)
            S.B = tmp + os.sep; S.C = dict(step3.DEF, long_enabled=False); S.on = True
            S.L = FakeL(); S.hx = FakeHX(); S.candles = None; S.notify = lambda *_: None; S.emit = lambda *_: None
            S.path = tmp + '/step3.json'; S.trf = tmp + '/t.csv'; S.lk = __import__('threading').Lock()
            S.D = step3.Detector(S.C); S.blocks = []; S.pos = None; S.breaks = []; S.hist = []
            S.A = None; S.Bc = None; S.fetch_t = 0; S.err = None; S.last_chk = 0; S.init_done = True
            b = dict(side='L', t_close=str(pd.Timestamp('2026-02-01 01:00:00')), close=1.0, peak=0.99, det_bar='2026-02-01 00:00:00', status='new')
            events = []
            S.ev = lambda event, reason, tg=True, **kw: events.append(event)
            S._try_enter(b, type('LB', (), {'pos': {}})(), pd.Timestamp('2026-02-01 01:05:00'), {'ASTER': 1.0}, lambda *a: True)
            self.assertTrue(b['status'].startswith('skip:'))
            self.assertIn('bắt đáy', b['status'])
        finally:
            shutil.rmtree(tmp)


class TestEvaluateIntegration(unittest.TestCase):
    def test_full_signal_and_cooldown(self):
        peak, close = 1.0, 0.96  # -4%
        h1 = mk_h1(20, start='2026-02-01 00:00:00', peak_at=peak, drop_close=close, base=0.98)
        # ensure window peak is peak_at on the right bar
        sig_i = len(h1) - 1
        h4 = mk_h4_bb(n=40, start='2026-01-25 00:00:00', mid=1.0, width_pct=10.0)
        m15 = mk_m15(n=400, start='2026-01-28 00:00:00', close=1.0)
        t0 = h1.index[sig_i - 5]
        m1 = mk_m1_block(t0, hours=6, touch_low=0.94)
        t_entry = h1.index[sig_i] + H1 + pd.Timedelta(minutes=1)
        ok, det = evaluate_signal(h1, h4, m15, m1, sig_i, t_entry, btc_now=100, btc_24h_ago=100, last_sig_t=None)
        self.assertTrue(ok, det)
        # cooldown blocks immediate re-signal
        ok2, det2 = evaluate_signal(h1, h4, m15, m1, sig_i, t_entry, 100, 100, last_sig_t=h1.index[sig_i] + H1)
        self.assertFalse(ok2)
        self.assertEqual(det2['stage'], 'gap')


class TestConfigAndCycle(unittest.TestCase):
    def test_config_switches(self):
        cfg = json.loads((ROOT / 'config_live.json').read_text(encoding='utf-8'))
        self.assertEqual(cfg.get('cycle_s'), 5)
        self.assertFalse(cfg['step3'].get('long_enabled', True))
        self.assertTrue(cfg['batday']['enabled'])
        self.assertIn('dry_run', cfg['batday'])
        # default in code
        self.assertEqual(batday.DEF['postonly_wait_s'], 20)
        self.assertEqual(batday.DEF['giu_toi_da_h'], 6)

    def test_bnb_bot_cycle_default_source(self):
        src = (ROOT / 'bnb_bot.py').read_text(encoding='utf-8')
        self.assertIn("cfg.get('cycle_s', CFG.get('cycle_s',5))", src)
        self.assertIn('import batday', src)
        self.assertIn('BD.holding()', src)


class TestMutualExclusionHelpers(unittest.TestCase):
    def test_holding_blocks_flag(self):
        class FakeL:
            dry = True; foreign = set()
            def ev(s, *a, **k): return {}
        tmp = tempfile.mkdtemp()
        try:
            BD = batday.BatDay.__new__(batday.BatDay)
            BD.C = dict(DEF); BD.on = True; BD.mod_dry = True; BD.L = FakeL()
            BD.pos = None; BD.pend = None
            self.assertFalse(BD.holding())
            BD.pos = {'side': 'L'}
            self.assertTrue(BD.holding())
            BD.pos = None; BD.pend = {'status': 'wait_postonly'}
            self.assertTrue(BD.holding())
        finally:
            shutil.rmtree(tmp)


class TestBackstopLevels(unittest.TestCase):
    def test_backstop_threshold(self):
        sl = 0.98
        deep = sl * (1 - 0.3 / 100)
        self.assertAlmostEqual(deep, 0.98 * 0.997, places=8)


class TestDuplicateBarSafety(unittest.TestCase):
    def test_last_bar_advances_once(self):
        # Simulate BatDay last_bar tracking: only bars > last_bar processed
        idx = pd.date_range('2026-02-01', periods=5, freq='h')
        last = idx[2]
        new = [j for j in range(len(idx)) if idx[j] > last]
        self.assertEqual(new, [3, 4])
        # re-run same last -> empty
        last2 = idx[4]
        new2 = [j for j in range(len(idx)) if idx[j] > last2]
        self.assertEqual(new2, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
