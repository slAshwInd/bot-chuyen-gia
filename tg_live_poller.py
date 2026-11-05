"""Live Telegram command poller: /pnl, /action, /help. Reads live files only. Never prints secrets."""
import os, json, time, re, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import tg_notify as t
import mon_common as MC
OFF='tg_live_offset.txt'
TRADE={'STEP3_ENTRY','STEP3_EXIT','STEP3_DETECT','ENTRY','ADD','EXIT','STOP','TP_PARTIAL','EXIT_RUNNER','EXIT_15M','TP_PARTIAL_15M'}
def last_loop():
    try:
        with open('bot_live.log','rb') as f:
            f.seek(0,2); n=f.tell(); f.seek(max(0,n-20000)); data=f.read().decode('utf-8','replace')
        ls=[l for l in data.splitlines() if l.startswith('LOOP ')]
        return ls[-1] if ls else None
    except Exception: return None
def pnl():
    l=last_loop()
    if not l: return '[LIVE] Chưa đọc được trạng thái bot.'
    g=lambda k: (re.search(k+r'=(\S+)',l) or [None,'?'])[1]
    try: eq=f"{float(g('equity')):.2f}$"
    except Exception: eq=g('equity')
    ts=l[5:24]; age=''
    try:
        import datetime; a=(datetime.datetime.now()-datetime.datetime.strptime(ts,'%Y-%m-%d %H:%M:%S')).total_seconds(); age=f" ({int(a)}s trước)"
    except Exception: pass
    pos=re.search(r'pos=(\[.*?\])',l); st=re.search(r'stops=(\[.*?\])',l)
    return (f"[LIVE] BaseAster PnL\nCập nhật: {ts}{age}\nEquity: {eq}\nSync: {g('sync_ok')} | Halted: {g('halted')}\n"
            f"Vị thế: {pos.group(1) if pos else '?'}\nStop: {st.group(1) if st else '?'}"+s3())
def s3():
    try:
        d=json.load(open('state/step3.json',encoding='utf-8'));p=d.get('pos')
        if p:return f"\nBước 3: đang giữ {'SHORT' if p['side']=='S' else 'LONG'} ASTER {p['q']} @ {p['entry']:.5g}, SL {p['sl']:.5g}, TP {p['tp']:.5g}, đóng lúc {str(p['until'])[:16]}"
        pd_=(d.get('det') or {}).get('pend') or []
        if pd_:return '\nBước 3: chờ nến gãy '+', '.join(f"{'SHORT' if x['side']=='S' else 'LONG'} (phát hiện {x['det_bar'][:16]}, {x['n']}/24 nến)" for x in pd_)
        return '\nBước 3: không có tín hiệu'
    except Exception: return '\nBước 3: chưa có trạng thái'
# ---- /pnl v2 (Vien 04/11 t690u): Vốn / Số lệnh / PNL / Entry / PNL tạm tính
START_CAPITAL=1000.0   # override via config_live.json 'start_capital' (Vien t691u)
def _f(x,d=2):
    try: return f"{float(x):,.{d}f}"
    except Exception: return '?'
def pnl():
    try: st=json.load(open('state_live.json',encoding='utf-8'))
    except Exception: return '[LIVE] Chưa đọc được state_live.json.'
    lv=st.get('live') or {}; br=(st.get('branches') or {}).get('BASE_SL250') or next(iter((st.get('branches') or {}).values()),{})
    eq=lv.get('equity', br.get('equity')); wallet=lv.get('usdc_total') or lv.get('base')
    rows=[]
    try:
        import csv
        with open('trades_live.csv',encoding='utf-8') as f: rows=list(csv.DictReader(f))
    except Exception: pass
    if rows:
        nets=[float(r.get('net') or 0) for r in rows]; n=len(nets); w=sum(1 for x in nets if x>0); real=sum(nets)
    else:
        n=int(br.get('n_trades') or 0); w=int(br.get('n_wins') or 0); real=float(br.get('realized') or 0)
    l=n-w
    try: start=float(json.load(open('config_live.json',encoding='utf-8')).get('start_capital',START_CAPITAL))
    except Exception: start=START_CAPITAL
    out=['[LIVE] BÁO CÁO PNL', f"Cập nhật: {str(st.get('updated') or '')[:19]} (UTC+7)", '',
         f"💰 Vốn: {_f(eq)} USDT"+(f" (ví {_f(wallet)})" if wallet is not None else ''),
         f"📊 Số lệnh: {n} (thắng {w} / thua {l})"+(f" | từ {str(br.get('started'))[:10]}" if br.get('started') else ''),
         f"🏦 Vốn đầu: {start:,.0f} USDT",
         f"✅ PNL: {'+' if real>=0 else ''}{_f(real)} USDT"+(f" ({'+' if real>=0 else ''}{real/start*100:.2f}% so với vốn đầu)" if start else ''),
         '', '📍 Entry:']
    ex=lv.get('exchange_positions') or {}; bp=br.get('positions') or {}; lev=lv.get('leverage') or br.get('lev'); tu=0.0; tm=0.0
    coins=list(dict.fromkeys(list(ex.keys())+list(bp.keys())))
    if not coins: out.append('Không có lệnh')
    for c in coins:
        e=ex.get(c) or {}; b=bp.get(c) or {}
        szi=e.get('szi'); side=b.get('side') or ('SHORT' if (szi or 0)<0 else 'LONG')
        px=e.get('entryPx') or b.get('avg'); sz=abs(float(szi)) if szi is not None else None
        notl=(sz*float(px)) if (sz and px) else b.get('notional'); mg=b.get('margin') or ((notl/float(lev)) if (notl and lev) else None)
        u=e.get('upnl', b.get('unreal')); tu+=float(u or 0); tm+=float(mg or 0)
        out.append(f"• {c} {side} @ {float(px):.6g}" if px else f"• {c} {side}")
        out.append(f"  KL {_f(sz,0)} {c} ≈ {_f(notl)} USDT | Margin {_f(mg)} | x{_f(lev,0)}")
        stp=(lv.get('stops') or {}).get(c) or {}
        out.append(f"  Vào lúc: {str(b.get('since') or '?')[:16]} (UTC+7)"+(f" | SL {stp.get('trigger')}" if stp.get('trigger') else ''))
    if not coins: tu=0.0
    pct=(tu/tm*100) if tm else 0.0
    out+=['', f"📈 PNL tạm tính: "+('0' if not coins else f"{'+' if tu>=0 else ''}{_f(tu)} USDT ({'+' if pct>=0 else ''}{pct:.2f}% margin)")]
    return '\n'.join(out)
