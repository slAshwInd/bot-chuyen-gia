"""Module TrendMR-1H – mean-reversion BB 1h CÓ lọc xu hướng, cho các coin lớn (BTC/ETH/BNB).

Chiến lược (đã backtest, xem research đi kèm): vào lệnh khi giá chạm biên Bollinger (20, 2.0) khung 1h
của nến 1h đã đóng gần nhất, NHƯNG chỉ cùng chiều xu hướng EMA100 (LONG khi giá trên EMA100 – mua nhịp
giảm trong uptrend; SHORT khi dưới EMA100 – bán nhịp hồi trong downtrend). Giữ 2 lọc gốc của BaseAster:
momentum 4h (không vào khi giá đã chạy mạnh >= 0.7% vào biên) và độ dốc MA20 4h (không ngược quá 3%/10 nến).
Thoát: SL cứng -1.5% giá; lãi tạm tính từng chạm +0.6% -> dời stop về hoà vốn (x1.002/x0.998);
TP tại dải giữa BB 1h (cập nhật theo nến đóng mới, chỉ khi còn phía lãi >= 0.3%). KHÔNG có thoát theo
thời gian (như BaseAster): vị thế giữ đến khi chạm stop hoặc TP. Stop được nâng theo cơ chế khóa lãi
theo mốc của BaseAster (lock30): mỗi 1% lãi đỉnh -> stop khóa >= 2/3 mốc đó.
THOÁT PHỤ Book15 (bản 1.3.2, biến thể V1 đã backtest riêng trên walker 15m, dữ liệu 2021-2026):
giữ nguyên TP dải giữa 1h và stop/BE/khóa mốc ở trên, THÊM điều kiện đóng theo điểm triển vọng 15m
của BaseAster (score15/score15_short port nguyên văn): tại mỗi nến 15m đã đóng MỚI, nếu lãi tạm tính
sau phí vào (tại giá đóng 15m) > eps 8$ VÀ giá đã chạm dải giữa BB 4h kể từ lúc vào (cổng C2) thì
điểm <= -3 (hoặc <= -4 khi lãi < 60$) ở HAI nến 15m liên tiếp -> đóng toàn bộ (EXIT_15M, phí taker).
Ngưỡng $ giữ NGUYÊN văn Book15 như đã duyệt (biến thể chia ngưỡng theo sleeve bị loại). Backtest V1
so với 1.3.1 trên cùng walker 15m: OOS 2026 PF BTC 1,81->1,92 / ETH 1,99->2,02 / BNB 1,71->2,36,
lãi ròng OOS +93/+90/+535$; thoát 15m chỉ kích hoạt ~8% số lệnh. Bỏ TP dải giữa để chỉ thoát bằng
score15 (V2/V4) đã bị LOẠI (stress OOS âm ở BTC/ETH). Lưu ý: số PF tuyệt đối của walker 15m cao hơn
engine 1h đã công bố vì cách xử lý thứ tự chạm trong giờ - chỉ chênh lệch V1-V0 mới là kết luận.
Chỉ chạm ĐẦU TIÊN của một episode mới được vào (nến 1h trước không chạm biên đó); tối đa 1 vị thế/coin.

Nền tảng dùng chung với BaseAster: nến/giá qua hl.py (Binance), sự kiện qua LIVE.ev -> events_live.jsonl,
Telegram qua hàm send được tiêm vào (tg_live.send), trạng thái phơi bày qua status() -> state_live.json.
Bật/tắt từng coin: cfg['coins'][COIN]['enabled'] (tắt ở đây = tắt vào lệnh toàn hệ thống, xem bnb_bot)
và cfg['trendmr']['coins'][COIN]. ASTER mặc định KHÔNG chạy module này (ASTER thuộc các Book BaseAster,
BatDay, Step3 như hiện tại); muốn chạy TrendMR cho ASTER thì bật coins.ASTER=true trong cfg['trendmr'].

Vốn & sizing (bản 1.3.3, user duyệt 2026-11-08 sau nghiên cứu sizing_compare/sizing_gross/risk_mc;
1.3.4 đổi cách chia, user duyệt 2026-11-08 sau nghiên cứu leverage_alloc_eval):
mô hình NGÂN SÁCH TỔNG NOTIONAL LÃI KÉP kiểu G2 — tại thời điểm vào lệnh, ngân sách = equity ĐÃ CHỐT
(vốn gốc cfg['trendmr']['capital_usdt'], mặc định 5000, + PnL đã chốt) x gross_lev (5); vị thế mở thứ
1/2/3 lấy lần lượt 50% / 60% / 100% NGÂN SÁCH CÒN LẠI (gross_remain_fracs, ngân sách còn lại =
ngân sách - tổng notional đang mở), trần mỗi lệnh 50% ngân sách (gross_cap_frac, tính trước hệ số
coin) — tương đương đúng 50%/30%/20% ngân sách khi các vị thế trước đủ size, nhưng không lãng phí
ngân sách khi vị thế trước nhỏ hoặc bị hệ số giảm. SHORT BNB vẫn nhân hệ số 0.5 sau cùng.
Ngân sách tách riêng, KHÔNG gồm ASTER; vốn 1000 thì ngân sách 5000 — mọi thứ tỉ lệ theo equity.
Ký quỹ tối đa (= equity hiện tại ở isolated x5 khi dùng hết ngân sách) tăng theo lãi kép, lỗ thì co lại.
VAN TIẾT LƯU DD: khi equity đã chốt nằm dưới đỉnh của chính nó >= 12% thì hệ số ngân sách x0.5,
>= 20% thì x0.25 — chỉ GIẢM SIZE lệnh mới, không chặn lệnh (backtest: DD toàn kỳ -30,2% -> -22,0%,
lợi nhuận 2025/2026 gần như nguyên). Số liệu nền: backtest kỳ vọng winrate 72,5%, expectancy
+0,173%/lệnh, PF 1,62; Monte Carlo 400 đường theo tỉ lệ thắng/thua đó: DD điển hình -28,5%,
P(DD >= 50%) 0,2%, P(DD >= 70%) ~0% — rủi ro cháy thật nằm ở EDGE MÒN (edge giảm một nửa thì
expectancy ~0 và DD trung vị -89,9%), nên bản này thêm cơ chế giám sát quản trị ở dưới.
Quản trị rủi ro còn lại THEO ĐÚNG TRIẾT LÝ BaseAster: không có chặn giao dịch theo ngày/chuỗi lỗ/
drawdown (cat_stop của BaseAster cũng đang tắt) — bảo vệ cứng nằm ở stop của từng vị thế; các ngưỡng
lỗ ngày -3%, 3 SL liên tiếp, drawdown -12% chỉ phát sự kiện TRENDMR_ALERT, không chặn lệnh.
GIÁM SÁT NGƯỠNG HIỆU SUẤT (user lệnh 2026-11-08, khác hẳn các cảnh báo trên — đây là CỔNG QUẢN TRỊ):
sau mỗi lệnh đóng, module đối chiếu winrate/PF lũy kế với các MỐC theo số lệnh đã đóng (30: WR>=55%,
PF>=0,9; 75: WR>=60%, PF>=1,0 và PnL lũy kế >0; 150 — mốc nghiệm thu: WR>=65%, PF>=1,2) cộng kiểm tra
PF của 50 lệnh gần nhất >= 1,0. Vi phạm bất kỳ ngưỡng nào -> TRENDMR_REVIEW_HOLD: DỪNG VÀO LỆNH MỚI
(vị thế đang mở vẫn quản trị/thoát bình thường) cho tới khi phiên kiểm tra 1h review xong và user duyệt
điều chỉnh/cho chạy tiếp — vì theo phân tích Monte Carlo, dấu hiệu edge mòn phải được xử lý TRƯỚC KHI
chạy tiếp, không phải sau khi tài khoản đã cháy.

Thực thi: dry_run=true (mặc định trong config paper) = PAPER: khớp mô phỏng tại giá biên (maker),
phí đúng biểu Binance USDT-M futures hạng thường (maker 0.02%/taker 0.05%) và đúng ngữ nghĩa từng
loại lệnh như backtest: vào lệnh limit tại biên = maker; thoát TP limit tại dải giữa = maker; mọi
thoát kiểu market (stop SL/BE-TRAIL, EXIT_15M) = taker; trượt giá stop 0.05%; lệnh ghi vào
data/trendmr_trades.csv, trạng thái state/trendmr.json. Đường LIVE (allow_live) CỐ Ý chưa mở trong bản
này: module chỉ phát tín hiệu + sự kiện TRENDMR_LIVE_LOCKED. Mở live sau khi đạt tiêu chí nghiệm thu
paper (>=150 lệnh, winrate >= 65%, PF >= 1.2) và có quyết định riêng – xem README_TrendMR.md.
"""
import os, json, time, math, threading, datetime as dt
import pandas as pd

