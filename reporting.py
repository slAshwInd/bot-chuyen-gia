"""Read-only reporting. No exchange client, Telegram client or trading imports."""
import bisect
import csv
import datetime as dt
import json
import math
from collections import Counter
from pathlib import Path

ENTRY_EVENTS = {'ENTRY', 'STEP3_ENTRY', 'BATDAY_ENTRY'}
BLOCK_EVENTS = {'LIVE_ENTRY_VETO', 'LIVE_ENTRY_FAILED', 'LIVE_ENTRY_ABORTED',
                'STEP3_SKIP', 'STEP3_ABORT', 'STEP3_DROP', 'STEP3_ENTRY_FAILED'}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def read_json(path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            value = json.load(f)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def fmt(value):
    return '?' if value is None else f'{value:,.2f}'


def pnl_text(root):
    root = Path(root)
    st = read_json(root / 'state_live.json')
    if not st:
        return '[LIVE] PnL: khong doc duoc state_live.json; khong suy doan so du.'
    cfg = read_json(root / 'config_live.json')
    lv = st.get('live') or {}
    eq = number(lv.get('equity'))
    wallet = number(lv.get('usdc_total'))
    upnl = number(lv.get('upnl'))
    deposit = number(cfg.get('start_capital'))
    source = 'config.start_capital'
    if deposit is None or deposit <= 0:
        deposit = number(read_json(root / 'monitor_config.json').get('initial_deposit'))
        source = 'monitor_config.initial_deposit (chi bao cao)'
    if deposit is None or deposit <= 0:
        deposit = None
        source = 'CHUA CAU HINH; khong mac dinh von'
    net = 0.0
    n = wins = invalid = 0
    csv_ok = True
    try:
        with open(root / 'trades_live.csv', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or 'net' not in reader.fieldnames:
                raise ValueError('missing net column')
            for row in reader:
                v = number(row.get('net'))
                if v is None:
                    invalid += 1
                    continue
                net += v
                n += 1
                wins += v > 0
    except (OSError, ValueError, csv.Error):
        csv_ok = False
    out = ['[LIVE] PNL - chi doc', f'State: {str(st.get("updated", "?"))[:19]} (UTC+7)',
           f'Deposit: {fmt(deposit)} USDT ({source})',
           f'Balance: {fmt(wallet)} | Equity: {fmt(eq)} USDT',
           f'Unrealized san: {fmt(upnl)} USDT']
    if wallet is not None and deposit is not None:
        delta = wallet - deposit
        out.append(f'Balance - deposit: {delta:+.2f} ({delta/deposit*100:+.2f}%)')
        out.append('Chenh lech tai khoan, CHUA xac nhan la loi nhuan (nap/rut/funding).')
    if csv_ok:
        out += [f'CSV: {n} ban ghi dong/chot, thang {wins}, con lai {n-wins}',
                f'Net CSV: {net:+.2f} USDT' + (f' ({net/deposit*100:+.2f}% deposit)' if deposit else ' (chua co mau so deposit)')]
        if wallet is not None and deposit is not None:
            out.append(f'Chenh chua doi soat (balance-deposit-net CSV): {wallet-deposit-net:+.2f} USDT')
        if invalid:
            out.append(f'CSV co {invalid} dong net khong hop le; tong chua day du.')
    else:
        out.append('Net CSV: KHONG XAC DINH (thieu/loi lich su); khong thay bang PnL paper.')
    if eq is not None and wallet is not None and upnl is not None and abs(eq-wallet-upnl) > .05:
        out.append('CANH BAO: equity/balance/upnl khong khop; can doi soat snapshot.')
    positions = lv.get('exchange_positions') or {}
    if isinstance(positions, dict):
        out.append(f'Vi the san: {len(positions)}')
        for coin, pos in list(positions.items())[:6]:
            if not isinstance(pos, dict):
                continue
            qty = number(pos.get('szi'))
            side = '?' if qty is None else ('SHORT' if qty < 0 else 'LONG')
            out.append(f'{str(coin)[:20]} {side} | KL {fmt(abs(qty) if qty is not None else None)} | entry {pos.get("entryPx", "?")} | uPnL {fmt(number(pos.get("upnl")))}')
        if len(positions) > 6:
            out.append('Chi hien 6 vi the dau; xem dashboard de biet day du.')
    out.append('PnL tai khoan sau phi/funding/nap-rut: CHUA DOI SOAT day du.')
    return '\n'.join(out)


def lines(path):
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as f:
            yield from f
    except OSError:
        return


def summary(root, t0, t1, parse_loop, classify):
    """Stream ALL retained records: no tail-byte approximation, no trading writes.
    LOOP = completed decision/scan cycle, NOT a shortlist refresh or API call.
    Entry counts are logged events, not independently verified exchange fills.
    """
    root = Path(root)
    loops = []
    shortlist_scans = 0
    lp = None
    errs = Counter()
    cur = None
    for line in lines(root / 'bot_live.log'):
        if line.startswith('SCAN_DONE '):
            try:
                scan_time = dt.datetime.strptime(line[10:29], '%Y-%m-%d %H:%M:%S')
                shortlist_scans += t0 <= scan_time < t1
            except ValueError:
                pass
        if line.startswith('LOOP '):
            p = parse_loop(line)
            cur = p.get('ts')
            if cur and t0 <= cur < t1:
                loops.append(cur)
                lp = p
        else:
            try:
                cur = dt.datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
            if cur and t0 <= cur < t1:
                k = classify(line)
                if k:
                    errs[k] += 1
    loops.sort()
    entries = []
    blocked = Counter()
    event_seen = set()
    for line in lines(root / 'events_live.jsonl'):
        try:
            e = json.loads(line)
            if not isinstance(e, dict):
                continue
            ts = dt.datetime.strptime(str(e.get('time', ''))[:19], '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            continue
        if not t0 <= ts < t1:
            continue
        ev = str(e.get('event', ''))
        # Exact duplicate log records only; no heuristic removal of valid entries.
        key = json.dumps(e, sort_keys=True, ensure_ascii=True)
        if key in event_seen:
            continue
        event_seen.add(key)
        if ev in ENTRY_EVENTS:
            entries.append(ts)
        elif ev.startswith('ENTRY_BLOCKED') or ev in BLOCK_EVENTS:
            blocked[ev] += 1
    cycles_entered = set()
    for ts in entries:
        i = bisect.bisect_left(loops, ts)
        if i < len(loops) and (loops[i]-ts).total_seconds() <= 120:
            cycles_entered.add(i)
    no_entry = len(loops) - len(cycles_entered)
    labels = {'ENTRY_BLOCKED_RULES': 'rule chua dat', 'ENTRY_BLOCKED_BTC': 'BTC chan',
              'LIVE_ENTRY_VETO': 'live veto', 'LIVE_ENTRY_FAILED': 'gui lenh that bai',
              'LIVE_ENTRY_ABORTED': 'entry huy', 'STEP3_SKIP': 'S3 bo qua',
              'STEP3_ABORT': 'S3 huy', 'STEP3_DROP': 'S3 het dieu kien',
              'STEP3_ENTRY_FAILED': 'S3 entry loi'}
    reasons = '; '.join(f'{labels.get(k,k)}: {v}' for k,v in blocked.most_common(5))
    state = 'co heartbeat' if lp and (t1-lp['ts']).total_seconds() < 120 else 'heartbeat thieu/cu'
    out = [f'[LIVE] PHIEN {t0:%d/%m %H:%M}-{t1:%d/%m %H:%M} UTC+7',
           f'Bot: {state} | Vong quet: {len(loops)} | Shortlist scan: {shortlist_scans}',
           f'Entry ghi log: {len(entries)} | Vong khong ghi Entry (uoc tinh): {no_entry}',
           f'Chan/bo qua ghi log: {sum(blocked.values())}',
           'Ly do: ' + (reasons or 'khong co ly do duoc ghi; khong suy doan.'),
           'Chi theo log con luu; LOOP la vong quet, khong phai refresh shortlist. Chan la event, khong phai fill san.']
    if errs:
        out.append('Loi: ' + ', '.join(f'{k}={v}' for k,v in errs.items()))
    if lp:
        out.append(f'Sync={lp.get("sync_ok")} | Halted={lp.get("halted")} | Equity={lp.get("equity")}')
    return '\n'.join(out)[:1800], {'loops':len(loops), 'entries':len(entries), 'no_entry':no_entry,
                                   'blocked':dict(blocked), 'errs':dict(errs), 'shortlist_scans':shortlist_scans}


def due_window(state, now):
    """Pure scheduling helper. Caller persists state after queueing the report.
    Returns fixed 120-minute windows; missed windows are explicitly combined.
    """
    if state.get('summary_schema') != 2:
        state['summary_schema'] = 2
        state['summary_start'] = now.isoformat()
        state['next_sum'] = (now+dt.timedelta(minutes=120)).isoformat()
        return None
    try:
        due = dt.datetime.fromisoformat(state['next_sum'])
        start = dt.datetime.fromisoformat(state['summary_start'])
    except (KeyError, ValueError, TypeError):
        state.pop('summary_schema', None)
        return due_window(state, now)
    if now < due:
        return None
    end = due
    while end+dt.timedelta(minutes=120) <= now:
        end += dt.timedelta(minutes=120)
    if start >= end:
        state.pop('summary_schema', None)
        return due_window(state, now)
    state['summary_start'] = end.isoformat()
    state['next_sum'] = (end+dt.timedelta(minutes=120)).isoformat()
    return start, end


def decision_text(root):
    """Snapshot-only explanation. Does not call the strategy or predict a fill."""
    st=read_json(Path(root)/'state_live.json')
    if not st:
        return 'Dieu kien vao: thieu snapshot; khong suy doan.'
    out=[f'Dieu kien theo snapshot {str(st.get("updated","?"))[:19]}:']
    btc=st.get('btc') or {}; recheck=st.get('btc_recheck') or {}
    out.append(f'BTC allow LONG={btc.get("allow","?")} SHORT={btc.get("allow_s","?")} | chan ngan han LONG={recheck.get("block","?")} SHORT={recheck.get("block_s","?")}')
    for coin,w in list((st.get('watch') or {}).items())[:3]:
        if not isinstance(w,dict): continue
        px=number(w.get('price')); lo=number(w.get('lo')); up=number(w.get('up'))
        if px is None: continue
        if lo and lo > 0:
            text=f'cho cham dai duoi {lo:.6g} (cach {(px/lo-1)*100:.2f}%)' if px>lo else f'da cham/qua dai duoi {lo:.6g}'
            out.append(f'{coin} px={px:.6g}: LONG {text}')
        if up and up>0:
            text=f'cho cham dai tren {up:.6g} (cach {(up/px-1)*100:.2f}%)' if px>0 and px<up else f'da cham/qua dai tren {up:.6g}'
            out.append(f'{coin}: SHORT {text}')
    out.append('halted=None: khong co co tam dung, KHONG phai du dieu kien vao. Cham dai van can dat bo rule; khong du doan duoc thoi diem/fill.')
    return '\n'.join(out)