BLOCK_PFX=('ENTRY_BLOCKED','ADD_BLOCKED')
BLOCK_EV={'LIVE_ENTRY_VETO','LIVE_ENTRY_FAILED','STEP3_SKIP','STEP3_ABORT','STEP3_DROP','STEP3_ENTRY_FAILED'}
def _isact(ev): return ev in TRADE or ev in BLOCK_EV or ev.startswith(BLOCK_PFX)
def _fmt(e):
    ev=str(e.get('event','')); tm=str(e.get('time',''))[5:16].replace('-','/')
    if ev.startswith(BLOCK_PFX) or ev in BLOCK_EV:
        lbl={'ENTRY_BLOCKED_RULES':'⛔ CHẶN VÀO LỆNH (rule)','ENTRY_BLOCKED_BTC':'⛔ CHẶN VÀO LỆNH (BTC)','ADD_BLOCKED_RULES':'⛔ CHẶN NHỒI (rule)','ADD_BLOCKED_BTC':'⛔ CHẶN NHỒI (BTC)',
             'LIVE_ENTRY_VETO':'⛔ SÀN TỪ CHỐI VÀO LỆNH','LIVE_ENTRY_FAILED':'⚠️ VÀO LỆNH THẬT THẤT BẠI'}.get(ev,'⛔ BƯỚC 3: '+ev.replace('STEP3_',''))
        sd=e.get('side') or ''; px=e.get('price'); r=str(e.get('reason') or '')
        r=r.split(': ',1)[1] if r.startswith('Chặn') and ': ' in r else r
        return f"{tm} {lbl} {sd} {e.get('coin','')}"+(f" @ {px}" if px not in (None,'',0,'0') else '')+f"\nLý do: {r[:300]}"
    if ev.startswith('STEP3'): return f"{tm} 🔷 {e.get('reason')}"
    try: return t.format_action(e)
    except Exception: return f"{tm} {ev} {e.get('coin')} @ {e.get('price')}"
def action(n=5):
    evs=[];seen={}
    try:
        with open('events_live.jsonl',encoding='utf-8',errors='replace') as f:
            for line in f:
                try:
                    e=json.loads(line);ev=str(e.get('event',''))
                    if not _isact(ev): continue
                    if ev in TRADE: evs.append(e); continue
                    import datetime as _d
                    k=(ev,e.get('coin'),e.get('side'),str(e.get('reason'))[:80]); ts=_d.datetime.strptime(str(e.get('time'))[:19],'%Y-%m-%d %H:%M:%S')
                    if k in seen and (ts-seen[k]).total_seconds()<900: continue   # same block <15 min -> skip (anti-spam)
                    seen[k]=ts; evs.append(e)
                except Exception: pass
    except Exception: pass
    if not evs: return '[LIVE] Chưa có hành động nào trong log live.'
    out=['[LIVE] %d hành động gần nhất (mới nhất trước, gồm cả lệnh bị chặn):'%min(n,len(evs))]
    for e in reversed(evs[-n:]): out.append(_fmt(e))
    return '\n\n'.join(out)
