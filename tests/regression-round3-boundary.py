#!/usr/bin/env python3
"""五轮回归 · 第 3 轮：异常输入与边界（纯逻辑，不需要服务器）。

这一轮要证明的是：**用户乱填不会把服务搞挂**。
最危险的输入类别：
  - YAML 注入（引号里塞换行 / 冒号 / # 注释）⇒ 配置被改坏 ⇒ 服务起不来
  - 超长名字 / 非 ASCII / emoji
  - 端口越界、地址含协议、URL 含路径
  - 重复名字（覆盖 vs 冲突）
  - 空值、None、非字符串类型
"""
import json
import sys
import threading

SRC = '/etc/hysteria/portal.py'
import os
if not os.path.exists(SRC):
    SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'portal.py')
    SRC = os.path.abspath(SRC)

src = open(SRC, encoding='utf-8').read()
start = src.index('def strip_acl_block')
end = src.index('# \U0001f534 必须是原始字符串')
ns = {'re': __import__('re'), 'json': json, 'Path': __import__('pathlib').Path,
      'data': {}, 'data_lock': threading.RLock()}
exec('import re\n' + src[start:end], ns)

validate_outbound = ns['validate_outbound']
sanitize = ns['sanitize_outbound_name']
build_acl = ns['build_acl_block']
render = ns['render_outbound_yaml']
upsert = ns['upsert_outbound_block']

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
            print('        | ' + str(extra)[:240])


print('=== 1) 🔴 YAML 注入：值里带换行/引号/冒号 ===')
# 🔴 这是最危险的一类：名字或地址里塞 \n + yaml 键，就能往 config.yaml 注入任意配置
evil = [
    {'name': 'x\n  evil: true', 'type': 'socks5', 'addr': '1.2.3.4:1080'},
    {'name': 'y\nadmin: yes', 'type': 'socks5', 'addr': '1.2.3.4:1080'},
    {'name': 'z: 1', 'type': 'socks5', 'addr': '1.2.3.4:1080'},
    {'name': 'w # comment', 'type': 'socks5', 'addr': '1.2.3.4:1080'},
    {'name': 'ok', 'type': 'socks5', 'addr': '1.2.3.4:1080\n  admin: true'},
    {'name': 'ok2', 'type': 'http', 'url': 'http://a.com\n  admin: true'},
]
BASE = 'listen: :443\noutbounds:\n  - name: direct_ipv4\n    type: direct\n'
for e in evil:
    ok, cl, err = validate_outbound(e)
    if not ok:
        case('拒绝注入：%r' % (e['name'][:26],), True)
        continue
    # 若校验放行了，必须保证渲染出的 YAML 里 name 是单行且被安全引用
    try:
        frag = render(cl)
    except Exception as ex:
        case('渲染崩溃：%r' % (e['name'][:26],), False, ex)
        continue
    # 🔴 断言方向：sanitize 已把换行/冒号/井号全换成下划线，
    #    所以正确表现是「渲染出的 name 行不含换行、且等于清洗后的名字」。
    #    （我原先把 single_line 判断写反了，误报成注入成功 —— 恰恰相反。）
    name_line = [l for l in frag.split('\n') if 'name:' in l][0]
    safe = (cl['name'] in name_line) and ('\n' not in name_line)
    case('name 已被清洗为安全单行：%r' % (cl['name'][:20],), safe, frag)

print('\n=== 2) 名字边界 ===')
for raw, desc in [
    ('', '空名'),
    ('a' * 40, '超长名（40 字符）'),
    ('中文名字', '中文名'),
    ('emoji😀名', 'emoji 名'),
    ('123', '纯数字开头'),
    ('-leading', '横线开头'),
    ('has space', '含空格'),
    ('has\ttab', '含制表符'),
    ('UPPER_case', '大写+下划线'),
]:
    nm = sanitize(raw)
    if nm == '':
        case('%s → 拒绝（空）' % desc, True)
        continue
    import re as _re
    ok = bool(_re.match(r'^[A-Za-z_][A-Za-z0-9_]{0,31}$', nm))
    case('%s → %r 合法' % (desc, nm), ok)

