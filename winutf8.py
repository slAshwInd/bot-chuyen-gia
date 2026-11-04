"""Windows helper (added for the Windows bundle): force UTF-8 text I/O and LF newlines so behaviour/files match Linux.
Imported first by bot.py and status.py. No trading logic."""
import builtins,sys
_o=builtins.open
def _uopen(f,mode='r',*a,**k):
    if 'b' not in mode:
        if 'encoding' not in k and len(a)<2:k['encoding']='utf-8'
        if ('w' in mode or 'a' in mode or 'x' in mode) and 'newline' not in k and len(a)<3:k['newline']='\n'
    return _o(f,mode,*a,**k)
builtins.open=_uopen
for _s in (sys.stdout,sys.stderr):
    try:_s.reconfigure(encoding='utf-8',errors='replace')
    except Exception:pass
