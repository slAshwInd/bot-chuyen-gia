"""Integration-style offline tests: real BatDay / Step3 methods against a FAKE exchange + FAKE clock.
No network, no keys, no Telegram, bot main() never started."""
import os, sys, json, tempfile, shutil, unittest, importlib.util
from pathlib import Path
import pandas as pd, numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
def _no_net(*a, **k): raise AssertionError('network call in offline test')
requests.Session.request = _no_net
import batday, step3, bnx as hlx
batday.time.sleep = lambda *_: None

SPEC = hlx.Spec(tick='0.0001', step='1', min_qty=1, min_notional=5)

class FakeHX:
    def __init__(s):
        s.bid, s.ask = 0.6990, 0.7000
        s.pos = {}  # coin -> dict(szi, entryPx)
        s.oo = []; s.calls = []; s.seq = 100
        s.po_mode = 'rest'; s.ioc_fill = 'all'; s.cancel_fail = 0; s.stop_fail = 0; s.max_open_notional = 0
    def _id(s): s.seq += 1; return s.seq
    def asset(s, c): return dict(spec=SPEC, asset=c + 'USDT', szDecimals=0)
    def min_notional(s, c): return 5.0
    def book(s, c): return s.bid, s.ask
    def set_leverage(s, *a, **k): s.calls.append(('lev',)); return {'status': 'ok'}
    def _fill(s, c, q, px):
        x = s.pos.get(c)
        if x: n = x['szi'] + q; x['entryPx'] = (x['entryPx'] * x['szi'] + px * q) / n; x['szi'] = n
        else: s.pos[c] = dict(szi=q, entryPx=px)
    def limit_postonly(s, c, is_buy, sz, px, reduce_only=False, label=''):
        s._guard(sz * px); s.calls.append(('postonly', sz, px))
        if s.po_mode == 'expire': return hlx._ok([{'error': 'order EXPIRED (not filled)'}])
        if s.po_mode == 'apierr': return {'status': 'err', 'response': {'code': -2019}}
        if s.po_mode == 'raise': raise hlx.HLXError('timeout after send')
        oid = s._id(); s.oo.append(dict(coin=c, oid=oid, isTrigger=False, reduceOnly=False, sz=sz, px=px, side='BUY'))
        return hlx._ok([{'resting': {'oid': oid}}])
    def _guard(s, n):
        if n > s.max_open_notional * 1.02: raise hlx.HLXError('GUARD')
    def ioc(s, c, is_buy, sz, px, reduce_only=False, label=''):
        s._guard(sz * px); s.calls.append(('ioc', sz, px))
        q = sz if s.ioc_fill == 'all' else float(s.ioc_fill)
        if q <= 0: return hlx._ok([{'error': 'order EXPIRED (not filled)'}])
        s._fill(c, q, s.ask); return hlx._ok([{'filled': {'oid': s._id(), 'avgPx': s.ask, 'totalSz': q}}])
    def stop(s, c, close_is_buy, sz, trig, lim=None, label=''):
        s.calls.append(('stop', trig))
        if s.stop_fail: s.stop_fail -= 1; return {'status': 'err', 'response': {'code': -1}}
        oid = 'algo:%d' % s._id(); s.oo.append(dict(coin=c, oid=oid, isTrigger=True, reduceOnly=True, triggerPx=trig))
        return hlx._ok([{'resting': {'oid': oid}}])
    def limit_reduce(s, c, is_buy, sz, px, label=''):
        s.calls.append(('tp', sz, px)); oid = s._id()
        s.oo.append(dict(coin=c, oid=oid, isTrigger=False, reduceOnly=True, sz=sz, px=px)); return hlx._ok([{'resting': {'oid': oid}}])
    def cancel(s, c, oid, label=''):
        s.calls.append(('cancel', oid))
        if s.cancel_fail: s.cancel_fail -= 1; raise hlx.HLXError('cancel timeout')
        if not any(str(o['oid']) == str(oid) for o in s.oo): raise hlx.HLXError('-2011 unknown order')
        s.oo = [o for o in s.oo if str(o['oid']) != str(oid)]; return hlx._ok(['success'])
    def open_orders(s): return [dict(o) for o in s.oo]
    def n(s, k): return sum(1 for x in s.calls if x[0] == k)
    def maker_fill(s, c, q):  # part of the resting post-only fills
        o = next(o for o in s.oo if not o['reduceOnly'] and not o['isTrigger'])
        s._fill(c, q, o['px']); o['sz'] -= q
        if o['sz'] <= 0: s.oo.remove(o)

