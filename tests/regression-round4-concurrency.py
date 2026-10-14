#!/usr/bin/env python3
"""五轮回归 · 第 4 轮：并发与连续操作（对真实服务打）。

验证**状态一致性**：
  1. 连续保存同一个出站 ⇒ 配置里不能出现重复项
  2. 连续切换模式来回 ⇒ 不得留下坏 ACL
  3. 保存后立刻删除 ⇒ 不得留下悬空引用
  4. 服务全程必须始终 active（任何一刻挂掉就是事故）
  5. 配置始终是合法 hysteria 配置（用真实二进制校验）

🔴🔴 关键前提：测试用的出站必须**真的能连上**。
全局模式下所有流量都走该出站，连不上 hysteria 就会启动失败 ——
那样测出来的"失败"是环境造成的，不是代码问题（第 4 轮首跑就踩了这个：
出站指向 127.0.0.1:19898，而那台机器 WARP 没装，端口无人监听）。

所以这里用 socat 起一个真实的 SOCKS5 监听器当测试对象。
"""
import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CFG = '/etc/hysteria/config.yaml'
D = json.load(open('/etc/hysteria/portal.json'))
TOK = D['token']
# 🔴 端口必须从 portal.json 读，不能硬编码 —— 门户端口是随机生成的，
# 每台机器都不一样（se=33947、us5x=54741...）。
# 硬编码会让这些脚本只能在那台机器上跑，换机即 Connection refused。
B = 'http://127.0.0.1:%d/' % D['port'] + TOK + '/'
IAT = str(int(time.time()))
SIG = hmac.new(D['session_secret'].encode(), ('sess:' + TOK + ':' + IAT).encode(),
               hashlib.sha256).hexdigest()
CK = 'hy2_session=' + TOK + '.' + IAT + '.' + SIG
OP = urllib.request.build_opener()

