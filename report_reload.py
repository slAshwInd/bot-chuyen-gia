"""Reload reporting.py only, per request. No service control or trading imports."""
import hashlib
import threading
import types
from pathlib import Path

class ReportLoader:
    def __init__(self,path):
        self.path=Path(path); self.lock=threading.RLock()
        self.good=None; self.digest=None; self.rejected=None
        self.last_error=None

    def summary(self,*args,**kwargs):
        with self.lock:
            candidate=None; digest=None
            try:
                data=self.path.read_bytes()
                digest=hashlib.sha256(data).hexdigest()
                if digest == self.digest:
                    self.last_error=None; self.rejected=None
                if digest != self.digest and digest != self.rejected:
                    candidate=types.ModuleType('_baseaster_reporting_live')
                    candidate.__file__=str(self.path)
                    exec(compile(data,str(self.path),'exec'),candidate.__dict__)
                    if not callable(getattr(candidate,'summary',None)):
                        raise TypeError('missing summary interface')
                    # Promote only after the new report renders successfully.
                    result=candidate.summary(*args,**kwargs)
                    if not isinstance(result,tuple) or len(result)!=2 or not isinstance(result[0],str) or not isinstance(result[1],dict):
                        raise TypeError('invalid summary result')
                    self.good=candidate; self.digest=digest; self.rejected=None; self.last_error=None
                    print("REPORT_RELOAD_OK sha256="+digest,flush=True)
                    return result
            except Exception as ex:
                self.rejected=digest
                self.last_error=type(ex).__name__
                # No exception text/data: may contain private runtime observations.
                print('REPORT_RELOAD_FAILED type='+self.last_error,flush=True)
            if self.good is None:
                return ('[LIVE] Báo cáo tạm chưa khả dụng; xem /status. Không ảnh hưởng Trade.',{})
            result=self.good.summary(*args,**kwargs)
            if self.last_error:
                return (result[0]+'\nCảnh báo: bản cập nhật báo cáo lỗi; đang dùng bản tốt trước.',result[1])
            return result
