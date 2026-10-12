import ast
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch
import unittest
ROOT=Path(__file__).resolve().parents[1]

class ImmutableUpgradeTests(unittest.TestCase):
    def test_all_fallback_sources_use_same_commit(self):
        tree=ast.parse((ROOT/'portal.py').read_text(encoding='utf-8'))
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_fetch_repo_file')
        import re
        env={'urllib':urllib,'AWG_REPO':'yys9253462-gif/hysteria2-installer','re':re}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'fetch-ref','exec'),env)
        urls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'MARK'+b' '*4096
        def open_(req,**kwargs):
            urls.append(req.full_url)
            if len(urls)<3:raise OSError('first source unavailable')
            return Response()
        ref='a'*40
        with patch.object(urllib.request,'urlopen',open_):
            result=env['_fetch_repo_file']('portal.py',b'MARK',1024,ref=ref)
        self.assertIsNotNone(result)
        self.assertEqual(len(urls),3)
        self.assertTrue(all(ref in u and '/main/' not in u and '@main/' not in u for u in urls))
        with self.assertRaises(ValueError):env['_fetch_repo_file']('portal.py',ref='../main')

    def test_upgrade_lock_rejects_concurrent_and_releases_on_failure(self):
        tree=ast.parse((ROOT/'portal.py').read_text(encoding='utf-8'))
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_h_post_do_upgrade')
        lock=threading.Lock();env={'portal_upgrade_lock':lock}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'upgrade-lock','exec'),env)
        class Handler:
            def is_authenticated(self):return True
            def reply_json(self,code,body):return code,body
            def _h_post_do_upgrade_impl(self,prefix):return 200,{'ok':True}
        lock.acquire()
        self.assertEqual(env['_h_post_do_upgrade'](Handler(),'')[0],409)
        lock.release()
        self.assertEqual(env['_h_post_do_upgrade'](Handler(),'')[0],200)
        self.assertFalse(lock.locked())
        class Broken(Handler):
            def _h_post_do_upgrade_impl(self,prefix):raise OSError('write failed')
        with self.assertRaises(OSError):env['_h_post_do_upgrade'](Broken(),'')
        self.assertFalse(lock.locked())