DEF = dict(
    enabled=True, dry_run=True, allow_live=False,
    coins={'BTC': True, 'ETH': True, 'BNB': True, 'ASTER': False},
    capital_usdt=5000.0, notional_x=1.0, lev=5,
    # Gross-budget compounding sizing (1.3.3, scheme G2 - user-approved 2026-11-08): budget at entry
    # = realised equity x gross_lev. Allocation since 1.3.4 (user-approved 2026-11-08): position
    # #1/#2/#3 takes gross_remain_fracs of the REMAINING budget (budget minus deployed notional),
    # capped per position at gross_cap_frac of the budget (cap applied before the coin factor);
    # identical to 50/30/20 of the budget when prior positions are full-size. DD throttle shrinks
    # the budget factor while realised equity is underwater vs its own peak (sizing only, no block).
    gross_lev=5.0, gross_remain_fracs=[0.5, 0.6, 1.0], gross_cap_frac=0.5,
    throttle_dd1_pct=12.0, throttle_m1=0.5, throttle_dd2_pct=20.0, throttle_m2=0.25,
    # Performance governance gate (user, 2026-11-08): cumulative WR/PF milestones by closed-trade
    # count + rolling-window PF; a breach sets REVIEW_HOLD (no new entries; open positions still
    # managed) until the hourly review is done and the user approves resuming/adjusting.
    review_milestones=[{'n': 30, 'wr': 55.0, 'pf': 0.9}, {'n': 75, 'wr': 60.0, 'pf': 1.0},
                       {'n': 150, 'wr': 65.0, 'pf': 1.2}],
    review_netpos_n=75, review_roll_window=50, review_roll_pf=1.0,
    bb_n=20, bb_k=2.0, ema_n=100, mom_gate_pct=0.7, slope_gate_pct=3.0,
    sl_pct=1.5, be_trigger_pct=0.6, be_buf=1.002, tp_min_profit_pct=0.3, max_hold_h=0,
    lock_step_pct=1.0, lock_frac=2.0 / 3.0,
    # Book15 auxiliary exit (1.3.2, variant V1): literal strat.Book15 thresholds, USD on the position's
    # own unrealised PnL (after entry fee). Score exit only, TP-at-mid stays the primary take-profit.
    book15_exit=True, book15_eps=8.0, book15_small=60.0, book15_th=-3, book15_th_small=-4,
    fee_maker=0.0002, fee_taker=0.0005, slip=0.0005,
    short_size_factor={'BNB': 0.5},
    # Risk layer mirrors BaseAster: NO trading blocks (no daily-loss halt, no consecutive-loss pause,
    # no drawdown halt - BaseAster's cat_stop is disabled and its DD/daily figures are ALERTS only).
    # Hard protection is per-position (the SL/lock stop). These thresholds only fire TRENDMR_ALERT events.
    daily_loss_alert_pct=3.0, consec_sl_alert=3, dd_alert_pct=12.0,
    # Per-pair overrides, adopted ONLY where a deviation beat the unified set on OOS + cost-stress
    # (per-pair comparison, 2021-2026 data): BNB bb_k 2.5 wins on every criterion (FULL PF 1.56 vs 1.43,
    # OOS 1.35 vs 1.27, stress-OOS 1.05 vs 0.97, DD -10.3% vs -14.0%). BTC/ETH deviations and the
    # BatDay-5 / EMA-Lock satellites were evaluated and REJECTED (no demonstrable improvement).
    coin_params={'BNB': {'bb_k': 2.5}},
)

H1 = pd.Timedelta('1h')
H4 = pd.Timedelta('4h')


def vn_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=7)


