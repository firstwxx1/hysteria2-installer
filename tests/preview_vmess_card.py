"""VMess 卡片的安全预览夹具。

渲染真实的 portal.page_html()，但所有 /api 状态请求都由本进程 mock 返回，
不启动门户、不读写 /etc、不执行 systemctl —— 只用于确认前端展示与交互。

运行：python tests/preview_vmess_card.py
访问：http://127.0.0.1:8768/?state=off|on|noxray
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal

META = {'server_name': 'node.example.com', 'public_ip': '203.0.113.9',
        'listen_port': 19984, 'subscription_port': 29421, 'auth_password': 'fixture'}
URI = 'vmess://ZmFrZS1maXh0dXJl'


def _status(state):
    if state == 'noxray':
        return {'ok': True, 'installed': False, 'active': False, 'config': {}}
    if state == 'on':
        return {'ok': True, 'installed': True, 'active': True,
                'config': {'uri': URI, 'port': 2087, 'domain': 'node.example.com',
                           'sni': 'node.example.com', 'uuid': '11111111-2222-3333-4444-555555555555',
                           'ws_path': '/AbCdEf123456', 'qr_svg': ''}}
    return {'ok': True, 'installed': True, 'active': False, 'config': {}}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        state = parse_qs(url.query).get('state', ['off'])[0]
        if url.path.endswith('/vmess-status'):
            body, kind = json.dumps(_status(state)).encode(), 'application/json'
        elif url.path.endswith('/reality-status'):
            body, kind = json.dumps({'ok': True, 'installed': True, 'active': False,
                                     'config': {}}).encode(), 'application/json'
        else:
            body = portal.page_html(META, 'hysteria2://fixture@node.example.com:19984',
                                    'https://node.example.com:29421/sub',
                                    '{}', '{}').encode()
            kind = 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    print('VMess card fixture: http://127.0.0.1:8768/', flush=True)
    HTTPServer(('127.0.0.1', 8768), Handler).serve_forever()
