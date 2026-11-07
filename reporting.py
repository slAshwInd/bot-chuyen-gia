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
        return '[LIVE] PnL: không đọc được trạng thái; không suy đoán số dư.'
    cfg = read_json(root / 'config_live.json')
    lv = st.get('live') or {}
    eq = number(lv.get('equity'))
    wallet = number(lv.get('usdc_total'))
    upnl = number(lv.get('upnl'))
    deposit = number(cfg.get('start_capital'))
    source = 'cấu hình vốn nạp'
    if deposit is None or deposit <= 0:
        deposit = number(read_json(root / 'monitor_config.json').get('initial_deposit'))
        source = 'cấu hình báo cáo'
    if deposit is None or deposit <= 0:
        deposit = None
        source = 'CHƯA CẤU HÌNH; không mặc định vốn'
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
    out = ['[LIVE] LÃI/LỖ — chỉ đọc', f'Cập nhật: {str(st.get("updated", "?"))[:19]} (UTC+7)',
           f'Vốn nạp: {fmt(deposit)} USDT ({source})',
           f'Số dư: {fmt(wallet)} | Tài sản: {fmt(eq)} USDT',
           f'Lãi/lỗ chưa chốt trên sàn: {fmt(upnl)} USDT']
    if wallet is not None and deposit is not None:
        delta = wallet - deposit
        out.append(f'Số dư − vốn nạp: {delta:+.2f} ({delta/deposit*100:+.2f}%)')
        out.append('Chênh lệch tài khoản, chưa phải lợi nhuận đã đối soát (nạp/rút/phí tài trợ).')
    if csv_ok:
        out += [f'CSV: {n} bản ghi đóng/chốt, thắng {wins}, còn lại {n-wins}',
                f'Lãi/lỗ ròng CSV: {net:+.2f} USDT' + (f' ({net/deposit*100:+.2f}% vốn nạp)' if deposit else ' (chưa có vốn nạp)')]
        if wallet is not None and deposit is not None:
            out.append(f'Chênh chưa đối soát (số dư − vốn − lãi/lỗ CSV): {wallet-deposit-net:+.2f} USDT')
        if invalid:
            out.append(f'CSV co {invalid} dòng lãi/lỗ không hợp lệ; tổng chưa đầy đủ.')
    else:
        out.append('Lãi/lỗ ròng CSV: CHƯA XÁC ĐỊNH (thiếu/lỗi lịch sử); không thay bằng số liệu mô phỏng.')
    if eq is not None and wallet is not None and upnl is not None and abs(eq-wallet-upnl) > .05:
        out.append('CẢNH BÁO: tài sản/số dư/lãi chưa chốt không khớp; cần đối soát.')
    positions = lv.get('exchange_positions') or {}
    if isinstance(positions, dict):
        out.append(f'Vị thế sàn: {len(positions)}')
        for coin, pos in list(positions.items())[:6]:
            if not isinstance(pos, dict):
                continue
            qty = number(pos.get('szi'))
            side = '?' if qty is None else ('Bán' if qty < 0 else 'Mua')
            out.append(f'{str(coin)[:20]} {side} | KL {fmt(abs(qty) if qty is not None else None)} | giá vào {pos.get("entryPx", "?")} | lãi/lỗ chưa chốt {fmt(number(pos.get("upnl")))}')
        if len(positions) > 6:
            out.append('Chỉ hiện 6 vị thế đầu; xem bảng điều khiển để biết đầy đủ.')
    out.append('Lãi/lỗ tài khoản sau phí/tài trợ/nạp-rút: chưa đối soát đầy đủ.')
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
    labels = {'ENTRY_BLOCKED_RULES': 'điều kiện chưa đạt', 'ENTRY_BLOCKED_BTC': 'BTC chặn',
              'LIVE_ENTRY_VETO': 'cổng giao dịch chặn', 'LIVE_ENTRY_FAILED': 'gửi lệnh thất bại',
              'LIVE_ENTRY_ABORTED': 'hủy vào lệnh', 'STEP3_SKIP': 'bước 3 bỏ qua',
              'STEP3_ABORT': 'bước 3 hủy', 'STEP3_DROP': 'bước 3 hết điều kiện',
              'STEP3_ENTRY_FAILED': 'bước 3 lỗi vào lệnh'}
    reasons = '; '.join(f'{labels.get(k,k)}: {v}' for k,v in blocked.most_common(5))
    state = 'có nhịp cập nhật' if lp and (t1-lp['ts']).total_seconds() < 120 else 'thiếu nhịp cập nhật hoặc dữ liệu cũ'
    out = [f'[LIVE] TỔNG HỢP {t0:%d/%m %H:%M}-{t1:%d/%m %H:%M} UTC+7',
           f'Bot: {state} | Vòng quét: {len(loops)} | Lượt lọc danh sách: {shortlist_scans}',
           f'Lần vào lệnh ghi nhận: {len(entries)} | Vòng không ghi nhận vào lệnh (ước tính): {no_entry}',
           f'Chặn/bỏ qua ghi nhận: {sum(blocked.values())}',
           'Lý do: ' + (reasons or 'không có lý do được ghi; không suy đoán.'),
           'Theo nhật ký còn lưu; sự kiện không phải xác nhận khớp trên sàn.']
    if errs:
        out.append('Lỗi: ' + ', '.join(f'{dict(auth="xác thực",api="kết nối API",exit="tiến trình dừng",exc="ngoại lệ").get(k,k)}={v}' for k,v in errs.items()))
    out.append(decision_text(root, now=t1))
    text = '\n'.join(out)
    if len(text)>3800: text=text[:3650]+'\nĐã rút gọn. Mốc chỉ tham khảo theo dữ liệu lưu; chưa xác minh đủ điều kiện/khớp lệnh.'
    return text, {'loops':len(loops), 'entries':len(entries), 'no_entry':no_entry,
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


def tri(v):
    if v is True or v == 'True': return 'có'
    if v is False or v == 'False': return 'không'
    return 'chưa rõ'


def decision_text(root, now=None):
    """Saved observations only; never execute strategy or exchange code."""
    st = read_json(Path(root)/'state_live.json')
    cfg = read_json(Path(root)/'config_live.json')
    if not st: return 'Hiện tại: thiếu dữ liệu trạng thái; chưa xác định điều kiện.'
    now = now or dt.datetime.now()
    stamp = str(st.get('updated', '?'))[:19]
    try: fresh = 0 <= (now-dt.datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S')).total_seconds() < 120
    except (ValueError, TypeError): fresh = False
    out = [f'Hiện tại — dữ liệu {stamp} (UTC+7)' + (':' if fresh else ' — CŨ/CHƯA XÁC MINH:')]
    lv = st.get('live') or {}
    out.append('Đồng bộ: ' + tri(lv.get('sync_ok')) + ' | Tạm dừng: ' + ('chưa rõ' if 'halted' not in lv else 'không' if lv.get('halted') in (None, 'None') else 'có, xem /log'))
    exchange = lv.get('exchange_positions')
    if not isinstance(exchange, dict): out.append('Vị thế sàn: thiếu dữ liệu; không mặc định là rỗng.')
    elif exchange:
        out.append(f'Có {len(exchange)} vị thế theo dữ liệu sàn:')
        for coin, ep in list(exchange.items())[:3]:
            if not isinstance(ep,dict): continue
            qty=number(ep.get('szi')); side='Bán' if qty is not None and qty<0 else 'Mua' if qty is not None else '?'
            out.append(f'{coin} {side} | Giá vào {ep.get("entryPx", "?")}')
            matches=[(n,b,p) for n,b in (st.get('branches') or {}).items() if isinstance(b,dict) for c,p in (b.get('positions') or {}).items() if c==coin and isinstance(p,dict)]
            if not matches: out.append('Chưa ghép được nhánh quản lý; chưa xác định mốc tiếp theo.')
            active=(cfg.get('live') or {}).get('branch')
            matches=[x for x in matches if not active or x[0]==active]
            if not matches: out.append('Chưa có chi tiết của nhánh chạy thật; không lấy mốc từ nhánh mô phỏng.')
            for name,b,p in matches[:2]:
                out.append(f'{name}: dừng bảo vệ {p.get("stop_px") or "chưa có dữ liệu"} | khóa lãi {p.get("trail") or "chưa kích hoạt"}')
                z=p.get('outlook_15m') or {}
                if isinstance(z,dict) and z: out.append(f'Điểm 15 phút: {z.get("score", "?")} / ngưỡng {z.get("th", "?")} (nến {z.get("candle", "?")}).')
                rule=str(b.get('exit_rule') or '')
                if '15m' in rule or '15 phút' in rule:
                    if (cfg.get('exit_hold_mid4h') or {}).get('enabled') and not p.get('mid4h_hit'):
                        w=(st.get('watch') or {}).get(coin) or {}
                        out.append(f'Mốc dải giữa 4 giờ: {w.get("mid", "?")}; đang chờ chạm để mở cổng chốt 15 phút.')
                    out.append('Tiếp theo: theo dõi nến 15 phút; chốt khi điểm ≤ ngưỡng hai nến liên tiếp và đạt cổng lãi/cổng dải giữa. Không có giá chốt cố định.')
                else: out.append('Quy tắc thoát: '+(rule or 'chưa có dữ liệu')[:220])
                if p.get('adds_paused'): out.append('Nhồi lệnh: tạm dừng.')
        out.append('Mốc bảo vệ theo dữ liệu lưu, chưa xác minh lệnh chờ trên sàn; giá khớp có thể khác.')
    else: out.append('Chưa có vị thế theo dữ liệu sàn. Điều kiện tham khảo:')
    btc=st.get('btc') or {}; r=st.get('btc_recheck') or {}; info=r.get('info') or {}
    out.append(f'BTC ngày/tuần: %B {btc.get("d1_pb", "?")}/{btc.get("w1_pb", "?")}; cho mua {tri(btc.get("allow"))}, bán {tri(btc.get("allow_s"))}.')
    out.append(f'BTC kiểm tra ngắn hạn: chặn mua {tri(r.get("block"))}, bán {tri(r.get("block_s"))}.')
    for key,label in [('why','mua'),('why_s','bán')]:
        if r.get(key): out.append(f'BTC chặn {label}: '+'; '.join(map(str,r[key]))[:200])
    out.append(f'BTC biến động: 4 nến 15 phút {info.get("m15x4", "?")}% | giờ đang chạy {info.get("h1_now", "?")}% | giờ đã đóng {info.get("h1_closed", "?")}%.' )
    br=cfg.get('btc_recheck') or {}; bv=cfg.get('btc_vol') or {}
    if br:
        out.append(f'Ngưỡng giảm chặn mua (4×15 phút/giờ đang chạy/giờ đóng): {br.get("m15x4", "?")}/{br.get("h1_now", "?")}/{br.get("h1_closed", "?")}%; tăng đối xứng chặn bán. Còn xét dải và khối lượng BTC.')
    if bv.get('enabled'):
        out.append(f'Cổng biến động BTC: ngưỡng 4×15 phút {bv.get("m15", "?")}%, giờ {bv.get("h1", "?")}%, biên giờ {bv.get("rng", "?")}%; chưa có đủ dữ liệu xác nhận cổng này.')
    if exchange == {}:
        rank=st.get('ranking') or {}; watch=st.get('watch') or {}
        for coin in sorted(watch,key=lambda c:(c!='ASTER',c))[:3]:
            w=watch[coin]
            if not isinstance(w,dict): continue
            tr=(st.get('trend1h') or {}).get(coin) or {}; h1=bool(tr)
            lo=number(tr.get('lo1h') if h1 else w.get('lo')); up=number(tr.get('up1h') if h1 else w.get('up')); px=number(w.get('price'))
            out.append(f'{coin}: giá {px if px is not None else "?"} | dải {"1" if h1 else "4"} giờ.')
            if px is not None and lo is not None and lo>0:
                out.append(f'Mua: chờ giá ≤ {lo:.6g} (cách {(px/lo-1)*100:+.2f}%).' if px>lo else f'Mua: đã chạm dải dưới {lo:.6g}; vẫn cần đủ bộ điều kiện.')
            else: out.append('Mua: thiếu giá/dải dưới; chưa xác định mốc.')
            if up is not None and px is not None and px>0:
                out.append(f'Bán hồi: chờ giá ≥ {up:.6g} (cách {(up/px-1)*100:+.2f}%).' if px<up else f'Bán hồi: đã chạm dải trên {up:.6g}; còn phải kiểm tra cổng bán.')
            else: out.append('Bán: thiếu giá/dải trên; chưa xác định mốc.')
            out.append('Bán phá vỡ: cần nến 4 giờ đóng dưới dải dưới; nhánh 4 giờ còn cần giá ở dưới dải, nhánh 1 giờ chờ hồi dải trên. Chưa đủ dữ liệu xác nhận cổng này.')
            cr=(rank.get('coins') or {}).get(coin) or {}; sr=cr.get('short') or {}
            out.append(f'Điểm dải mua/bán: {cr.get("score", "?")}/{sr.get("score", "?")} | tối thiểu {rank.get("bb_min", "?")} | ổn định ngày: {tri(cr.get("stab_ok"))}.')
            for k,f in (st.get('filtered') or {}).items():
                if k.split('|')[0]==coin and isinstance(f,dict): out.append('Bộ lọc ghi nhận: '+str(f.get('why') or 'chưa rõ')[:160])
        if not watch: out.append('Thiếu giá/dải ASTER; không suy đoán mốc vào.')
        out.append('Chưa xác minh đủ MA20, cổng bán, vốn/ký quỹ và điều kiện thực thi. Chạm dải không đồng nghĩa sẽ vào lệnh; mốc thay đổi theo nến.')
    return '\n'.join(out)


EVENT_LABELS = {
    'ENTRY':'Vào lệnh', 'ADD':'Nhồi lệnh', 'EXIT':'Đóng lệnh', 'STOP':'Cắt lỗ',
    'TP_PARTIAL':'Chốt một phần', 'TP_PARTIAL_15M':'Chốt một phần theo nến 15 phút',
    'EXIT_15M':'Chốt theo nến 15 phút', 'EXIT_RUNNER':'Đóng phần còn lại',
    'STEP3_ENTRY':'Bước 3 vào lệnh', 'STEP3_EXIT':'Bước 3 đóng lệnh', 'STEP3_DETECT':'Bước 3 phát hiện tín hiệu',
    'STEP3_SKIP':'Bước 3 bỏ qua', 'STEP3_ABORT':'Bước 3 hủy', 'STEP3_DROP':'Bước 3 hết điều kiện',
    'STEP3_ENTRY_FAILED':'Bước 3 lỗi vào lệnh', 'ENTRY_BLOCKED_RULES':'Chặn vào lệnh: điều kiện',
    'ENTRY_BLOCKED_BTC':'Chặn vào lệnh: BTC', 'ADD_BLOCKED_RULES':'Chặn nhồi: điều kiện',
    'ADD_BLOCKED_BTC':'Chặn nhồi: BTC', 'LIVE_ENTRY_VETO':'Cổng giao dịch chặn vào lệnh',
    'LIVE_ENTRY_FAILED':'Gửi lệnh thất bại', 'LIVE_ENTRY_ABORTED':'Hủy vào lệnh',
    'TRAIL_15M':'Dời mức bảo vệ', 'DAILY_TARGET_HIT':'Đạt mục tiêu ngày', 'LIVE_FLATTEN':'Đóng toàn bộ vị thế'
}


def action_text(e):
    """Localize command output only; preserve the recorded reason as evidence."""
    event=str(e.get('event') or '')
    label=EVENT_LABELS.get(event, 'Sự kiện giao dịch')
    side={'LONG':'Mua','SHORT':'Bán','L':'Mua','S':'Bán'}.get(e.get('side'),'')
    out=[f'{str(e.get("time", ""))[:19]} {label} {side} {e.get("coin", "")} | Giá {e.get("price", "?")}']
    reason=str(e.get('reason') or '').strip()
    branches=e.get('branches')
    if branches: out.append('Nhánh: '+(', '.join(map(str,branches)) if isinstance(branches,list) else str(branches))[:150])
    if reason: out.append('Lý do ghi nhận: '+reason[:280])
    for branch, item in (e.get('per_branch') or {}).items():
        if isinstance(item,dict):
            net=number(item.get('net',item.get('trade_net')))
            if net is not None: out.append(f'{branch}: lãi/lỗ ròng {net:+.2f}')
    return '\n'.join(out)
