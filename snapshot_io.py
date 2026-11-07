"""Atomic, bounded-retry writes for NON-AUTHORITATIVE reporting snapshots only.
Never use best-effort snapshots for order journals, positions, pending closes or config.
"""
import json
import os
from pathlib import Path
import tempfile
import time


def write_snapshot(path, value, attempts=6, delay=.05, log=print):
    """Keep old valid snapshot if Windows denies replacement. Never kills trading.
    Unique same-directory temp; closed before replace; at most .75s retry delay.
    Returns False on exhausted filesystem errors and emits a diagnostic.
    """
    target=Path(path)
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=target.parent,
                                         prefix=target.name+'.',suffix='.tmp',delete=False) as f:
            tmp=f.name
            json.dump(value,f,ensure_ascii=False,indent=1,default=str)
            f.flush()
            os.fsync(f.fileno())
        for i in range(attempts):
            try:
                os.replace(tmp,target)
                return True
            except OSError as ex:
                if not (isinstance(ex,PermissionError) or getattr(ex,'winerror',None) in (5,32,33)) or i+1 >= attempts:
                    raise
                time.sleep(delay*(i+1))
    except OSError as ex:
        log(f'SNAPSHOT_WRITE_FAILED file={target.name} type={type(ex).__name__} winerror={getattr(ex,"winerror",None)}; old snapshot kept, loop continues')
        return False
    finally:
        if tmp:
            try: os.unlink(tmp)
            except OSError: pass
