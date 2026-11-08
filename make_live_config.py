"""[BINANCE USDT-M build] Generate config_live.json from the paper config.json (BASE_SL250 (x5) rule set) scaled to the live test capital.
Usage: python make_live_config.py [paper_config.json] [capital_usd]   (default 7.2)
Every DOLLAR-denominated parameter is multiplied by F = capital/5000 (0.00144 at 7.2$). Prices, %, thresholds, scores are unchanged.
Ladder 750/250/250/250 (x live equity/5000), adds ON, x5 isolated, max 2 positions (paper BASE_SL250); sizing compounds from the real account equity."""
import json,sys,copy
src=sys.argv[1] if len(sys.argv)>1 else 'ref/config_paper.json';CAP=float(sys.argv[2]) if len(sys.argv)>2 else 3800.0;BASE=5000.0;F=CAP/BASE
c=json.load(open(src,encoding='utf-8'));o={}
for k in('exit_rule','partial_frac','gate2','btc_recheck','entry_rank','stop_exec','reentry','excluded_coins','dynamic_universe','short_side','exit_hold_mid4h','sizing','short_gate','leverage','exit_rule_history','btc_vol','entry_filter','force_universe','min_equity','branch_display_name','step3','batday','cycle_s','coins','trendmr'):
    if k in c:o[k]=copy.deepcopy(c[k])
B=copy.deepcopy(c['groups']['BASE'])
B['ladder']=list(c['groups']['BASE']['ladder'])    # 750/250/250/250 margin at base 5000; scaled by live equity/5000 in LiveBook.leg()
for k in('eps_15m','be_after_15m','small_15m'):B[k]=round(B[k]*F,8)        # 8 -> 0.01152, 40 -> 0.0576, 60 -> 0.0864
o['groups']={'BASE':B}
o['stops']={'SL250':float(c['stops']['SL250'])}      # Vien 2026-10-21: SL FIXED at 250$ per position (NOT scaled by equity)
o['daily_target_usd']=round(c['daily_target_usd']*F,8)                       # 200 -> 0.288
o['trail_15m']=copy.deepcopy(c['trail_15m']);o['trail_15m']['tiers']=[[round(a*F,8),f] for a,f in c['trail_15m']['tiers']]   # 80 -> 0.1152, 150 -> 0.216
o['lock_milestones']=copy.deepcopy(c['lock_milestones']);o['lock_milestones']['step']=round(c['lock_milestones']['step']*F,8)   # 30 -> 0.0432 (locks 2/3: 0.0288)
o['alerts_scaled']={k:round(v*F,8) for k,v in c['alerts'].items() if isinstance(v,(int,float))}
o['scaling']={'capital_usd':CAP,'base_usd':BASE,'factor':F,'note':'Dollar params (SL, BE, lock, trail, alerts) x factor (fixed at config build; re-run make_live_config.py <equity> after funding). Ladder 750/250/250/250 margin x live equity/5000, adds ON (Vien 2026-10-21). Sizing base = live realized equity (exchange).'}
o['pct_thresholds']=dict(enabled=True,ref_equity=1000.0,since='2026-11-03 (Vien t641u)',note='BE/LOCK/TRAIL/15m-exit $ = pct% x current equity, recomputed when flat at each new entry; fixed while a position is open. pct = config $ value / 1000.',
 pct=dict(be_after_15m=round(B['be_after_15m']/10,6),eps_15m=round(B['eps_15m']/10,6),small_15m=round(B['small_15m']/10,6),lock_step=round(o['lock_milestones']['step']/10,6),trail_tiers=[[round(a/10,6),f] for a,f in o['trail_15m']['tiers']]))
o['live']={
 'exchange':'binance_usdm','address':None,
 'branch':'BASE_SL250','leverage':5,'margin_mode':'isolated','capital_ref':CAP,
 'max_positions':1,'entry_slip_pct':0.3,'stop_limit_slip_pct':3.0,'close_slip_pct':1.0,
 'max_spread_pct':0.15,'max_px_dev_pct':0.5,'stop_backstop_s':25,'stop_update_min_s':8,
 'allowed_abstractions':['binanceUSDM'],'min_equity_to_trade':(c.get('min_equity') or None),
 'seeds':[]}
json.dump(o,open('config_live.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print('config_live.json written; F =',F);print({k:o[k] for k in('stops','daily_target_usd')},o['lock_milestones']['step'],o['trail_15m']['tiers'],{k:B[k] for k in('ladder','eps_15m','be_after_15m','small_15m')})
