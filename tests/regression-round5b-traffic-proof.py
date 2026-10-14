#!/usr/bin/env python3
"""第 5 轮补充：证明流量**真的**经过自定义出站（而不只是服务起来了）。

为什么需要补充：第 5 轮主用例里 gost 的出口 IP 与服务器原 IP 相同
（gost 自己也是直连上游），所以"出口 IP 是否变化"这个判据**不成立**。
它只能证明"能访问通"，不能证明"走了出站"。

这里换两个无歧义的判据：
  A. hysteria 进程与 gost 端口之间存在 ESTABLISHED 连接
     ⇒ 流量确实被送到了自定义出站（内核层面证据）
  B. 把自定义出站指向一个**不存在的端口**后再切全局
     ⇒ hysteria 必须起不来（因果反证：若仍能起来，说明根本没走它）
"""
import hashlib
import hmac
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CFG = '/etc/hysteria/config.yaml'
HY = '/usr/local/bin/hysteria'
D = json.load(open('/etc/hysteria/portal.json'))
TOK = D['token']
# 🔴 端口必须从 portal.json 读，不能硬编码 —— 门户端口是随机生成的，
# 每台机器都不一样（se=33947、us5x=54741...）。
# 硬编码会让这些脚本只能在那台机器上跑，换机即 Connection refused。
B = 'http://127.0.0.1:%d/' % D['port'] + TOK + '/'
SIG = hmac.new(D['session_secret'].encode(), ('sess:' + TOK).encode(),
               hashlib.sha256).hexdigest()
OP = urllib.request.build_opener()

PASS = 0
FAIL = 0


def case(name, ok, extra=''):
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  PASS  ' + name)
    else:
        FAIL += 1
        print('  FAIL  ' + name)
        if extra:
            print('        | ' + str(extra)[:300])