# ---------------- pure helpers (unit-testable, no network) ----------------
def build_context(h1_closed, C=None):
    """Context from CLOSED 1h bars (index = open time VN, cols o/h/l/c, BB cols mid/up/lo already applied
    by hl.bb on closes). Returns dict or None when data is insufficient. All values point-in-time:
    bands/EMA/momentum use closes up to and including the last closed bar."""
    C = dict(DEF, **(C or {}))
    if h1_closed is None or len(h1_closed) < max(C['ema_n'] + 5, 130):
        return None
    x = h1_closed
    closes = x['c'].astype(float)
    last = x.iloc[-1]
    if not (float(last['mid']) == float(last['mid'])):
        return None
    ema = closes.ewm(span=C['ema_n'], adjust=False).mean().iloc[-1]
    mom4h = (float(closes.iloc[-1]) / float(closes.iloc[-5]) - 1.0) * 100.0
    # 4h MA20 slope over 10 bars, UTC-aligned 4h closes derived from the 1h closes (VN index -> UTC);
    # only COMPLETE 4h buckets count (a trailing partial bucket is not a closed 4h bar)
    utc = pd.DataFrame({'c': closes.values}, index=(x.index - pd.Timedelta(hours=7)))
    grp = utc['c'].resample('4h', origin='epoch')
    h4c = grp.last()[grp.count() >= 4].dropna()
    if len(h4c) < 31:
        return None
    ma20 = h4c.rolling(20).mean()
    slope10 = (float(ma20.iloc[-1]) / float(ma20.iloc[-11]) - 1.0) * 100.0
    return dict(mid=float(last['mid']), up=float(last['up']), lo=float(last['lo']),
                ema=float(ema), last_close=float(closes.iloc[-1]), mom4h=mom4h, slope10=slope10,
                mid4=float(ma20.iloc[-1]),
                bar_t=str(x.index[-1]))


def entry_signal(ctx, price, prev_touch_long, prev_touch_short, C=None):
    """Decide an entry at the current price against the active bands. Returns (side|None, detail).
    LONG: price <= lower band, first touch of the episode, close above EMA (uptrend), gates pass.
    SHORT: mirror. Fail-closed on missing context."""
    C = dict(DEF, **(C or {}))
    if ctx is None or price is None or price <= 0:
        return None, dict(why='no_context')
    lc, ema = ctx['last_close'], ctx['ema']
    if price <= ctx['lo']:
        if prev_touch_long:
            return None, dict(why='not_fresh_long')
        if not lc > ema:
            return None, dict(why='trend_down_long_blocked', last_close=lc, ema=ema)
        if ctx['mom4h'] <= -C['mom_gate_pct']:
            return None, dict(why='mom4h_long_blocked', mom4h=ctx['mom4h'])
        if ctx['slope10'] <= -C['slope_gate_pct']:
            return None, dict(why='slope_long_blocked', slope10=ctx['slope10'])
        return 'L', dict(band=ctx['lo'], mid=ctx['mid'], ema=ema, mom4h=ctx['mom4h'], slope10=ctx['slope10'])
    if price >= ctx['up']:
        if prev_touch_short:
            return None, dict(why='not_fresh_short')
        if not lc < ema:
            return None, dict(why='trend_up_short_blocked', last_close=lc, ema=ema)
        if ctx['mom4h'] >= C['mom_gate_pct']:
            return None, dict(why='mom4h_short_blocked', mom4h=ctx['mom4h'])
        if ctx['slope10'] >= C['slope_gate_pct']:
            return None, dict(why='slope_short_blocked', slope10=ctx['slope10'])
        return 'S', dict(band=ctx['up'], mid=ctx['mid'], ema=ema, mom4h=ctx['mom4h'], slope10=ctx['slope10'])
    return None, dict(why='no_touch')


