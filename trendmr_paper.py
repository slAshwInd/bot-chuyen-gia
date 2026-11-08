"""Headless PAPER launcher for the TrendMR-1H module (trendmr.py) – for hosts that cannot run the
full bnb_bot (e.g. no Binance FAPI access / no API keys). It wires the REAL module to:
  - market data: Binance public spot data (data-api.binance.vision) shaped exactly like hl.candles()
    (1h for entries/context, 15m for the Book15 auxiliary exit since 1.3.2);
  - indicators: hl.bb (the platform's own function);
  - events: a JSONL log (the same dicts LIVE.ev would emit into events_live.jsonl);
  - notify: stdout (on the Windows runtime the same module gets tg_live.send inside bnb_bot).
Runtime files (state/trendmr.json, data/trendmr_trades.csv, events.jsonl, heartbeat.json) live in
--runtime DIR, OUTSIDE the source tree. No orders are ever sent: the module runs dry_run paper.

Usage: python trendmr_paper.py --runtime /path/to/runtime [--config ref/config_paper.json] [--cycle 5]
"""
import argparse, json, os, sys, time
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import trendmr
from hl import bb as bb_fn

FEED = os.environ.get('TRENDMR_FEED') or 'https://data-api.binance.vision'
S = requests.Session()


def candles_vision(coin, interval='1h', days=14):
    """hl.candles-shaped frame from Binance spot public klines: index = bar OPEN in Vietnam time,
    columns o h l c v closed. Supports the intervals TrendMR needs: 1h (entries/context) and
    15m (Book15 auxiliary exit, 1.3.2)."""
    per_day = {'1h': 24, '15m': 96}.get(interval)
    if per_day is None:
        raise ValueError(f'trendmr_paper provider does not support interval {interval}')
    limit = min(1000, int(days * per_day) + 10)
    r = S.get(f'{FEED}/api/v3/klines', params={'symbol': coin + 'USDT', 'interval': interval, 'limit': limit}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    now_ms = int(time.time() * 1000)
    df = pd.DataFrame([[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]), int(x[6]) < now_ms]
                       for x in rows], columns=['t', 'o', 'h', 'l', 'c', 'v', 'closed'])
    df.index = pd.to_datetime(df['t'], unit='ms') + pd.Timedelta(hours=7)
    return df[['o', 'h', 'l', 'c', 'v', 'closed']]


def price(coin):
    r = S.get(f'{FEED}/api/v3/ticker/price', params={'symbol': coin + 'USDT'}, timeout=15)
    r.raise_for_status()
    return float(r.json()['price'])


class PaperLive:
    """Minimal LIVE facade the module needs: .ev(), .dry, .foreign (unused in paper)."""

    def __init__(s, ev_path):
        s.dry = True
        s.foreign = set()
        s.ev_path = ev_path

    def ev(s, event, coin='*', reason='', **kw):
        e = dict(time=str(trendmr.vn_now().replace(microsecond=0)), event=event, coin=coin, reason=reason, dry_run=True, **kw)
        try:
            with open(s.ev_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + '\n')
        except Exception:
            pass
        print(e['time'], event, coin, reason, flush=True)
        return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runtime', required=True)
    ap.add_argument('--config', default=os.path.join(ROOT, 'ref', 'config_paper.json'))
    ap.add_argument('--cycle', type=float, default=5.0)
    a = ap.parse_args()
    rt = os.path.abspath(a.runtime)
    os.makedirs(rt + '/state', exist_ok=True)
    os.makedirs(rt + '/data', exist_ok=True)
    cfg = json.load(open(a.config, encoding='utf-8'))
    live = PaperLive(rt + '/events.jsonl')

    def notify(msg):
        print(str(trendmr.vn_now().replace(microsecond=0)), 'NOTIFY', msg, flush=True)

    tmr = trendmr.TrendMR(rt + os.sep, cfg, live, candles_vision, bb_fn, notify, lambda e: None)
    print(f'TRENDMR PAPER start: enabled={tmr.on} coins={tmr.coins} capital={(cfg.get("trendmr") or {}).get("capital_usdt")} cycle={a.cycle}s feed={FEED}', flush=True)
    hb = rt + '/heartbeat.json'
    while True:
        t0 = time.time()
        try:
            m = {}
            for c in tmr.coins:
                try:
                    m[c] = price(c)
                except Exception as ex:
                    print('price fail', c, repr(ex)[:120], flush=True)
            if m:
                tmr.tick(trendmr.vn_now(), m)
                st = tmr.status()
                unreal = 0.0
                for c, cs in tmr.cs.items():
                    p = cs['pos']
                    if p and cs['price']:
                        g = 1 if p['side'] == 'L' else -1
                        unreal += g * (cs['price'] - p['entry']) * p['q'] - p['fee_in']
                st['equity'] = tmr.C['capital_usdt'] + tmr.realized + unreal
                st['unrealized'] = round(unreal, 4)
                tmp = hb + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(dict(ts=time.time(), status=st), f, ensure_ascii=False, default=str)
                os.replace(tmp, hb)
        except Exception as ex:
            import traceback
            print('paper loop error', traceback.format_exc()[-500:], flush=True)
        time.sleep(max(0.5, a.cycle - (time.time() - t0)))


if __name__ == '__main__':
    main()
