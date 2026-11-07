"""BaseAster monitor: theo doi bot_live.log, canh bao Telegram (co chong spam + bao phuc hoi),
tong hop phien 5 phut vao logs\\session_YYYYMMDD.txt, tong hop dinh ky len Telegram, phat hien doi IP public.
Chi DOC file cua bot. Khong goi getUpdates (poller lo phan do). Khong in secrets.
  python monitor_bnb.py               chay vong lap
  python monitor_bnb.py --test-alert  gui 1 tin canh bao thu
  python monitor_bnb.py --test-log F  quet file log F (ban sao) va in canh bao phat hien, KHONG gui"""
import os, sys, json, time, datetime as dt, urllib.request
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import mon_common as M
import tg_notify as T
ST_F = M.P('state', 'monitor_state.json'); HB = M.P('logs', 'monitor_heartbeat.txt'); MLOG = M.P('logs', 'monitor.log')
os.makedirs(M.P('logs'), exist_ok=True); os.makedirs(M.P('state'), exist_ok=True)
def mlog(s):
    try:
        with open(MLOG, 'a', encoding='utf-8') as f: f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {s}\n")
    except Exception: pass
def load():
    try: return json.load(open(ST_F, encoding='utf-8'))
    except Exception: return {}
def save(s):
    tmp = ST_F + '.tmp'; json.dump(s, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False); os.replace(tmp, ST_F)
