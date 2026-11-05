"""Rebuild config_live.json from the REAL account equity before each bot start (called by run_live.ps1). Read-only exchange access (signed GET account, USDT wallet; nothing is sent).
CAP = real equity (Vien 2026-10-21: no floor, no min-equity guard). Orders below the exchange per-symbol minimum (MIN_NOTIONAL, usually 5$) are skipped by the bot.
Rules unchanged (make_live_config.py): SL fixed 250$, every other dollar param x CAP/5000, ladder x live equity/5000 at runtime.
On any error the existing config_live.json is kept."""
import os,sys,json,subprocess,shutil
B=os.path.dirname(os.path.abspath(__file__))+os.sep;os.environ['BNB_DRY_RUN']='1';sys.path.insert(0,B)
def main():
    import bnx
    cfg=B+'config_live.json';old=json.load(open(cfg,encoding='utf-8'))
    hx=bnx.BNX(dry_run=True)
    if not hx.has_key:print('rescale: no API key yet -> kept old config');return 1
    eq=float(hx.user_state()['equity'])
    if not eq>0:print(f'rescale: equity {eq} <= 0 -> kept old config');return 1
    cap=round(eq,2);prev=float(old['scaling']['capital_usd'])
    import safe
    sp=safe.load_profile(B,json.load(open(B+'ref/config_paper.json',encoding='utf-8')))
    same_safe=(bool(sp)==bool(old.get('safe'))) and (not sp or abs(float(old['safe'].get('risk_pct',-1))-float(sp['risk_pct']))<1e-9)      # bật/tắt/đổi risk_pct SAFE -> phải tạo lại config
    if abs(cap/prev-1)<0.001 and same_safe:print(f'rescale: equity {eq:.4f} -> CAP {cap} (unchanged)');return 0
    shutil.copy(cfg,cfg+'.bak')
    r=subprocess.run([sys.executable,B+'make_live_config.py',B+'ref/config_paper.json',str(cap)],cwd=B,capture_output=True,text=True)
    new=json.load(open(cfg,encoding='utf-8'))
    sl0=float(json.load(open(B+'ref/config_paper.json',encoding='utf-8'))['stops']['SL250'])*float((new.get('safe') or {}).get('k',1.0))      # SAFE: SL = SL paper x k
    ok=r.returncode==0 and abs(new['stops']['SL250']-sl0)<1e-4 and new['live'].get('exchange')=='binance_usdm' and abs(new['scaling']['capital_usd']-cap)<1e-6
    if not ok:shutil.copy(cfg+'.bak',cfg);print('rescale FAILED -> kept old config',r.stderr[-300:]);return 1
    print(f"rescale: equity {eq:.4f} -> CAP {cap} (was {prev}); SL {new['stops']['SL250']}, factor {new['scaling']['factor']}");return 0
if __name__=='__main__':
    try:sys.exit(main())
    except Exception as ex:print('rescale error -> kept old config:',type(ex).__name__,str(ex)[:200]);sys.exit(1)
