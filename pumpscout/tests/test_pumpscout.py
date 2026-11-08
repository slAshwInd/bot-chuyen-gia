"""Unit tests for PumpScout pure modules + shadow pipeline (P1)."""
import struct
import tempfile
import time
import unittest
from pathlib import Path

from pumpscout import curve_math
from pumpscout.budget import RequestBudget
from pumpscout.config import Config, load_config
from pumpscout.creator import CreatorStore
from pumpscout.enrichment import (b58decode, b58encode, bonding_curve_address,
                                  parse_mint_account)
from pumpscout.flow import evaluate_flow
from pumpscout.ledger import Ledger
from pumpscout.models import Analysis, HolderSlice, NewToken, Trade
from pumpscout.pipeline import ShadowPipeline
from pumpscout.recorder import Recorder
from pumpscout.risk import DayState, can_trade
from pumpscout.safety import evaluate_safety
from pumpscout.scoring import compute_score

CFG = Config()


def good_analysis(**over) -> Analysis:
    trades = tuple(
        Trade(wallet=f"w{i}", is_buy=True, sol_amount=0.5, token_amount=1000.0,
              ts=1000.0 + i * 40.0, slot=100 + i)
        for i in range(16)) + tuple(
        Trade(wallet=f"s{i}", is_buy=False, sol_amount=0.4, token_amount=900.0,
              ts=1700.0 + i * 40.0, slot=120 + i)
        for i in range(4))
    base = dict(
        mint="Mint111", ts=2000.0,
        virtual_sol=100.0, virtual_token=1.0e9, real_sol=50.0,
        real_token=5.0e8, complete=False, supply_tokens=1.0e9,
        mint_authority=None, freeze_authority=None, authorities_read=True,
        token_program_ok=True, sell_simulated_ok=True,
        rugcheck_score=0.2, rugcheck_extreme=False,
        top_holders=tuple(HolderSlice(f"h{i}", 0.01) for i in range(10)),
        holders_read=True, trades=trades, trades_read=True,
        has_socials=True, reply_count=10,
    )
    base.update(over)
    return Analysis(**base)


class CurveMathTest(unittest.TestCase):
    def test_buy_impact_identity(self):
        imp = curve_math.price_impact(100.0, 1.0e9, 3.0)
        self.assertAlmostEqual(imp, 0.03, places=9)

    def test_roundtrip(self):
        tokens, _ = curve_math.buy_quote(100.0, 1.0e9, 10.0)
        sol, _ = curve_math.sell_quote(110.0, 1.0e9 - tokens, tokens)
        self.assertAlmostEqual(sol, 10.0, places=6)

    def test_size_for_impact(self):
        size = curve_math.size_for_impact(100.0, 1.0e9, 0.03, 0.05, 10.0)
        self.assertAlmostEqual(size, 3.0, places=9)


class SafetyTest(unittest.TestCase):
    def test_mint_authority_veto(self):
        rep = evaluate_safety(good_analysis(mint_authority="SomeAuth"), CFG)
        self.assertEqual(rep.veto, "safety_veto")

    def test_blind_is_not_pass(self):
        rep = evaluate_safety(good_analysis(authorities_read=False), CFG)
        self.assertIsNone(rep.veto)
        self.assertIn("authorities", rep.blind)
        self.assertFalse(rep.passed)

    def test_clean_passes(self):
        self.assertTrue(evaluate_safety(good_analysis(), CFG).passed)


class FlowTest(unittest.TestCase):
    def test_creator_veto(self):
        holders = (HolderSlice("creator1", 0.30),) + tuple(
            HolderSlice(f"h{i}", 0.01) for i in range(9))
        rep = evaluate_flow(good_analysis(top_holders=holders), CFG,
                            "creator1", 1.0e9)
        self.assertEqual(rep.veto, "safety_veto")

    def test_top10_reject(self):
        holders = tuple(HolderSlice(f"h{i}", 0.04) for i in range(10))
        rep = evaluate_flow(good_analysis(top_holders=holders), CFG, "c", 1.0e9)
        self.assertEqual(rep.reason_code, "high_risk")


class ScoringRiskTest(unittest.TestCase):
    def test_missing_component_unscorable(self):
        rep = compute_score(CFG, risk_score=0.4, liquidity_health=0.9,
                            wallet_diversity=None, potential=0.5, social=0.5)
        self.assertIsNone(rep.total)
        self.assertIn("wallet_diversity", rep.missing)

    def test_daily_loss_hard_block(self):
        day = DayState(realised_pnl_sol=-0.30)
        v = can_trade(CFG, day, 0.9, kill_flag=False, regime_is_open=True)
        self.assertFalse(v.allowed)

    def test_sizing_formula(self):
        v = can_trade(CFG, DayState(), 0.8, kill_flag=False, regime_is_open=True)
        self.assertTrue(v.allowed)
        self.assertAlmostEqual(v.size_sol, min(0.25 * 0.8, 1.0 * 0.30))


