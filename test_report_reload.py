import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from report_reload import ReportLoader

class ReloadTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.path=Path(t.name)/'reporting.py';self.loader=ReportLoader(self.path)
    def put(self,value):
        self.path.write_text("def summary(*args,**kw): return ("+repr(value)+", {})",encoding='utf-8')
    def test_reload_same_process_even_same_size(self):
        self.put('one');self.assertEqual(self.loader.summary()[0],'one')
        self.put('two');self.assertEqual(self.loader.summary()[0],'two')
    def test_invalid_source_retains_last_good(self):
        self.put('good');self.loader.summary()
        self.path.write_text('invalid python !!',encoding='utf-8')
        self.assertTrue(self.loader.summary()[0].startswith('good'))
        self.assertEqual(self.loader.last_error,'SyntaxError')
        self.assertTrue(self.loader.summary()[0].startswith('good'))
        self.put('next');self.assertEqual(self.loader.summary()[0],'next')
        self.assertIsNone(self.loader.last_error)
    def test_runtime_failure_not_promoted(self):
        self.put('good');self.loader.summary()
        self.path.write_text('def summary(*a,**k): raise ValueError("private")',encoding='utf-8')
        result=self.loader.summary()[0]
        self.assertTrue(result.startswith('good'));self.assertNotIn('private',result)
    def test_missing_interface_and_bad_result(self):
        self.put('good');self.loader.summary()
        for source in ['x=1','def summary(*a,**k): return None']:
            self.path.write_text(source,encoding='utf-8')
            self.assertTrue(self.loader.summary()[0].startswith('good'))
    def test_first_load_missing_or_bad_is_unavailable(self):
        self.assertIn('chưa khả dụng',self.loader.summary()[0])
        self.path.write_text('bad syntax !!',encoding='utf-8')
        self.assertIn('chưa khả dụng',self.loader.summary()[0])
    def test_restoring_good_clears_warning(self):
        self.put('good');self.loader.summary()
        self.path.write_text('bad !!',encoding='utf-8');self.loader.summary()
        self.put('good');self.assertEqual(self.loader.summary()[0],'good')
    def test_parallel_calls_and_forwarded_arguments(self):
        self.path.write_text('def summary(*a,**k): return (str(a[0])+str(k["detail"]), {})',encoding='utf-8')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda n:self.loader.summary(n,detail=True)[0],range(20)))
        self.assertEqual(results,[str(n)+'True' for n in range(20)])
    def test_loader_never_controls_services_or_trading(self):
        import ast,report_reload
        tree=ast.parse(Path(report_reload.__file__).read_text(encoding='utf-8'))
        imports={a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names}
        imports|={n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)}
        self.assertFalse(imports&{'subprocess','ctypes','strat','bnb_bot','livecore','tg_notify'})

if __name__=='__main__': unittest.main()