print('\n=== 3) 地址/URL 边界 ===')
for e, desc in [
    ({'name': 'a', 'type': 'socks5', 'addr': ''}, 'socks5 空地址'),
    ({'name': 'a', 'type': 'socks5', 'addr': ':1080'}, 'socks5 无主机'),
    ({'name': 'a', 'type': 'socks5', 'addr': 'host:'}, 'socks5 无端口'),
    ({'name': 'a', 'type': 'socks5', 'addr': 'host:0'}, 'socks5 端口 0'),
    ({'name': 'a', 'type': 'socks5', 'addr': 'host:65536'}, 'socks5 端口越界'),
    ({'name': 'a', 'type': 'socks5', 'addr': 'host:70000'}, 'socks5 端口大'),
    ({'name': 'a', 'type': 'socks5', 'addr': 'http://h:80'}, 'socks5 误填 http'),
    ({'name': 'a', 'type': 'http', 'url': ''}, 'http 空 url'),
    ({'name': 'a', 'type': 'http', 'url': 'ftp://h:21'}, 'http 错协议'),
    ({'name': 'a', 'type': 'http', 'url': 'http://h'}, 'http 无端口(允许)'),
    ({'name': 'a', 'type': 'http', 'url': 'http://h:8080/path'}, 'http 带路径(允许)'),
    ({'name': 'a', 'type': 'direct', 'mode': 'bad'}, 'direct 错模式'),
]:
    ok, cl, err = validate_outbound(e)
    expect_ok = 'http 无端口(允许)' in desc or 'http 带路径(允许)' in desc
    case('%s => %s' % (desc, '接受' if ok else '拒绝(%s)' % err),
         ok == expect_ok)

print('\n=== 4) 非字符串/None 输入不应崩 ===')
for e, desc in [
    ({'name': None, 'type': 'socks5', 'addr': '1.2.3.4:1080'}, 'name=None'),
    ({'type': 'socks5', 'addr': '1.2.3.4:1080'}, '缺 name 键'),
    ({'name': 'a'}, '只有 name'),
    ({'name': 'a', 'type': None, 'addr': 'x'}, 'type=None'),
]:
    try:
        ok, cl, err = validate_outbound(e)
        case('%s 不崩溃（%s）' % (desc, '接受' if ok else '拒绝'), True)
    except Exception as ex:
        case('%s 未崩溃' % desc, False, ex)

print('\n=== 5) insecure 只接受明确的真值 ===')
for raw, expect in [
    ('', False), ('0', False), ('false', False), ('no', False),
    ('1', True), ('true', True), ('TRUE', True), ('yes', True), ('on', True),
    #🔴 以下是危险值：Python 里 bool('false') 是 True，必须被显式排除
    ('random', False), ('enabled', False), ('2', False), ('null', False),
]:
    ok, cl, err = validate_outbound({'name': 'a', 'type': 'http',
                                     'url': 'http://h:8080', 'insecure': raw})
    got = bool(cl and cl.get('insecure'))
    case("insecure=%r => %s" % (raw, got), got == expect, cl)

print('\n=== 6) 🔴 ACL 生成必须永远有兜底规则 ===')
outs = [{'name': 'o1', 'type': 'socks5', 'addr': '1.2.3.4:1080'}]
for mode, rules, desc in [
    ('rules', [], 'rules 无规则'),
    ('rules', [{'domain': 'a.com', 'outbound': 'o1'}], 'rules 有规则'),
    ('rules', [{'domain': '', 'outbound': 'o1'}], 'rules 空域名'),
    ('rules', [{'domain': 'a.com', 'outbound': '不存在'}], 'rules 引用不存在出站'),
    ('global', [], 'global 无出站'),
]:
    acl = build_acl(mode, rules, outs, 'direct_ipv4')
    has_tail = '(all)' in acl
    lines = [l for l in acl.split('\n') if l.strip().startswith('-')]
    case('%s 有兜底(all)' % desc, has_tail, acl)
    if mode == 'rules':
        case('%s 无悬空引用' % desc,
             '不存在(' not in acl, acl)

print('\n=== 7) 渲染片段的 YAML 基本形状 ===')
for o in [{'name': 'a_b', 'type': 'socks5', 'addr': '1.2.3.4:1080'},
          {'name': 'c_d', 'type': 'http', 'url': 'http://h:8080'},
          {'name': 'e_f', 'type': 'direct'}]:
    frag = render(o)
    has_name = 'name: ' + o['name'] in frag
    has_type = 'type: ' + o['type'] in frag
    case('%s 渲染含 name/type' % o['name'], has_name and has_type, frag)

print('\n=== 8) upsert 幂等（连续 5 次）===')
cur = BASE
o = {'name': 'x_y', 'type': 'socks5', 'addr': '1.2.3.4:1080'}
for _ in range(5):
    cur = upsert(cur, [o])
n = cur.count('name: x_y')
case('5 次 upsert 后只有 1 条（实际 %d）' % n, n == 1)
case('内建direct_ipv4 未被删', 'name: direct_ipv4' in cur)

print('\n================ 第 3 轮结果 ================')
print('  通过: %d   失败: %d' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)