class ConfigTest(unittest.TestCase):
    def test_unknown_key_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text('[operation]\nbogus_key = 1\n')
            with self.assertRaises(ValueError):
                load_config(p, d)

    def test_live_requires_flag(self):
        with self.assertRaises(ValueError):
            Config(mode="live_auto", allow_live=False).validate()

    def test_secret_key_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text('[operation]\napi_token = "x"\n')
            with self.assertRaises(ValueError):
                load_config(p, d)

    def test_example_loads(self):
        cfg = load_config(Path(__file__).parent.parent / "config.example.toml",
                          tempfile.mkdtemp())
        self.assertEqual(cfg.mode, "shadow")


class EnrichmentParseTest(unittest.TestCase):
    def test_b58_roundtrip(self):
        raw = bytes(range(32))
        self.assertEqual(b58decode(b58encode(raw)), raw)

    def test_mint_layout(self):
        data = bytearray(82)
        struct.pack_into("<I", data, 0, 0)     # mint authority: none
        struct.pack_into("<I", data, 46, 0)    # freeze authority: none
        ma, fa = parse_mint_account(bytes(data))
        self.assertIsNone(ma)
        self.assertIsNone(fa)
        struct.pack_into("<I", data, 0, 1)
        data[4:36] = bytes(range(32))
        ma, _ = parse_mint_account(bytes(data))
        self.assertEqual(ma, b58encode(bytes(range(32))))

    def test_pda_deterministic(self):
        a = bonding_curve_address("So11111111111111111111111111111111111111112")
        b = bonding_curve_address("So11111111111111111111111111111111111111112")
        self.assertEqual(a, b)
        self.assertEqual(len(b58decode(a)), 32)


class FakeEnricher:
    def __init__(self, analysis):
        self.analysis = analysis

    def enrich(self, mint, creator, uri=""):
        return self.analysis

    def enrich_holders(self, a):
        return a


class PipelineTest(unittest.TestCase):
    def _run(self, analysis, budget_limit=30000):
        tmp = tempfile.mkdtemp()
        ledger = Ledger(Path(tmp) / "ledger.db")
        recorder = Recorder(tmp)
        creators = CreatorStore(Path(tmp) / "creators.json")
        budget = RequestBudget(Path(tmp) / "budget.json",
                               {"rpc": budget_limit, "api": 10})
        pipe = ShadowPipeline(CFG, ledger, recorder, creators,
                              FakeEnricher(analysis), budget)
        tok = NewToken(mint="Mint111", creator="creator1", name="Tok",
                       symbol="TOK", uri="ipfs://x", created_ts=1000.0,
                       seen_ts=1000.0)
        pipe.on_token(tok)
        decisions = pipe.sweep(1000.0 + 200.0, DayState(), True)
        ledger.close()
        recorder.close()
        return decisions

    def test_would_buy(self):
        decisions = self._run(good_analysis())
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0].accepted)
        self.assertEqual(decisions[0].reason_code, "shadow_would_buy")

    def test_veto_path(self):
        decisions = self._run(good_analysis(freeze_authority="EvilAuth"))
        self.assertFalse(decisions[0].accepted)
        self.assertEqual(decisions[0].reason_code, "safety_veto")

    def test_budget_exhausted_fails_closed(self):
        decisions = self._run(good_analysis(), budget_limit=0)
        self.assertFalse(decisions[0].accepted)
        self.assertEqual(decisions[0].reason_code, "data_unavailable")
        self.assertEqual(decisions[0].detail, "rpc_budget_exhausted")


class BudgetTest(unittest.TestCase):
    def test_daily_cap_and_rollover(self):
        import time as _time
        with tempfile.TemporaryDirectory() as d:
            b = RequestBudget(Path(d) / "b.json", {"rpc": 10, "api": 2})
            now = _time.time()
            self.assertTrue(b.allow("rpc", 10, now))
            b.spend("rpc", 10, now)
            self.assertFalse(b.allow("rpc", 1, now))
            self.assertTrue(b.allow("api", 1, now))  # buckets independent
            b2 = RequestBudget(Path(d) / "b.json", {"rpc": 10, "api": 2})
            self.assertEqual(b2.used["rpc"], 10)     # persisted
            self.assertTrue(b.allow("rpc", 1, now + 86400))  # next UTC day


if __name__ == "__main__":
    unittest.main()


class MetadataUrlTest(unittest.TestCase):
    def test_gateway_lock(self):
        from pumpscout.enrichment import _metadata_url
        self.assertEqual(_metadata_url("ipfs://QmXyz/1.json"),
                         "https://ipfs.io/ipfs/QmXyz/1.json")
        self.assertEqual(_metadata_url("ipfs://ipfs/QmXyz"),
                         "https://ipfs.io/ipfs/QmXyz")
        self.assertEqual(_metadata_url("ar://abc123"),
                         "https://arweave.net/abc123")
        self.assertEqual(_metadata_url("https://random.host/ipfs/QmXyz"),
                         "https://ipfs.io/ipfs/QmXyz")
        self.assertEqual(_metadata_url("https://ipfs.io/ipfs/QmXyz"),
                         "https://ipfs.io/ipfs/QmXyz")
        self.assertIsNone(_metadata_url("https://floatv2.xyz/meta.json"))
        self.assertIsNone(_metadata_url("https://garphe.workers.dev/x"))
        self.assertIsNone(_metadata_url(""))