def paper_step(pos, price, now_ts, C=None):
    """One paper-execution step for an open position at the current price.
    Returns (exit_reason|None, exit_px|None). Mutates pos['peak_move'], pos['stop'], pos['be_done'].
    Stop management mirrors BaseAster: BE arms from the favourable excursion (+be_trigger_pct),
    then the MILESTONE LOCK (lock30 in strat.Book15): every lock_step_pct of peak excursion the stop
    locks >= lock_frac of that milestone. There is NO time exit by default (max_hold_h=0), exactly
    like BaseAster - a position exits on its stop, the mid-band TP, or not at all."""
    C = dict(DEF, **(C or {}))
    g = 1 if pos['side'] == 'L' else -1
    move = g * (price / pos['entry'] - 1.0) * 100.0
    if move > pos.get('peak_move', 0.0):
        pos['peak_move'] = move
    if not pos.get('be_done') and pos['peak_move'] >= C['be_trigger_pct']:
        be = pos['entry'] * (C['be_buf'] if g > 0 else 2.0 - C['be_buf'])
        if g * be > g * pos['stop']:
            pos['stop'] = be
        pos['be_done'] = True
    step = C.get('lock_step_pct') or 0
    if step and pos['peak_move'] >= step:
        M = int(pos['peak_move'] // step) * step
        lock_px = pos['entry'] * (1.0 + g * M * C['lock_frac'] / 100.0)
        if g * lock_px > g * pos['stop']:
            pos['stop'] = lock_px
            pos['lock_armed'] = True
    if g * price <= g * pos['stop']:
        return ('BE/TRAIL' if (pos.get('be_done') or pos.get('lock_armed')) else 'SL'), pos['stop'] * (1.0 - g * C['slip'])
    tp = pos.get('tp')
    if tp and g * (tp - pos['entry']) > C['tp_min_profit_pct'] / 100.0 * pos['entry'] and g * price >= g * tp:
        return 'TP', tp
    if C.get('max_hold_h') and now_ts - pos['t0_ts'] >= C['max_hold_h'] * 3600:
        return 'TIME', price
    return None, None


def settle(pos, exit_px, C=None, reason=None):
    """Net PnL of a paper trade at exit_px (entry fee already stored on pos).
    Exit fee follows Binance USDT-M futures semantics, exactly like the backtest engine:
    the TP is a resting LIMIT order at the mid band -> MAKER fee; every market-type exit
    (stop SL / BE-TRAIL, EXIT_15M score close, TIME) -> TAKER fee. Rates are cfg fee_maker /
    fee_taker (Binance regular tier 0.02% / 0.05%)."""
    C = dict(DEF, **(C or {}))
    g = 1 if pos['side'] == 'L' else -1
    gross = g * (exit_px - pos['entry']) * pos['q']
    rate = C['fee_maker'] if reason == 'TP' else C['fee_taker']
    fee_out = exit_px * pos['q'] * rate
    return gross - pos['fee_in'] - fee_out, gross, fee_out


def prep15_frame(x):
    """15m frame (closed candles, BB(20,2) already applied by hl.bb) -> add RSI14, ATR14, MA80.
    Mirrors strat.prep15 exactly."""
    x = x.copy()
    d = x['c'].diff(); u = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    x['rsi'] = 100 - 100 / (1 + u / dn)
    x['atr'] = pd.concat([x['h'] - x['l'], (x['h'] - x['c'].shift()).abs(),
                          (x['l'] - x['c'].shift()).abs()], axis=1).max(axis=1).rolling(14).mean()
    x['ma80'] = x['c'].rolling(80).mean()
    return x


def score15_last(x, side='L'):
    """Book15 outlook score on the LAST closed 15m candle of a prep15_frame: port of
    strat.score15 / strat.score15_short (8 factors, +1/0/-1 each). Returns int, or None when
    the frame is too short or any input is not yet defined (fail-closed: no score, no exit)."""
    if x is None or len(x) < 90:
        return None
    z = x.iloc[-1]; z1 = x.iloc[-2]
    need = [z['pb'], z['mid'], z['bw'], z['rsi'], z1['rsi'], z['atr'], z['ma80'], z['vma'],
            x['mid'].iloc[-5], x['bw'].iloc[-4]]
    if any(v != v for v in need):
        return None
    o, h, l, c, v = float(z['o']), float(z['h']), float(z['l']), float(z['c']), float(z['v'])
    rng = max(h - l, 1e-12)
    F = []
    if side == 'L':
        F.append(1 if z['pb'] >= 0.8 else (-1 if z['pb'] < 0.5 else 0))
        F.append(1 if z['mid'] > x['mid'].iloc[-5] else -1)
        bwx = z['bw'] > x['bw'].iloc[-4]
        F.append(1 if (bwx and c > z['mid']) else (-1 if (bwx and c < z['mid']) else 0))
        r0, r1 = float(z1['rsi']), float(z['rsi'])
        F.append(1 if (r1 >= 55 and r1 > r0 and r1 < 75) else (-1 if (r1 < 50 or (r0 > 70 and r1 < r0)) else 0))
        F.append((1 if c >= o else -1) if v >= z['vma'] else 0)
        up_w = h - max(o, c)
        F.append(-1 if (c < o and (c < z1['l'] or (o - c) / rng >= 0.6)) or up_w / rng >= 0.5
                 else (1 if c > o and (c - o) / rng >= 0.6 else 0))
        F.append(1 if c > z['ma80'] else -1)
        rm = (z['up'] - c) / z['atr'] if z['atr'] > 0 else 0
        F.append(1 if rm >= 1.0 else (-1 if rm < 0 else 0))
    else:
        F.append(1 if z['pb'] <= 0.2 else (-1 if z['pb'] > 0.5 else 0))
        F.append(1 if z['mid'] < x['mid'].iloc[-5] else -1)
        bwx = z['bw'] > x['bw'].iloc[-4]
        F.append(1 if (bwx and c < z['mid']) else (-1 if (bwx and c > z['mid']) else 0))
        r0, r1 = float(z1['rsi']), float(z['rsi'])
        F.append(1 if (r1 <= 45 and r1 < r0 and r1 > 25) else (-1 if (r1 > 50 or (r0 < 30 and r1 > r0)) else 0))
        F.append((1 if c <= o else -1) if v >= z['vma'] else 0)
        lo_w = min(o, c) - l
        F.append(-1 if (c > o and (c > z1['h'] or (c - o) / rng >= 0.6)) or lo_w / rng >= 0.5
                 else (1 if c < o and (o - c) / rng >= 0.6 else 0))
        F.append(1 if c < z['ma80'] else -1)
        rm = (c - z['lo']) / z['atr'] if z['atr'] > 0 else 0
        F.append(1 if rm >= 1.0 else (-1 if rm < 0 else 0))
    return int(sum(F))


def score_exit_check(uz, score, mid4_hit, weak_prev, C=None):
    """Book15 V1 exit decision for ONE newly closed 15m candle. uz = unrealised PnL in USD after the
    entry fee, at that candle's close. Returns (fire, weak_now): fire only when the score is weak on
    this AND the previous evaluated candle; weak_now resets to False whenever the candle is not
    evaluated (uz <= eps, 4h-mid gate not yet hit, or no score) - the walk15 backtest semantics."""
    C = dict(DEF, **(C or {}))
    if not C.get('book15_exit') or score is None or uz is None or uz <= C['book15_eps'] or not mid4_hit:
        return False, False
    th = C['book15_th'] if uz >= C['book15_small'] else C['book15_th_small']
    weak = score <= th
    return bool(weak and weak_prev and uz > 0), bool(weak)


def sleeve(capital, n_coins, C=None):
    if n_coins <= 0:
        return 0.0
    return float(capital) / n_coins


def throttle_mult(equity, peak_realized, C=None):
    """DD throttle on the gross budget factor: full size while realised equity is within
    throttle_dd1_pct of its peak, x throttle_m1 below that, x throttle_m2 below throttle_dd2_pct."""
    C = dict(DEF, **(C or {}))
    if not peak_realized or peak_realized <= 0:
        return 1.0
    uw = (equity / peak_realized - 1.0) * 100.0
    if uw <= -float(C['throttle_dd2_pct']):
        return float(C['throttle_m2'])
    if uw <= -float(C['throttle_dd1_pct']):
        return float(C['throttle_m1'])
    return 1.0


def gross_notional(equity, peak_realized, n_open, C=None, deployed=None):
    """G2 compounding sizing (1.3.4 remaining-budget allocation): returns
    (notional_before_coin_factor, budget, throttle_mult).
    budget = realised equity x gross_lev x throttle. The (n_open+1)-th concurrent position takes
    gross_remain_fracs[n_open] of the REMAINING budget (budget - deployed), capped at
    gross_cap_frac x budget. `deployed` = total notional of positions already open (after coin
    factors); when omitted, the clean full-size deployment implied by the fractions themselves is
    assumed, which reproduces the old 50/30/20 slot shares exactly."""
    C = dict(DEF, **(C or {}))
    mult = throttle_mult(equity, peak_realized, C)
    budget = equity * float(C['gross_lev']) * mult
    fracs = C.get('gross_remain_fracs') or [0.5, 0.6, 1.0]
    k = min(n_open, len(fracs) - 1)
    if deployed is None:
        rem_frac = 1.0
        for j in range(k):
            rem_frac *= (1.0 - float(fracs[j]))
        deployed = budget * (1.0 - rem_frac)
    remaining = max(budget - float(deployed), 0.0)
    raw = float(fracs[k]) * remaining
    cap = float(C.get('gross_cap_frac') or 0.5) * budget
    return min(raw, cap), budget, mult


def stats_wr_pf(stt):
    """(winrate %, PF) from cumulative counters dict(n, wins, gp, gl)."""
    n = int(stt.get('n') or 0)
    if not n:
        return 0.0, 0.0
    wr = float(stt.get('wins') or 0) / n * 100.0
    gp, gl = float(stt.get('gp') or 0.0), float(stt.get('gl') or 0.0)
    pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0.0)
    return wr, pf


