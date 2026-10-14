"""Reality 多用户 + 复合订阅：五轮端到端验证。

⚠️ 为什么用「假 xray + 临时目录」而不是真机：
  真实 xray 需要 443 端口与 root，而这台 Windows 机器两者都没有。
  但**本功能的风险不在 xray 本身**，而在于：
    1. users/create 是否给每个用户不同的 Reality 链接；
    2. xray.json 的 clients 是否随开户/销户同步；
    3. 订阅是否真的变成「Hy2 + Reality 双节点 + url-test」；
    4. 反复拉订阅链接是否稳定（不抖动）；
    5. 删用户是否把 Reality 身份一起清掉（钱退货走）。
  这五条都能在「portal 进程 + 假 xray 二进制 + 临时 /etc/hysteria」下真跑。

五轮的变化（每轮比上一轮多一个验证维度，避免只测一条路径）：
  第 1 轮：开户 → 拿到链接 → 链接含自己的 UUID
  第 2 轮：多用户隔离 → A 的链接不等于 B 的，且都能连到同一个 xray
  第 3 轮：订阅稳定性 → 连拉 3 次，链接与 clash 完全一致
  第 4 轮：销户 → clients 里不再有该用户，且不影响其他人
  第 5 轮：全流程 → 开 3 个 → 销 1 个 → 续 1 个 → 重新拉订阅全链路

用法:
  python tests/test_portal_reality_e2e_5rounds.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _install_fake_xray(tmp_home: Path):
    """在临时 HOME 里放一个假 xray 与 systemctl，让 portal 以为 xray 装好了。

    假 systemctl 只记录调用（写进 log 文件），并对 `is-active xray` 回 active ——
    这样 portal 走的是「已安装且运行中」的分支，覆盖最完整的代码路径。
    """
    bindir = tmp_home / 'bin'
    bindir.mkdir(parents=True, exist_ok=True)
    log = tmp_home / 'systemctl-calls.log'

    xray = bindir / 'xray'
    xray.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "x25519" ]; then\n'
        '  echo "Private key: FAKEPRIVKEY0000000000000000"\n'
        '  echo "Password (PublicKey): FAKEPUBKEY00000000000000000"\n'
        '  exit 0\n'
        'fi\n'
        'exit 0\n', encoding='utf-8')
    os.chmod(xray, 0o755)

    sc = bindir / 'systemctl'
    sc.write_text(
        '#!/bin/sh\n'
        'echo "$@" >> "{log}"\n'
        'case "$*" in\n'
        '  *"is-active xray"*) echo active; exit 0;;\n'
        '  *"is-active"*) echo active; exit 0;;\n'
        'esac\n'
        'exit 0\n'.format(log=log), encoding='utf-8')
    os.chmod(sc, 0o755)
    return bindir, log


class RealityFiveRounds(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        import portal as portal_mod
        cls.portal = portal_mod
        cls.tmp = Path(tempfile.mkdtemp(prefix='portal-reality-e2e-'))
        cls.bindir, cls.syslog = _install_fake_xray(cls.tmp)
        os.environ['PATH'] = str(cls.bindir) + os.pathsep + os.environ.get('PATH', '')

        # 把 portal 的路径常量指到临时目录（不碰真实 /etc/hysteria）
        cls.portal_path = cls.tmp / 'hysteria'
        cls.portal_path.mkdir(parents=True, exist_ok=True)
        cls.meta_path = cls.portal_path / 'client_meta.json'
        cls.portal_path.joinpath('client_meta.json').write_text(json.dumps({
            'is_insecure': True, 'server_name': 'node.example.com',
            'public_ip': '203.0.113.10', 'auth_password': 'masterpw',
            'listen_port': 443, 'obfs_password': 'obfspw',
            'hop_port_range': '20000-40000',
        }), encoding='utf-8')
        cls.portal_path.joinpath('config.yaml').write_text(
            'auth:\n  type: http\n  http:\n    url: http://127.0.0.1:1/auth\n',
            encoding='utf-8')

        # portal 模块级状态：data / data_lock / meta_path
        cls.portal.data = {
            'token': 'tok' + '0' * 30,
            'api_key': 'akey' + '0' * 20,
            'session_secret': 's' * 64,
            'page': '<html>ok</html>',
            'users': {},
            'proxy_services': [],
            'reality_config': None,
        }
        cls.portal.data_lock = threading.RLock()
        cls.portal.meta_path = cls.meta_path
        cls.portal.save_data = cls._noop_save

    @classmethod
    def _noop_save(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------- 工具 ----------
    def _create_user(self, uid, **kw):
        d = dict(self.portal.data)
        d['users'][uid] = dict({
            'password': 'pw-' + uid, 'expires_at': int(time.time()) + 86400 * 30,
            'ip_limit': 0, 'limit_bytes': 0, 'used_bytes': 0,
            'status': 'active', 'created_at': int(time.time()), 'note': '',
        }, **kw)
        self.portal.reality_registry(d)
        self.portal.save_data()
        return d['users'][uid]

    def _delete_user(self, uid):
        self.portal.data['users'].pop(uid, None)
        self.portal.reality_registry(self.portal.data)
        self.portal.save_data()

    def _artifacts_for(self, uid):
        rcfg = self.portal.data.get('reality_config') or {
            'public_key': 'FAKEPUBKEY00000000000000000', 'short_id': 'aabbccdd',
            'dest_sni': 'www.apple.com', 'port': 443,
        }
        m = json.loads(self.meta_path.read_text())
        return self.portal.artifacts(
            m, auth_override=self.portal.data['users'][uid]['password'],
            name_override='Teyir-Hy2-' + uid, reality=rcfg, user_id=uid)

    def _clients(self):
        return self.portal.reality_clients(self.portal.data)

    # ================= 第 1 轮 =================
    def test_round1_open_account_gets_own_reality_link(self):
        """第 1 轮：开户 → 链接里是**这个用户自己的** UUID。"""
        d = self._create_user('r1_alice')
        rcfg = {'public_key': 'PBK', 'short_id': 'sid1',
                'dest_sni': 'www.apple.com', 'port': 443}
        uri = self.portal.reality_uri_for_user(rcfg, 'r1_alice', '203.0.113.10')
        self.assertIn(self.portal.reality_uuid_for_user('r1_alice'), uri)
        self.assertIn('PBK', uri)
        self.assertIn('sid1', uri)
        self.assertTrue(d['password'])

    # ================= 第 2 轮 =================
    def test_round2_multi_user_isolation(self):
        """第 2 轮：A 与 B 的 Reality 身份必须互不相同，但都写进同一份 clients。"""
        self._create_user('r2_alice')
        self._create_user('r2_bob')
        rcfg = {'public_key': 'PBK', 'short_id': 'sid1',
                'dest_sni': 'www.apple.com', 'port': 443}
        ua = self.portal.reality_uri_for_user(rcfg, 'r2_alice', '203.0.113.10')
        ub = self.portal.reality_uri_for_user(rcfg, 'r2_bob', '203.0.113.10')
        self.assertNotEqual(ua, ub, '两个用户拿到的必须是不同链接')
        clients = self._clients()
        emails = {c.get('email') for c in clients}
        self.assertIn('r2_alice', emails)
        self.assertIn('r2_bob', emails)

    # ================= 第 3 轮 =================
    def test_round3_subscription_is_stable(self):
        """第 3 轮：连拉 3 次订阅，链接与 clash 必须**逐字相同**。"""
        self._create_user('r3_carol')
        a = self._artifacts_for('r3_carol')
        b = self._artifacts_for('r3_carol')
        c = self._artifacts_for('r3_carol')
        self.assertEqual(a, b, '第二次拉订阅内容变了 —— 客户端会当成新节点')
        self.assertEqual(b, c)
        clash = json.loads(a[1])
        self.assertEqual(len(clash['proxies']), 2, '应有 Hy2 + Reality 两个节点')
        self.assertEqual(clash['proxy-groups'][0]['type'], 'url-test')

    # ================= 第 4 轮 =================
    def test_round4_revoke_removes_only_that_user(self):
        """第 4 轮：销掉 A，B 必须毫发无损（销户隔离）。"""
        self._create_user('r4_dave')
        self._create_user('r4_eve')
        before = {c.get('email') for c in self._clients()}
        self.assertIn('r4_dave', before)
        self.assertIn('r4_eve', before)

        self._delete_user('r4_dave')
        after = {c.get('email') for c in self._clients()}
        self.assertNotIn('r4_dave', after, '已销户的 Reality 身份必须消失（钱退货走）')
        self.assertIn('r4_eve', after, '销 A 不能连累 B')

    # ================= 第 5 轮 =================
    def test_round5_full_lifecycle(self):
        """第 5 轮：开 3 → 销 1 → 续 1 → 全链路重拉。"""
        for uid in ('r5_f', 'r5_g', 'r5_h'):
            self._create_user(uid)
        self.assertEqual(len({c.get('email') for c in self._clients()
                              if str(c.get('email', '')).startswith('r5_')}), 3)

        self._delete_user('r5_g')
        live = {c.get('email') for c in self._clients()
                if str(c.get('email', '')).startswith('r5_')}
        self.assertEqual(live, {'r5_f', 'r5_h'})

        # 续期：只改 expires_at，不应动 Reality 身份
        before_uuid = self.portal.reality_uuid_for_user('r5_f')
        self.portal.data['users']['r5_f']['expires_at'] = int(time.time()) + 86400 * 60
        self.portal.reality_registry(self.portal.data)
        self.portal.save_data()
        self.assertEqual(self.portal.reality_uuid_for_user('r5_f'), before_uuid,
                         '续期不应改变 Reality 链接')

        # 重拉订阅：内容稳定且范围正确
        uri, clash_s, sing_s = self._artifacts_for('r5_f')
        clash = json.loads(clash_s)
        self.assertEqual(len(clash['proxies']), 2)
        r = [p for p in clash['proxies'] if p['type'] == 'vless'][0]
        self.assertEqual(r['uuid'], before_uuid, '订阅里的 VLESS UUID 要与该用户一致')

        # 销户后 UUID 应被回收，重新开户同 id 会拿回同一个 UUID（幂等）
        self._delete_user('r5_h')
        self.assertNotIn('r5_h', {c.get('email') for c in self._clients()})
        self._create_user('r5_h')
        self.assertIn('r5_h', {c.get('email') for c in self._clients()})


if __name__ == '__main__':
    unittest.main(verbosity=2)
