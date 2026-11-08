"""Deterministic offline tests for trendmr (TrendMR-1H). No network, no exchange, no Telegram.
Covers: context build, trend-aligned entry decisions (incl. counter-trend control), paper exit
engine (SL / TP / BE / TIME), settle math, sleeve sizing, per-coin toggles and the BNB bb_k split.
"""
import os, sys, json, math, time, unittest, tempfile, shutil
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
_o = requests.Session.post
def _block(self, url, *a, **k):
    raise AssertionError('TEST TRIED TO POST: ' + str(url))
requests.Session.post = _block

import trendmr
from trendmr import build_context, entry_signal, paper_step, settle, sleeve, DEF
from trendmr import prep15_frame, score15_last, score_exit_check
from trendmr import gross_notional, throttle_mult, review_evaluate
from hl import bb as bb_fn


def mk_frame(closes, start='2026-01-01 00:00:00'):
    idx = pd.date_range(start, periods=len(closes), freq='h')
    c = np.array(closes, dtype=float)
    df = pd.DataFrame({'o': c, 'h': c * 1.001, 'l': c * 0.999, 'c': c, 'v': 1.0, 'closed': True}, index=idx)
    return df


def uptrend(n=220, rate=0.002, base=100.0):
    return [base * (1 + rate) ** i for i in range(n)]


class FakeLive:
    dry = True

    def __init__(self):
        self.foreign = set(); self.events = []

    def ev(self, event, coin='*', reason='', **kw):
        e = dict(event=event, coin=coin, reason=reason, **kw)
        self.events.append(e)
        return e


def fake_candles(frame):
    def f(coin, interval='1h', days=14):
        assert interval == '1h'
        return frame.copy()
    return f


def mk_module(tmp, frame, cfg_extra=None):
    cfg = {'trendmr': dict(dry_run=True), 'coins': {}}
    if cfg_extra:
        for k, v in cfg_extra.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    live = FakeLive()
    notes = []
    m = trendmr.TrendMR(tmp + os.sep, cfg, live, fake_candles(frame), bb_fn, notes.append, lambda e: None)
    return m, live, notes


class TestContextAndSignal(unittest.TestCase):
    def test_context_uptrend(self):
        x = bb_fn(mk_frame(uptrend()))
        ctx = build_context(x, DEF)
        self.assertIsNotNone(ctx)
        self.assertGreater(ctx['last_close'], ctx['ema'])
        self.assertGreater(ctx['lo'], 0)
        self.assertLess(ctx['lo'], ctx['mid'])
        self.assertLess(ctx['mid'], ctx['up'])
        # 4h mid (SMA20 of complete 4h closes) feeds the Book15 C2 gate: in an uptrend it trails price
        self.assertGreater(ctx['mid4'], 0)
        self.assertLess(ctx['mid4'], ctx['last_close'])

    def test_long_entry_on_band_touch_uptrend(self):
        x = bb_fn(mk_frame(uptrend()))
        ctx = build_context(x, DEF)
        side, det = entry_signal(ctx, ctx['lo'] * 0.999, False, False, DEF)
        self.assertEqual(side, 'L')

    def test_long_blocked_in_downtrend(self):
        closes = [100.0 * (0.998) ** i for i in range(220)]
        x = bb_fn(mk_frame(closes))
        ctx = build_context(x, DEF)
        self.assertLess(ctx['last_close'], ctx['ema'])
        side, det = entry_signal(ctx, ctx['lo'] * 0.999, False, False, DEF)
        self.assertIsNone(side)
        self.assertEqual(det['why'], 'trend_down_long_blocked')

    def test_countertrend_control_loses(self):
        # mirror image: a lower-band touch while trend is DOWN must not long; upper touch while UP must not short
        closes = [100.0 * (0.998) ** i for i in range(220)]
        ctx = build_context(bb_fn(mk_frame(closes)), DEF)
        side, _ = entry_signal(ctx, ctx['up'] * 1.001, False, False, DEF)
        self.assertEqual(side, 'S')  # short WITH the downtrend is allowed
        ctx2 = build_context(bb_fn(mk_frame(uptrend())), DEF)
        side2, det2 = entry_signal(ctx2, ctx2['up'] * 1.001, False, False, DEF)
        self.assertIsNone(side2)
        self.assertEqual(det2['why'], 'trend_up_short_blocked')

    def test_fresh_touch_required(self):
        ctx = build_context(bb_fn(mk_frame(uptrend())), DEF)
        side, det = entry_signal(ctx, ctx['lo'] * 0.999, True, False, DEF)
        self.assertIsNone(side)
        self.assertEqual(det['why'], 'not_fresh_long')

    def test_no_touch_no_entry(self):
        ctx = build_context(bb_fn(mk_frame(uptrend())), DEF)
        side, det = entry_signal(ctx, ctx['mid'], False, False, DEF)
        self.assertIsNone(side)


