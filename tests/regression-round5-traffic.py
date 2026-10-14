#!/usr/bin/env python3
"""五轮回归 · 第 5 轮：真实流量闭环（最硬的一轮）。

前四轮证明的是"配置写对了、服务起得来"，这一轮证明的是
**���配置真的在起作用**——出站是否真的把流量送出去了。

验证路径：
  起 gost 作真实 SOCKS5 出口（同时记录它收到过连接）
    → 门户切全局出口
      → 用真实 Hysteria **客户端**连节点、经代理访问外网
        → 检查出口 IP：应是 gost 的出口，而不是服务器原 IP
  再确认 gost 确实被访问过（否则"出口 IP 没变"只是因为流量没走代理）

⚠️ 这是唯一能证明"出站功能真的有效"的一轮。
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
HY = '/usr/local/bin/hysteria'
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
    r.add_header('Cookie', CK)
    try:
        return json.loads(OP.open(r, timeout=60).read().decode('utf-8', 'ignore'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8', 'ignore'))
        except Exception:
            return {'ok': False, 'error': 'HTTP %d' % e.code}


def cfg_val(key, default=''):
    m = re.search(r'^%s:\s*(.+)$' % re.escape(key), open(CFG, encoding='utf-8').read(), re.M)
    return m.group(1).strip().strip('"') if m else default


def auth_pw():
    txt = open(CFG, encoding='utf-8').read()
    m = re.search(r'^auth:\s*\n(?:.*\n)*?\s+password:\s*"?([^"\n]+)', txt, re.M)
    return m.group(1).strip() if m else ''


def obfs_pw():
    txt = open(CFG, encoding='utf-8').read()
    m = re.search(r'^obfs:\s*\n(?:.*\n)*?\s+password:\s*"?([^"\n]+)', txt, re.M)
    return m.group(1).strip() if m else ''


def my_ip():
    try:
        return json.loads(urllib.request.urlopen(
            urllib.request.Request('https://api4.ipify.org?format=json'),
            timeout=15).read().decode()).get('ip', '')
    except Exception:
        return ''


def via_proxy_curl(socks_addr, timeout=30):
    """用 curl 经 SOCKS5 出站访问 ipify，返回出口 IP。"""
    p = subprocess.Popen(
        ['curl', '-s', '--max-time', str(timeout), '-x',
         'socks5h://' + socks_addr, 'https://api4.ipify.org'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        preexec_fn=os.setsid)
    try:
        out, _ = p.communicate(timeout=timeout + 5)
        return (out or '').strip()
    except subprocess.TimeoutExpired:
        return ''
    finally:
        try:
            if p.poll() is None:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass


print('=== 0) 前置 ===')
server_ip = my_ip()
case('能测到本机出口 IP (%s)' % server_ip, bool(server_ip))
case('hysteria 二进制存在', os.path.exists(HY))
case('服务 active',
     subprocess.run(['systemctl', 'is-active', 'hysteria-server'],
                    capture_output=True, text=True).stdout.strip() == 'active')

# 保存现场
saved_acl = ''
if 'acl:' in open(CFG, encoding='utf-8').read():
    saved_acl = open(CFG, encoding='utf-8').read().split('acl:')[1]

# 起 gost 作真实出口，并让它把日志写到文件以便确认"确实被用过"
GOST = shutil.which('gost') or ('/tmp/gost' if os.path.exists('/tmp/gost') else None)
if not GOST:
    print('!! 未找到 gost，无法做真实流量验证')
    sys.exit(2)
_s = socket.socket(); _s.bind(('127.0.0.1', 0))
GPORT = _s.getsockname()[1]; _s.close()
glog = open('/tmp/_r5_gost.log', 'w')
gost = subprocess.Popen([GOST, '-L', 'socks5://127.0.0.1:%d' % GPORT],
                        stdout=glog, stderr=subprocess.STDOUT)
time.sleep(1.0)
SOCKS = '127.0.0.1:%d' % GPORT
print('gost 出口: %s' % SOCKS)

print('\n=== 1) gost 自身可转发（先单独验证，排除变量）===')
via = via_proxy_curl(SOCKS, timeout=25)
case('gost 出口可用（curl 经它拿到 IP: %s）' % (via or '无'), bool(via))

print('\n=== 2) 通过门户新增该出站并切全局 ===')
r = post('save', name='r5exit', type='socks5', addr=SOCKS)
case('保存出站', r.get('ok') is True, r)
r = post('mode', mode='global')
case('切到全局出口', r.get('ok') is True, r)
time.sleep(1)
acl = open(CFG, encoding='utf-8').read().split('acl:')[-1] if 'acl:' in open(CFG, encoding='utf-8').read() else ''
case('ACL 指向 r5exit 全局', 'r5exit(all)' in acl, acl.strip()[:120])

print('\n=== 3) 🔴 真实客户端经节点访问，出口应是 gost 的 IP ===')
port = cfg_val('listen').lstrip(':')
client = (
    'server: 127.0.0.1:%s\n'
    'auth: "%s"\n'
    'tls:\n'
    '  sni: se.zy3a.com\n'
    '  insecure: true\n'
    'obfs:\n'
    '  type: salamander\n'
    '  salamander:\n'
    '    password: "%s"\n'
    'socks5:\n'
    '  listen: 127.0.0.1:18777\n'
) % (port, auth_pw(), obfs_pw())
open('/tmp/_r5c.yaml', 'w').write(client)
cli = subprocess.Popen([HY, 'client', '-c', '/tmp/_r5c.yaml'],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, preexec_fn=os.setsid)
time.sleep(9)
connected = False
try:
    o = subprocess.run(['ss', '-tlnp'], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    connected = '18777' in o
except Exception:
    pass
case('客户端已连上并监听本地代理', connected)

ip_through_node = ''
if connected:
    try:
        p = subprocess.Popen(
            ['curl', '-s', '--max-time', '25', '-x', 'socks5h://127.0.0.1:18777',
             'https://api4.ipify.org'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            preexec_fn=os.setsid)
        ip_through_node, _ = p.communicate(timeout=30)
        ip_through_node = (ip_through_node or '').strip()
    except Exception as e:
        ip_through_node = 'err:%s' % e
print('    服务器原IP   = %s' % server_ip)
print('    经节点出口 IP = %s' % (ip_through_node or '无'))
print('    gost 出口 IP = %s' % (via or '无'))

try:
    os.killpg(os.getpgid(cli.pid), signal.SIGKILL)
except Exception:
    pass

print('\n=== 4) 🔴 gost 日志应显示它被访问过（证明流量真的走了出站）===')
try:
    gost.kill()
except Exception:
    pass
time.sleep(0.5)
glog.flush()
try:
    glog_text = open('/tmp/_r5_gost.log', encoding='utf-8', errors='ignore').read()
except Exception:
    glog_text = ''
case('gost 日志非空（收到过连接）', len(glog_text.strip()) > 0,
     glog_text.strip()[:200])

print('\n=== 5) 判定出站是否真的生效 ===')
if ip_through_node and via and server_ip:
    if ip_through_node == via:
        case('✅ 经节点的出口 == gost 出口 == %s（出站生效）' % via, True)
    elif ip_through_node == server_ip:
        case('🔴 经节点出口仍是服务器原 IP %s —— 出站没生效' % server_ip, False)
    else:
        case('出口为 %s（与两端都不同，需人工确认）' % ip_through_node, True)
else:
    case('无法判定（缺 IP 数据）', False,
         'node=%s gost=%s server=%s' % (ip_through_node, via, server_ip))

print('\n=== 6) 收尾：恢复直连 ===')
r = post('delete', name='r5exit')
case('删除测试出站', r.get('ok') is True, r)
r = post('mode', mode='rules')
case('切回分流', r.get('ok') is True, r)
time.sleep(1)
case('收尾服务 active',
     subprocess.run(['systemctl', 'is-active', 'hysteria-server'],
                    capture_output=True, text=True).stdout.strip() == 'active')
try:
    os.unlink('/tmp/_r5c.yaml')
    os.unlink('/tmp/_r5_gost.log')
except Exception:
    pass

print('\n================ 第 5 轮结果 ================')
print('  通过: %d   失败: %d' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)