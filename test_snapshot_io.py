import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import snapshot_io as S
import reporting as R

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'state_live.json'
        self.path.write_text('{"old":true}',encoding='utf-8')
    def test_success(self):
        self.assertTrue(S.write_snapshot(self.path,{'new':1}))
        self.assertEqual(json.loads(self.path.read_text()),{'new':1})
        self.assertEqual(len(list(self.root.glob('*.tmp'))),0)
    def test_transient_lock_retried_with_closed_temp(self):
        real=S.os.replace; calls=[]
        def replace(src,dst):
            # A re-openable temp is already closed before replacement.
            with open(src,'a'): pass
            calls.append(src)
            if len(calls)<3: raise PermissionError('simulated lock')
            return real(src,dst)
        with patch.object(S.os,'replace',side_effect=replace),patch.object(S.time,'sleep'):
            self.assertTrue(S.write_snapshot(self.path,{'new':2}))
        self.assertEqual(len(calls),3)
    def test_permanent_lock_keeps_old_state_without_raising(self):
        logs=[]
        with patch.object(S.os,'replace',side_effect=PermissionError('simulated')),patch.object(S.time,'sleep'):
            self.assertFalse(S.write_snapshot(self.path,{'new':3},log=logs.append))
        self.assertEqual(json.loads(self.path.read_text()),{'old':True})
        self.assertIn('SNAPSHOT_WRITE_FAILED',logs[0])
        self.assertEqual(len(list(self.root.glob('*.tmp'))),0)
    def test_disk_error_does_not_destroy_old_snapshot(self):
        with patch.object(S.json,'dump',side_effect=OSError('disk full')):
            self.assertFalse(S.write_snapshot(self.path,{'new':4},log=lambda m:None))
        self.assertEqual(json.loads(self.path.read_text()),{'old':True})
    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_real_windows_reader_lock_does_not_kill_writer(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        create=kernel.CreateFileW
        create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        create.restype=wintypes.HANDLE
        close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
        handle=create(str(self.path),0x80000000,1,None,3,0,None)
        self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
        try:
            self.assertFalse(S.write_snapshot(self.path,{'new':5},attempts=2,delay=.01,log=lambda m:None))
            self.assertEqual(json.loads(self.path.read_text()),{'old':True})
        finally:
            close(handle)
        self.assertTrue(S.write_snapshot(self.path,{'new':6}))
    def test_decision_status_is_not_fill_prediction(self):
        self.path.write_text(json.dumps({'live':{'exchange_positions':{}},'watch':{'ASTER':{'price':.67925,'lo':.67567,'up':.71416}},'btc':{'allow':True},'updated':'2026-11-07 14:29:35'}),encoding='utf-8')
        text=R.decision_text(self.root)
        self.assertIn('Mua: chờ giá ≤',text)
        self.assertIn('Chạm dải không đồng nghĩa sẽ vào lệnh',text)
        self.assertIn('Chưa xác minh đủ',text)

if __name__=='__main__':unittest.main(verbosity=2)
