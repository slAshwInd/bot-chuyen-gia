"""EMERGENCY flatten (independent of the bot): create KILL, cancel ALL open orders, close ALL positions reduce-only at market.
Usage: python emergency.py          -> sends real orders (sets HL_DRY_RUN=0 itself)
       python emergency.py --dry    -> only prints what it would send"""
import os,sys,json,time
B=os.path.dirname(os.path.abspath(__file__))+os.sep
DRY='--dry' in sys.argv
if not DRY:os.environ['BNB_DRY_RUN']='0'
import winutf8,livecore
import bnx as hlx
cfg=json.load(open(B+'config_live.json',encoding='utf-8'));L=cfg['live']
if not DRY:
    try:open(B+'KILL','w').write('emergency '+time.strftime('%Y-%m-%d %H:%M:%S')+'\n')
    except OSError as ex:print('cannot create KILL:',ex)
def emit(e):
    e=dict(e);e.setdefault('time',time.strftime('%Y-%m-%d %H:%M:%S'));print(e.get('event'),e.get('coin',''),str(e.get('reason',''))[:200])
    with open(B+'events_live.jsonl','a',encoding='utf-8') as f:f.write(json.dumps(e,ensure_ascii=False,default=str)+'\n')
hx=hlx.HLX(None,dry_run=DRY,log=lambda e:emit(dict(e,reason=e.get('label',''))),max_open_notional=float(L.get('max_notional') or 0.0))
LV=livecore.Live(B,cfg,hx,emit,dry=hx.dry_run)
st=LV.refresh(0.0);print('positions before:',{c:x['szi'] for c,x in st['positions'].items()} or 'none','| dry_run =',hx.dry_run)
LV.flatten_all('EMERGENCY')
time.sleep(1.5);st=hx.user_state();print('positions after:',{c:x['szi'] for c,x in st['positions'].items()} or 'none','| open orders:',len(hx.open_orders()),'| equity:',round(st['equity'],4),'USDT')
print('KILL file created: the live loop will not restart. To resume: run nut BNB X5 - BAT BOT')
