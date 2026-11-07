"""BaseAster monitor helpers (read-only). Doc trang thai bot tu bot_live.log / events_live.jsonl / state_live.json.
Khong gui lenh giao dich, khong doc/in secrets. Moi duong dan tuong doi theo thu muc file nay."""
import os, re, json, time, datetime as dt, subprocess
DIR = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(DIR, *a)
LOG = P('bot_live.log'); EVF = P('events_live.jsonl'); CFG_F = P('monitor_config.json')
TASK = 'HLBot BNB X5'
DEF_CFG = {"summary_interval_min": 120, "session_min": 5, "alert_cooldown_min": 15, "stale_loop_s": 120,
           "ip_check_min": 10, "api_fail_threshold": 3, "api_fail_window_min": 5, "proc_check_s": 60,
           "send_session_to_telegram": False, "ip_url": "https://api.ipify.org", "trusted_ips": []}
def cfg():
    c = dict(DEF_CFG)
    try: c.update(json.load(open(CFG_F, encoding='utf-8')))
    except Exception: pass
    c["summary_interval_min"] = 120  # User-required reporting cadence; no config write.
    return c
TS_RE = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
RX_AUTH = re.compile(r'-2015|-2014|-2008|HTTP 401|HTTP 403|Invalid API-key|API-key format invalid', re.I)
RX_IP = re.compile(r'request ip:\s*([0-9a-fA-F:.]+)')
RX_API = re.compile(r'API fail|ConnectionError|ConnectTimeout|ReadTimeout|Timeout|Max retries|HTTP 5\d\d|HTTP 429|-1003|-1021|NameResolution|getaddrinfo', re.I)
RX_EXIT = re.compile(r'bot exited code|restarting in 30s|WATCHDOG:|KILL|BLOCKED:|STOP: ')
RX_EXC = re.compile(r'SNAPSHOT_WRITE_FAILED|Traceback \(most recent call last\)|fast-loop error|live sync error|LOOP log err|Exception|Error:')
TRADE = {'STEP3_ENTRY','STEP3_EXIT','ENTRY','ADD','EXIT','STOP','TP_PARTIAL','EXIT_RUNNER','EXIT_15M','TP_PARTIAL_15M',
         'TRAIL_15M','DAILY_TARGET_HIT','LIVE_FLATTEN','LIVE_ENTRY_FAILED','LIVE_ENTRY_VETO','STEP3_DETECT'}
def is_action(ev): return ev in TRADE or ev.startswith(('ENTRY_BLOCKED','ADD_BLOCKED')) or ev.startswith('STEP3_')
def classify(line):
    """-> 'auth' | 'api' | 'exit' | 'exc' | None"""
    if line.startswith('LOOP '): return None
    if RX_AUTH.search(line): return 'auth'
    if RX_EXIT.search(line): return 'exit'
    if RX_API.search(line): return 'api'
    if RX_EXC.search(line): return 'exc'
    return None
def parse_ts(s):
    try: return dt.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
    except Exception: return None
def parse_loop(l):
    g = lambda k: (re.search(k + r'=(\S+)', l) or [None, None])[1]
    pos = re.search(r'pos=(\[.*?\])', l); st = re.search(r'stops=(\[.*?\])', l)
    return {'ts': parse_ts(l[5:24]), 'sync_ok': g('sync_ok'), 'halted': g('halted'), 'equity': g('equity'),
            'pos': pos.group(1) if pos else '?', 'stops': st.group(1) if st else '?', 'watch': g('watch')}
def tail_lines(path, nbytes):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - nbytes)); d = f.read()
        ls = d.decode('utf-8', 'replace').splitlines()
        return ls[1:] if n > nbytes else ls
    except Exception: return []
def last_loop():
    for l in reversed(tail_lines(LOG, 60000)):
        if l.startswith('LOOP '): return parse_loop(l)
    return None
def _hidden():
    return 0x08000000 if os.name == 'nt' else 0
