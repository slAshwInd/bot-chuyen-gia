"""Read-only check of the Binance setup (sends NO order, changes NO setting). Never prints the key.
Checks: key files, API permissions (futures on, withdrawals off, IP restriction), USDT futures balance, one-way mode, single-asset mode, leverage brackets, current symbol config, open positions."""
import os,sys,json
os.environ['BNB_DRY_RUN']='1'
B=os.path.dirname(os.path.abspath(__file__))+os.sep;sys.path.insert(0,B)
import winutf8,bnx,hl
ok=True
def chk(n,c,i=''):
    global ok;ok=ok and bool(c);print(('[OK]  ' if c else '[FAIL]'),n,i)
try:bnx.load_key();chk('API key + secret found (not shown)',True)
except bnx.HLXError as ex:chk('API key found',False,str(ex));sys.exit(1)
hx=bnx.BNX(dry_run=True)
try:
    r=hx.base;hx.base='https://api.binance.com';ar=hx.signed('GET','/sapi/v1/account/apiRestrictions');hx.base=r
    chk('Futures permission ON',ar.get('enableFutures'));chk('Withdrawals OFF',not ar.get('enableWithdrawals'));print('       IP restricted:',ar.get('ipRestrict'),'| spot trading:',ar.get('enableSpotAndMarginTrading'))
except bnx.HLXError as ex:hx.base=bnx.BASE;print('[WARN] cannot read API restrictions:',str(ex)[:150])
st=hx.user_state()
chk('one-way position mode (not hedge) + single-asset margin',st['abstraction']=='binanceUSDM',st['abstraction'])
print(f"       USDT wallet {st['base']:.4f} | equity {st['equity']:.4f} | available {st.get('available',0):.4f}");chk('USDT balance > 0',st['base']>0)
print('       open positions:',{c:x['szi'] for c,x in st['positions'].items()} or 'none');print('       open orders:',len(hx.open_orders()))
ML=hx.max_leverages();chk('leverageBracket readable',len(ML)>100,f'{len(ML)} symbols; >=5x: {sum(v>=5 for v in ML.values())}')
for c in('SOL','LINK','DOGE'):
    ad=hx.asset_data(c);print(f'       {c}: max lev {ML.get(c)} | now {ad["leverage"]} | min notional {hx.min_notional(c)}$')
print('NOTHING WAS SENT. Result:','READY' if ok else 'NOT READY');sys.exit(0 if ok else 1)
