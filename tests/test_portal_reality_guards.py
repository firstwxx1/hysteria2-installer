import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal


class TestRealityClientSyncGuards(unittest.TestCase):
    """_sync_reality_clients 的降级守卫。

    真实场景：不是每台机器都装了 Reality。
    开户/销户时若无脑去写 /etc/hysteria/xray.json，会在
    「没装 xray 的节点」上凭空创建配置、或抛权限错误把 HTTP handler 打死。
    """

    def test_source_has_existence_guard(self):
        """源码级钉死：必须先判断 xray.json 存在再动手。"""
        import inspect
        src = inspect.getsource(portal.serve)
        i = src.find('def _sync_reality_clients')
        self.assertGreater(i, 0, '找不到 _sync_reality_clients')
        body = src[i:i + 1200]
        self.assertIn("Path('/etc/hysteria/xray.json')", body)
        self.assertIn('if not xj.exists()', body,
                      '必须在写盘前判断 xray.json 存在，否则会给未装 Reality 的节点'
                      '凭空创建配置')
        self.assertIn('return False', body, '不该装时必须静默跳过而不是抛错')

    def test_source_handles_unreadable_json(self):
        import inspect
        src = inspect.getsource(portal.serve)
        i = src.find('def _sync_reality_clients')
        body = src[i:src.find('def _reality_restart_allowed', i)]
        self.assertIn("json.loads(xj.read_text(encoding='utf-8'))", body)
        # 解析失败也要 return False 而不是崩
        self.assertIn('except Exception', body)


class TestEncodingHardening(unittest.TestCase):
    """所有 write_text 必须显式 encoding='utf-8'。

    实测（2026-10-03 Windows）：Path.write_text 不给 encoding 时跟随
    locale，Windows 是 GBK —— 页面里含 `₂` 之类字符时直接
    UnicodeEncodeError，把 HTTP handler 打死，客户端只看到
    "Remote end closed connection without response"，完全看不出是编码问题。
    Linux 上是 UTF-8 所以不会踩 —— 也就是说这个 bug 在开发机（Linux）
    上永远发现不了。
    """

    def test_no_write_text_without_encoding(self):
        src = (Path(__file__).resolve().parents[1] / 'portal.py').read_text(encoding='utf-8')
        import ast
        tree = ast.parse(src)
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == 'write_text'):
                continue
            kw = {k.arg for k in node.keywords}
            if 'encoding' not in kw:
                bad.append(node.lineno)
        self.assertEqual(bad, [],
                         '这些 write_text 没带 encoding：行 %s。'
                         'Windows 上会因 GBK 打死 HTTP handler' % bad)

    def test_save_data_uses_utf8(self):
        import inspect
        src = inspect.getsource(portal.serve)
        i = src.find('def save_data')
        body = src[i:i + 900]
        self.assertIn("encoding='utf-8'", body,
                      'save_data 必须显式 UTF-8 —— 它写的是整个 portal.json')


if __name__ == '__main__':
    unittest.main(verbosity=2)
