"""Module bắt đáy ASTER (bottom-bounce LONG) – thay LONG của bước 3 cũ.
Tín hiệu khi nến 1H đóng: giảm ≥3% so với đỉnh 6 nến 1H; từng bị BaseAster chặn LONG (BB dưới 4h + mom 0.7%);
BB4h rộng ≤15%; BTC 24h > −8%; giãn cách 12h giữa các tín hiệu đủ điều kiện 1–4 (kể cả lúc tài khoản bận).
Vào: ≤3 phút, post-only bid 20s rồi IOC phần còn, bỏ nếu ask > signal_close×1.01; notional 4.9× vốn, x5 isolated.
Thoát: TP +1.5% / SL −2% neo signal_close; hết 6h; backstop giá dưới SL thêm 0.3% liên tục 25s.
Không dùng trailing 80% BaseAster. SHORT bước 3 và BaseAster giữ nguyên khi module không giữ lệnh.
"""
import os, json, time, math, threading, datetime as dt
import pandas as pd
import bnx as hlx
from bnx import HLXError

COIN = 'ASTER'
DEF = dict(
    enabled=True, dry_run=False, coin='ASTER',
    so_nen=6, giam_pct=3.0, can_bi_chan=True, mom_pct=0.7,
    gian_cach_h=12, loc_bb4h_rong_max_pct=15.0, loc_btc_24h_min_pct=-8.0,
    notional_x_von=4.9, tp_pct=1.5, sl_pct=2.0, giu_toi_da_h=6,
    postonly_wait_s=20, entry_window_min=3, entry_dev_pct=1.0,
    ioc_ask_buf_pct=0.3, sl_backstop_extra_pct=0.3, sl_backstop_s=25,
    lev=5, fee_est=0.0005,
)

def vn_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=7)

H1 = pd.Timedelta('1h')
H4 = pd.Timedelta('4h')
M15 = pd.Timedelta('15min')
M1 = pd.Timedelta('1min')


# ---------------- pure signal helpers (unit-testable, no network) ----------------
def drop_ok(h1_closed, signal_i, so_nen=6, giam_pct=3.0):
    """h1_closed: DataFrame index=open VN, cols h,c; signal_i = index of just-closed bar.
    Returns (ok, info). Missing/short data -> fail closed."""
    if h1_closed is None or len(h1_closed) < so_nen or signal_i < so_nen - 1:
        return False, dict(why='missing_1h')
    win = h1_closed.iloc[signal_i - so_nen + 1:signal_i + 1]
    if len(win) != so_nen:
        return False, dict(why='missing_1h_window')
    # contiguous hours
    for k in range(1, so_nen):
        if win.index[k] - win.index[k - 1] != H1:
            return False, dict(why='gap_1h')
    peak = float(win.h.max()); close = float(win.c.iloc[-1])
    if peak <= 0:
        return False, dict(why='bad_peak')
    drop = (close / peak - 1.0) * 100.0
    ok = drop <= -float(giam_pct)
    return ok, dict(drop_pct=round(drop, 4), peak=peak, close=close, win0=str(win.index[0]), win1=str(win.index[-1]))


def bb4h_asof(h4_closed, t):
    """Most recent fully closed 4h bar as of timestamp t. Bar open T closes at T+4h."""
    if h4_closed is None or not len(h4_closed):
        return None
    t = pd.Timestamp(t)
    ok = h4_closed.index + H4 <= t
    if not ok.any():
        return None
    return h4_closed.loc[h4_closed.index[ok][-1]]


def ref15_asof(m15_closed, t, lookback=H4):
    """Last 15m close whose bar ended <= t-lookback."""
    if m15_closed is None or not len(m15_closed):
        return None
    t = pd.Timestamp(t)
    ended = m15_closed.index + M15
    ok = ended <= (t - lookback)
    if not ok.any():
        return None
    return float(m15_closed.c.loc[m15_closed.index[ok][-1]])


def prior_block_ok(m1, h4_closed, m15_closed, t0, t1, mom_pct=0.7):
    """Some 1m bar in [t0, t1] had low <= BB4h.lower(asof minute) AND low <= 15m_ref_4h_earlier * (1 - mom_pct/100).
    Missing required series -> fail closed."""
    if m1 is None or not len(m1) or h4_closed is None or not len(h4_closed) or m15_closed is None or not len(m15_closed):
        return False, dict(why='missing_prior_data')
    t0, t1 = pd.Timestamp(t0), pd.Timestamp(t1)
    rows = m1[(m1.index >= t0) & (m1.index <= t1)]
    if not len(rows):
        return False, dict(why='no_1m_in_window')
    thr = 1.0 - float(mom_pct) / 100.0
    hits = 0
    for ts, r in rows.iterrows():
        # as of the START of this 1m bar: a 4h bar closing at ts+1m contains this minute -> not known yet (no lookahead)
        b = bb4h_asof(h4_closed, ts)
        if b is None or not (b.lo == b.lo):
            continue
        ref = ref15_asof(m15_closed, ts)
        if ref is None or ref <= 0:
            continue
        lo = float(r.l if hasattr(r, 'l') else r['l'] if 'l' in r.index else r['low'])
        if lo <= float(b.lo) and lo <= ref * thr:
            hits += 1
            if hits >= 1:
                return True, dict(hit_ts=str(ts), low=lo, bb_lo=float(b.lo), ref15=ref)
    return False, dict(why='no_prior_block_minute')