# ---- proactive push (Vien 03/11 t663u): only types NOT already sent by bnb_bot (tg_live.notify sends ENTRY/ADD/EXIT/STOP/TP*; step3 sends its own STEP3_*)
import threading, datetime as _dt
PUSH_EV={'LIVE_ENTRY_VETO','LIVE_ENTRY_FAILED','TRAIL_15M','DAILY_TARGET_HIT','LIVE_FLATTEN'}
CUR='tg_push_cursor.txt'
def _push_ok(ev): return ev.startswith(BLOCK_PFX) or ev in PUSH_EV
def _pfmt(e):
    ev=str(e.get('event',''))
    if ev in ('TRAIL_15M','DAILY_TARGET_HIT','LIVE_FLATTEN'):
        lbl={'TRAIL_15M':'🔒 LOCK/TRAIL','DAILY_TARGET_HIT':'🎯 ĐẠT MỤC TIÊU NGÀY','LIVE_FLATTEN':'⚠️ ĐÓNG HẾT VỊ THẾ'}[ev]
        tm=str(e.get('time',''))[5:16].replace('-','/');px=e.get('price')
        return f"{tm} {lbl} {e.get('coin','')}"+(f" @ {px}" if px not in (None,'',0,'0') else '')+f"\nLý do: {str(e.get('reason') or '')[:300]}"
    return _fmt(e)
def _pusher():
    try: pos=int(open(CUR).read().strip())
    except Exception:
        pos=os.path.getsize('events_live.jsonl') if os.path.exists('events_live.jsonl') else 0; open(CUR,'w').write(str(pos))
    seen={}
    while True:
        try:
            n=os.path.getsize('events_live.jsonl')
            if n<pos: pos=n; open(CUR,'w').write(str(pos))
            if n>pos:
                with open('events_live.jsonl','rb') as f:
                    f.seek(pos); data=f.read(n-pos)
                k=data.rfind(b'\n')
                if k>=0:
                    for ln in data[:k+1].decode('utf-8','replace').splitlines():
                        try:
                            e=json.loads(ln);ev=str(e.get('event',''))
                            if not _push_ok(ev): continue
                            key=(ev,e.get('coin'),e.get('side'),str(e.get('reason'))[:80]);now=time.time()
                            if now-seen.get(key,0)<900: continue    # same coin/side/reason <15 min -> skip
                            seen[key]=now; t._send_direct('[LIVE] '+_pfmt(e))
                        except Exception: pass
                    pos+=k+1; open(CUR,'w').write(str(pos))
                    seen={a:b for a,b in seen.items() if time.time()-b<900}
        except Exception as ex: print(time.strftime('%H:%M:%S'),'push err',type(ex).__name__,flush=True)
        time.sleep(5)
def rd():
    try: return int(open(OFF).read().strip())
    except Exception: return 0
def main():
    tok,chat=t._load_env()
    if not tok or not chat: print('no env'); return
    off=rd()
    threading.Thread(target=_pusher,daemon=True).start()
    try: t._api('setMyCommands',{'commands':json.dumps([{'command':a,'description':b} for a,b in MC.COMMANDS])})
    except Exception as ex: print('setMyCommands err',type(ex).__name__,flush=True)
    while True:
        try:
            j=t._api('getUpdates',{'offset':off,'timeout':25},timeout=35)
            for u in (j or {}).get('result',[]):
                off=u['update_id']+1; open(OFF,'w').write(str(off))
                m=u.get('message') or u.get('edited_message') or {}
                if str((m.get('chat') or {}).get('id'))!=str(chat): continue
                c=(m.get('text') or '').strip().split()[:1]; c=c[0].lower().split('@')[0].lstrip('/') if c else ''
                if c=='pnl': t._send_direct(pnl())
                elif c=='action': t._send_direct(action())
                elif c=='status': t._send_direct(MC.status_text())
                elif c=='summary':
                    a=(m.get('text') or '').split(); mn=int(a[1]) if len(a)>1 and a[1].isdigit() else 60
                    t._send_direct(MC.summary_text(max(1,min(mn,1440)))[0])
                elif c=='log': t._send_direct(MC.log_text())
                elif c=='ip': t._send_direct(MC.ip_text())
                elif c in ('trustip','untrustip'):
                    a=(m.get('text') or '').split(); t._send_direct(MC.ip_request(c[:-2], a[1] if len(a)>1 else ''))
                elif c=='confirm': t._send_direct(MC.ip_confirm())
                elif c=='cancel': t._send_direct(MC.ip_confirm(cancel=True))
                elif c in ('help','start'): t._send_direct(MC.HELP)
                elif c: t._send_direct('[LIVE] Lệnh không hợp lệ.\n'+MC.HELP)
        except Exception as ex:
            print(time.strftime('%H:%M:%S'),'poll err',type(ex).__name__,flush=True); time.sleep(10)
if __name__=='__main__': main()
