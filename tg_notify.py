"""Telegram action alerts + PNL/ACTION commands for the paper bot. Never raises into trading loop."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_DIR, "secrets", "telegram.env")
_STATE = os.path.join(_DIR, "state.json")
_OFFSET = os.path.join(_DIR, "state", "telegram_offset.txt")
_PNL_BRANCHES = ("BASE_SL250", "BASE_SL250_X10")
_PNL_CMDS = frozenset({"pnl", "/pnl"})
_ACTION_CMDS = frozenset({"action", "/action"})
_STATUS_MAX = 3500
_PNL_MAX = 3900

_TAGS = {
    "ENTRY": "🟢 ENTRY",
    "ADD": "🔵 ADD",
    "EXIT": "⚪ EXIT",
    "STOP": "🔴 STOP",
    "EXIT_RUNNER": "🟠 EXIT_RUNNER",
    "EXIT_15M": "🟡 EXIT_15M",
    "TP_PARTIAL": "🟣 TP_PARTIAL",
    "TP_PARTIAL_15M": "🟣 TP_PARTIAL_15M",
}

_poller_started = False


def _load_env() -> tuple[str, str]:
    token = chat = ""
    try:
        with open(_ENV, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "TELEGRAM_BOT_TOKEN":
                    token = v
                elif k == "TELEGRAM_CHAT_ID":
                    chat = v
    except Exception:
        pass
    token = os.environ.get("TELEGRAM_BOT_TOKEN", token).strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", chat).strip()
    return token, chat


def _api(method: str, params: dict | None = None, timeout: float = 8.0):
    token, _ = _load_env()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    if params is not None:
        data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


_OUTBOX = os.path.join(_DIR, "state", "tg_outbox")


def _external_mode() -> bool:
    return os.path.exists(os.path.join(_DIR, "state", "tg_external_poller.flag")) and os.environ.get("TG_POLLER") != "1"


def send(text: str) -> bool:
    """Bot side (external poller mode): enqueue to state/tg_outbox and return at once, so a stalled Telegram socket
    never blocks the trading loop; tg_poller.py delivers. Poller side / no flag: direct send below."""
    if _external_mode() and text:
        try:
            os.makedirs(_OUTBOX, exist_ok=True)
            fn = os.path.join(_OUTBOX, f"{time.time():.6f}_{os.getpid()}.txt")
            with open(fn + ".tmp", "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(fn + ".tmp", fn)
            return True
        except Exception as ex:
            print(f"tg_notify: enqueue failed, sending direct: {ex}", file=sys.stderr)
    return _send_direct(text)


def drain_outbox(max_items: int = 20) -> int:
    """Poller side: deliver queued bot alerts in order. Failed items stay for next round (dropped after 6h)."""
    n = 0
    try:
        files = sorted(f for f in os.listdir(_OUTBOX) if f.endswith(".txt"))
    except FileNotFoundError:
        return 0
    for f in files[:max_items]:
        p = os.path.join(_OUTBOX, f)
        try:
            text = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if _send_direct(text):
            os.remove(p); n += 1
        else:
            if time.time() - os.path.getmtime(p) > 6 * 3600:
                os.replace(p, p + ".expired")
            break
    return n


def _send_direct(text: str) -> bool:
    """POST sendMessage. Returns True on ok. Missing secrets or errors -> False, never raise."""
    token, chat_id = _load_env()
    if not token or not chat_id or not text:
        return False
    if len(text) > 4000:
        text = text[:3997] + "..."
    for attempt in range(3):
        try:
            j = _api("sendMessage", {"chat_id": chat_id, "text": text}, timeout=15.0)
            if j and j.get("ok"):
                return True
        except Exception as ex:
            print(f"tg_notify: send failed (try {attempt+1}/3): {type(ex).__name__}: {ex}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    return False


def _fmt_px(px) -> str:
    try:
        p = float(px)
    except Exception:
        return str(px)
    if abs(p) >= 1000:
        return f"{p:,.2f}"
    if abs(p) >= 1:
        return f"{p:.4f}".rstrip("0").rstrip(".")
    s = f"{p:.8f}".rstrip("0").rstrip(".")
    return s or "0"


def _nets(per_branch) -> str:
    if not isinstance(per_branch, dict) or not per_branch:
        return ""
    parts = []
    for br, info in per_branch.items():
        if not isinstance(info, dict):
            continue
        net = info.get("net", info.get("trade_net"))
        if net is None:
            continue
        try:
            parts.append(f"{br}:{float(net):+.2f}$")
        except Exception:
            parts.append(f"{br}:{net}")
    if not parts:
        return ""
    if len(parts) > 6:
        return " | net " + ", ".join(parts[:6]) + f" (+{len(parts)-6})"
    return " | net " + ", ".join(parts)


def format_action(ev: dict) -> str:
    """Concise Vietnamese Telegram line for trading action events."""
    kind = str(ev.get("event") or "?")
    tag = _TAGS.get(kind, f"📣 {kind}")
    coin = ev.get("coin") or "?"
    side = ev.get("side")
    side_s = f" {side}" if side in ("LONG", "SHORT") else ""
    px = _fmt_px(ev.get("price", 0))
    branches = ev.get("branches") or []
    if isinstance(branches, list):
        br_s = ",".join(str(b) for b in branches[:12])
        if len(branches) > 12:
            br_s += f" (+{len(branches)-12})"
    else:
        br_s = str(branches)
    reason = str(ev.get("reason") or "").replace("\n", " ").strip()
    if len(reason) > 280:
        reason = reason[:277] + "..."
    net_s = ""
    if kind in ("EXIT", "STOP", "EXIT_RUNNER", "EXIT_15M", "TP_PARTIAL", "TP_PARTIAL_15M"):
        net_s = _nets(ev.get("per_branch"))
    lines = [f"{tag} {coin}{side_s} @ {px}"]
    if br_s:
        lines.append(f"nhánh: {br_s}{net_s}")
    if reason:
        lines.append(reason)
    return "\n".join(lines)


def _pos_entry_block(coin: str, p: dict) -> list[str]:
    """Few Vietnamese lines for one open position (entry details)."""
    if not isinstance(p, dict):
        return [f"  • {coin}: (bad pos)"]
    side = p.get("side") or "LONG"
    avg = p.get("avg", p.get("entry", "?"))
    since = str(p.get("since") or p.get("t0") or "?").strip()
    adds = p.get("adds", 0)
    try:
        adds_i = int(adds)
    except Exception:
        adds_i = adds
    u = p.get("u_net", p.get("unreal"))
    try:
        u_s = f"{float(u):+.2f}$"
    except Exception:
        u_s = f"{u}$"
    lines = [f"  • {side} {coin}"]
    detail = [f"vào {_fmt_px(avg)}"]
    if since and since != "?":
        detail.append(f"lúc {since}")
    detail.append(f"{adds_i}n")
    notional = p.get("notional")
    margin = p.get("margin")
    money = []
    try:
        if notional is not None:
            money.append(f"notional {float(notional):,.0f}$")
    except Exception:
        pass
    try:
        if margin is not None:
            money.append(f"margin {float(margin):,.0f}$")
    except Exception:
        pass
    if money:
        detail.append(" / ".join(money))
    lines.append("    " + " | ".join(detail))
    stop_px = p.get("stop_px")
    stop_bit = ""
    if stop_px is not None:
        try:
            stop_bit = f" | SL {_fmt_px(stop_px)}"
        except Exception:
            stop_bit = f" | SL {stop_px}"
    lines.append(f"    tạm tính {u_s}{stop_bit}")
    return lines


def format_pnl(state: dict | None = None, branches=_PNL_BRANCHES) -> str:
    """Concise Vietnamese PnL for live BASE branches from state.json (with entry details)."""
    if state is None:
        try:
            with open(_STATE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as ex:
            return f"📊 PAPER PnL\n(không đọc được state.json: {type(ex).__name__})"
    brs = (state or {}).get("branches") or {}
    updated = str((state or {}).get("updated") or "?")[:19]
    lines = [f"📊 PAPER PnL (cập nhật {updated} giờ VN)"]
    for name in branches:
        v = brs.get(name)
        if not v:
            lines.append(f"\n{name}: (không có trong state)")
            continue
        eq = float(v.get("equity") or 0)
        real = float(v.get("realized") or 0)
        ur = float(v.get("unrealized") or 0)
        today = float(v.get("today_realized") or 0)
        nt = int(v.get("n_trades") or 0)
        nw = int(v.get("n_wins") or 0)
        ns = int(v.get("n_stops") or 0)
        lines.append(
            f"\n{name}\n"
            f"  equity {eq:,.2f}$ | đã chốt {real:+.2f}$ | tạm tính {ur:+.2f}$\n"
            f"  hôm nay chốt {today:+.2f}$ | lệnh {nt} (thắng {nw}, stop {ns})"
        )
        pos = v.get("positions") or {}
        if not pos:
            lines.append("  vị thế: không")
        else:
            lines.append(f"  vị thế ({len(pos)}):")
            for c, p in pos.items():
                lines.extend(_pos_entry_block(c, p if isinstance(p, dict) else {}))
    out = "\n".join(lines)
    if len(out) > _PNL_MAX:
        out = out[: _PNL_MAX - 3] + "..."
    return out


def _coins_line(label: str, coins, soft_cap: int = 18) -> str:
    if not coins:
        return f"{label}: (trống)"
    if isinstance(coins, dict):
        # group -> list
        parts = []
        for g, lst in coins.items():
            if not isinstance(lst, list):
                continue
            shown = lst[:soft_cap]
            extra = len(lst) - len(shown)
            s = ",".join(str(x) for x in shown)
            if extra > 0:
                s += f" (+{extra})"
            parts.append(f"{g}: {s}" if len(coins) > 1 else s)
        body = " | ".join(parts) if parts else "(trống)"
        return f"{label}: {body}"
    if isinstance(coins, list):
        shown = coins[:soft_cap]
        extra = len(coins) - len(shown)
        s = ",".join(str(x) for x in shown)
        if extra > 0:
            s += f" (+{extra})"
        return f"{label} ({len(coins)}): {s}"
    return f"{label}: {coins}"


def format_action_report(state: dict | None = None, branches=_PNL_BRANCHES) -> str:
    """Status report for Telegram /action — what the paper bot is doing now.
    Not to be confused with format_action(ev) used for ENTRY/EXIT alerts.
    """
    if state is None:
        try:
            with open(_STATE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as ex:
            return f"📋 PAPER ACTION\n(không đọc được state.json: {type(ex).__name__})"
    st = state or {}
    updated = str(st.get("updated") or "?")[:19]
    mode = st.get("mode") or "?"
    lines = [f"📋 PAPER ACTION ({mode}) — cập nhật {updated} giờ VN"]

    brs = st.get("branches") or {}
    for name in branches:
        v = brs.get(name)
        if not v:
            lines.append(f"\n{name}: (không có)")
            continue
        eq = float(v.get("equity") or 0)
        ur = float(v.get("unrealized") or 0)
        hit = bool(v.get("daily_target_hit"))
        hit_s = " | ✅ hit mục tiêu ngày" if hit else ""
        lines.append(f"\n{name}\n  equity {eq:,.2f}$ | tạm tính {ur:+.2f}${hit_s}")
        pos = v.get("positions") or {}
        if not pos:
            lines.append("  mở: không")
        else:
            bits = []
            for c, p in pos.items():
                if not isinstance(p, dict):
                    continue
                side = p.get("side") or "?"
                adds = p.get("adds", 0)
                u = p.get("u_net", p.get("unreal"))
                try:
                    u_s = f"{float(u):+.0f}$"
                except Exception:
                    u_s = f"{u}$"
                bits.append(f"{side} {c} {adds}n {u_s}")
            lines.append("  mở: " + "; ".join(bits))

    sl = st.get("shortlist") or {}
    lines.append("\nShortlist")
    lists = sl.get("lists") or {}
    lists_s = sl.get("lists_short") or {}
    # Prefer BASE group if present; else show all groups
    long_src = lists.get("BASE") if isinstance(lists, dict) and "BASE" in lists else lists
    short_src = lists_s.get("BASE") if isinstance(lists_s, dict) and "BASE" in lists_s else lists_s
    lines.append("  " + _coins_line("LONG", long_src))
    lines.append("  " + _coins_line("SHORT", short_src))
    sl_busy = "busy" if sl.get("busy") else "idle"
    sl_t = str(sl.get("time") or "?")[:19]
    lines.append(f"  shortlist {sl_busy} | quét lúc {sl_t}")

    sc = st.get("scanner") or {}
    lines.append("\nScanner")
    alive = "alive" if sc.get("alive") else "down"
    busy = "busy" if sc.get("busy") else "idle"
    fresh = "fresh" if sc.get("fresh") else "stale"
    last = str(sc.get("last_scan") or "?")[:19]
    nxt = str(sc.get("next_due") or "?")[:19]
    every = sc.get("every_min", "?")
    err = sc.get("err")
    lines.append(f"  {alive}/{busy}/{fresh} | mỗi {every}p")
    lines.append(f"  last {last} | next {nxt}")
    if sc.get("running_since"):
        lines.append(f"  running_since {str(sc.get('running_since'))[:19]}")
    if err:
        lines.append(f"  err: {str(err)[:120]}")

    brc = st.get("btc_recheck") or {}
    btc = st.get("btc") or {}
    lines.append("\nBTC short-term")
    blk_l = "CHẶN LONG" if brc.get("block") else "cho phép LONG"
    blk_s = "CHẶN SHORT" if brc.get("block_s") else "cho phép SHORT"
    why = brc.get("why") or []
    why_s = brc.get("why_s") or []
    lines.append(f"  {blk_l} | {blk_s}")
    if why:
        lines.append("  why L: " + ", ".join(str(x) for x in why[:6]))
    if why_s:
        lines.append("  why S: " + ", ".join(str(x) for x in why_s[:6]))
    allow = btc.get("allow")
    allow_s = btc.get("allow_s")
    fav = btc.get("fav")
    fav_s = btc.get("fav_s")
    fng = btc.get("fng")
    fng_s = ""
    if isinstance(fng, (list, tuple)) and fng:
        fng_s = f" | FNG {fng[0]}" + (f" {fng[1]}" if len(fng) > 1 else "")
    lines.append(
        f"  gate allow L/S={allow}/{allow_s} fav L/S={fav}/{fav_s}{fng_s}"
    )
    if brc.get("px") is not None:
        try:
            lines.append(f"  BTC {_fmt_px(brc.get('px'))} @ {str(brc.get('at') or '')[:19]}")
        except Exception:
            pass

    # daily target / other clear "doing now" flags
    flags = []
    for name in branches:
        v = brs.get(name) or {}
        if v.get("daily_target_hit"):
            flags.append(f"{name} đã hit daily")
    if flags:
        lines.append("\nCờ: " + "; ".join(flags))

    out = "\n".join(lines)
    if len(out) > _STATUS_MAX:
        out = out[: _STATUS_MAX - 3] + "..."
    return out



def _read_offset() -> int:
    try:
        with open(_OFFSET, "r", encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


def _write_offset(n: int) -> None:
    try:
        os.makedirs(os.path.dirname(_OFFSET), exist_ok=True)
        tmp = _OFFSET + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(int(n)))
        os.replace(tmp, _OFFSET)
    except Exception:
        pass


def _norm_cmd(text: str) -> str:
    t = (text or "").strip()
    # "/pnl@BotName" -> "/pnl"
    if t.startswith("/") and "@" in t:
        t = t.split("@", 1)[0]
    return t.strip().lower()


def handle_update(upd: dict, allowed_chat: str) -> bool:
    """Process one getUpdates item. Returns True if a command reply was sent."""
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id or chat_id != str(allowed_chat):
        return False
    cmd = _norm_cmd(msg.get("text") or "")
    ok = None
    if cmd in _PNL_CMDS:
        ok = send(format_pnl())
    elif cmd in _ACTION_CMDS:
        ok = send(format_action_report())
    print(f"{time.strftime('%F %T')} tg_notify: update {upd.get('update_id')} cmd={cmd[:20]!r} reply_sent={ok}", flush=True)
    return bool(ok)


def poll_once(timeout: float = 0) -> int:
    """One getUpdates cycle. Returns new offset (last update_id+1) or current. Never raises."""
    token, chat_id = _load_env()
    if not token or not chat_id:
        return _read_offset()
    offset = _read_offset()
    try:
        # short poll in this thread only; timeout=0 = immediate
        j = _api(
            "getUpdates",
            {"offset": offset, "timeout": int(timeout), "allowed_updates": json.dumps(["message"])},
            timeout=max(8.0, float(timeout) + 5.0),
        )
        if not j or not j.get("ok"):
            return offset
        for upd in j.get("result") or []:
            uid = int(upd.get("update_id") or 0)
            offset = max(offset, uid + 1)
            try:
                handle_update(upd, chat_id)
            except Exception as ex:
                print(f"tg_notify: handle_update: {type(ex).__name__}: {ex}", file=sys.stderr)
        _write_offset(offset)
        return offset
    except Exception as ex:
        print(f"tg_notify: poll failed: {type(ex).__name__}: {ex}", file=sys.stderr)
        return offset


def _poller_loop(interval: float = 2.5) -> None:
    # Skip backlog on first start: advance offset to latest without handling
    token, chat_id = _load_env()
    if token and chat_id:
        try:
            j = _api("getUpdates", {"offset": -1, "timeout": 0}, timeout=8.0)
            if j and j.get("ok") and j.get("result"):
                last = max(int(u.get("update_id") or 0) for u in j["result"])
                _write_offset(last + 1)
        except Exception:
            pass
    while True:
        try:
            poll_once(timeout=0)
        except Exception as ex:
            print(f"tg_notify: poller: {type(ex).__name__}: {ex}", file=sys.stderr)
        time.sleep(interval)


def start_poller(interval: float = 2.5) -> bool:
    """Start daemon thread for inbound PNL/ACTION commands. Idempotent. Never raises."""
    global _poller_started
    if _poller_started:
        return True
    if os.path.exists(os.path.join(_DIR, "state", "tg_external_poller.flag")):
        print("tg_notify: in-process poller disabled (external tg_poller.py handles getUpdates)", flush=True)
        return False
    token, chat_id = _load_env()
    if not token or not chat_id:
        print("tg_notify: poller not started (missing secrets)", file=sys.stderr)
        return False
    try:
        t = threading.Thread(target=_poller_loop, args=(interval,), name="tg_poller", daemon=True)
        t.start()
        _poller_started = True
        print("tg_notify: poller started", flush=True)
        return True
    except Exception as ex:
        print(f"tg_notify: start_poller failed: {type(ex).__name__}: {ex}", file=sys.stderr)
        return False
