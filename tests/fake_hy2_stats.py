"""测试用假 Hysteria trafficStats 服务（只监听 127.0.0.1）。

真实安装里 usage-meter-config.json 一定存在（install.sh 生成 / 升级时补写），
门户撤销用户时要通过它确认旧会话已断开；拿不到就按设计返回 503。
端到端测试起门户子进程前，用这里写好配置并起一个「没人在线」的假服务。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = 'fake-stats-secret'


class _Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get('Content-Length', 0) or 0)
        if length:
            self.rfile.read(length)
        body = b'{}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply

    def log_message(self, *args):
        pass


def start(hy2_dir):
    """起假服务并写 usage-meter-config.json；返回 server，测试结束时 shutdown()。"""
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = {'hysteria': {'url': 'http://127.0.0.1:%d' % server.server_address[1], 'secret': SECRET}}
    (hy2_dir / 'usage-meter-config.json').write_text(json.dumps(cfg), encoding='utf-8')
    return server