def bb_width_ok(h4_closed, t, max_pct=15.0):
    b = bb4h_asof(h4_closed, t)
    if b is None:
        return False, dict(why='missing_bb4h')
    mid = float(b.mid) if 'mid' in b.index or hasattr(b, 'mid') else None
    if mid is None or mid != mid or mid <= 0:
        return False, dict(why='bad_bb_mid')
    bw = (float(b.up) - float(b.lo)) / mid * 100.0
    ok = bw <= float(max_pct)
    return ok, dict(bw_pct=round(bw, 4), bb_lo=float(b.lo), bb_up=float(b.up), bb_mid=mid)


def btc_24h_ok(btc_now, btc_24h_ago, min_pct=-8.0):
    if btc_now is None or btc_24h_ago is None or btc_24h_ago <= 0:
        return False, dict(why='missing_btc')
    chg = (float(btc_now) / float(btc_24h_ago) - 1.0) * 100.0
    ok = round(chg, 9) > float(min_pct)  # strict: exactly −8% is blocked (float-safe)
    return ok, dict(btc_chg_24h=round(chg, 4))


def gap_ok(last_sig_t, now_t, gap_h=12):
    if last_sig_t is None:
        return True, dict(gap_h=None)
    dt_h = (pd.Timestamp(now_t) - pd.Timestamp(last_sig_t)).total_seconds() / 3600.0
    return dt_h >= float(gap_h), dict(gap_h=round(dt_h, 4))



def _merge_det(stage, *parts, **base):
    det = dict(base, stage=stage)
    for part in parts:
        if not part: continue
        for k, v in part.items():
            det[k if k not in det else (k + '_')] = v
    return det

def evaluate_signal(h1, h4, m15, m1, signal_i, t_entry, btc_now, btc_24h_ago, last_sig_t, C=None):
    """Full primary checks 1–4 + gap. Does NOT check account flat.
    Returns (qualified:bool, detail:dict). Fail-closed on missing data."""
    C = dict(DEF, **(C or {}))
    ok1, i1 = drop_ok(h1, signal_i, C['so_nen'], C['giam_pct'])
    if not ok1:
        return False, _merge_det('drop', i1)
    sig_bar = h1.index[signal_i]
    t_close = sig_bar + H1
    t0 = h1.index[signal_i - C['so_nen'] + 1]
    if C.get('can_bi_chan', True):
        ok2, i2 = prior_block_ok(m1, h4, m15, t0, t_entry, C['mom_pct'])
        if not ok2:
            return False, _merge_det('prior_block', i1, i2, signal_close=i1['close'], t_close=str(t_close))
    else:
        i2 = dict(prior_skipped=True)
    ok3, i3 = bb_width_ok(h4, t_close, C['loc_bb4h_rong_max_pct'])
    if not ok3:
        return False, _merge_det('bb_width', i1, i2, i3, signal_close=i1['close'], t_close=str(t_close))
    ok4, i4 = btc_24h_ok(btc_now, btc_24h_ago, C['loc_btc_24h_min_pct'])
    if not ok4:
        return False, _merge_det('btc', i1, i2, i3, i4, signal_close=i1['close'], t_close=str(t_close))
    ok5, i5 = gap_ok(last_sig_t, t_close, C['gian_cach_h'])
    if not ok5:
        return False, _merge_det('gap', i1, i2, i3, i4, i5, signal_close=i1['close'], t_close=str(t_close))
    return True, _merge_det('ok', i1, i2, i3, i4, i5, signal_bar=str(sig_bar), t_close=str(t_close),
                            signal_close=i1['close'], peak=i1['peak'], drop_pct=i1['drop_pct'])


def entry_limits(signal_close, ask, bid, C=None):
    """Return (skip_why_or_None, postonly_px, ioc_limit)."""
    C = dict(DEF, **(C or {}))
    sc = float(signal_close)
    if ask is None or bid is None or ask <= 0 or bid <= 0:
        return 'no_book', None, None
    if ask > sc * (1.0 + C['entry_dev_pct'] / 100.0):
        return f'ask {ask:.6g} > signal_close*{1+C["entry_dev_pct"]/100:.4f}', None, None
    ioc_cap = min(ask * (1.0 + C['ioc_ask_buf_pct'] / 100.0), sc * (1.0 + C['entry_dev_pct'] / 100.0))
    return None, float(bid), float(ioc_cap)


def tp_sl_px(signal_close, C=None):
    C = dict(DEF, **(C or {}))
    sc = float(signal_close)
    return sc * (1.0 + C['tp_pct'] / 100.0), sc * (1.0 - C['sl_pct'] / 100.0)


def size_notional(equity, available, lev, x_von, px):
    """Notional = x_von * equity, capped by available*lev*0.97. No extra 5x on top of x_von."""
    if equity is None or equity <= 0 or px is None or px <= 0:
        return 0.0
    n = float(x_von) * float(equity)
    if available is not None:
        n = min(n, float(available) * float(lev) * 0.97)
    return max(0.0, n)


