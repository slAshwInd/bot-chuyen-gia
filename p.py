s=open('bnb_bot.py',encoding='utf-8').read()
a="_m1=M1.get(c);_hi=px;_lo=px"
b="_m1=(M1.get(c) if c in book.pos else (get1m(c) if (c in UL or c in US) else None));_hi=px;_lo=px"
a2="_hi=max(px,float(_m1['high'].iloc[-1]));_lo=min(px,float(_m1['low'].iloc[-1]))"
b2="_hi=max(px,float(_m1['high'].iloc[-2:].max()));_lo=min(px,float(_m1['low'].iloc[-2:].min()))"
assert s.count(a)==1 and s.count(a2)==1
s=s.replace(a,b).replace(a2,b2);open('bnb_bot.py','w',encoding='utf-8').write(s)
