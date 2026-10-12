"""节点计数器只读适配器；不 reset，不输出 secret。"""
import json
import subprocess
import urllib.request
from urllib.parse import urlparse


def service_epoch(service):
    if service not in ('hysteria-server', 'xray'):
        raise ValueError('unsupported service')
    r = subprocess.run(['systemctl','show',service,'--property=InvocationID','--value'],
                       capture_output=True, timeout=5, check=True)
    value = r.stdout.decode().strip()
    if not value:
        raise RuntimeError('missing InvocationID')
    return value


def read_hysteria(url, secret):
    p=urlparse(url)
    if p.scheme != 'http' or p.hostname not in ('127.0.0.1','::1') or p.path not in ('','/'):
        raise ValueError('stats endpoint must be loopback HTTP base URL')
    req=urllib.request.Request(url.rstrip('/')+'/traffic',headers={'Authorization':secret})
    with urllib.request.urlopen(req, timeout=4) as response:
        raw=response.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:
        raise ValueError('traffic response too large')
    result=json.loads(raw)
    if not isinstance(result,dict): raise ValueError('invalid traffic response')
    return result


def parse_xray(raw):
    obj=json.loads(raw)
    rows=obj.get('stat', obj.get('stats'))
    if rows is None:
        # statsquery 对空统计返回 {}；只允许空对象作为合法空响应。
        if obj == {}: return {}
        raise ValueError('missing stat array')
    if not isinstance(rows,list): raise ValueError('invalid stat array')
    out={}
    for row in rows:
        name=row.get('name','')
        parts=name.split('>>>')
        if len(parts)!=4 or parts[0]!='user' or parts[2]!='traffic': continue
        if parts[3] not in ('uplink','downlink'): continue
        value=row.get('value')
        if isinstance(value,str) and value.isdigit(): value=int(value)
        if type(value) is not int or value<0: raise ValueError('invalid xray counter')
        pair=out.setdefault(parts[1],{'tx':0,'rx':0})
        key='tx' if parts[3]=='downlink' else 'rx'
        if key in pair and pair[key]!=0: raise ValueError('duplicate traffic stat')
        pair[key]=value
    return out


def read_xray(binary, address):
    if not address.startswith('127.0.0.1:'): raise ValueError('xray endpoint must be loopback')
    r=subprocess.run([binary,'api','statsquery','--server='+address,'-pattern','user>>>','-reset=false'],
                     capture_output=True,timeout=5,check=True)
    return parse_xray(r.stdout.decode())


def read_hysteria_online(url, secret):
    p = urlparse(url)
    if p.scheme != 'http' or p.hostname not in ('127.0.0.1', '::1') or p.path not in ('', '/'):
        raise ValueError('stats endpoint must be loopback HTTP base URL')
    req = urllib.request.Request(url.rstrip('/') + '/online', headers={'Authorization': secret})
    with urllib.request.urlopen(req, timeout=4) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('online response too large')
    result = json.loads(raw)
    if not isinstance(result, dict) or any(not isinstance(uid, str) or type(count) is not int or count < 0 for uid, count in result.items()):
        raise ValueError('invalid online counters')
    return result