def proc_task():
    """Service is authoritative when installed; task is legacy fallback only.
    Count Python application leaves, excluding parent venv launcher shims.
    Scope to this service descendants or Python commands scoped to DIR.
    Return unknown on failed/denied query, never convert it into Disabled.
    """
    root = DIR.replace("'", "''")
    ps = ("$ErrorActionPreference='Stop';"
          "$s=Get-Service -Name BaseAsterTrade -ErrorAction SilentlyContinue;"
          "$all=@(Get-CimInstance Win32_Process);$ids=@();"
          "if($s){$v=Get-CimInstance Win32_Service -Filter \"Name='BaseAsterTrade'\";"
          "if($v.ProcessId -gt 0){$ids=@($v.ProcessId);do{"
          "$ch=@($all | Where-Object {$_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids} | ForEach-Object {$_.ProcessId});"
          "$ids+= $ch}while($ch.Count -gt 0)};$state=[string]$s.Status}"
          "else{$state=[string](Get-ScheduledTask -TaskName 'HLBot BNB X5' -ErrorAction SilentlyContinue).State};"
          "$py=@($all | Where-Object {$_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -and $_.CommandLine -match '\\bbnb_bot\\.py\\b' -and "
          "($_.ProcessId -in $ids -or ($_.CommandLine -and $_.CommandLine -match '\\bbnb_bot\\.py\\b' -and $_.CommandLine.Contains('%s')))});$n=@($py | Where-Object {$_.ProcessId -notin $py.ParentProcessId}).Count;"
          "Write-Output \"$state|$n\"") % root
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                           capture_output=True, text=True, timeout=40, creationflags=_hidden())
        if r.returncode: return None, None
        a, b = r.stdout.strip().splitlines()[-1].split('|')
        return a or 'Missing', int(b)
    except Exception:
        return None, None

def window(t0, t1):
    """Tong hop log + events trong [t0,t1)."""
    r = {'loops': 0, 'first': None, 'last': None, 'errs': {'auth': 0, 'api': 0, 'exit': 0, 'exc': 0}, 'err_samples': [],
         'actions': [], 'ips': set(), 'halted': set(), 'sync_false': 0}
    span = (t1 - t0).total_seconds()
    cur = None
    for l in tail_lines(LOG, int(max(400000, span * 40))):
        if l.startswith('LOOP '):
            lp = parse_loop(l); cur = lp['ts']
            if cur and t0 <= cur < t1:
                r['loops'] += 1; r['last'] = lp; r['first'] = r['first'] or lp
                if lp['halted'] not in (None, 'None'): r['halted'].add(lp['halted'])
                if lp['sync_ok'] == 'False': r['sync_false'] += 1
            continue
        m = TS_RE.match(l)
        if m: cur = parse_ts(m.group(1)) or cur
        if cur is None or not (t0 <= cur < t1): continue
        k = classify(l)
        if k:
            r['errs'][k] += 1
            ip = RX_IP.search(l)
            if ip: r['ips'].add(ip.group(1))
            if len(r['err_samples']) < 5: r['err_samples'].append(l[:200])
    for l in tail_lines(EVF, int(max(300000, span * 30))):
        try:
            e = json.loads(l); ev = str(e.get('event', '')); ts = parse_ts(str(e.get('time', '')))
            if ts and t0 <= ts < t1 and is_action(ev): r['actions'].append(e)
        except Exception: pass
    return r
def _eq(x):
    try: return f"{float(x):,.2f}$"
    except Exception: return str(x)
def act_line(e):
    ev = e.get('event'); tm = str(e.get('time', ''))[11:16]
    px = e.get('price'); rs = str(e.get('reason') or '')[:120]
    return f"• {tm} {ev} {e.get('side') or ''} {e.get('coin') or ''}" + (f" @ {px}" if px not in (None, '', 0) else '') + (f" — {rs}" if rs and ev.endswith(('BLOCKED_RULES','BLOCKED_BTC','VETO','FAILED')) else '')
def summary_text(minutes=120, title='PHIEN', t0=None, t1=None):
    from reporting import summary
    t1 = t1 or dt.datetime.now()
    t0 = t0 or t1 - dt.timedelta(minutes=minutes)
    return summary(DIR, t0, t1, parse_loop, classify)

