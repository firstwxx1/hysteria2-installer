"""Reality 多用户 + 复合订阅的单元测试。

背景（2026-10-03）：
  旧实现的 Reality 是「整台机器一个 UUID」——
    * users/create 返回的 reality_uri 所有人都一样（rcfg['uri']）；
    * xray.json 的 clients 写死 1 个元素；
    * _generate_and_apply_reality 每次都 uuid.uuid4() 重新生成密钥与 UUID，
      刷新一次所有客户端链接全挂。
  这套用例把「一个用户一个 UUID + 链接稳定 + 销户即删」钉死。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal

META = {
    'is_insecure': True,
    'server_name': 'node.example.com',
    'public_ip': '203.0.113.10',
    'auth_password': 'masterpw',
    'listen_port': 443,
    'obfs_password': 'obfspw',
    'hop_port_range': '20000-40000',
}
RCFG = {
    'public_key': 'PBK_TEST',
    'short_id': 'abcd1234',
    'dest_sni': 'www.apple.com',
    'port': 443,
}


class TestRealityUuidDerivation(unittest.TestCase):
    def test_same_user_gets_same_uuid(self):
        """订阅要能反复拉 —— 同一个用户的 Reality 链接不能变。"""
        a = portal.reality_uuid_for_user('hy2_alice')
        b = portal.reality_uuid_for_user('hy2_alice')
        self.assertEqual(a, b, '同一 user_id 必须派生出同一个 UUID，否则客户端会被当成新节点')

    def test_different_users_get_different_uuids(self):
        a = portal.reality_uuid_for_user('hy2_alice')
        b = portal.reality_uuid_for_user('hy2_bob')
        self.assertNotEqual(a, b, '多用户共用一个 UUID 意味着任何一个人销户会连累所有人')

    def test_uuid_is_valid_form(self):
        import uuid as _uuid
        got = portal.reality_uuid_for_user('hy2_alice')
        # 能被 uuid 解析 ⇒ 格式合法（xray 会校验）
        self.assertEqual(str(_uuid.UUID(got)), got)

    def test_namespace_is_stable(self):
        """命名空间写死 —— 换了会让所有已发出去的链接失效。"""
        import inspect
        src = inspect.getsource(portal.reality_uuid_for_user)
        self.assertIn('uuid.NAMESPACE_URL', src,
                      '命名空间必须是固定的 NAMESPACE_URL，不能改成别的')


class TestRealityRegistry(unittest.TestCase):
    def test_backfills_existing_users(self):
        """升级后老用户也该有 UUID，不需要重新开户。"""
        data = {'users': {'alice': {}, 'bob': {}}}
        reg = portal.reality_registry(data)
        self.assertEqual(len(reg), 2)
        self.assertIn('alice', reg)
        self.assertIn('bob', reg)

    def test_drops_deleted_users(self):
        """销户后 UUID 必须从注册表消失，否则 xray clients 无限增长。"""
        data = {'users': {'alice': {}, 'bob': {}}}
        portal.reality_registry(data)
        del data['users']['bob']
        reg = portal.reality_registry(data)
        self.assertNotIn('bob', reg, '已删用户的 UUID 不能留在注册表里')
        self.assertIn('alice', reg)

    def test_registry_persists_into_data(self):
        data = {'users': {'alice': {}}}
        portal.reality_registry(data)
        self.assertIn('reality_users', data, '注册表要写回 data，否则重启后丢失')


class TestRealityClientsNeverEmpty(unittest.TestCase):
    def test_empty_user_table_still_yields_placeholder(self):
        """clients 为空时 xray 拒绝所有连接，但 systemctl 仍显示 active ——
        极难排查。宁可给一个占位 client。"""
        clients = portal.reality_clients({'users': {}})
        self.assertGreaterEqual(len(clients), 1)
        self.assertIn('id', clients[0])
        self.assertIn('flow', clients[0])

    def test_one_client_per_user(self):
        data = {'users': {'a': {}, 'b': {}, 'c': {}}}
        clients = portal.reality_clients(data)
        self.assertEqual(len(clients), 3)
        emails = sorted(c.get('email', '') for c in clients)
        self.assertEqual(emails, ['a', 'b', 'c'], '每个 user_id 一个 client，且 email 便于排查')


class TestRealityUri(unittest.TestCase):
    def test_each_user_gets_own_link(self):
        a = portal.reality_uri_for_user(RCFG, 'alice', '203.0.113.10', 'A')
        b = portal.reality_uri_for_user(RCFG, 'bob', '203.0.113.10', 'B')
        self.assertNotEqual(a, b)
        self.assertIn(portal.reality_uuid_for_user('alice'), a)
        self.assertIn(portal.reality_uuid_for_user('bob'), b)

    def test_link_contains_required_params(self):
        u = portal.reality_uri_for_user(RCFG, 'alice', '203.0.113.10', 'A')
        for need in ('security=reality', 'pbk=PBK_TEST', 'sid=abcd1234',
                     'sni=www.apple.com', 'flow=xtls-rprx-vision', 'type=tcp'):
            self.assertIn(need, u, f'Reality 链接缺 {need}')

    def test_incomplete_config_returns_empty(self):
        """配置不全时返回空串而不是半截链接 —— 半截链接会让客户端导入失败。"""
        self.assertEqual(portal.reality_uri_for_user({}, 'a', '1.2.3.4'), '')
        self.assertEqual(portal.reality_uri_for_user({'public_key': 'X'}, 'a', '1.2.3.4'), '')


class TestCompositeSubscription(unittest.TestCase):
    def test_clash_has_both_protocols_and_url_test(self):
        _, clash_s, sing_s = portal.artifacts(
            META, auth_override='pw', name_override='Teyir-Hy2-alice',
            reality=RCFG, user_id='alice')
        clash = json.loads(clash_s)
        types = sorted(p['type'] for p in clash['proxies'])
        self.assertEqual(types, ['hysteria2', 'vless'],
                         '一条订阅里应当同时有 Hy2 与 Reality')
        grp = clash['proxy-groups'][0]
        self.assertEqual(grp['type'], 'url-test',
                         '双通道时策略组必须是 url-test（自动择优），否则用户要手动切')
        self.assertEqual(len(grp['proxies']), 2)

    def test_singbox_has_urltest_outbound(self):
        _, _, sing_s = portal.artifacts(
            META, auth_override='pw', name_override='X',
            reality=RCFG, user_id='alice')
        sing = json.loads(sing_s)
        kinds = [o.get('type') for o in sing['outbounds']]
        self.assertIn('urltest', kinds, 'sing-box 侧也要有 urltest outbound')
        self.assertEqual(sing.get('route', {}).get('final'), 'PROXY',
                         'sing-box 要把 final 指向 urltest 组')

    def test_without_reality_keeps_legacy_shape(self):
        """未启用 Reality 的节点必须保持旧行为 —— 不能被顺手改坏。"""
        _, clash_s, sing_s = portal.artifacts(
            META, auth_override='pw', name_override='X')
        clash = json.loads(clash_s)
        self.assertEqual(len(clash['proxies']), 1)
        self.assertEqual(clash['proxy-groups'][0]['type'], 'select')
        sing = json.loads(sing_s)
        self.assertNotIn('route', sing)

    def test_user_id_none_does_not_add_reality(self):
        """单机管理员视角（不给 user_id）不该凭空多出一个用不上的节点。"""
        _, clash_s, _ = portal.artifacts(
            META, auth_override='pw', name_override='X', reality=RCFG, user_id=None)
        self.assertEqual(len(json.loads(clash_s)['proxies']), 1)

    def test_incomplete_reality_degrades_safely(self):
        """Reality 配置不全时安全降级成单节点，而不是抛异常/半截配置。"""
        _, clash_s, _ = portal.artifacts(
            META, auth_override='pw', name_override='X',
            reality={'port': 443}, user_id='alice')
        self.assertEqual(len(json.loads(clash_s)['proxies']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
