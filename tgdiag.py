import tg_notify as t, json, urllib.request
tok,chat=t._load_env()
print('env_token',bool(tok),'env_chat',bool(chat))
try:
    r=t._api('getMe'); print('getMe ok',r.get('ok'),(r.get('result') or {}).get('username'))
except Exception as e: print('getMe err',repr(e)[:200].replace(tok,'<T>') if tok else repr(e)[:200])
try:
    r=t._api('getWebhookInfo'); print('webhook',bool((r.get('result') or {}).get('url')),'pending',(r.get('result') or {}).get('pending_update_count'))
except Exception as e: print('wh err',type(e).__name__)
try:
    r=t._api('sendMessage',{'chat_id':chat,'text':'[live] telegram retest - BaseAster Admin'}); print('send ok',r.get('ok'))
except Exception as e:
    b=''
    try: b=e.read().decode()[:200]
    except Exception: pass
    print('send err',type(e).__name__,b)