class FakeLive:
    def __init__(s, hx):
        s.hx = hx; s.avail = 2000.0; s.dry = False; s.foreign = set(); s.halted = None; s.evs = []; s.closes = []; s.st = None
    def ev(s, event, coin='*', reason='', **kw): e = dict(event=event, reason=reason, **kw); s.evs.append(e); return e
    def refresh(s, max_age=2.0):
        s.st = dict(base=1000.0, available=s.avail, positions={k: dict(v) for k, v in s.hx.pos.items() if v['szi'] != 0}); return s.st
    def read_journal(s): return {}
    def kill_requested(s): return False
    def fill_of_oid(s, c, oid, avg, fsz, fee): return avg, fsz, fee
    def market_close(s, c, p, reason='', szi=None): s.closes.append(reason); s.hx.pos.pop(c, None); return True
    def cancel_coin_orders(s, c): s.hx.oo = [o for o in s.hx.oo if o['coin'] != c]
    def collect(s, c, p): return dict(px=0.69, net=-1.0, fee=0.1)

SC = 0.70000  # signal close
T_CLOSE = pd.Timestamp('2026-11-06 10:00:00')
DET = dict(signal_close=SC, signal_bar=str(T_CLOSE - pd.Timedelta('1h')), t_close=str(T_CLOSE), drop_pct=-3.5)

class Base(unittest.TestCase):
    def setUp(s):
        s.dir = tempfile.mkdtemp() + '/'; os.makedirs(s.dir + 'state'); os.makedirs(s.dir + 'data')
        s.hx = FakeHX(); s.L = FakeLive(s.hx); s.clock = [1_000_000.0]; s.tg = []
        s.bd = s.mk()
    def mk(s):
        bd = batday.BatDay(s.dir, {'batday': {}}, s.L, None, None, s.tg.append, lambda e: None)
        bd._now = lambda: s.clock[0]; return bd
    def tearDown(s): shutil.rmtree(s.dir, ignore_errors=True)
    def at(s, sec): s.clock[0] = 1_000_000.0 + sec; return T_CLOSE + pd.Timedelta(seconds=10 + sec)
    def start(s): s.bd._start_entry(dict(DET), s.at(0), {})
    def opening_orders(s): return [o for o in s.hx.oo if not o['reduceOnly'] and not o['isTrigger']]

