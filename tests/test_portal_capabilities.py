"""节点能力端点（/api/v1/capabilities）的单元测试。

背景（2026-10-04）：
  主面板要一眼知道「这台节点有什么能力、什么版本」，但既有状态端点
  （/reality-status、/bbr-status、/warp-status、/awg-state）**全部走网页会话
  鉴权**，Bearer api_key 够不到 —— 主面板只能显示"未知"。
  新端点的价值就在于「让 Bearer 通道也能读到能力」。

同时钉死一条安全边界：
  capabilities **只报能力与状态，绝不报凭据**（私钥/公钥/UUID/api_key/密码）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal


class TestCapabilitiesEndpoint(unittest.TestCase):
    def setUp(self):
        self.src = (Path(portal.__file__).parent / 'portal.py').read_text(encoding='utf-8')
        self.start = self.src.find("if sub == 'capabilities':")
        self.assertGreater(self.start, 0, '找不到 capabilities 端点')
        # 截到下一个同级分支
        end = self.src.find("if sub == 'node/meta':", self.start)
        self.block = self.src[self.start:end if end > 0 else self.start + 6000]

    # ---------- 存在性与注册 ----------

    def test_endpoint_registered_in_bearer_channel(self):
        """必须挂在 /api/v1/（Bearer）分支里 —— 这正是它存在的理由。"""
        bearer = self.src.find("if sub == 'capabilities':")
        section = self.src.rfind('def do_GET', 0, bearer)
        self.assertGreater(section, 0)
        head = self.src[section:bearer]
        self.assertIn("self.path.startswith('/api/v1/')", head,
                      'capabilities 必须位于 Bearer 鉴权分支内')
        self.assertIn('verify_api_key()', head,
                      '必须经过 api_key 校验')

    def test_portal_version_constant_exists(self):
        self.assertTrue(hasattr(portal, 'PORTAL_VERSION'),
                        '缺少 PORTAL_VERSION —— 主面板无法判断节点门户版本')
        v = portal.PORTAL_VERSION
        self.assertIsInstance(v, str)
        self.assertRegex(v, r'^\d+\.\d+$', '版本号格式应为 x.y')

    # ---------- 安全边界：只输出白名单客户端参数 ----------

    def test_response_uses_public_parameter_whitelist(self):
        """端点允许客户端必需的公开参数，但不能透传服务端秘密。"""
        rj = self.block.find('return self.reply_json(200, {')
        self.assertGreater(rj, 0, '找不到 capabilities 的响应体')
        body = self.block[rj:]
        self.assertIn('_reality_public_params(d_snap[\'reality\'])', body)
        self.assertNotIn('api_key', body)
        self.assertNotIn('auth_password', body)

    def test_configured_flag_is_boolean_not_value(self):
        """`configured` 只能是布尔，不能顺手把私钥带出去。"""
        self.assertIn("'configured':", self.block)
        self.assertIn('bool(', self.block,
                      'configured 必须显式转 bool，不能直接塞原值')

    # ---------- 内容完整性 ----------

    def test_reports_both_protocols(self):
        """至少报 Hy2 与 VLESS-Reality 两个协议的状态。"""
        self.assertIn("'hysteria2':", self.block)
        self.assertIn("'vless_reality':", self.block)

    def test_reports_extras(self):
        """gost / amneziawg / warp / bbr 四类扩展都要报。"""
        for name in ('gost', 'amneziawg', 'warp', 'bbr'):
            self.assertIn(f"'{name}':", self.block, f'缺少 {name} 能力项')

    def test_reports_node_identity(self):
        self.assertIn("'node':", self.block)
        for field in ('public_ip', 'server_name', 'users_count'):
            self.assertIn(f"'{field}'", self.block, f'缺少节点字段 {field}')

    def test_portal_version_is_parsed_not_hardcoded(self):
        """版本必须从本文件解析，不能硬编码 —— 否则改版本会两处不一致。"""
        self.assertIn('PORTAL_VERSION', self.block)
        self.assertIn('__file__', self.block,
                      '应从 portal.py 自身读取版本，避免与常量漂移')

    # ---------- 与既有端点的一致性 ----------

    def test_reality_port_matches_config(self):
        """报的端口要来自 reality_config，而不是写死 443。"""
        self.assertIn("d_snap['reality'].get('port'", self.block,
                      'Reality 端口应取自 reality_config（tokyo 就是 8443 不是 443）')

    def test_does_not_claim_hysteria2_installed_by_checking_file(self):
        """Hy2 是主协议，装了 portal 就有 —— 不应靠探测文件假装判断。

        这一条防的是「以后有人把 installed 改成 Path(...).exists()」，
        那会在某些部署形态下误报 false。
        """
        self.assertIn("'installed': True", self.block,
                      'Hy2 主协议应直接报 installed: True')


if __name__ == '__main__':
    unittest.main(verbosity=2)
