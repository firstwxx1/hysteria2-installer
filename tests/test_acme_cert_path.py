"""ACME 缓存路径回归：不再把正常 ACME 节点误报为证书缺失。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal


class AcmeCertPathTests(unittest.TestCase):
    def resolve(self, root, config):
        path = root / 'config.yaml'
        path.write_text(config, encoding='utf-8')
        with patch.object(portal, '_parse_cert_subject', return_value=('', [], False)):
            return portal._resolve_live_cert(root, str(path))[0]

    def cert(self, storage, domain='node.example.com', ca='acme-v02.api.letsencrypt.org-directory'):
        path = storage / 'certificates' / ca / domain / (domain + '.crt')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
        return path

    def test_default_storage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = self.cert(root / 'acme')
            self.assertEqual(self.resolve(root, 'acme:\n  domains:\n    - "node.example.com"\n  type: http\n'), expected)

    def test_relative_storage_and_comments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = self.cert(root / 'custom cache')
            self.assertEqual(self.resolve(root, "acme:\n  domains: [node.example.com]\n  dir: 'custom cache' # comment\n"), expected)

    def test_absolute_storage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = self.cert(root / 'absolute')
            self.assertEqual(self.resolve(root, 'acme:\n  domains: ["node.example.com"]\n  dir: "' + (root / 'absolute').as_posix() + '"\n'), expected)

    def test_missing_acme_does_not_use_stale_self_signed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'cert').mkdir()
            (root / 'cert/server.crt').write_text('stale')
            actual = self.resolve(root, 'acme:\n  domains:\n    - node.example.com\n')
            self.assertFalse(actual.exists())
            self.assertIn('acme', actual.parts)

    def test_other_domain_not_selected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.cert(root / 'acme', domain='other.example.com')
            self.assertFalse(self.resolve(root, 'acme:\n  domains: [node.example.com]\n').exists())

    def test_renewal_reuses_live_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = self.cert(root / 'acme')
            config = 'acme:\n  domains: [node.example.com]\n'
            self.assertEqual(self.resolve(root, config), expected)
            expected.write_text('renewed certificate')
            self.assertEqual(self.resolve(root, config), expected)

    def test_staging_not_selected_for_production(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.cert(root / 'acme', ca='acme-staging-v02.api.letsencrypt.org-directory')
            self.assertFalse(self.resolve(root, 'acme:\n  domains: [node.example.com]\n').exists())

    def test_zerossl(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = self.cert(root / 'acme', ca='acme.zerossl.com-v2-dv90')
            self.assertEqual(self.resolve(root, 'acme:\n  domains: [node.example.com]\n  ca: zerossl\n'), expected)

    def test_custom_ca(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = self.cert(root / 'acme', ca='ca.example.com-directory')
            self.assertEqual(self.resolve(root, 'acme:\n  domains: [node.example.com]\n  ca: https://ca.example.com/directory\n'), expected)

    def test_environment_storage_and_config_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = self.cert(root / 'env-cache')
            with patch.dict(portal.os.environ, {'HYSTERIA_ACME_DIR': str(root / 'env-cache')}):
                self.assertEqual(self.resolve(root, 'acme:\n  domains: [node.example.com]\n'), expected)
                explicit = self.cert(root / 'explicit')
                self.assertEqual(self.resolve(root, 'acme:\n  domains: [node.example.com]\n  dir: explicit\n'), explicit)

    def test_tls_path_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected = root / 'custom.crt'
            expected.write_text('fixture')
            self.assertEqual(self.resolve(root, 'tls:\n  cert: custom.crt\n  key: custom.key\n'), expected)

    def test_self_signed_fallback_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(self.resolve(root, 'listen: :443\n'), root / 'cert/server.crt')


if __name__ == '__main__':
    unittest.main()