def review_evaluate(stt, last_nets, C=None):
    """Governance gate: list of breach reasons (empty = within expectations). stt = cumulative
    counters (n, wins, gp, gl, net); last_nets = most recent net PnLs (rolling PF check). The tier
    applied is the LARGEST milestone whose trade count has been reached."""
    C = dict(DEF, **(C or {}))
    reasons = []
    n = int(stt.get('n') or 0)
    tier = None
    for m in (C.get('review_milestones') or []):
        if n >= int(m['n']):
            tier = m
    if tier:
        wr, pf = stats_wr_pf(stt)
        if wr < float(tier['wr']):
            reasons.append(f"winrate lũy kế {wr:.1f}% < ngưỡng mốc {int(tier['n'])} lệnh "
                           f"{float(tier['wr']):g}% (kỳ vọng backtest 72,5%)")
        if pf < float(tier['pf']):
            reasons.append(f"PF lũy kế {pf:.2f} < ngưỡng mốc {int(tier['n'])} lệnh "
                           f"{float(tier['pf']):g} (kỳ vọng backtest 1,62)")
        if n >= int(C.get('review_netpos_n') or 75) and float(stt.get('net') or 0.0) <= 0:
            reasons.append(f"PnL lũy kế {float(stt.get('net') or 0.0):+,.2f}$ <= 0 sau {n} lệnh đóng")
    w = int(C.get('review_roll_window') or 0)
    if w and len(last_nets or []) >= w:
        seg = list(last_nets)[-w:]
        gp = sum(v for v in seg if v > 0); gl = -sum(v for v in seg if v <= 0)
        rpf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0.0)
        if rpf < float(C['review_roll_pf']):
            reasons.append(f"PF trượt {w} lệnh gần nhất {rpf:.2f} < {float(C['review_roll_pf']):g}")
    return reasons