class TestPaperEngine(unittest.TestCase):
    def pos(self, side='L', entry=100.0):
        g = 1 if side == 'L' else -1
        return dict(side=side, entry=entry, q=10.0, notional=1000.0, fee_in=0.2,
                    stop=entry * (1 - g * 0.015), tp=101.5, be_done=False, peak_move=0.0, t0_ts=time.time())

    def test_sl_exit_with_slippage(self):
        p = self.pos()
        reason, px = paper_step(p, 98.4, time.time(), DEF)
        self.assertEqual(reason, 'SL')
        self.assertAlmostEqual(px, p['stop'] * (1 - 0.0005), places=6)

    def test_be_arms_then_exit_at_be(self):
        p = self.pos()
        reason, _ = paper_step(p, 100.7, time.time(), DEF)   # +0.7% excursion arms BE
        self.assertIsNone(reason)
        self.assertTrue(p['be_done'])
        self.assertAlmostEqual(p['stop'], 100.0 * 1.002, places=6)
        reason, px = paper_step(p, 100.1, time.time(), DEF)  # falls back to BE stop
        self.assertEqual(reason, 'BE/TRAIL')   # stop exit AFTER BE armed is reported as BE/TRAIL, not SL
        self.assertGreater(px, 100.0)

    def test_tp_at_mid_band(self):
        p = self.pos()
        reason, px = paper_step(p, 101.6, time.time(), DEF)
        self.assertEqual(reason, 'TP')
        self.assertEqual(px, 101.5)

    def test_time_exit_only_when_configured(self):
        # BaseAster has NO time exit: default max_hold_h=0 -> an old position is still held
        p = self.pos()
        p['t0_ts'] = time.time() - 121 * 3600
        reason, _ = paper_step(p, 100.2, time.time(), DEF)
        self.assertIsNone(reason)
        # ...but the cap still works when a config explicitly sets it
        reason, px = paper_step(p, 100.2, time.time(), dict(DEF, max_hold_h=120))
        self.assertEqual(reason, 'TIME')

    def test_milestone_lock_ratchets_stop(self):
        p = self.pos()
        reason, _ = paper_step(p, 101.3, time.time(), DEF)   # peak excursion +1.3% -> milestone 1% -> lock >= +0.667%
        self.assertIsNone(reason)
        self.assertTrue(p.get('lock_armed'))
        self.assertAlmostEqual(p['stop'], 100.0 * (1 + 0.01 * (2 / 3)), places=4)
        reason, px = paper_step(p, 100.5, time.time(), DEF)  # falls back to the locked stop: profitable exit
        self.assertEqual(reason, 'BE/TRAIL')
        net, gross, _ = settle(p, px, DEF)
        self.assertGreater(net, 0)

    def test_settle_math(self):
        p = self.pos()
        net, gross, fee_out = settle(p, 101.5, DEF)
        self.assertAlmostEqual(gross, 15.0, places=6)
        self.assertAlmostEqual(fee_out, 101.5 * 10 * 0.0005, places=6)
        self.assertAlmostEqual(net, 15.0 - 0.2 - fee_out, places=6)

    def test_settle_fee_by_exit_type(self):
        # Binance futures semantics (same as the backtest engine): TP is a resting limit -> maker fee;
        # market-type exits (stop, EXIT_15M) -> taker fee. Rates: regular tier 0.02% / 0.05%.
        p = self.pos()
        _, _, fee_tp = settle(p, 101.5, DEF, 'TP')
        self.assertAlmostEqual(fee_tp, 101.5 * 10 * 0.0002, places=6)
        for r in ('SL', 'BE/TRAIL', 'EXIT_15M', 'TIME'):
            _, _, fee_mkt = settle(p, 101.5, DEF, r)
            self.assertAlmostEqual(fee_mkt, 101.5 * 10 * 0.0005, places=6)

    def test_sleeve_split(self):
        self.assertAlmostEqual(sleeve(5000.0, 3), 5000.0 / 3)
        self.assertEqual(sleeve(5000.0, 0), 0.0)