# ---------------- live module ----------------
class BatDay:
    def __init__(s, B, cfg, LIVE, candles, bb_fn, notify, emit, busy_check=None):
        s.B = B
        raw = dict(cfg.get('batday') or {})
        s.C = dict(DEF, **raw)
        s.on = bool(s.C.get('enabled'))
        s.mod_dry = bool(s.C.get('dry_run'))  # module-level paper (signals only)
        s.L = LIVE; s.hx = LIVE.hx; s.candles = candles; s.bb = bb_fn
        s.notify = notify; s.emit = emit
        s.busy_check = busy_check or (lambda: (False, ''))
        s.path = B + 'state/batday.json'
        s.trf = B + 'data/batday_trades.csv'
        s.lk = threading.Lock()
        try:
            with open(s.path, encoding='utf-8') as f_:
                st = json.load(f_)
        except Exception:
            st = {}
        s.last_sig_t = pd.Timestamp(st['last_sig_t']) if st.get('last_sig_t') else None
        s.last_bar = pd.Timestamp(st['last_bar']) if st.get('last_bar') else None
        s.pos = st.get('pos')  # bottom-bounce position only
        s.pend = st.get('pend')  # pending entry state machine
        s.hist = st.get('hist', [])
        s.err = None
        s.fetch_t = 0
        s.A = None; s.H4 = None; s.M15 = None; s.M1 = None
        s.btc_px = None; s.btc_24 = None
        s.last_chk = 0
        s.sl_breach_t = None
        if s.pos or s.pend:
            s.L.foreign.add(s.C['coin'])

    def holding(s):
        return bool(s.pos) or bool(s.pend)

    def save(s):
        d = dict(
            last_sig_t=str(s.last_sig_t) if s.last_sig_t is not None else None,
            last_bar=str(s.last_bar) if s.last_bar is not None else None,
            pos=s.pos, pend=s.pend, hist=s.hist[-50:],
            updated=str(vn_now().replace(microsecond=0)), cfg=s.C,
        )
        try:
            with open(s.path + '.tmp', 'w', encoding='utf-8') as f_:
                json.dump(d, f_, ensure_ascii=False, indent=1, default=str)
            os.replace(s.path + '.tmp', s.path)
        except Exception as ex:
            print('batday save', ex, flush=True)

    def ev(s, event, reason, tg=True, **kw):
        e = s.L.ev(event, s.C['coin'], reason, **kw)
        if tg:
            try:
                s.notify('[LIVE] ' + reason)
            except Exception:
                pass
        return e

    def status(s):
        return dict(
            enabled=s.on, dry_run=s.mod_dry or s.L.dry, replacement_of_step3_long=True,
            pos=s.pos, pend=s.pend, last_sig_t=str(s.last_sig_t) if s.last_sig_t is not None else None,
            last_bar=str(s.last_bar) if s.last_bar is not None else None, err=s.err,
        )

    def _bars(s, force=False):
        now = pd.Timestamp(vn_now()); need = now.floor('h') - H1
        if not force and s.A is not None and len(s.A) and s.A.index[-1] >= need:
            return True
        if not force and time.time() - s.fetch_t < 15 and s.A is not None:
            return True
        s.fetch_t = time.time()
        try:
            a = s.candles(s.C['coin'], '1h', 5); a = a[a.closed]
            h4 = s.bb(s.candles(s.C['coin'], '4h', 30)); h4 = h4[h4.closed]
            m15 = s.candles(s.C['coin'], '15m', 5); m15 = m15[m15.closed]
            # rename low/high if needed
            for df in (a, h4, m15):
                if 'l' not in df.columns and 'low' in df.columns:
                    df.rename(columns={'low': 'l', 'high': 'h', 'open': 'o', 'close': 'c'}, inplace=True)
            s.A, s.H4, s.M15 = a, h4, m15
            # BTC 24h from 1h closes
            b = s.candles('BTC', '1h', 2); b = b[b.closed]
            if len(b) >= 25:
                s.btc_px = float(b.c.iloc[-1]); s.btc_24 = float(b.c.iloc[-25])
            else:
                s.btc_px = s.btc_24 = None
            s.err = None
            return True
        except Exception as ex:
            s.err = f'data: {ex}'[:150]
            return s.A is not None

    def _m1_window(s, t0, t1):
        try:
            days = max(0.3, (pd.Timestamp(vn_now()) - pd.Timestamp(t0)).total_seconds() / 86400.0 + 0.05)
            m1 = s.candles(s.C['coin'], '1m', days)
            if 'l' not in m1.columns and 'low' in m1.columns:
                m1 = m1.rename(columns={'low': 'l', 'high': 'h', 'open': 'o', 'close': 'c'})
            s.M1 = m1
            return m1
        except Exception as ex:
            s.err = f'm1: {ex}'[:120]
            return None

    def tick(s, LB, tnow, m, account_flat_fn=None):
        if not s.on:
            return
        try:
            if s.pend:
                s._progress_entry(LB, tnow, m)
            if s.pos:
                s._manage(tnow, m)
            if not s._bars():
                return
            A = s.A
            if s.last_bar is None:
                # start from latest closed only (no backfill entries)
                s.last_bar = A.index[-1]
                s.save()
                return
            new = [j for j in range(len(A)) if A.index[j] > s.last_bar]
            for j in new:
                s.last_bar = A.index[j]
                s.save()  # bar consumed before any order -> never re-processed after a restart
                s._on_closed_bar(j, LB, tnow, m, account_flat_fn)
            s.save()
        except Exception as ex:
            import traceback
            s.err = repr(ex)[:200]
            print('batday tick error', traceback.format_exc()[-600:], flush=True)

    def _on_closed_bar(s, j, LB, tnow, m, account_flat_fn):
        C = s.C; A = s.A
        sig_bar = A.index[j]; t_close = sig_bar + H1
        age = (pd.Timestamp(tnow) - t_close).total_seconds()
        # cheap drop filter first — avoid 1m history fetch on non-candidate bars
        ok_drop, _ = drop_ok(A, j, C['so_nen'], C['giam_pct'])
        if not ok_drop:
            return
        t0 = A.index[j - C['so_nen'] + 1]
        m1 = s._m1_window(t0, min(pd.Timestamp(tnow), t_close + pd.Timedelta(minutes=C['entry_window_min'])))
        btc_now = m.get('BTC', s.btc_px); btc_24 = s.btc_24
        ok, det = evaluate_signal(A, s.H4, s.M15, m1, j, pd.Timestamp(tnow), btc_now, btc_24, s.last_sig_t, C)
        if not ok:
            if det.get('stage') in ('drop', 'gap'):
                return  # quiet on non-events
            s.ev('BATDAY_SKIP', f"BẮT ĐÁY – bỏ nến {str(sig_bar)[:16]}: {det.get('stage')} {det.get('why','')}", tg=False, **{k: det[k] for k in det if k in ('stage','why','drop_pct','bw_pct','btc_chg_24h')})
            return
        # qualified signal (conditions 1-4 + gap) — always advances cooldown
        s.last_sig_t = t_close
        s.hist.append(dict(time=str(tnow), **det))
        s.save()
        s.ev('BATDAY_SIGNAL',
             f"BẮT ĐÁY – tín hiệu LONG ASTER nến 1h {str(sig_bar)[:16]} đóng {str(t_close)[11:16]}: giảm {det['drop_pct']:.2f}% từ đỉnh 6 nến, BB4h {det.get('bw_pct',0):.2f}%, BTC24h {det.get('btc_chg_24h',0):+.2f}%.",
             tg=True, signal_close=det['signal_close'], drop_pct=det['drop_pct'])
        if age > C['entry_window_min'] * 60:
            s.ev('BATDAY_SKIP', f"BẮT ĐÁY – quá cửa sổ {C['entry_window_min']} phút (đã {age/60:.1f} phút)", tg=False)
            return
        if s.pos or s.pend:
            s.ev('BATDAY_SKIP', 'BẮT ĐÁY – đang giữ/đang vào lệnh bắt đáy', tg=True)
            return
        busy, why = s.busy_check()
        if account_flat_fn is not None:
            flat, fwhy = account_flat_fn()
            if not flat:
                busy, why = True, fwhy
        if busy:
            s.ev('BATDAY_SKIP', f'BẮT ĐÁY – tài khoản bận: {why} (vẫn tính giãn cách 12h)', tg=True)
            return
        s._start_entry(det, tnow, m)

    # ---------------- execution (entry state machine) ----------------
    def _now(s):
        return time.time()  # injectable clock for tests

    def _xpos(s):
        """Fresh exchange position of the module coin (LONG only) or None."""
        st = s.L.refresh(0.0)
        x = (st.get('positions') or {}).get(s.C['coin'])
        return x if (x and float(x['szi']) > 0) else None

    def _note_fill(s, p, q, avg, tnow):
        q = float(q)
        if q <= 0:
            return False
        if not p.get('t_fill'):
            p['t_fill'] = str(tnow); p['t_fill_ms'] = int(s._now() * 1000)  # 6h deadline counts from the first real fill
        grew = q > float(p.get('filled') or 0) + 1e-12
        p['filled'] = q
        if avg:
            p['avg'] = float(avg)
        return grew

    def _start_entry(s, det, tnow, m):
        C = s.C; c = C['coin']; sc = float(det['signal_close'])
        if s.L.dry or s.mod_dry:
            s.ev('BATDAY_SKIP', f"BẮT ĐÁY – DRY-RUN: tín hiệu ok close={sc:.5g} (không gửi lệnh)", tg=True, signal_close=sc)
            return
        try:
            bid, ask = s.hx.book(c)
            why, po_px, ioc_lim = entry_limits(sc, ask, bid, C)
            if why:
                s.ev('BATDAY_SKIP', f'BẮT ĐÁY – không vào: {why}', tg=True); return
            st = s.L.refresh(0.0)
            if st.get('positions'):
                s.ev('BATDAY_SKIP', 'BẮT ĐÁY – sàn còn vị thế: ' + ','.join(sorted(st['positions'])), tg=True); return
            base = float(st['base']); avail = float(st.get('available') or base)
            notional = size_notional(base, avail, C['lev'], C['notional_x_von'], ask or sc)
            sp = s.hx.asset(c)['spec']
            po_px = hlx.px_round(po_px, sp, 'down')
            sz = hlx.sz_round(notional / max(po_px, sc), sp, 'down')
            if sz <= 0 or sz < float(getattr(sp, 'min_qty', 0) or 0) or sz * po_px < s.hx.min_notional(c):
                s.ev('BATDAY_SKIP', f'BẮT ĐÁY – size quá nhỏ {sz}', tg=True); return
            r = s.hx.set_leverage(c, C['lev'], cross=False, label='BATDAY leverage')
            if r.get('status') != 'ok':
                s.ev('BATDAY_ENTRY_FAILED', f'BẮT ĐÁY – set leverage thất bại {r}'[:300], tg=True); return
        except Exception as ex:
            s.ev('BATDAY_ENTRY_FAILED', f'BẮT ĐÁY – chuẩn bị vào lệnh lỗi: {ex}'[:400], tg=True); return
        tp, sl = tp_sl_px(sc, C)
        # persisted BEFORE sending: a crash after the POST is reconciled on restart (cancel + read position), never orphaned
        s.pend = dict(
            signal_close=sc, signal_bar=det['signal_bar'], t_close=det['t_close'],
            t0=str(tnow), t_place_ms=int(s._now() * 1000), oid=None,
            sz_target=float(sz), filled=0.0, avg=None, fee=0.0, t_fill=None, t_fill_ms=None,
            po_px=float(po_px), ioc_lim=float(ioc_lim), tp=tp, sl=sl, sl_oid=None, tp_oid=None, tp_q=None,
            notional_target=round(notional, 4), base=base, status='placing', ioc_sent=False, no_ioc=False,
        )
        s.L.foreign.add(c); s.save()
        try:
            s.hx.max_open_notional = sz * ioc_lim * 1.01
            r = s.hx.limit_postonly(c, True, sz, po_px, label=f'BATDAY POSTONLY LONG {c}')
            stt = [dict(kind='resting', oid='dry')] if r.get('dry') else hlx.parse_statuses(r)  # API error -> HLXError
            k = stt[0]['kind'] if stt else 'error'
            p = s.pend
            if k == 'filled':
                p['oid'] = stt[0].get('oid')
                s._note_fill(p, stt[0]['totalSz'], stt[0]['avgPx'], tnow)
            elif k == 'resting':
                p['oid'] = stt[0].get('oid')
            else:
                # GTX expired (would take liquidity): nothing rests -> go straight to the IOC step for the whole size
                p['status'] = 'postonly_rejected'; s.save()
                s.ev('BATDAY_ENTRY_POSTONLY', f"BẮT ĐÁY – post-only bị từ chối ({stt[0].get('error') if stt else r}); chuyển IOC"[:300], tg=False)
                s._finish_entry(tnow, m); return
            p['status'] = 'wait_postonly'; s.save()
            s.ev('BATDAY_ENTRY_POSTONLY', f"BẮT ĐÁY – đặt post-only BUY {sz:g} @ {po_px:.5g}, chờ {C['postonly_wait_s']}s (signal {sc:.5g})", tg=True)
            if p['filled'] > 0:
                s._protect_partial(tnow)
            if C['postonly_wait_s'] <= 0 or p.get('force_finish') or p['filled'] >= p['sz_target'] * 0.98:
                s._finish_entry(tnow, m)
        except Exception as ex:
            s.ev('BATDAY_ENTRY_FAILED', f'BẮT ĐÁY – gửi post-only lỗi: {ex} → đối soát (huỷ lệnh chờ, đọc vị thế)'[:400], tg=True)
            if s.pend:
                s.pend['status'] = 'reconcile'; s.pend['no_ioc'] = True; s.save()
                s._finish_entry(tnow, m)

    def _progress_entry(s, LB, tnow, m):
        p = s.pend
        if not p:
            return
        C = s.C
        if p.get('status') in ('placing', 'reconcile', 'cancel_pending', 'finishing', 'postonly_rejected') or p.get('force_finish'):
            if p.get('status') == 'placing':  # restart while the POST state is unknown: no IOC, reconcile only
                p['no_ioc'] = True
            s._finish_entry(tnow, m); return
        age_sig = (pd.Timestamp(tnow) - pd.Timestamp(p['t_close'])).total_seconds()
        waited = (s._now() * 1000 - p['t_place_ms']) / 1000.0
        try:
            x = s._xpos()
            if x and s._note_fill(p, abs(float(x['szi'])), x.get('entryPx'), tnow):
                s.save(); s._protect_partial(tnow)
        except Exception as ex:
            s.L.ev('LIVE_WARN', C['coin'], f'BATDAY fill check {ex}'[:200])
        if p.get('force_finish') or float(p.get('filled') or 0) >= p['sz_target'] * 0.98:
            s._finish_entry(tnow, m); return
        if waited < C['postonly_wait_s'] and age_sig <= C['entry_window_min'] * 60:
            return
        s._finish_entry(tnow, m)

    def _cancel_entry_orders(s, p):
        """Cancel the maker entry order and CONFIRM no opening order rests on the coin. True only when confirmed."""
        c = s.C['coin']; oid = p.get('oid')
        if oid not in (None, 'dry'):
            try:
                s.hx.cancel(c, oid, label='BATDAY cancel postonly')
            except Exception as ex:
                s.L.ev('LIVE_WARN', c, f'BATDAY cancel postonly {ex}'[:200])  # e.g. already filled -> verified below
        try:
            for attempt in range(2):
                oo = [o for o in s.hx.open_orders() if o.get('coin') == c and not o.get('reduceOnly') and not o.get('isTrigger')]
                if not oo:
                    return True
                for o in oo:
                    try:
                        s.hx.cancel(c, o['oid'], label='BATDAY cancel opening order')
                    except Exception as ex:
                        s.L.ev('LIVE_WARN', c, f'BATDAY cancel {o.get("oid")} {ex}'[:200])
            oo = [o for o in s.hx.open_orders() if o.get('coin') == c and not o.get('reduceOnly') and not o.get('isTrigger')]
            return not oo
        except Exception as ex:
            s.L.ev('LIVE_WARN', c, f'BATDAY open orders check {ex}'[:200])
            return False

    def _finish_entry(s, tnow, m):
        p = s.pend; C = s.C; c = C['coin']
        if not p:
            return
        try:
            if not s._cancel_entry_orders(p):
                # never IOC while the maker order may still be live; protect what is filled and retry next tick
                if p.get('status') != 'cancel_pending':
                    s.ev('BATDAY_WARN', 'BẮT ĐÁY – chưa xác nhận huỷ được lệnh post-only, thử lại vòng sau (không IOC)', tg=True)
                p['status'] = 'cancel_pending'; s.save()
                x = s._xpos()
                if x and s._note_fill(p, abs(float(x['szi'])), x.get('entryPx'), tnow):
                    s.save(); s._protect_partial(tnow)
                return
            p['status'] = 'finishing'; s.save()
            x = s._xpos()
            if x:
                s._note_fill(p, abs(float(x['szi'])), x.get('entryPx'), tnow)
            else:
                p['filled'] = 0.0
            filled = float(p.get('filled') or 0)
            remain = float(p['sz_target']) - filled
            age_sig = (pd.Timestamp(tnow) - pd.Timestamp(p['t_close'])).total_seconds()
            if remain > 0 and not p.get('ioc_sent') and not p.get('no_ioc') and not p.get('force_finish'):
                if age_sig > C['entry_window_min'] * 60:
                    s.L.ev('BATDAY_INFO', c, f'BATDAY no IOC: > {C["entry_window_min"]} min after signal close')
                else:
                    bid, ask = s.hx.book(c)
                    why, _, ioc_lim = entry_limits(p['signal_close'], ask, bid, C)
                    if why:
                        s.L.ev('BATDAY_INFO', c, f'BATDAY no IOC: {why}'[:200])
                    else:
                        sp = s.hx.asset(c)['spec']
                        rem = hlx.sz_round(remain, sp, 'down')
                        lim = hlx.px_round(ioc_lim, sp, 'down')  # round DOWN: never above min(ask*1.003, close*1.01)
                        if rem > 0 and rem >= float(getattr(sp, 'min_qty', 0) or 0) and rem * lim >= s.hx.min_notional(c):
                            p['ioc_sent'] = True; s.save()  # persisted first: a crash/retry never sends a 2nd IOC
                            s.hx.max_open_notional = rem * lim * 1.01
                            stt = []
                            try:
                                r = s.hx.ioc(c, True, rem, lim, False, label=f'BATDAY IOC LONG {c}')
                                stt = hlx.parse_statuses(r)
                            except Exception as ex:
                                s.L.ev('LIVE_WARN', c, f'BATDAY IOC error {ex}'[:300])
                            x = s._xpos()  # exchange position = truth (qty + avg entry)
                            if x:
                                s._note_fill(p, abs(float(x['szi'])), x.get('entryPx'), tnow)
                            if stt and stt[0]['kind'] == 'filled':
                                try:
                                    _a, _q, fee = s.L.fill_of_oid(c, stt[0]['oid'], float(stt[0]['avgPx']), float(stt[0]['totalSz']),
                                                                   float(stt[0]['avgPx']) * float(stt[0]['totalSz']) * C['fee_est'])
                                except Exception:
                                    fee = float(stt[0]['avgPx']) * float(stt[0]['totalSz']) * C['fee_est']
                                p['fee'] = float(p.get('fee') or 0) + fee
            if float(p.get('filled') or 0) <= 0:
                s.ev('BATDAY_SKIP', 'BẮT ĐÁY – không khớp được khối lượng (đã huỷ post-only)', tg=True)
                s.pend = None; s.L.foreign.discard(c); s.save(); return
            s._complete_from_pend(tnow)
        except Exception as ex:
            s.ev('BATDAY_ENTRY_FAILED', f'BẮT ĐÁY – hoàn tất vào lệnh lỗi: {ex} (thử lại vòng sau)'[:400], tg=True)
            try:
                s.save()
            except Exception:
                pass

    def _complete_from_pend(s, tnow):
        p = s.pend; C = s.C; c = C['coin']
        tp, sl = p['tp'], p['sl']
        t_fill = p.get('t_fill') or str(tnow)
        t_fill_ms = int(p.get('t_fill_ms') or s._now() * 1000)
        avg = float(p.get('avg') or p['signal_close'])
        s.pos = dict(
            side='L', module='batday', entry=avg, q=float(p['filled']),
            notional=round(avg * float(p['filled']), 4), fee_in=p.get('fee') or 0.0,
            t0=t_fill, t_entry_ms=t_fill_ms,
            signal_close=p['signal_close'], signal_bar=p.get('signal_bar'),
            sl=sl, tp=tp, base=p.get('base'),
            until=str(pd.Timestamp(t_fill) + pd.Timedelta(hours=C['giu_toi_da_h'])),
            sl_oid=p.get('sl_oid'), sl_trig=p.get('sl_trig'), tp_oid=p.get('tp_oid'), tp_q=p.get('tp_q'), tp_px=p.get('tp_px'),
        )
        s.pend = None; s.L.foreign.add(c); s.save()
        ok = s._place_orders()  # reuses the partial-fill SL (closePosition), resizes TP to the full qty
        if not ok:
            s.ev('BATDAY_ABORT', 'BẮT ĐÁY – không đặt được SL: đóng ngay', tg=True)
            s._close_market(tnow, 'không đặt được SL'); return
        s.ev('BATDAY_ENTRY',
             f"BẮT ĐÁY – VÀO LONG ASTER {s.pos['q']:g} @ {s.pos['entry']:.5g} (signal {p['signal_close']:.5g}). "
             f"TP {tp:.5g} (+{C['tp_pct']:g}%), SL {sl:.5g} (−{C['sl_pct']:g}%), hết hạn {s.pos['until'][:16]}.",
             tg=True, price=s.pos['entry'], side='LONG', size=s.pos['q'], sl=sl, tp=tp)
        s.save()

    def _protect_partial(s, tnow):
        """Place SL (closePosition) and size the TP to the currently filled qty while the entry is still pending."""
        p = s.pend
        if not p or float(p.get('filled') or 0) <= 0 or s.pos:
            return
        keys = ('sl_oid', 'sl_trig', 'tp_oid', 'tp_q', 'tp_px')
        tmp = dict(side='L', q=float(p['filled']), sl=p['sl'], tp=p['tp'], **{k: p.get(k) for k in keys})
        s.pos = tmp
        try:
            ok = s._place_orders(save=False)
        finally:
            for k in keys:
                p[k] = tmp.get(k)
            s.pos = None
        if not ok:  # unprotected partial: stop adding size, finish now (no IOC) -> complete retries SL or closes
            p['no_ioc'] = True; p['force_finish'] = True
            s.ev('BATDAY_WARN', 'BẮT ĐÁY – không đặt được SL cho phần đã khớp: dừng vào thêm', tg=True)
        s.save()

    def _place_orders(s, save=True):
        C = s.C; c = C['coin']; p = s.pos; hx = s.hx; sp = hx.asset(c)['spec']; ok = True
        # SL: STOP_MARKET on MARK, closePosition (covers any filled qty) -> placed once, reused across partial fills
        if not p.get('sl_oid'):
            ok = False
            for i in range(3):
                try:
                    trig = hlx.px_round(p['sl'], sp, 'up')  # toward entry: loss never beyond −2% of signal close
                    r = hx.stop(c, False, p['q'], trig, None, label=f'BATDAY SL {c} {trig}')
                    stt = hlx.parse_statuses(r) if not r.get('dry') else [dict(kind='resting', oid='dry-sl')]
                    if stt and stt[0]['kind'] in ('resting', 'ok'):
                        p['sl_oid'] = stt[0].get('oid') or 'unknown'; p['sl_trig'] = trig; ok = True; break
                    s.L.ev('LIVE_WARN', c, f'BATDAY SL rejected {r}'[:300])
                except Exception as ex:
                    s.L.ev('LIVE_WARN', c, f'BATDAY SL error {ex}'[:300])
                time.sleep(0.2)
        # TP: reduce-only LIMIT sized to the current qty
        q_tp = hlx.sz_round(p['q'], sp, 'down')
        if p.get('tp_oid') and p.get('tp_q') is not None and abs(float(p['tp_q']) - q_tp) > 1e-12:
            try:
                hx.cancel(c, p['tp_oid'], label='BATDAY TP resize')
            except Exception as ex:
                s.L.ev('LIVE_WARN', c, f'BATDAY TP cancel {ex}'[:200])
            p['tp_oid'] = None
        if ok and not p.get('tp_oid') and q_tp > 0:
            try:
                tpx = hlx.px_round(p['tp'], sp, 'up')
                r = hx.limit_reduce(c, False, q_tp, tpx, label=f'BATDAY TP {c} {tpx}')
                stt = hlx.parse_statuses(r) if not r.get('dry') else [dict(kind='resting', oid='dry-tp')]
                if stt and stt[0]['kind'] in ('resting', 'filled', 'ok'):
                    p['tp_oid'] = stt[0].get('oid'); p['tp_px'] = tpx; p['tp_q'] = q_tp
                else:
                    s.L.ev('LIVE_WARN', c, f'BATDAY TP rejected {r}'[:300])
            except Exception as ex:
                s.L.ev('LIVE_WARN', c, f'BATDAY TP error {ex}'[:300])
        if save:
            s.save()
        return ok

    def _manage(s, tnow, m):
        C = s.C; c = C['coin']; p = s.pos
        st = s.L.st or s.L.refresh(0.0); x = (st.get('positions') or {}).get(c)
        if x is None:
            if s._now() * 1000 - p.get('t_entry_ms', 0) < 15000:
                return
            return s._settle(tnow, 'exchange')
        if x['szi'] <= 0:
            s.L.ev('LIVE_WARN', c, 'BATDAY exchange side not LONG'); return
        q = abs(float(x['szi']))
        if abs(q - p['q']) > 1e-12:
            p['q'] = q  # e.g. partial TP fill: TP is resized by _place_orders, SL is closePosition
            s._place_orders()
        # resting orders check
        if s._now() - s.last_chk > 60:
            s.last_chk = s._now()
            try:
                oo = [o for o in s.hx.open_orders() if o.get('coin') == c]
                ids = {str(o['oid']) for o in oo}
                if p.get('sl_oid') and str(p['sl_oid']) not in ('dry-sl',) and str(p['sl_oid']) not in ids:
                    if str(p['sl_oid']) != 'unknown' or not any(o.get('isTrigger') for o in oo):
                        p['sl_oid'] = None; s.L.ev('LIVE_STOP_MISSING', c, 'BATDAY SL missing: re-placing')
                if p.get('tp_oid') and str(p['tp_oid']) not in ('unknown', 'dry-tp') and str(p['tp_oid']) not in ids:
                    p['tp_oid'] = None; s.L.ev('LIVE_WARN', c, 'BATDAY TP missing: re-placing')
                if not p.get('sl_oid') or not p.get('tp_oid'):
                    if not s._place_orders() and not p.get('sl_oid'):
                        s._close_market(tnow, 'SL mất và không đặt lại được'); return
            except HLXError as ex:
                s.L.ev('LIVE_WARN', c, f'BATDAY orders check {ex}'[:200])
        # time exit: 6h after the first real fill
        if pd.Timestamp(tnow) >= pd.Timestamp(p['until']):
            s._close_market(tnow, f'hết {C["giu_toi_da_h"]}h'); return
        # backstop: price below SL*(1 - 0.3%) CONTINUOUSLY for 25s (any rebound above resets the timer)
        px = m.get(c)
        if px is not None:
            deep = float(p['sl']) * (1.0 - C['sl_backstop_extra_pct'] / 100.0)
            if px < deep:
                if s.sl_breach_t is None:
                    s.sl_breach_t = s._now()
                elif s._now() - s.sl_breach_t >= C['sl_backstop_s']:
                    s.ev('BATDAY_BACKSTOP', f'BẮT ĐÁY – backstop: giá {px:.5g} < SL-0.3% suốt {C["sl_backstop_s"]}s', tg=True)
                    s._close_market(tnow, 'backstop SL+0.3%/25s')
            else:
                s.sl_breach_t = None

    def _close_market(s, tnow, why):
        c = s.C['coin']; s.pos['close_why'] = why
        flat = s.L.market_close(c, None, 'BATDAY ' + why)
        if flat:
            s._settle(tnow, why)
        else:
            s.L.ev('LIVE_CLOSE_FAILED', c, 'BATDAY market close failed, retry next loop')

    def _settle(s, tnow, src):
        c = s.C['coin']; p = s.pos
        s.L.cancel_coin_orders(c)
        res = None
        try:
            res = s.L.collect(c, dict(live=dict(t_entry_ms=p['t_entry_ms'])))
        except Exception as ex:
            print('batday collect', ex, flush=True)
        ex_px = res['px'] if res else None; net = res['net'] if res else None
        why = p.get('close_why')
        if not why and ex_px is not None:
            if ex_px >= p['tp'] * 0.998:
                why = f"TP +{s.C['tp_pct']:g}%"
            elif ex_px <= p['sl'] * 1.01:
                why = 'SL'
            else:
                why = 'đóng trên sàn'
        why = why or 'đóng trên sàn (không tìm thấy fill)'
        row = dict(side='LONG', module='batday', signal_bar=p.get('signal_bar'), signal_close=p.get('signal_close'),
                   t0=p['t0'], entry=p['entry'], q=p['q'], notional=p['notional'], sl=p['sl'], tp=p['tp'],
                   t1=str(tnow), exit=ex_px, why=why, net=net, fee=res['fee'] if res else None)
        try:
            pd.DataFrame([row]).to_csv(s.trf, mode='a', header=not os.path.exists(s.trf), index=False)
        except Exception as e_:
            print('batday csv', e_, flush=True)
        s.pos = None; s.sl_breach_t = None; s.L.foreign.discard(c); s.save()
        s.ev('BATDAY_EXIT',
             f"BẮT ĐÁY – ĐÓNG LONG ASTER: {why}; giá ra {ex_px if ex_px is None else f'{ex_px:.5g}'} (vào {p['entry']:.5g}), "
             f"lãi/lỗ ròng {('%+.2f$'%net) if net is not None else '?'}.",
             tg=True, price=ex_px, side='LONG', net=net, why=why)