class TestEntry(Base):
    def test_postonly_waits_20s_then_ioc_capped(s):
        s.start()
        s.assertEqual(s.hx.n('postonly'), 1); po = s.hx.calls[1]
        s.assertEqual(po[2], 0.699)  # bid, rounded down
        s.assertAlmostEqual(po[1] * max(po[2], SC), 4900, delta=1)  # 4.9x of 1000 (available not binding)
        s.bd._progress_entry(None, s.at(10), {})
        s.assertEqual(s.hx.n('ioc') + s.hx.n('cancel'), 0)
        s.bd._progress_entry(None, s.at(20), {})
        s.assertEqual(s.hx.n('ioc'), 1); s.assertEqual(s.opening_orders(), [])
        lim = [c for c in s.hx.calls if c[0] == 'ioc'][0][2]
        s.assertLessEqual(lim, min(0.7 * 1.003, SC * 1.01) + 1e-12)
        p = s.bd.pos; s.assertIsNotNone(p); s.assertIsNone(s.bd.pend)
        s.assertAlmostEqual(p['tp'], SC * 1.015); s.assertAlmostEqual(p['sl'], SC * 0.98)  # anchored on signal close
        s.assertEqual(s.hx.n('stop'), 1); s.assertEqual(s.hx.n('tp'), 1)
        s.assertEqual([c for c in s.hx.calls if c[0] == 'tp'][0][1], s.hx.pos['ASTER']['szi'])
        s.assertEqual(pd.Timestamp(p['until']) - pd.Timestamp(p['t0']), pd.Timedelta('6h'))

    def test_size_capped_by_available(s):
        s.L.avail = 1000.0; s.start(); po = s.hx.calls[1]
        s.assertLessEqual(po[1] * max(po[2], SC), 1000 * 5 * 0.97 + 1e-9)

    def test_ioc_rounding_never_above_cap(s):
        s.hx.ask = 0.70695  # ask*1.003 > close*1.01=0.707 -> cap 0.707, must not round up
        s.start(); s.bd._progress_entry(None, s.at(21), {})
        lim = [c for c in s.hx.calls if c[0] == 'ioc'][0][2]
        s.assertLessEqual(lim, SC * 1.01 + 1e-12)

    def test_partial_maker_fill_protected_then_resized(s):
        s.start(); target = s.bd.pend['sz_target']
        s.hx.maker_fill('ASTER', 3000)
        s.bd._progress_entry(None, s.at(5), {})
        s.assertEqual(s.hx.n('stop'), 1)
        tps = [c for c in s.hx.calls if c[0] == 'tp']; s.assertEqual(tps[-1][1], 3000)
        s.assertEqual(s.hx.n('ioc'), 0)
        s.bd._progress_entry(None, s.at(20), {})
        s.assertEqual([c for c in s.hx.calls if c[0] == 'ioc'][0][1], target - 3000)
        s.assertEqual(s.hx.n('stop'), 1)  # SL closePosition reused, no duplicate stop
        rest = [o for o in s.hx.oo if o['reduceOnly'] and not o['isTrigger']]
        s.assertEqual(len(rest), 1); s.assertEqual(rest[0]['sz'], target)  # single TP sized to the whole position
        s.assertEqual(len([o for o in s.hx.oo if o['isTrigger']]), 1)
        s.assertEqual(s.bd.pos['q'], target)

    def test_full_maker_fill_cancels_rest_no_ioc(s):
        s.start(); s.hx.maker_fill('ASTER', s.bd.pend['sz_target'])
        s.bd._progress_entry(None, s.at(3), {})
        s.assertIsNotNone(s.bd.pos); s.assertEqual(s.hx.n('ioc'), 0); s.assertEqual(s.opening_orders(), [])

    def test_cancel_unconfirmed_blocks_ioc(s):
        s.start(); s.hx.cancel_fail = 3  # cancel times out, order stays open
        s.bd._progress_entry(None, s.at(20), {})
        s.assertEqual(s.hx.n('ioc'), 0); s.assertEqual(s.bd.pend['status'], 'cancel_pending')
        s.bd._progress_entry(None, s.at(25), {})
        s.assertEqual(s.hx.n('ioc'), 1); s.assertIsNotNone(s.bd.pos)

    def test_ask_too_far_no_ioc_and_clean_abort(s):
        s.start(); s.hx.ask = SC * 1.012
        s.bd._progress_entry(None, s.at(20), {})
        s.assertEqual(s.hx.n('ioc'), 0); s.assertIsNone(s.bd.pend); s.assertIsNone(s.bd.pos)
        s.assertEqual(s.hx.oo, []); s.assertNotIn('ASTER', s.L.foreign)

    def test_ask_too_far_at_signal_skips(s):
        s.hx.ask = SC * 1.0101; s.start()
        s.assertEqual(s.hx.n('postonly'), 0); s.assertIsNone(s.bd.pend)

    def test_3min_window_no_ioc_late(s):
        s.start(); s.bd.pend['t_close'] = str(s.at(0) - pd.Timedelta(seconds=170))
        s.bd._progress_entry(None, s.at(15), {})  # age 185s > 180
        s.assertEqual(s.hx.n('ioc'), 0); s.assertIsNone(s.bd.pend); s.assertEqual(s.hx.oo, [])

    def test_postonly_expired_goes_ioc(s):
        s.hx.po_mode = 'expire'; s.start()
        s.assertEqual(s.hx.n('ioc'), 1); s.assertIsNotNone(s.bd.pos)

    def test_postonly_api_error_send_unknown_reconciles(s):
        s.hx.po_mode = 'raise'; s.start()
        s.assertEqual(s.hx.n('ioc'), 0); s.assertIsNone(s.bd.pend); s.assertIsNone(s.bd.pos)

    def test_partial_sl_failure_stops_adding_and_closes(s):
        s.start(); s.hx.maker_fill('ASTER', 2000); s.hx.stop_fail = 99
        s.bd._progress_entry(None, s.at(5), {})
        s.bd._progress_entry(None, s.at(6), {})
        s.assertEqual(s.hx.n('ioc'), 0)
        s.assertTrue(s.L.closes); s.assertIsNone(s.bd.pos); s.assertNotIn('ASTER', s.hx.pos)

    def test_restart_after_ioc_sent_no_duplicate(s):
        s.start(); s.bd._finish_entry  # noqa
        s.bd.pend['ioc_sent'] = True; s.bd.save()
        bd2 = s.mk(); s.assertIn('ASTER', s.L.foreign)
        bd2._progress_entry(None, s.at(21), {})
        s.assertEqual(s.hx.n('ioc'), 0)

    def test_restart_while_placing_reconciles_without_ioc(s):
        s.start(); s.hx.maker_fill('ASTER', 1500)
        s.bd.pend['status'] = 'placing'; s.bd.save()
        bd2 = s.mk(); bd2._progress_entry(None, s.at(2), {})
        s.assertEqual(s.hx.n('ioc'), 0); s.assertEqual(s.opening_orders(), [])
        s.assertEqual(bd2.pos['q'], 1500); s.assertEqual(s.hx.n('stop'), 1)