class ExecutorTest(unittest.TestCase):
    def _curve_bytes(self, v_sol=35.0, v_tok=800_000_000.0, real_sol=5.0):
        import struct as _s
        return (b"\x00" * 8 + _s.pack("<QQQQQB",
                int(v_tok * 1_000_000), int(v_sol * 1_000_000_000),
                0, int(real_sol * 1_000_000_000), 1_000_000_000_000_000, 0))

    def _executor(self, tmp):
        from pumpscout.executor import Executor
        ledger = Ledger(Path(tmp) / "ledger.db")

        class FakeRpc:
            def __init__(self, data):
                self.data = data

            def account_data(self, addr):
                return self.data, "prog"

        return Executor(CFG, ledger, FakeRpc(self._curve_bytes())), ledger

    def test_entry_and_stop_exit(self):
        from pumpscout.models import Decision
        with tempfile.TemporaryDirectory() as d:
            ex, ledger = self._executor(d)
            dec = Decision("9QxBnBerBX1111111111111111111111111111111", time.time(), "S8", True, "paper_buy",
                           0.8, (), "size_sol=0.2000")
            self.assertTrue(ex.enter(dec, snapshot_price=35.0 / 800_000_000.0))
            pos = ex.positions["9QxBnBerBX1111111111111111111111111111111"]
            self.assertGreater(pos.tokens_held, 0)
            self.assertEqual(len(ex.open_mints()), 1)
            # spot falls to ~0.57x of entry spot, real SOL intact
            # (a drained pool would correctly fire rug_exit first)
            ex.on_price("9QxBnBerBX1111111111111111111111111111111", 20.0, 800_000_000.0, 5.0, time.time())
            self.assertEqual(len(ex.open_mints()), 0)
            row = ledger.db.execute(
                "SELECT reason FROM paper_fills WHERE kind='exit_final'"
            ).fetchone()
            self.assertEqual(row[0], "stop_loss")
            state = ex.day_state(time.time())
            self.assertEqual(state.open_positions, 0)
            self.assertEqual(state.trades_today, 1)
            self.assertLess(state.realised_pnl_sol, 0)

    def test_exit_rules(self):
        from pumpscout.executor import Position, evaluate_exit
        pos = Position(mint="M", entry_ts=1000.0, entry_price=1.0,
                       tokens_initial=100.0, tokens_held=100.0,
                       cost_total_sol=0.2, peak_price=1.0)
        self.assertEqual(evaluate_exit(pos, 0.69, 5.0, 1010.0, CFG)[2],
                         "stop_loss")
        self.assertEqual(evaluate_exit(pos, 2.05, 5.0, 1010.0, CFG),
                         ("partial", 0.5, "take_profit_1"))
        pos.tp_done = True
        pos.peak_price = 2.05
        self.assertEqual(evaluate_exit(pos, 1.30, 5.0, 1020.0, CFG)[2],
                         "trailing_stop")
        pos2 = Position(mint="M", entry_ts=1000.0, entry_price=1.0,
                        tokens_initial=100.0, tokens_held=100.0,
                        cost_total_sol=0.2, peak_price=1.1)
        self.assertEqual(evaluate_exit(pos2, 1.05, 5.0, 1000.0 + 1201, CFG)[2],
                         "decay_exit")
        self.assertEqual(evaluate_exit(pos2, 1.05, 5.0, 1000.0 + 3601, CFG)[2],
                         "decay_exit")  # decay precedes max_hold when stalled
        pos3 = Position(mint="M", entry_ts=1000.0, entry_price=1.0,
                        tokens_initial=100.0, tokens_held=100.0,
                        cost_total_sol=0.2, peak_price=1.5)
        self.assertEqual(evaluate_exit(pos3, 1.40, 5.0, 1000.0 + 3601, CFG)[2],
                         "max_hold")
        pos4 = Position(mint="M", entry_ts=1000.0, entry_price=1.0,
                        tokens_initial=100.0, tokens_held=100.0,
                        cost_total_sol=0.2, peak_price=1.0)
        pos4.samples.append((1000.0, 10.0))
        self.assertEqual(evaluate_exit(pos4, 1.0, 6.9, 1100.0, CFG)[2],
                         "rug_exit")

    def test_backtest_simulation(self):
        from pumpscout.backtest import simulate
        base = 1_000_000.0
        path = [{"ts": base, "price": 1.0, "sol": 1.0},
                {"ts": base + 10, "price": 2.1, "sol": 1.0},
                {"ts": base + 20, "price": 1.30, "sol": 1.0},
                {"ts": base + 30, "price": 1.25, "sol": 1.0}]
        got = simulate(base - 100, 0.2, path, CFG)
        self.assertTrue(got["closed"])
        self.assertEqual(got["exit_reason"], "trailing_stop")
        self.assertGreater(got["pnl_sol"], 0)