def session_line(t0, t1):
    r = window(t0, t1); lp = r['last'] or {}
    return (f"{t0:%Y-%m-%d %H:%M}-{t1:%H:%M} loops={r['loops']} run={'Y' if r['loops'] >= 10 else ('PARTIAL' if r['loops'] else 'N')} "
            f"err_auth={r['errs']['auth']} err_api={r['errs']['api']} exit={r['errs']['exit']} exc={r['errs']['exc']} "
            f"actions={len(r['actions'])}[{';'.join(str(e.get('event'))+':'+str(e.get('coin') or '') for e in r['actions'])}] "
            f"equity={lp.get('equity')} pos={lp.get('pos')} stops={lp.get('stops')} sync_ok={lp.get('sync_ok')} halted={lp.get('halted')}")
def status_text():
    c = cfg(); lp = last_loop(); launcher, n = proc_task(); now = dt.datetime.now()
    age = (now-lp['ts']).total_seconds() if lp and lp['ts'] else None
    fresh = age is not None and 0 <= age < c['stale_loop_s']
    alive = fresh and launcher == 'Running' and n is not None and n == 1
    state = 'DANG HOAT DONG' if alive else ('CHUA XAC MINH SERVICE' if launcher is None else 'BAT THUONG')
    out = [f'[LIVE] STATUS {now:%d/%m/%Y %H:%M:%S} UTC+7', f'Bot: {state}',
           f'Launcher BaseAsterTrade (task chi fallback): {launcher} | Python bot: {n}',
           f'Heartbeat: {lp["ts"] if lp else "?"} | age={int(age) if age is not None else "?"}s']
    if lp:
        out += [f'Sync={lp["sync_ok"]} | Halted={lp["halted"]}',
                f'Equity={_eq(lp["equity"])} | Vi the={lp["pos"]}']
    from reporting import decision_text
    out.append(decision_text(DIR))
    errs = last_errors(1)
    if errs:
        match = TS_RE.search(errs[0])
        out.append('Su co gan nhat trong LICH SU: ' + (match.group(1) if match else 'xem /log') + '; xem heartbeat/Sync hien tai de xac dinh phuc hoi.')
    out.append('Service Running khong dong nghia giao dich binh thuong; xem Sync/Halted va heartbeat.')
    return '\n'.join(out)

def last_errors(n=8):
    res = []; cur = ''
    for l in tail_lines(LOG, 800000):
        m = TS_RE.match(l)
        if m: cur = m.group(1)
        if classify(l): res.append((l if m else f"{cur} | {l}")[:220])
    return res[-n:][::-1]
def log_text(n=8):
    e = last_errors(n)
    return '[LIVE] LỖI GẦN NHẤT (mới nhất trước):\n' + ('\n'.join(e) if e else 'Không có lỗi trong log gần đây.')
HELP = ("[LIVE] Các lệnh được phép (chỉ đọc, không đặt lệnh):\n"
        "/status – bot có chạy không, task, tiến trình, LOOP cuối, equity, vị thế, lỗi cuối\n"
        "/pnl – vốn, số lệnh, PNL\n/action – các hành động gần nhất (vào/thoát/chặn)\n"
        "/summary – tổng hợp 60 phút gần nhất (gõ /summary 15 để chọn số phút)\n"
        "/log – các lỗi gần nhất trong log\n"
        "/ip – IP hiện tại + danh sách IP đã trust\n/trustip <ip> – thêm IP trust (cần /confirm)\n"
        "/untrustip <ip> – xoá IP trust (cần /confirm)\n/confirm, /cancel – xác nhận / huỷ\n/help – danh sách lệnh")