class TestManage(Base):
    def open_pos(s):
        s.start(); s.bd._progress_entry(None, s.at(20), {}); return s.bd.pos
    def test_6h_from_fill(s):
        p = s.open_pos(); t0 = pd.Timestamp(p['t0'])
        s.bd.last_chk = 1e18
        s.bd._manage(t0 + pd.Timedelta('5h59min'), {'ASTER': 0.70}); s.assertFalse(s.L.closes)
        s.bd.L.st = None
        s.bd._manage(t0 + pd.Timedelta('6h'), {'ASTER': 0.70}); s.assertEqual(len(s.L.closes), 1)
        s.assertIsNone(s.bd.pos)
    def test_backstop_continuous_resets(s):
        s.open_pos(); s.bd.last_chk = 1e18; deep = SC * 0.98 * 0.997; t = T_CLOSE
        def tick(sec, px): s.clock[0] = 2_000_000 + sec; s.L.refresh(); s.bd._manage(t, {'ASTER': px})
        tick(0, deep * 0.999); tick(20, deep * 0.999); tick(22, deep * 1.0005)  # rebound resets
        tick(30, deep * 0.999); tick(50, deep * 0.999); s.assertFalse(s.L.closes)
        tick(55, deep * 0.999); s.assertEqual(len(s.L.closes), 1)
    def test_tp_partial_fill_resizes_tp_only(s):
        p = s.open_pos(); s.bd.last_chk = 1e18
        s.hx.pos['ASTER']['szi'] -= 1000; s.L.refresh()
        s.bd._manage(T_CLOSE, {'ASTER': 0.70})
        tp = [o for o in s.hx.oo if o['reduceOnly'] and not o['isTrigger']]
        s.assertEqual(len(tp), 1); s.assertEqual(tp[0]['sz'], s.hx.pos['ASTER']['szi'])
        s.assertEqual(s.hx.n('stop'), 1)
    def test_exchange_flat_settles_and_cleans(s):
        s.open_pos(); s.hx.pos.pop('ASTER'); s.L.refresh(); s.clock[0] += 60
        s.bd._manage(T_CLOSE, {}); s.assertIsNone(s.bd.pos); s.assertEqual(s.hx.oo, [])
        s.assertNotIn('ASTER', s.L.foreign)
    def test_missing_sl_replaced_once(s):
        s.open_pos(); s.hx.oo = [o for o in s.hx.oo if not o['isTrigger']]; s.bd.last_chk = 0; s.clock[0] += 100
        s.L.refresh(); s.bd._manage(T_CLOSE, {'ASTER': 0.70})
        s.assertEqual(len([o for o in s.hx.oo if o['isTrigger']]), 1)
        s.assertEqual(len([o for o in s.hx.oo if o['reduceOnly'] and not o['isTrigger']]), 1)