class TestModule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_open_and_tp_close_cycle(self):
        frame = mk_frame(uptrend())
        m, live, notes = mk_module(self.tmp, frame)
        self.assertEqual(sorted(m.coins), ['BNB', 'BTC', 'ETH'])
        ctx = build_context(bb_fn(frame.copy()), DEF)
        tnow = trendmr.vn_now()
        m.tick(tnow, {'BTC': ctx['lo'] * 0.999, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        pos = m.cs['BTC']['pos']
        self.assertIsNotNone(pos)
        # 1.3.3 G2 compounding sizing: first position takes 50% of budget = equity 5,000 x 5
        self.assertAlmostEqual(pos['notional'], 25000.0 * 0.5, places=2)
        self.assertAlmostEqual(pos['entry'], ctx['lo'], places=6)
        # price reaches the mid band -> TP close, trade written to CSV, realized > 0
        m.tick(tnow, {'BTC': pos['tp'] * 1.001, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        self.assertIsNone(m.cs['BTC']['pos'])
        self.assertGreater(m.realized, 0)
        rows = pd.read_csv(self.tmp + '/data/trendmr_trades.csv')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]['reason'], 'TP')
        evs = [e['event'] for e in live.events]
        self.assertIn('TRENDMR_ENTRY', evs)
        self.assertIn('TRENDMR_EXIT', evs)

    def test_master_toggle_disables_coin(self):
        frame = mk_frame(uptrend())
        m, _, _ = mk_module(self.tmp, frame, {'coins': {'BTC': {'enabled': False}}})
        self.assertNotIn('BTC', m.coins)
        self.assertIn('ETH', m.coins)

    def test_bnb_uses_bb_k_25(self):
        frame = mk_frame(uptrend())
        m, _, _ = mk_module(self.tmp, frame)
        self.assertEqual(m.pcfg['BNB']['bb_k'], 2.5)
        self.assertEqual(m.pcfg['BTC']['bb_k'], 2.0)
        self.assertEqual(m.pcfg['ETH']['bb_k'], 2.0)

    def test_aster_off_by_default(self):
        frame = mk_frame(uptrend())
        m, _, _ = mk_module(self.tmp, frame)
        self.assertNotIn('ASTER', m.coins)

    def test_context_rebuilt_on_restart_within_same_bar(self):
        # state saved by a previous process already knows the last closed bar; a restarted module
        # must still build its context on the first tick, not wait for the next bar to close
        frame = mk_frame(uptrend())
        ctx = build_context(bb_fn(frame.copy()), DEF)
        state = dict(realized=0.0, peak_equity=5000.0, day=dict(date='2026-11-08', realized=0.0),
                     coins={c: dict(pos=None, consec_sl=0, prev_touch_long=False, prev_touch_short=False,
                                    last_bar=ctx['bar_t']) for c in ('BTC', 'ETH', 'BNB')})
        os.makedirs(self.tmp + '/state', exist_ok=True)
        with open(self.tmp + '/state/trendmr.json', 'w', encoding='utf-8') as f_:
            json.dump(state, f_)
        m, _, _ = mk_module(self.tmp, frame)
        self.assertIsNone(m.cs['BTC']['ctx'])
        m.tick(trendmr.vn_now(), {'BTC': ctx['mid'], 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        self.assertIsNotNone(m.cs['BTC']['ctx'])
        self.assertIsNotNone(m.cs['BTC']['ctx']['mid4'])

    def test_daily_loss_alerts_but_never_blocks(self):
        # BaseAster risk philosophy: a bad day raises an alert, entries keep flowing
        frame = mk_frame(uptrend())
        m, live, notes = mk_module(self.tmp, frame)
        m.day['realized'] = -1000.0   # way past the -3% daily alert threshold
        ctx = build_context(bb_fn(frame.copy()), DEF)
        m.tick(trendmr.vn_now(), {'BTC': ctx['lo'] * 0.999, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        self.assertIsNotNone(m.cs['BTC']['pos'])
        self.assertIn('TRENDMR_ALERT', [e['event'] for e in live.events])

    def test_bnb_short_half_size(self):
        closes = [100.0 * (0.998) ** i for i in range(220)]
        frame = mk_frame(closes)
        m, _, _ = mk_module(self.tmp, frame)
        ctx = build_context(bb_fn(frame.copy(), 20, 2.5), m.pcfg['BNB'])   # BNB bands use bb_k=2.5 like the module
        m.tick(trendmr.vn_now(), {'BNB': ctx['up'] * 1.001, 'BTC': ctx['mid'], 'ETH': ctx['mid']})
        pos = m.cs['BNB']['pos']
        self.assertIsNotNone(pos)
        self.assertEqual(pos['side'], 'S')
        # G2: 50% share of the 25,000 gross budget, then the BNB-short 0.5 factor
        self.assertAlmostEqual(pos['notional'], 25000.0 * 0.5 * 0.5, places=2)


class TestSizingAndGovernance(unittest.TestCase):
    """1.3.3: G2 compounding gross-budget sizing + DD throttle + the performance review gate.
    1.3.4: allocation by remaining budget (50%/60%/100% of what is left, cap 50% of budget)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gross_shares_and_throttle_pure(self):
        # 1.3.4 remaining-budget allocation with deployed omitted = clean full-size deployment,
        # which must reproduce the old 50/30/20 slot shares exactly.
        n, b, m = gross_notional(5000.0, 5000.0, 0, DEF)
        self.assertAlmostEqual(b, 25000.0); self.assertAlmostEqual(n, 12500.0); self.assertEqual(m, 1.0)
        n2, _, _ = gross_notional(5000.0, 5000.0, 1, DEF)
        self.assertAlmostEqual(n2, 7500.0)
        n3, _, _ = gross_notional(5000.0, 5000.0, 2, DEF)
        self.assertAlmostEqual(n3, 5000.0)
        self.assertEqual(throttle_mult(4400.0, 5000.0, DEF), 0.5)    # -12% underwater
        self.assertEqual(throttle_mult(3900.0, 5000.0, DEF), 0.25)   # -22% underwater
        self.assertEqual(throttle_mult(4900.0, 5000.0, DEF), 1.0)

    def test_gross_remaining_budget_and_cap(self):
        # 1.3.4: shares are taken from the REMAINING budget, per-position cap = 50% of budget.
        # Small first position (the live 2026-11-08 transition case: BNB sleeve 833$ open):
        # slot rule gave the 2nd position 7,500$; remaining rule gives 60% x (25,000-833) = 14,500
        # -> capped at 12,500$.
        n, b, _ = gross_notional(5000.0, 5000.0, 1, DEF, deployed=833.33)
        self.assertAlmostEqual(b, 25000.0); self.assertAlmostEqual(n, 12500.0)
        # Full-size first position: 60% of the remaining 12,500 = 7,500 (the old 30% share).
        n, _, _ = gross_notional(5000.0, 5000.0, 1, DEF, deployed=12500.0)
        self.assertAlmostEqual(n, 7500.0)
        # Third position takes all that remains: 25,000 - 20,000 = 5,000 (the old 20% share).
        n, _, _ = gross_notional(5000.0, 5000.0, 2, DEF, deployed=20000.0)
        self.assertAlmostEqual(n, 5000.0)
        # Third position after two small positions is still capped at 50% of the budget.
        n, _, _ = gross_notional(5000.0, 5000.0, 2, DEF, deployed=8333.33)
        self.assertAlmostEqual(n, 12500.0)
        # Nothing left (deployed >= budget, e.g. throttle shrank the budget): no new notional.
        n, _, _ = gross_notional(5000.0, 5000.0, 2, DEF, deployed=26000.0)
        self.assertAlmostEqual(n, 0.0)

    def test_second_position_gets_30pct_share(self):
        frame = mk_frame(uptrend())
        m, _, _ = mk_module(self.tmp, frame)
        ctx = build_context(bb_fn(frame.copy()), DEF)
        m.tick(trendmr.vn_now(), {'BTC': ctx['lo'] * 0.999, 'ETH': ctx['lo'] * 0.999, 'BNB': ctx['mid']})
        self.assertAlmostEqual(m.cs['BTC']['pos']['notional'], 12500.0, places=2)
        self.assertAlmostEqual(m.cs['ETH']['pos']['notional'], 7500.0, places=2)

    def test_budget_compounds_with_realized(self):
        frame = mk_frame(uptrend())
        m, _, _ = mk_module(self.tmp, frame)
        m.realized = 1000.0   # equity 6,000 -> budget 30,000 -> first share 15,000
        ctx = build_context(bb_fn(frame.copy()), DEF)
        m.tick(trendmr.vn_now(), {'BTC': ctx['lo'] * 0.999, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        self.assertAlmostEqual(m.cs['BTC']['pos']['notional'], 15000.0, places=2)

    def test_throttle_shrinks_new_entries_when_underwater(self):
        frame = mk_frame(uptrend())
        m, _, _ = mk_module(self.tmp, frame)
        m.realized = -700.0   # equity 4,300 vs peak 5,000 = -14% -> budget factor x0.5
        ctx = build_context(bb_fn(frame.copy()), DEF)
        m.tick(trendmr.vn_now(), {'BTC': ctx['lo'] * 0.999, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        self.assertAlmostEqual(m.cs['BTC']['pos']['notional'], 4300.0 * 5 * 0.5 * 0.5, places=2)

    def test_review_evaluate_tiers(self):
        ok = dict(n=150, wins=105, gp=130.0, gl=100.0, net=30.0)
        self.assertEqual(review_evaluate(ok, [], DEF), [])
        self.assertEqual(review_evaluate(dict(n=29, wins=0, gp=0.0, gl=10.0, net=-10.0), [], DEF), [])
        bad_wr = dict(n=30, wins=15, gp=80.0, gl=100.0, net=-20.0)
        self.assertTrue(any('winrate' in r for r in review_evaluate(bad_wr, [], DEF)))
        bad_pf = dict(n=150, wins=105, gp=110.0, gl=100.0, net=10.0)
        self.assertTrue(any('PF lũy kế' in r for r in review_evaluate(bad_pf, [], DEF)))
        bad_net = dict(n=75, wins=50, gp=100.0, gl=90.0, net=-5.0)
        rs = review_evaluate(bad_net, [], DEF)
        self.assertEqual(len(rs), 1); self.assertIn('PnL lũy kế', rs[0])
        roll = [1.0] * 25 + [-2.0] * 25
        rs = review_evaluate(dict(n=10, wins=5, gp=5.0, gl=5.0, net=0.0), roll, DEF)
        self.assertTrue(any('trượt' in r for r in rs))

    def test_review_hold_blocks_entries_until_cleared(self):
        frame = mk_frame(uptrend())
        m, live, _ = mk_module(self.tmp, frame)
        m.stats = dict(n=30, wins=15, gp=80.0, gl=100.0, net=-20.0)
        m._review_check(trendmr.vn_now())
        self.assertTrue(m.review_hold)
        self.assertIn('TRENDMR_REVIEW_HOLD', [e['event'] for e in live.events])
        ctx = build_context(bb_fn(frame.copy()), DEF)
        m.tick(trendmr.vn_now(), {'BTC': ctx['lo'] * 0.999, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        self.assertIsNone(m.cs['BTC']['pos'])   # held: no new entry
        self.assertTrue(m.clear_review_hold('test'))
        m.tick(trendmr.vn_now(), {'BTC': ctx['lo'] * 0.999, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        self.assertIsNotNone(m.cs['BTC']['pos'])   # resumed after approval


def mk15_frame(closes, start='2026-01-01 00:00:00'):
    idx = pd.date_range(start, periods=len(closes), freq='15min')
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0] * 1.001], c[:-1]])
    df = pd.DataFrame({'o': o, 'h': np.maximum(o, c) * 1.0002, 'l': np.minimum(o, c) * 0.9998,
                       'c': c, 'v': 1.0, 'closed': True}, index=idx)
    return df


class TestBook15Helpers(unittest.TestCase):
    def test_score15_directional(self):
        up = prep15_frame(bb_fn(mk15_frame([100.0 * (1.002) ** i for i in range(130)])))
        dn = prep15_frame(bb_fn(mk15_frame([100.0 * (0.998) ** i for i in range(130)])))
        self.assertGreaterEqual(score15_last(up, 'L'), 3)
        self.assertLessEqual(score15_last(up, 'S'), -3)
        self.assertGreaterEqual(score15_last(dn, 'S'), 3)
        self.assertLessEqual(score15_last(dn, 'L'), -3)

    def test_score15_insufficient_data(self):
        x = prep15_frame(bb_fn(mk15_frame([100.0] * 50)))
        self.assertIsNone(score15_last(x, 'L'))

    def test_exit_check_needs_two_consecutive_weak(self):
        fire, weak = score_exit_check(30.0, -5, True, False, DEF)
        self.assertFalse(fire); self.assertTrue(weak)
        fire, weak = score_exit_check(30.0, -5, True, True, DEF)
        self.assertTrue(fire); self.assertTrue(weak)

    def test_exit_check_gate_and_eps_reset_weak(self):
        # 4h-mid gate not hit yet -> never fires, weak flag resets (walk15 semantics)
        self.assertEqual(score_exit_check(30.0, -5, False, True, DEF), (False, False))
        # unrealised does not cover the eps floor -> not evaluated
        self.assertEqual(score_exit_check(5.0, -5, True, True, DEF), (False, False))

    def test_exit_check_threshold_switches_at_small(self):
        # uz < small (60$) -> th_small -4: a -3 score is NOT weak, -4 is
        self.assertEqual(score_exit_check(30.0, -3, True, False, DEF), (False, False))
        self.assertEqual(score_exit_check(30.0, -4, True, False, DEF), (False, True))
        # uz >= small -> th -3 applies
        self.assertEqual(score_exit_check(70.0, -3, True, False, DEF), (False, True))

    def test_exit_check_disabled(self):
        self.assertEqual(score_exit_check(30.0, -5, True, True, dict(DEF, book15_exit=False)), (False, False))


class TestBook15Module(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exit_15m_closes_after_two_weak_candles(self):
        frame1h = mk_frame(uptrend())
        ctx = build_context(bb_fn(frame1h.copy()), DEF)
        entry = ctx['lo']
        target = entry * 1.01   # +1% mark: uz ~ +16$ (> eps 8, < small 60 -> th_small -4)

        def dn15(n, last):
            return mk15_frame([last * (1.002) ** (n - 1 - i) for i in range(n)])

        feed = {'15m': dn15(130, target)}

        def candles(coin, interval='1h', days=14):
            if interval == '1h':
                return frame1h.copy()
            assert interval == '15m'
            return feed['15m'].copy()

        live = FakeLive()
        m = trendmr.TrendMR(self.tmp + os.sep, {'trendmr': dict(dry_run=True), 'coins': {}},
                            live, candles, bb_fn, lambda msg: None, lambda e: None)
        tnow = trendmr.vn_now()
        m.tick(tnow, {'BTC': entry * 0.999, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        pos = m.cs['BTC']['pos']
        self.assertIsNotNone(pos)
        # first weak 15m candle: evaluated, flagged, but NOT closed (needs two in a row)
        m.tick(tnow, {'BTC': target, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        pos = m.cs['BTC']['pos']
        self.assertIsNotNone(pos)
        self.assertTrue(pos['mid4h_hit'])
        self.assertTrue(pos['weak_prev'])
        self.assertLessEqual(pos['outlook']['score'], -4)
        # second consecutive weak candle (new close, forced refetch past the 15s throttle)
        feed['15m'] = dn15(131, target * 0.998)
        m.fetch15_t['BTC'] = 0
        m.tick(tnow, {'BTC': target, 'ETH': ctx['mid'], 'BNB': ctx['mid']})
        self.assertIsNone(m.cs['BTC']['pos'])
        self.assertGreater(m.realized, 0)
        rows = pd.read_csv(self.tmp + '/data/trendmr_trades.csv')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]['reason'], 'EXIT_15M')


if __name__ == '__main__':
    unittest.main()