class Mon:
    def __init__(self, send=True):
        self.s = load(); self.send_on = send
        self.s.setdefault('active', {}); self.s.setdefault('sent', {}); self.s.setdefault('outbox', [])
        self.api_hits = []; self.sent_now = []
    def tg(self, txt):
        self.sent_now.append(txt); mlog('SEND ' + txt.replace('\n', ' | ')[:300])
        if not self.send_on: print('---\n' + txt); return
        ob = self.s['outbox'] + [txt]; keep = []
        for m in ob:
            ok = False
            try: ok = T._send_direct(m)
            except Exception: ok = False
            if not ok: keep.append(m)
        self.s['outbox'] = keep[-20:]
    def retry_outbox(self):
        if not self.send_on: return
        keep = []
        for txt in self.s['outbox']:
            try: ok = T._send_direct(txt)
            except Exception: ok = False
            if not ok: keep.append(txt)
        self.s['outbox'] = keep

    def alert(self, key, txt, cd):
        now = time.time(); first = key not in self.s['active']
        self.s['active'][key] = now
        if first or now - self.s['sent'].get(key, 0) >= cd:
            self.s['sent'][key] = now; self.tg('🚨 [CẢNH BÁO] ' + txt)
    def clear(self, key, txt):
        if key in self.s['active']:
            self.s['active'].pop(key, None); self.s['sent'].pop(key, None); self.tg('✅ [ĐÃ PHỤC HỒI] ' + txt)
    def scan_lines(self, lines, c):
        cd = c['alert_cooldown_min'] * 60; win = c['api_fail_window_min'] * 60; now = time.time()
        auth_ip = None; got_auth = False; exc = []; exits = []; loop_ok = False
        for l in lines:
            if l.startswith('LOOP '):
                lp = M.parse_loop(l)
                if lp['sync_ok'] == 'True': loop_ok = True
                if lp['halted'] not in (None, 'None'): self.alert('halted', f"Bot báo halted={lp['halted']} — bot tạm dừng giao dịch. Kiểm tra log / /status.", cd)
                else: self.clear('halted', 'Bot hết trạng thái halted.')
                if lp['sync_ok'] == 'False': self.alert('sync', 'sync_ok=False — bot không đồng bộ được tài khoản Binance (lỗi API/mạng?).', cd)
                elif lp['sync_ok'] == 'True': self.clear('sync', 'Đồng bộ tài khoản Binance bình thường lại (sync_ok=True).')
                continue
            k = M.classify(l)
            if k == 'auth':
                got_auth = True; ip = M.RX_IP.search(l); auth_ip = ip.group(1) if ip else auth_ip
            elif k == 'api': self.api_hits.append(now)
            elif k == 'exit': exits.append(l.strip()[:200])
            elif k == 'exc': exc.append(l.strip()[:200])
        if got_auth:
            self.alert('auth', 'Binance TỪ CHỐI API key (-2015/401: sai key, sai IP hoặc thiếu quyền).'
                       + (f"\nIP đang gọi: {auth_ip} → cần thêm IP này vào whitelist API key trên Binance (API Management → Edit restrictions)." if auth_ip else '')
                       + '\nBot sẽ không giao dịch được cho tới khi sửa.', cd)
            if auth_ip and M.trusted_ips(c) and auth_ip not in M.trusted_ips(c): self.ip_alert(auth_ip, 'Binance báo request ip', c)
        elif loop_ok: self.clear('auth', 'API key hoạt động lại (bot đồng bộ được với Binance).')
        self.api_hits = [x for x in self.api_hits if now - x < win]
        if len(self.api_hits) >= c['api_fail_threshold']:
            self.alert('api', f"Gọi API Binance thất bại {len(self.api_hits)} lần trong {c['api_fail_window_min']} phút (mất mạng / modem restart / Binance lỗi?).", cd)
        elif not self.api_hits and loop_ok: self.clear('api', 'Gọi API Binance bình thường lại.')
        if exits: self.alert('exit', 'Bot thoát / được khởi động lại:\n' + '\n'.join(exits[-3:]), cd)
        if exc: self.alert('exc', 'Exception trong bot:\n' + '\n'.join(exc[-3:]), cd)
    def read_new(self):
        try: n = os.path.getsize(M.LOG)
        except Exception: return []
        pos = self.s.get('pos')
        if pos is None or pos > n: pos = n if pos is None else 0
        if n == pos: self.s['pos'] = pos; return []
        with open(M.LOG, 'rb') as f: f.seek(pos); d = f.read(min(n - pos, 5_000_000))
        k = d.rfind(b'\n')
        if k < 0: return []
        self.s['pos'] = pos + k + 1
        return d[:k + 1].decode('utf-8', 'replace').splitlines()
    def check_alive(self, c, proc):
        cd = c['alert_cooldown_min'] * 60; lp = M.last_loop()
        age = (dt.datetime.now() - lp['ts']).total_seconds() if lp and lp['ts'] else 1e9
        if age > c['stale_loop_s']:
            self.alert('stale', f"Bot KHÔNG hoạt động: không có LOOP mới {int(age//60)} phút (LOOP cuối {lp['ts'] if lp else '-'}).", cd)
        else: self.clear('stale', f"Bot hoạt động lại (LOOP cuối {lp['ts']:%H:%M:%S}).")
        if proc:
            task, n = proc
            if task is None: return
            if task != 'Running' or n == 0:
                self.alert('proc', f'Launcher BaseAsterTrade (task fallback): {task} | Python bot: {n}. Kiem tra Service va heartbeat; khong tu khoi dong ban thu hai.', cd)
            else: self.clear('proc', f"Launcher BaseAsterTrade (task fallback) Running, tiến trình bnb_bot.py: {n}.")
    def ip_alert(self, ip, src, c):
        tl = M.trusted_ips(c)
        if not tl: return
        if ip in tl: self.clear('ip_untrusted', f"IP public {ip} đã nằm trong danh sách trust. Bot gọi Binance bình thường."); return
        self.alert('ip_untrusted', f"IP {ip} ({src}) KHÔNG nằm trong danh sách trust ({', '.join(tl)}).\n"
                   f"1) Thêm {ip} vào whitelist API key trên Binance (API Management → Edit restrictions).\n"
                   f"2) Gõ /trustip {ip} rồi /confirm để cập nhật danh sách.\nNếu không, bot sẽ bị từ chối (-2015).", c['alert_cooldown_min'] * 60)
    def check_ip(self, c, ip='__fetch__'):
        if ip == '__fetch__': ip = M.public_ip(c)
        if not ip: return
        old = self.s.get('ip'); self.s['ip'] = ip
        if M.trusted_ips(c): self.ip_alert(ip, 'IP public', c); return
        if old and old != ip:
            self.tg(f"🌐 [CẢNH BÁO] IP public đổi: {old} → {ip}. Nếu API key giới hạn IP, cần thêm {ip} vào whitelist.")