class TestSignalFlow(Base):
    def run_bar(s, ok, busy=False, stage='bb_width'):
        A = pd.DataFrame({'h': [1.0] * 8, 'c': [1.0] * 8}, index=pd.date_range('2026-11-06 02:00', periods=8, freq='h'))
        s.bd.A = A; s.bd._m1_window = lambda *a: None
        orig = batday.drop_ok, batday.evaluate_signal
        batday.drop_ok = lambda *a, **k: (True, {})
        batday.evaluate_signal = lambda *a, **k: (ok, dict(DET, stage='ok' if ok else stage, bw_pct=5, btc_chg_24h=-1))
        started = []
        s.bd._start_entry = lambda *a: started.append(1)
        try:
            s.bd._on_closed_bar(7, None, A.index[7] + pd.Timedelta('1h30s'), {}, lambda: (not busy, 'BaseAster đang giữ ASTER'))
        finally:
            batday.drop_ok, batday.evaluate_signal = orig
        return started
    def test_busy_still_sets_cooldown(s):
        st = s.run_bar(True, busy=True); s.assertEqual(st, []); s.assertIsNotNone(s.bd.last_sig_t)
        s.assertIsNotNone(json.loads(Path(s.dir + 'state/batday.json').read_text())['last_sig_t'])
    def test_failed_filter_no_cooldown(s):
        st = s.run_bar(False); s.assertEqual(st, []); s.assertIsNone(s.bd.last_sig_t)
    def test_flat_starts_entry(s):
        s.assertEqual(s.run_bar(True), [1])
    def test_btc_strict(s):
        s.assertFalse(batday.btc_24h_ok(92.0, 100.0)[0]); s.assertTrue(batday.btc_24h_ok(92.0001, 100.0)[0])
    def test_bb_lower_no_lookahead_at_4h_close(s):
        # 4h bar 00:00-04:00 (closes 04:00) has a HIGH lower band; earlier bar low band. Minute 03:59 must use the earlier bar.
        h4 = pd.DataFrame({'mid': [1.0, 1.0], 'up': [1.05, 1.05], 'lo': [0.90, 0.99]}, index=pd.to_datetime(['2026-11-05 20:00', '2026-11-06 00:00']))
        m15 = pd.DataFrame({'c': [1.0] * 40}, index=pd.date_range('2026-11-05 18:00', periods=40, freq='15min'))
        m1 = pd.DataFrame({'l': [0.95]}, index=pd.to_datetime(['2026-11-06 03:59']))
        s.assertFalse(batday.prior_block_ok(m1, h4, m15, '2026-11-06 03:00', '2026-11-06 04:05')[0])
        m1b = pd.DataFrame({'l': [0.95]}, index=pd.to_datetime(['2026-11-06 04:00']))
        s.assertTrue(batday.prior_block_ok(m1b, h4, m15, '2026-11-06 03:00', '2026-11-06 04:05')[0])

def _load_base_step3():
    p = Path('/tmp/base/step3.py')
    if not p.exists(): return None
    spec = importlib.util.spec_from_file_location('step3_base', p); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

class TestStep3Short(unittest.TestCase):
    def data(s, seed):
        rng = np.random.default_rng(seed); n = 600
        idx = pd.date_range('2026-08-01', periods=n, freq='h')
        c = 0.7 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
        A = pd.DataFrame({'o': c, 'h': c * 1.006, 'l': c * 0.994, 'c': c}, index=idx)
        btc = 100000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
        return A, dict(zip(idx, btc))
    def test_short_events_identical_to_base(s):
        base = _load_base_step3()
        if base is None: s.skipTest('base snapshot not extracted at /tmp/base')
        tot = 0
        for seed in range(8):
            A, Bc = s.data(seed)
            d0 = base.Detector(dict(base.DEF), None); d1 = step3.Detector(dict(step3.DEF), None)
            e0 = []; e1 = []
            for j in range(1, len(A)):
                e0 += [e for e in d0.step(A, Bc, j) if e['side'] == 'S']; e1 += [e for e in d1.step(A, Bc, j)]
            s.assertEqual(json.dumps(e0, default=str), json.dumps(e1, default=str)); tot += len(e1)
        s.assertGreater(tot, 0)
    def test_short_break_discarded_while_batday_holds(s):
        S = step3.Step3.__new__(step3.Step3); S.C = dict(step3.DEF); evs = []
        S.ev = lambda *a, **k: evs.append(a); S.pos = None; S.blocks = []
        S.entry_block = lambda: (True, 'batday holding')
        b = dict(side='S', peak=0.8, close=0.75, t_close=str(T_CLOSE), det_bar=str(T_CLOSE - pd.Timedelta('5h')), status='new')
        S._try_enter(b, None, T_CLOSE + pd.Timedelta('1min'), {'ASTER': 0.75}, None)
        s.assertTrue(b['status'].startswith('skip:'))  # not 'retry' -> cannot replay after batday exits
        S.entry_block = lambda: (False, '')
        b2 = dict(b, status='new'); S.blocks = []
        S._try_enter(b2, None, T_CLOSE + pd.Timedelta('1min'), {'ASTER': 0.75}, None)
        s.assertIn('bước 2', b2['status'])  # hook off -> original rule path unchanged

if __name__ == '__main__':
    unittest.main()