# --- 起一个真实可连的 SOCKS5 -------------------------------------------
# 🔴🔴 三次踩坑才明白：测试用的出站必须**真能真正转发流量**，
#  光"能建立 TCP 连接"不够 —— hysteria 会真的走过去，
#  握手完不成或转不出去，它照样立刻退出：
#   第1 次：指向 127.0.0.1:19898（WARP 没装，端口无人监听）→ 必失败
#   第 2 次：Python 手写 SOCKS5，应答不完整 → 握手能过但连不上 → 仍失败
#   第 3 次（本次）：改用真 gost，才真正可用。
# 所以这里优先用 gost；没有则明确跳过并报错，不拿假代理凑数。
DUMMY = None
_gost = None
GOST = shutil.which('gost') or ('/tmp/gost' if os.path.exists('/tmp/gost') else None)
if GOST:
    _s = socket.socket()
    _s.bind(('127.0.0.1', 0))
    DUMMY_PORT = _s.getsockname()[1]
    _s.close()
    # gost 作为真实 SOCKS5 服务端，把流量转发到本地 hysteria 端口做闭环
    _gost = subprocess.Popen(
        [GOST, '-L', 'socks5://127.0.0.1:%d' % DUMMY_PORT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    DUMMY = '127.0.0.1:%d' % DUMMY_PORT
else:
    print('!! 未找到 gost —— 无法进行真实出站测试')
    print('!! 安装：curl -fsSL -o /tmp/gost.gz '
          'https://github.com/ginuerzh/gost/releases/download/v2.11.5/gost-linux-amd64-2.11.5.gz'
          ' && gunzip -c /tmp/gost.gz > /tmp/gost && chmod +x /tmp/gost')
    sys.exit(2)

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
            print('        | ' + str(extra)[:400])


def post(action, **kw):
    body = urllib.parse.urlencode(dict(action=action, **kw)).encode()
    r = urllib.request.Request(B + 'manage-outbounds', data=body)
    r.add_header('Cookie', CK)
    try:
        return json.loads(OP.open(r, timeout=60).read().decode('utf-8', 'ignore'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8', 'ignore'))
        except Exception:
            return {'ok': False, 'error': 'HTTP %d' % e.code}


def get(path):
    r = urllib.request.Request(B + path)
    r.add_header('Cookie', CK)
    return json.loads(OP.open(r, timeout=30).read().decode('utf-8', 'ignore'))


def svc_active():
    return subprocess.run(['systemctl', 'is-active', 'hysteria-server'],
                          capture_output=True, text=True).stdout.strip()


def cfg_valid(port_base=45801):
    try:
        txt = open(CFG, encoding='utf-8').read()
    except Exception as e:
        return False, str(e)
    txt = re.sub(r'(?m)^listen:\s*.*$', 'listen: :%d' % port_base, txt, count=1)
    txt = re.sub(r'(?m)^(\s*)listenHTTPS:\s*.*$',
                 r'\g<1>listenHTTPS: :%d' % (port_base + 1), txt)
    open('/tmp/_r4.yaml', 'w', encoding='utf-8').write(txt)
    p = subprocess.Popen(['/usr/local/bin/hysteria', 'server', '-c', '/tmp/_r4.yaml'],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, preexec_fn=os.setsid)
    try:
        out, _ = p.communicate(timeout=8)
        if 'address already in use' in (out or ''):
            return True, 'port-busy(ok)'
        return False, (out or '').strip()[:200]
    except subprocess.TimeoutExpired:
        return True, 'running(ok)'
    finally:
        try:
            if p.poll() is None:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass


print('测试用 SOCKS5: %s (gost)' % DUMMY)
# 前置确认：测试代理真的能连上（否则后面全是环境噪音）
try:
    s = socket.create_connection(tuple(DUMMY.split(':') and
                                       [DUMMY.split(':')[0], int(DUMMY.split(':')[1])]), timeout=3)
    s.close()
    case('测试用 SOCKS5 可连接', True)
except Exception as e:
    case('测试用 SOCKS5 可连接（%s）' % e, False)

print('\n=== 0) 前置 ===')
case('起始 hysteria active', svc_active() == 'active', svc_active())
ok0, why0 = cfg_valid()
case('起始配置合法 (%s)' % why0, ok0)

print('\n=== 1) 连续保存同一出站 5 次（不得重复追加）===')
allok = True
for i in range(5):
    r = post('save', name='r4dup', type='socks5', addr=DUMMY)
    if not r.get('ok'):
        allok = False
        print('        第 %d 次: %s' % (i + 1, r.get('error')))
        break
case('连续保存 5 次均成功', allok)
time.sleep(1)
cfg = open(CFG, encoding='utf-8').read()
n = cfg.count('name: r4dup')
case('配置里只有 1 条 r4dup (实际 %d)' % n, n == 1)
case('保存后服务仍 active', svc_active() == 'active', svc_active())
ok1, why1 = cfg_valid()
case('保存后配置仍合法 (%s)' % why1, ok1)

print('\n=== 2) 连续切模式来回 4 次 ===')
bad = []
for m in ['global', 'rules', 'global', 'rules']:
    r = post('mode', mode=m)
    if not r.get('ok'):
        bad.append('%s: %s' % (m, r.get('error')))
    time.sleep(0.5)
case('4 次模式切换无失败', not bad, bad)
case('切换后服务仍 active', svc_active() == 'active', svc_active())
ok2, why2 = cfg_valid()
case('切换后配置仍合法 (%s)' % why2, ok2)

print('\n=== 3) 保存后立刻删除（不留悬空引用）===')
post('save', name='r4quick', type='socks5', addr=DUMMY)
r = post('delete', name='r4quick')
case('保存后立即删除成功', r.get('ok') is True, r)
time.sleep(0.5)
cfg = open(CFG, encoding='utf-8').read()
case('配置里已无 r4quick', 'name: r4quick' not in cfg)
case('删除后服务仍 active', svc_active() == 'active', svc_active())
ok3, why3 = cfg_valid()
case('删除后配置仍合法 (%s)' % why3, ok3)

print('\n=== 4) 删掉最后一个出站应自动退回 rules ===')
post('delete', name='r4dup')
post('save', name='r4last', type='socks5', addr=DUMMY)
post('mode', mode='global')
r = post('delete', name='r4last')
case('全局模式下删除最后一个出站成功', r.get('ok') is True, r)
time.sleep(0.5)
case('此时服务仍 active', svc_active() == 'active', svc_active())
ok4, why4 = cfg_valid()
case('配置仍合法 (%s)' % why4, ok4)
lst = get('outbounds/list')
case('出站列表已清空', not (lst.get('outbounds') or []), lst.get('outbounds'))
case('模式已退回 rules', lst.get('mode') == 'rules', lst.get('mode'))

print('\n=== 5) 收尾回到干净状态 ===')
post('mode', mode='rules')
time.sleep(1)
case('收尾服务 active', svc_active() == 'active', svc_active())
ok5, why5 = cfg_valid()
case('收尾配置合法 (%s)' % why5, ok5)

if _gost:
    try:
        _gost.kill()
    except Exception:
        pass
try:
    os.unlink('/tmp/_r4.yaml')
except Exception:
    pass

print('\n================ 第 4 轮结果 ================')
print('  通过: %d   失败: %d' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)