# ---------------- live module ----------------
class TrendMR:
    def __init__(s, B, cfg, LIVE, candles, bb_fn, notify, emit):
        s.B = B
        raw = dict(cfg.get('trendmr') or {})
        s.C = dict(DEF, **raw)
        if isinstance(raw.get('coins'), dict):
            s.C['coins'] = dict(DEF['coins'], **raw['coins'])
        if isinstance(raw.get('short_size_factor'), dict):
            s.C['short_size_factor'] = dict(DEF['short_size_factor'], **raw['short_size_factor'])
        cp = dict(DEF.get('coin_params') or {})
        for c_, ov in (raw.get('coin_params') or {}).items():
            cp[str(c_).upper()] = dict(cp.get(str(c_).upper()) or {}, **ov)
        s.pcfg = {}
        master = cfg.get('coins') or {}
        s.coins = []
        for c, on in s.C['coins'].items():
            m = master.get(c) or {}
            if on and m.get('enabled') is not False:
                s.coins.append(str(c).upper())
        for c in list(s.C['coins']) + list(cp):
            cu = str(c).upper()
            if cu not in s.pcfg:
                s.pcfg[cu] = dict(s.C, **(cp.get(cu) or {}))
        s.on = bool(s.C.get('enabled')) and bool(s.coins)
        s.dry = bool(s.C.get('dry_run')) or bool(getattr(LIVE, 'dry', True))
        s.L = LIVE; s.candles = candles; s.bb = bb_fn
        s.notify = notify; s.emit = emit
        s.path = B + 'state/trendmr.json'
        s.trf = B + 'data/trendmr_trades.csv'
        s.lk = threading.Lock()
        try:
            with open(s.path, encoding='utf-8') as f_:
                st = json.load(f_)
        except Exception:
            st = {}
        s.realized = float(st.get('realized') or 0.0)
        s.peak_equity = float(st.get('peak_equity') or s.C['capital_usdt'])
        s.peak_realized = float(st.get('peak_realized') or max(s.C['capital_usdt'], s.C['capital_usdt'] + s.realized))
        s.stats = dict(st.get('stats') or dict(n=0, wins=0, gp=0.0, gl=0.0, net=0.0))
        s.last_nets = list(st.get('last_nets') or [])[-int(s.C.get('review_roll_window') or 50):]
        s.review_hold = bool(st.get('review_hold'))
        s.review_info = st.get('review_info')
        s.day = st.get('day') or dict(date=str(vn_now())[:10], realized=0.0)
        s.day_alerted = bool(st.get('day_alerted')); s.dd_alerted = bool(st.get('dd_alerted'))
        s.cs = {}
        saved = st.get('coins') or {}
        for c in s.coins:
            d = saved.get(c) or {}
            s.cs[c] = dict(pos=d.get('pos'), consec_sl=int(d.get('consec_sl') or 0),
                           prev_touch_long=bool(d.get('prev_touch_long')), prev_touch_short=bool(d.get('prev_touch_short')),
                           touch_used_long=False, touch_used_short=False,
                           last_bar=d.get('last_bar'), ctx=None, price=None, err=None)
        s.bars = {}
        s.fetch_t = {}
        s.bars15 = {}
        s.fetch15_t = {}
        s.err = None
        if not os.path.isdir(B + 'state'):
            os.makedirs(B + 'state', exist_ok=True)
        if not os.path.isdir(B + 'data'):
            os.makedirs(B + 'data', exist_ok=True)

    # ---------- plumbing ----------
    def ev(s, event, coin, reason, tg=True, **kw):
        e = s.L.ev(event, coin, reason, **kw)
        if tg:
            try:
                s.notify(('[PAPER] ' if s.dry else '[LIVE] ') + reason)
            except Exception:
                pass
        return e

    def save(s):
        d = dict(realized=s.realized, peak_equity=s.peak_equity, peak_realized=s.peak_realized,
                 stats=s.stats, last_nets=s.last_nets, review_hold=s.review_hold, review_info=s.review_info,
                 day=s.day, day_alerted=s.day_alerted, dd_alerted=s.dd_alerted,
                 updated=str(vn_now().replace(microsecond=0)), cfg=s.C,
                 coins={c: dict(pos=cs['pos'], consec_sl=cs['consec_sl'],
                                prev_touch_long=cs['prev_touch_long'], prev_touch_short=cs['prev_touch_short'],
                                last_bar=cs['last_bar']) for c, cs in s.cs.items()})
        try:
            with open(s.path + '.tmp', 'w', encoding='utf-8') as f_:
                json.dump(d, f_, ensure_ascii=False, indent=1, default=str)
            os.replace(s.path + '.tmp', s.path)
        except Exception as ex:
            print('trendmr save', ex, flush=True)

    def status(s):
        equity = s.C['capital_usdt'] + s.realized
        n_open = sum(1 for cs in s.cs.values() if cs['pos'])
        deployed = sum(float(cs['pos']['notional']) for cs in s.cs.values() if cs['pos'])
        nxt, budget, mult = gross_notional(equity, s.peak_realized, n_open, s.C, deployed)
        wr, pf = stats_wr_pf(s.stats)
        out = dict(enabled=s.on, dry_run=s.dry, allow_live=bool(s.C.get('allow_live')),
                   capital=s.C['capital_usdt'], sleeve=sleeve(s.C['capital_usdt'], len(s.coins)),
                   realized=round(s.realized, 4), err=s.err, coins={},
                   sizing=dict(equity_realized=round(equity, 2), peak_realized=round(s.peak_realized, 2),
                               gross_budget=round(budget, 2), throttle_mult=mult,
                               deployed=round(deployed, 2),
                               next_notional=round(nxt, 2), open_positions=n_open),
                   perf=dict(closed=int(s.stats.get('n') or 0), winrate=round(wr, 1), pf=round(pf, 3),
                             review_hold=s.review_hold, review_info=s.review_info))
        for c, cs in s.cs.items():
            ctx = cs['ctx'] or {}
            out['coins'][c] = dict(pos=cs['pos'], price=cs['price'],
                                   consec_sl=cs['consec_sl'], err=cs['err'],
                                   params=dict(bb_k=s.pcfg[c]['bb_k'], ema_n=s.pcfg[c]['ema_n']),
                                   ctx={k: ctx.get(k) for k in ('mid', 'up', 'lo', 'ema', 'last_close', 'mom4h', 'slope10', 'mid4', 'bar_t')})
        return out

    def holding(s):
        return any(cs['pos'] for cs in s.cs.values())

    # ---------- data ----------
    def _bars(s, coin, force=False):
        cs = s.cs[coin]
        now_ts = time.time()
        if not force and cs['ctx'] is not None and now_ts - s.fetch_t.get(coin, 0) < 15:
            return True
        s.fetch_t[coin] = now_ts
        try:
            pc = s.pcfg[coin]
            a = s.candles(coin, '1h', 14)
            a = a[a.closed]
            a = s.bb(a, pc['bb_n'], pc['bb_k'])
            s.bars[coin] = a
            cs['err'] = None
            return True
        except Exception as ex:
            cs['err'] = f'data: {ex}'[:150]
            return coin in s.bars

    def _roll_bar(s, coin):
        """When a new 1h bar has closed: score the just-closed bar's touch vs the bands that were
        active during it, refresh the TP of an open position, then rebuild the context."""
        cs = s.cs[coin]
        a = s.bars[coin]
        if a is None or not len(a):
            return
        last_t = str(a.index[-1])
        if cs['last_bar'] == last_t:
            if cs['ctx'] is None:
                # process restarted inside the same 1h bar: rebuild the context immediately instead of
                # staying blind (no entries, no 4h-mid gate) until the next bar closes
                cs['ctx'] = build_context(a, s.pcfg[coin])
            return
        old = cs['ctx']
        if old is not None:
            bar = a.iloc[-1]
            cs['prev_touch_long'] = float(bar['l']) <= old['lo']
            cs['prev_touch_short'] = float(bar['h']) >= old['up']
            cs['touch_used_long'] = cs['touch_used_short'] = False
            if cs['pos']:
                g = 1 if cs['pos']['side'] == 'L' else -1
                new_mid = float(bar['mid'])
                if g * (new_mid - cs['pos']['entry']) > 0:
                    cs['pos']['tp'] = new_mid
        cs['last_bar'] = last_t
        cs['ctx'] = build_context(a, s.pcfg[coin])

    def _bars15(s, coin):
        """Closed 15m candles + BB(20,2) + prep15 indicators for the Book15 exit; fetched only while a
        position is open, throttled like the 1h fetch. Fail-soft: keep the previous frame on error."""
        cs = s.cs[coin]
        now_ts = time.time()
        if coin in s.bars15 and now_ts - s.fetch15_t.get(coin, 0) < 15:
            return True
        s.fetch15_t[coin] = now_ts
        try:
            a = s.candles(coin, '15m', 2)
            a = a[a.closed]
            a = s.bb(a, 20, 2.0)
            s.bars15[coin] = prep15_frame(a)
            return True
        except Exception as ex:
            cs['err'] = f'data15: {ex}'[:150]
            return coin in s.bars15

    def _book15_step(s, coin, px):
        """Book15 V1 auxiliary exit for an open paper position at the current tick price.
        Returns (exit_reason|None, exit_px|None); mutates pos['mid4h_hit'], pos['weak_prev'],
        pos['last15'], pos['outlook']. The 4h-mid gate (C2) is tracked tick-by-tick against the
        last closed 4h SMA20 (ctx['mid4']), exactly like Book15.manage; the score itself is only
        evaluated on a NEWLY closed 15m candle, and a firing exit closes at the current price,
        again like the live Book15. Stop/TP (paper_step) run first: they are resting price levels."""
        C = s.pcfg[coin]; cs = s.cs[coin]; p = cs['pos']
        if not p or not C.get('book15_exit'):
            return None, None
        g = 1 if p['side'] == 'L' else -1
        mid4 = (cs['ctx'] or {}).get('mid4')
        if mid4 is not None and not p.get('mid4h_hit') and g * (px - mid4) >= 0:
            p['mid4h_hit'] = True
        if not s._bars15(coin):
            return None, None
        x = s.bars15[coin]
        if x is None or not len(x):
            return None, None
        last15 = str(x.index[-1])
        if p.get('last15') == last15:
            return None, None
        p['last15'] = last15
        zc = float(x['c'].iloc[-1])
        uz = g * (zc - p['entry']) * p['q'] - p['fee_in']
        sc = score15_last(x, p['side'])
        p['outlook'] = dict(candle=last15, score=sc, uz=round(uz, 2), mid4h_hit=bool(p.get('mid4h_hit')))
        fire, weak = score_exit_check(uz, sc, bool(p.get('mid4h_hit')), bool(p.get('weak_prev')), C)
        p['weak_prev'] = weak
        if fire:
            return 'EXIT_15M', px
        return None, None

    # ---------- trading (paper) ----------
    def _open(s, coin, side, det, tnow):
        C = s.pcfg[coin]; cs = s.cs[coin]
        fill = float(det['band'])
        equity = C['capital_usdt'] + s.realized
        n_open = sum(1 for c2 in s.cs.values() if c2['pos'])
        deployed = sum(float(c2['pos']['notional']) for c2 in s.cs.values() if c2['pos'])
        notional, budget, mult = gross_notional(equity, s.peak_realized, n_open, C, deployed)
        if notional <= 0:
            s.ev('TRENDMR_SKIP', coin,
                 f"TrendMR-1H – bỏ qua tín hiệu {coin}: ngân sách còn lại = 0 "
                 f"(đã dùng {deployed:,.0f}$ / ngân sách {budget:,.0f}$ sau van tiết lưu x{mult:g}).")
            return
        notional *= C['notional_x']
        if side == 'S':
            notional *= float((C.get('short_size_factor') or {}).get(coin, 1.0))
        q = notional / fill
        g = 1 if side == 'L' else -1
        cs['pos'] = dict(side=side, entry=fill, q=q, notional=round(notional, 4),
                         fee_in=notional * C['fee_maker'], t0=str(tnow), t0_ts=time.time(),
                         stop=fill * (1.0 - g * C['sl_pct'] / 100.0), tp=float(det['mid']),
                         be_done=False, peak_move=0.0,
                         mid4h_hit=False, weak_prev=False, last15=None, outlook=None)
        s.ev('TRENDMR_ENTRY', coin,
             f"TrendMR-1H – VÀO {'LONG' if side == 'L' else 'SHORT'} {coin} {q:.5f} @ {fill:.5g} "
             f"(notional {notional:,.0f}$ — sizing G2 lãi kép 1.3.4 (chia theo ngân sách còn lại): ngân sách {budget:,.0f}$ = equity đã chốt {equity:,.0f}$ x{C['gross_lev']:g}"
             f"{'' if mult >= 1.0 else f' x{mult:g} van tiết lưu DD'}, đã dùng {deployed:,.0f}$, vị thế thứ {n_open + 1} lấy theo phần còn lại, trần 50% ngân sách/lệnh). "
             f"SL {cs['pos']['stop']:.5g} (-{C['sl_pct']:g}%), TP dải giữa {float(det['mid']):.5g}, khóa lãi theo mốc {C['lock_step_pct']:g}%/≥{C['lock_frac']:.0%} (kiểu BaseAster), thoát phụ Book15 15m (điểm ≤{C['book15_th']}/{C['book15_th_small']} hai nến, sau khi chạm dải giữa 4h), không giới hạn thời gian giữ.",
             tg=True, price=fill, side=('LONG' if side == 'L' else 'SHORT'), size=q, notional=notional,
             sl=cs['pos']['stop'], tp=float(det['mid']))
        s.save()

    def _close(s, coin, reason, exit_px, tnow):
        C = s.pcfg[coin]; cs = s.cs[coin]; p = cs['pos']
        net, gross, fee_out = settle(p, exit_px, C, reason)
        row = dict(t0=p['t0'], t1=str(tnow), coin=coin, side=('LONG' if p['side'] == 'L' else 'SHORT'),
                   entry=p['entry'], exit=round(exit_px, 8), q=p['q'], notional=p['notional'],
                   volume=round(exit_px * p['q'], 2), fee_in=round(p['fee_in'], 4), fee_out=round(fee_out, 4),
                   gross=round(gross, 4), net=round(net, 4), reason=reason,
                   hold_h=round((time.time() - p['t0_ts']) / 3600, 2), be_armed=bool(p['be_done'] or p.get('lock_armed')))
        try:
            pd.DataFrame([row]).to_csv(s.trf, mode='a', header=not os.path.exists(s.trf), index=False)
        except Exception as e_:
            print('trendmr csv', e_, flush=True)
        s.realized += net
        if str(tnow)[:10] == s.day['date']:
            s.day['realized'] += net
        s.peak_realized = max(s.peak_realized, C['capital_usdt'] + s.realized)
        s.stats['n'] = int(s.stats.get('n') or 0) + 1
        if net > 0:
            s.stats['wins'] = int(s.stats.get('wins') or 0) + 1
            s.stats['gp'] = float(s.stats.get('gp') or 0.0) + net
        else:
            s.stats['gl'] = float(s.stats.get('gl') or 0.0) - net
        s.stats['net'] = float(s.stats.get('net') or 0.0) + net
        s.last_nets = (s.last_nets + [round(net, 4)])[-int(C.get('review_roll_window') or 50):]
        if reason == 'SL':
            cs['consec_sl'] += 1
            if cs['consec_sl'] == int(C.get('consec_sl_alert') or 0):
                # BaseAster style: consecutive losses raise an ALERT only - entries are never blocked
                s.ev('TRENDMR_ALERT', coin, f"TrendMR-1H – CẢNH BÁO: {coin} vừa chịu {cs['consec_sl']} SL liên tiếp (chỉ cảnh báo, vẫn giao dịch bình thường).", tg=True)
        else:
            cs['consec_sl'] = 0
        cs['pos'] = None
        s.ev('TRENDMR_EXIT', coin,
             f"TrendMR-1H – ĐÓNG {'LONG' if p['side'] == 'L' else 'SHORT'} {coin}: {reason}; giá ra {exit_px:.5g} "
             f"(vào {p['entry']:.5g}), lãi/lỗ ròng {net:+.2f}$.",
             tg=True, price=exit_px, side=row['side'], net=round(net, 4), why=reason)
        s.save()
        s._review_check(tnow)

    def _review_check(s, tnow):
        """Governance gate evaluation after every close. On a breach: REVIEW_HOLD — new entries stop
        (open positions keep being managed and can exit), a TRENDMR_REVIEW_HOLD event fires, and the
        bot stays held until clear_review_hold() is called after the hourly review + user approval."""
        if s.review_hold:
            return
        reasons = review_evaluate(s.stats, s.last_nets, s.C)
        if not reasons:
            return
        wr, pf = stats_wr_pf(s.stats)
        s.review_hold = True
        s.review_info = dict(since=str(tnow), reasons=reasons, closed=int(s.stats.get('n') or 0),
                             winrate=round(wr, 1), pf=round(pf, 3), net=round(float(s.stats.get('net') or 0.0), 2))
        s.ev('TRENDMR_REVIEW_HOLD', '*',
             "TrendMR-1H – ⛔ REVIEW BẮT BUỘC: hiệu suất thực tế dưới ngưỡng kỳ vọng — " +
             '; '.join(reasons) +
             f". ĐÃ DỪNG VÀO LỆNH MỚI (vị thế đang mở vẫn quản trị/thoát bình thường). Chiến lược phải "
             f"được review ở phiên kiểm tra 1h và chỉ chạy tiếp sau khi user duyệt điều chỉnh/cho phép.",
             tg=True, **s.review_info)
        s.save()

    def clear_review_hold(s, note=''):
        """Resume entries after a completed review + user approval (ops action, called by the operator)."""
        if not s.review_hold:
            return False
        s.review_hold = False
        info = s.review_info
        s.review_info = None
        s.ev('TRENDMR_REVIEW_CLEAR', '*',
             f"TrendMR-1H – ĐÃ GỠ REVIEW_HOLD sau review và duyệt của user. {note}".strip(),
             tg=True, was=info)
        s.save()
        return True

    def tick(s, tnow, m):
        if not s.on:
            return
        try:
            today = str(tnow)[:10]
            if s.day['date'] != today:
                s.day = dict(date=today, realized=0.0)
                s.day_alerted = False
            unreal = 0.0
            for coin in s.coins:
                cs = s.cs[coin]
                px = m.get(coin) if m else None
                if px:
                    cs['price'] = float(px)
                if not s._bars(coin):
                    continue
                s._roll_bar(coin)
                px = cs['price']
                if px is None:
                    continue
                p = cs['pos']
                if p:
                    g = 1 if p['side'] == 'L' else -1
                    unreal += g * (px - p['entry']) * p['q'] - p['fee_in']
                    if s.dry:
                        reason, exit_px = paper_step(p, px, time.time(), s.pcfg[coin])
                        if not reason:
                            reason, exit_px = s._book15_step(coin, px)
                        if reason:
                            s._close(coin, reason, exit_px, tnow)
                    continue
                if cs['touch_used_long'] and cs['touch_used_short']:
                    continue
                if s.review_hold:
                    # governance gate: performance below the expected thresholds - no new entries
                    # until the hourly review is done and the user approves (positions above still run)
                    continue
                side, det = entry_signal(cs['ctx'], px, cs['prev_touch_long'], cs['prev_touch_short'], s.pcfg[coin])
                if side is None:
                    continue
                if side == 'L':
                    cs['touch_used_long'] = True
                else:
                    cs['touch_used_short'] = True
                if not s.dry and not s.C.get('allow_live'):
                    s.ev('TRENDMR_LIVE_LOCKED', coin,
                         f"TrendMR-1H – tín hiệu {'LONG' if side == 'L' else 'SHORT'} {coin} @ {det['band']:.5g}: "
                         f"đường LIVE chưa mở (allow_live=false) – chỉ ghi nhận, không gửi lệnh.", tg=True)
                    continue
                if not s.dry:
                    s.ev('TRENDMR_LIVE_LOCKED', coin,
                         f"TrendMR-1H – {'LONG' if side == 'L' else 'SHORT'} {coin}: thực thi live chưa được bật trong bản này.", tg=True)
                    continue
                s._open(coin, side, det, tnow)
            equity = s.C['capital_usdt'] + s.realized + unreal
            if equity > s.peak_equity:
                s.peak_equity = equity
            dd = (equity / s.peak_equity - 1.0) * 100.0 if s.peak_equity > 0 else 0.0
            # BaseAster-style alerts (its dd_alert / daily figures never stop trading):
            if not s.day_alerted and s.day['realized'] <= -s.C['daily_loss_alert_pct'] / 100.0 * s.C['capital_usdt']:
                s.day_alerted = True
                s.ev('TRENDMR_ALERT', '*', f"TrendMR-1H – CẢNH BÁO ngày: PnL đã chốt hôm nay {s.day['realized']:+,.2f}$ (≤ −{s.C['daily_loss_alert_pct']:g}% vốn). Chỉ cảnh báo theo kiểu BaseAster – bot vẫn giao dịch bình thường.", tg=True)
            if dd <= -s.C['dd_alert_pct'] and not s.dd_alerted:
                s.dd_alerted = True
                s.ev('TRENDMR_ALERT', '*', f"TrendMR-1H – CẢNH BÁO drawdown {dd:.1f}% từ đỉnh (equity {equity:,.2f}$). Chỉ cảnh báo – bot vẫn giao dịch bình thường.", tg=True)
            if dd > -s.C['dd_alert_pct'] / 2:
                s.dd_alerted = False
            s.save()
        except Exception as ex:
            import traceback
            s.err = repr(ex)[:200]
            print('trendmr tick error', traceback.format_exc()[-600:], flush=True)
