"""LIVE Telegram alerts (ENTRY/ADD/EXIT/STOP...). Non-blocking: sends in a daemon thread; never raises into trading loop."""
import threading
try:
    import tg_notify
except Exception:
    tg_notify=None
EVENTS=('ENTRY','ADD','EXIT','STOP','TP_PARTIAL','EXIT_RUNNER','EXIT_15M','TP_PARTIAL_15M')
def _go(text):
    try: tg_notify._send_direct(text)
    except Exception: pass
def notify(ev):
    try:
        if tg_notify is None or ev.get('event') not in EVENTS: return
        t=tg_notify.format_action(ev)
        if not t: return
        threading.Thread(target=_go,args=('[LIVE] '+t,),daemon=True).start()
    except Exception: pass
def test(msg='[live] telegram test'):
    return tg_notify._send_direct(msg)
def send(text):
    try:
        if tg_notify is None:return
        threading.Thread(target=_go,args=(text,),daemon=True).start()
    except Exception: pass
