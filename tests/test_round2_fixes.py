"""第二轮排查修复的回归测试（2026-10-15）。

- 用户密码不得与其他用户（含机主）重复
- 登录会话在服务端过期，旧的「只签 token」格式不再有效
- portal.json 原子、0600 写入
- 超额停用的 AWG 服务名与 awgctl.sh 实际注册的一致
"""
import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))
import fake_hy2_stats  # noqa: E402
import portal  # noqa: E402

META = {
    'public_ip': '203.0.113.10', 'server_name': 'node.example.com',
    'listen_port': 443, 'auth_password': 'master-pw-0001', 'obfs_password': 'obfs',
    'is_insecure': True, 'hop_port_range': '20000-40000', 'subscription_port': 8443,
}


class PortalServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        (cls.root / 'client_meta.json').write_text(json.dumps(META), encoding='utf-8')
        portal.prepare(cls.root / 'client_meta.json', cls.port)
        cls.data = json.loads((cls.root / 'portal.json').read_text(encoding='utf-8'))
        cls.access = json.loads((cls.root / 'portal-access.json').read_text(encoding='utf-8'))
        cls.prefix = '/' + cls.data['token'] + '/'
        cls.stats = fake_hy2_stats.start(cls.root)
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'portal.py'), 'serve', str(cls.root / 'portal.json')],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', cls.port), .1):
                    break
            except OSError:
                time.sleep(.05)
        else:
            cls.proc.kill()
            raise RuntimeError('门户服务未能启动')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        cls.stats.shutdown()
        cls._tmp.cleanup()

    def request(self, path, payload=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), data=body,
                                     headers=dict(headers or {}, **({'Content-Type': 'application/json'} if body else {})))
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode('utf-8', 'replace')

    def api(self, sub, payload):
        return self.request('/api/v1/' + sub, payload, {'Authorization': 'Bearer ' + self.data['api_key']})


class PasswordUniquenessTest(PortalServer):
    def test_create_rejects_password_of_another_user(self):
        st, _ = self.api('users/create', {'user_id': 'dup_a', 'password': 'shared-pw-1'})
        self.assertEqual(st, 200)
        st, body = self.api('users/create', {'user_id': 'dup_b', 'password': 'shared-pw-1'})
        self.assertEqual(st, 409, body)

    def test_create_rejects_master_password(self):
        st, body = self.api('users/create', {'user_id': 'dup_master', 'password': META['auth_password']})
        self.assertEqual(st, 409, body)

    def test_recreating_same_user_with_own_password_is_allowed(self):
        """控制台幂等重试会带着同一 user_id + 同一密码再建一次，不能被误拒。"""
        payload = {'user_id': 'dup_same', 'password': 'own-pw-2'}
        self.assertEqual(self.api('users/create', payload)[0], 200)
        self.assertEqual(self.api('users/create', payload)[0], 200)

    def test_update_rejects_password_of_another_user(self):
        self.assertEqual(self.api('users/create', {'user_id': 'dup_c', 'password': 'pw-c'})[0], 200)
        self.assertEqual(self.api('users/create', {'user_id': 'dup_d', 'password': 'pw-d'})[0], 200)
        st, body = self.api('users/update', {'user_id': 'dup_d', 'password': 'pw-c'})
        self.assertEqual(st, 409, body)

    def test_generated_passwords_still_work(self):
        st, body = self.api('users/create', {'user_id': 'dup_gen'})
        self.assertEqual(st, 200, body)


class SessionExpiryTest(PortalServer):
    def cookie(self, issued_at=None, legacy=False):
        token, secret = self.data['token'], self.data['session_secret']
        if legacy:
            sig = hmac.new(secret.encode(), f'sess:{token}'.encode(), hashlib.sha256).hexdigest()
            return f'hy2_session={token}.{sig}'
        iat = str(int(time.time() if issued_at is None else issued_at))
        sig = hmac.new(secret.encode(), f'sess:{token}:{iat}'.encode(), hashlib.sha256).hexdigest()
        return f'hy2_session={token}.{iat}.{sig}'

    def logged_in(self, cookie):
        _, body = self.request(self.prefix, headers={'Cookie': cookie})
        return 'teyir-join-card' in body

    def test_fresh_session_is_accepted(self):
        self.assertTrue(self.logged_in(self.cookie()))

    def test_session_older_than_max_age_is_rejected(self):
        self.assertFalse(self.logged_in(self.cookie(time.time() - 31 * 86400)))

    def test_legacy_never_expiring_cookie_is_rejected(self):
        self.assertFalse(self.logged_in(self.cookie(legacy=True)))

    def test_tampered_issue_time_is_rejected(self):
        token, iat, sig = self.cookie(time.time() - 31 * 86400).split('=', 1)[1].split('.')
        forged = f'hy2_session={token}.{int(time.time())}.{sig}'
        self.assertFalse(self.logged_in(forged))

    def test_login_sets_three_part_cookie(self):
        form = 'username=%s&password=%s&remember=1' % (self.access['username'], self.access['password'])
        req = urllib.request.Request('http://127.0.0.1:%d%slogin' % (self.port, self.prefix),
                                     data=form.encode(), method='POST')

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            urllib.request.build_opener(NoRedirect).open(req, timeout=10)
            self.fail('登录应返回 302')
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            cookie = e.headers.get('Set-Cookie', '')
        value = re.match(r'hy2_session=([^;]+)', cookie).group(1)
        self.assertEqual(len(value.split('.')), 3, cookie)
        self.assertIn('Max-Age=2592000', cookie)


class AtomicWriteTest(unittest.TestCase):
    def test_writes_content_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'portal.json'
            target.write_text('old', encoding='utf-8')
            portal.atomic_write_private(target, b'{"new": true}')
            self.assertEqual(target.read_bytes(), b'{"new": true}')
            self.assertFalse(target.with_suffix('.tmp').exists())

    @unittest.skipIf(os.name != 'posix', '权限位只在 POSIX 上有意义')
    def test_file_is_private_even_with_permissive_umask(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'portal.json'
            old = os.umask(0o000)
            try:
                portal.atomic_write_private(target, b'{}')
            finally:
                os.umask(old)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class SuspendUnitsTest(unittest.TestCase):
    def test_awg_unit_matches_awgctl_service_name(self):
        """超额停用列表里的 AWG 服务名必须是 awgctl.sh 真正注册的那个，否则 AWG 停不掉。"""
        name = re.search(r'^AWG_SERVICE_NAME="([^"]+)"', (ROOT / 'awgctl.sh').read_text(encoding='utf-8'), re.M).group(1)
        src = (ROOT / 'portal.py').read_text(encoding='utf-8')
        units = re.search(r"TRAFFIC_SUSPEND_UNITS = \(([^)]*)\)", src).group(1)
        self.assertIn("'%s'" % name, units)


if __name__ == '__main__':
    unittest.main()