def run():
    m = Mon(); c = M.cfg(); now = dt.datetime.now()
    m.s['pos'] = m.s.get('pos', os.path.getsize(M.LOG) if os.path.exists(M.LOG) else 0)
    sm = c['session_min']; nxt_sess = m.s.get('next_sess') or (now.replace(second=0, microsecond=0) + dt.timedelta(minutes=sm - now.minute % sm)).isoformat()
    m.s['next_sess'] = nxt_sess; m.s.setdefault('next_sum', (now + dt.timedelta(minutes=c['summary_interval_min'])).isoformat())
    from reporting import due_window
    due_window(m.s, now) if m.s.get('summary_schema') != 2 else None
    t_proc = t_ip = 0; mlog('monitor start'); m.tg('ℹ [MONITOR] Bộ giám sát BaseAster đã khởi động. Gõ /status để xem trạng thái.')
    while True:
        try:
            c = M.cfg(); now = dt.datetime.now(); proc = None
            if m.s['outbox']: m.retry_outbox()
            m.scan_lines(m.read_new(), c)
            if time.time() - t_proc >= c['proc_check_s']: proc = M.proc_task(); t_proc = time.time()
            m.check_alive(c, proc)
            if time.time() - t_ip >= c['ip_check_min'] * 60: m.check_ip(c); t_ip = time.time()
            ns = dt.datetime.fromisoformat(m.s['next_sess'])
            if now >= ns:
                t0 = ns - dt.timedelta(minutes=c['session_min']); line = M.session_line(t0, ns)
                with open(M.P('logs', f"session_{t0:%Y%m%d}.txt"), 'a', encoding='utf-8') as f: f.write(line + '\n')
                if c.get('send_session_to_telegram'): m.tg(M.summary_text(c['session_min'], 'PHIÊN')[0])
                while ns <= now: ns += dt.timedelta(minutes=c['session_min'])
                m.s['next_sess'] = ns.isoformat()
            schedule = dict(m.s)
            due = due_window(schedule, now)
            if due:
                text = M.summary_text(120, t0=due[0], t1=due[1])[0]
                # Persist queued report + schedule together before attempting delivery.
                m.s.update(schedule)
                m.s['outbox'].append(text)
                save(m.s)
                m.retry_outbox()
            else:
                m.s.update(schedule)
            save(m.s)
            open(HB, 'w', encoding='utf-8').write(f"{now:%Y-%m-%d %H:%M:%S} ok active={list(m.s['active'])} ip={m.s.get('ip')}\n")
        except Exception as ex:
            mlog(f"loop err {type(ex).__name__}: {str(ex)[:200]}")
        time.sleep(15)
def test_log(path):
    global ST_F
    ST_F = M.P('state', 'monitor_state_test.json')
    m = Mon(send=False); m.s = {'active': {}, 'sent': {}, 'outbox': []}; c = M.cfg()
    lines = open(path, encoding='utf-8', errors='replace').read().splitlines()
    m.scan_lines(lines, c); print(f"\n== {len(m.sent_now)} canh bao phat hien tu {path}")
if __name__ == '__main__':
    if '--test-alert' in sys.argv:
        print('sent', T._send_direct('🧪 [TEST] Cảnh báo thử từ monitor_bnb.py (BaseAster). Nếu anh thấy tin này, kênh cảnh báo hoạt động.'))
    elif '--test-ip' in sys.argv:   # mo phong IP (KHONG gui Telegram, state test rieng)
        ST_F = M.P('state', 'monitor_state_test.json'); m = Mon(send=False); m.s = {'active': {}, 'sent': {}, 'outbox': []}; c = M.cfg()
        for ip in sys.argv[sys.argv.index('--test-ip') + 1:]: print('== IP', ip); m.check_ip(c, ip)
        print(f"== {len(m.sent_now)} tin")
    elif '--test-log' in sys.argv: test_log(sys.argv[sys.argv.index('--test-log') + 1])
    elif '--summary' in sys.argv: print(M.summary_text(int(sys.argv[-1]) if sys.argv[-1].isdigit() else 60)[0])
    elif '--status' in sys.argv: print(M.status_text())
    else: run()