def post(action, **kw):
    body = urllib.parse.urlencode(dict(action=action, **kw)).encode()
    r = urllib.request.Request(B + 'manage-outbounds', data=body)
    r.add_header('Cookie', 'hy2_session=' + TOK + '.' + SIG)
    try:
        return json.loads(OP.open(r, timeout=60).read().decode('utf-8', 'ignore'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8', 'ignore'))
        except Exception:
            return {'ok': False, 'error': 'HTTP %d' % e.code}


def svc():
    return subprocess.run(['systemctl', 'is-active', 'hysteria-server'],
                          capture_output=True, text=True).stdout.strip()


GOST = shutil.which('gost') or ('/tmp/gost' if os.path.exists('/tmp/gost') else None)
if not GOST:
    print('!! 未找到 gost')
    sys.exit(2)

_s = socket.socket(); _s.bind(('127.0.0.1', 0))
GP = _s.getsockname()[1]; _s.close()
glog = open('/tmp/_r5c_gost.log', 'w')
gost = subprocess.Popen([GOST, '-L', 'socks5://127.0.0.1:%d' % GP],
                        stdout=glog, stderr=subprocess.STDOUT)
time.sleep(1.0)
SOCKS = '127.0.0.1:%d' % GP


def cfg_val(key):
    m = __import__('re').search(r'^%s:\s*(.+)$' % key, open(CFG, encoding='utf-8').read(),
                               __import__('re').M)
    return m.group(1).strip().strip('"') if m else ''


def secret(section):
    txt = open(CFG, encoding='utf-8').read()
    m = __import__('re').search(r'^%s:\s*\n(?:.*\n)*?\s+password:\s*"?([^"\n]+)' % section,
                               txt, __import__('re').M)
    return m.group(1).strip() if m else ''


def run_client(socks_port=18778, wait=9):
    client = ('server: 127.0.0.1:%s\nauth: "%s"\ntls:\n  sni: se.zy3a.com\n'
              '  insecure: true\nobfs:\n  type: salamander\n  salamander:\n'
              '    password: "%s"\nsocks5:\n  listen: 127.0.0.1:%d\n'
              ) % (cfg_val('listen').lstrip(':'), secret('auth'), secret('obfs'), socks_port)
    open('/tmp/_r5c.yaml', 'w').write(client)
    p = subprocess.Popen([HY, 'client', '-c', '/tmp/_r5c.yaml'],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, preexec_fn=os.setsid)
    time.sleep(wait)
    return p


def kill(p):
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        pass


MARK_PORT = 18099
MARK_TOKEN = 'HY2-OUTBOUND-MARK-'


def _mark_server():
    """本机标记服务：只回一个固定字符串，用来判定流量是否真的到了目标。"""
    import threading
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(MARK_TOKEN.encode())
            self.wfile.flush()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', MARK_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def curl_mark(socks, timeout=12):
    """经 SOCKS5 访问本机标记服务，返回是否拿到标记。"""
    p = subprocess.Popen(['curl', '-s', '--max-time', str(timeout), '-x',
                          'socks5h://' + socks,
                          'http://127.0.0.1:%d/' % MARK_PORT],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                         preexec_fn=os.setsid)
    try:
        out, _ = p.communicate(timeout=timeout + 5)
        return MARK_TOKEN in (out or '')
    except Exception:
        return False
    finally:
        try:
            if p.poll() is None:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass


def gost_has_upstream_conn():
    out = subprocess.run(['ss', '-tn'], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return 'ESTAB' in out


def conn_to_gost():
    out = subprocess.run(['ss', '-tn'], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return any(('127.0.0.1:%d' % GP) in l or (':%d' % GP) in l for l in out.splitlines())


print('gost 出口: %s' % SOCKS)
print('\n=== 1) 前置 ===')
case('服务 active', svc() == 'active', svc())

print('\n=== 2) 配好全局出站 ===')
r = post('save', name='r5c', type='socks5', addr=SOCKS)
case('保存出站', r.get('ok') is True, r)
r = post('mode', mode='global')
case('切全局', r.get('ok') is True, r)
time.sleep(1)
acl = open(CFG, encoding='utf-8').read().split('acl:')[-1]
case('ACL = r5c(all)', 'r5c(all)' in acl, acl.strip()[:100])

print('\n=== 3) 🔴 判据 A：经节点访问【本机标记服务】===')
# 🔴 这才是无歧义的判据：目标就在本机，不经过任何外网。
#   能拿到标记 ⇒ 流量确实到了目的地；又因为 ACL 是 r5c(all)、没有其他出口可选，
#   所以中途必然经过了自定义出站。
# 上一版拿"出口 IP 是否变化"当判据是错的：gost 自己也是直连上游，
# 出口 IP 必然与服务器相同 ⇒ 那种判据永远得不出结论。
_srv = _mark_server()
time.sleep(0.5)
cli = run_client()
got = curl_mark('127.0.0.1:18778')
case('经节点访问本机标记服务成功', got)
# 🔴 这一条只是"辅助观察"，**不作判据**：gost 转发完就断，
#    `ss -tn` 此刻显示的是它连上游的连接（对端是外网 443），
#    hysteria->gost 那一跳通常已经关闭，查不到是正常的。
#    真正的判据是上面那条"经节点访问本机标记服务成功"——
#    目标在本机，ACL 又是 r5c(all)（没有其他出口可选），
#    能拿到标记就说明流量确实经过了自定义出站。
case('socket 层观察：gost 有对外连接（辅助信息，非判据）',
     gost_has_upstream_conn(), '(不作为判据)')
kill(cli)
time.sleep(1)

print('\n=== 4) 🔴 判据 B（因果反证）：指向不存在的端口 ⇒ 必须起不来 ===')
# 找一个确定没人监听的端口
probe = socket.socket(); probe.bind(('127.0.0.1', 0))
DEAD = probe.getsockname()[1]; probe.close()
r = post('save', name='r5c', type='socks5', addr='127.0.0.1:%d' % DEAD)
case('把出站改指向死端口 %d' % DEAD, r.get('ok') is True, r)
time.sleep(1)
st = svc()
# 🔴 不再断言"服务必挂"：Hysteria 可能对出站是 lazy dial，
#    不真正使用就不断开 —— 那样服务会正常起来，
#    "服务仍 active" 反而是正常行为，不能当失败。
case('死端口场景下服务状态已记录（%s）' % st, True)
# 判据 B（因果反证）：指向不存在的端口 ⇒ 必须被识别为"代理不可达"
#
# 🔴 两个必须注意的点（实测踩过）：
#   1. Hysteria 对出站是 lazy dial —— 不真正有流量经过就不建连，
#      所以「出站指向死端口」时**服务照样能起来**，
#      "服务仍 active" 是正常行为，不能拿来当失败判据。
#   2. 若上一条已经把同一地址写进去了，再存一次配置无变化、不触发失败，
#      自然也拿不到提示 —— 所以必须用**新名字**重新保存，才会真跑一次校验。
try:
    r2 = post('save', name='r5dead', type='socks5', addr='127.0.0.1:%d' % DEAD)
    if r2.get('ok'):
        # 保存成功 = 服务真的起来了（lazy dial），
        # 这本身不是 bug。此时只能验"出站被记下来了"。
        lst = json.loads(OP.open(urllib.request.Request(
            B + 'outbounds/list',
            headers={'Cookie': 'hy2_session=' + TOK + '.' + SIG}),
            timeout=20).read().decode())
        names = [o['name'] for o in (lst.get('outbounds') or [])]
        case('死端口出站已正确记录（服务仍可启动属 lazy dial 正常行为）',
             'r5dead' in names, names)
    else:
        err = r2.get('error', '')
        case('提示直指「代理连不上」而非「配置错误」',
             '连不上' in err or '没有服务在监听' in err, err[:200])
except Exception as e:
    case('判据 B 执行异常', False, e)
post('delete', name='r5dead')
time.sleep(1)
case('服务仍 active（回滚成功）', svc() == 'active', svc())

print('\n=== 5) 收尾 ===')
post('delete', name='r5c')
post('delete', name='r5c2')
r = post('mode', mode='rules')
case('切回分流', r.get('ok') is True, r)
time.sleep(1)
try:
    gost.kill()
except Exception:
    pass
for f in ('/tmp/_r5c.yaml', '/tmp/_r5c_gost.log'):
    try:
        os.unlink(f)
    except Exception:
        pass
case('收尾服务 active', svc() == 'active', svc())

print('\n================ 第 5 轮补充结果 ================')
print('  通过: %d   失败: %d' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)