COMMANDS = [('status', 'Bot có đang chạy không + trạng thái'), ('pnl', 'Vốn, số lệnh, PNL'),
            ('action', 'Hành động gần nhất'), ('summary', 'Tổng hợp 60 phút (vd /summary 15)'),
            ('log', 'Lỗi gần nhất trong log'), ('ip', 'IP hiện tại + danh sách trust'),
            ('trustip', 'Thêm IP trust: /trustip 1.2.3.4'), ('untrustip', 'Xoá IP trust'),
            ('confirm', 'Xác nhận thay đổi IP'), ('cancel', 'Huỷ thay đổi IP'), ('help', 'Danh sách lệnh')]

# ---- Trusted IP (05/11): danh sach IP da whitelist tren Binance, luu trong monitor_config.json 'trusted_ips'
import ipaddress
PEND_F = P('state', 'ip_pending.json')
def trusted_ips(c=None):
    c = c or cfg(); return [str(x).strip() for x in (c.get('trusted_ips') or []) if str(x).strip()]
def valid_ipv4(ip):
    try: return isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address)
    except Exception: return False
def _save_cfg_key(k, v):
    try: d = json.load(open(CFG_F, encoding='utf-8'))
    except Exception: d = {}
    d[k] = v; tmp = CFG_F + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_F)
def public_ip(c=None):
    c = c or cfg()
    try:
        import urllib.request
        ip = urllib.request.urlopen(c['ip_url'], timeout=10).read().decode().strip()
        return ip if ip and len(ip) <= 45 else None
    except Exception: return None
def ip_text():
    c = cfg(); ip = public_ip(c); tl = trusted_ips(c)
    st = ('✅ nằm trong danh sách trust' if ip in tl else '⚠️ KHÔNG nằm trong danh sách trust') if ip else '❓ không đọc được'
    return (f"[LIVE] IP PUBLIC hiện tại: {ip or '-'} ({st})\nDanh sách IP đã trust ({len(tl)}):\n"
            + ('\n'.join(f"• {x}" for x in tl) if tl else '• (trống)')
            + "\n\nThêm: /trustip <ip> rồi /confirm trong 2 phút. Xoá: /untrustip <ip> rồi /confirm.")
def ip_request(op, ip):
    ip = (ip or '').strip(); tl = trusted_ips()
    if not valid_ipv4(ip): return f"[LIVE] IP không hợp lệ: '{ip}'. Ví dụ: /{op}ip 1.2.3.4"
    if op == 'trust' and ip in tl: return f"[LIVE] {ip} đã có trong danh sách trust."
    if op == 'untrust' and ip not in tl: return f"[LIVE] {ip} không có trong danh sách trust."
    json.dump({'op': op, 'ip': ip, 'ts': time.time()}, open(PEND_F, 'w', encoding='utf-8'))
    act = 'THÊM' if op == 'trust' else 'XOÁ'
    note = '\nChỉ thêm sau khi anh ĐÃ whitelist IP này trên Binance (API Management → Edit restrictions).' if op == 'trust' else ''
    return f"[LIVE] Xác nhận {act} IP {ip} {'vào' if op=='trust' else 'khỏi'} danh sách trust?{note}\nGõ /confirm trong 2 phút để xác nhận, /cancel để huỷ."
def ip_confirm(cancel=False):
    try: p = json.load(open(PEND_F, encoding='utf-8'))
    except Exception: p = None
    try: os.remove(PEND_F)
    except Exception: pass
    if not p: return '[LIVE] Không có yêu cầu nào đang chờ xác nhận.'
    if cancel: return f"[LIVE] Đã huỷ yêu cầu {p['op']} {p['ip']}."
    if time.time() - p.get('ts', 0) > 120: return '[LIVE] Yêu cầu đã quá 2 phút, hết hạn. Gõ lại /trustip hoặc /untrustip.'
    tl = trusted_ips()
    if p['op'] == 'trust' and p['ip'] not in tl: tl.append(p['ip'])
    if p['op'] == 'untrust': tl = [x for x in tl if x != p['ip']]
    _save_cfg_key('trusted_ips', tl)
    return f"[LIVE] ✅ Đã {'thêm' if p['op']=='trust' else 'xoá'} {p['ip']}. Danh sách trust: {', '.join(tl) or '(trống)'}"
