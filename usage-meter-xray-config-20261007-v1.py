"""幂等添加仅回环 StatsService；不修改任何 clients 或 Reality 参数。"""
import copy
import pathlib
import json
import os
import shutil
import subprocess
import time

def add_stats(config, port):
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('invalid port')
    cfg=copy.deepcopy(config)
    original_clients=[copy.deepcopy(i.get('settings',{}).get('clients')) for i in cfg.get('inbounds',[])]
    if cfg.get('api') and cfg['api'].get('tag')!='usage-meter-api':
        raise ValueError('existing api requires explicit compatibility review')
    cfg.setdefault('stats',{})
    levels=cfg.setdefault('policy',{}).setdefault('levels',{})
    used={'0'}
    for inbound in cfg.get('inbounds',[]):
        for client in inbound.get('settings',{}).get('clients',[]) or []:used.add(str(client.get('level',0)))
    for level in used:
        levels.setdefault(level,{})['statsUserUplink']=True
        levels[level]['statsUserDownlink']=True
        levels[level]['statsUserOnline']=True
    cfg['api']={'tag':'usage-meter-api','services':['StatsService']}
    add_iplimit_routing(cfg)
    entries=[i for i in cfg.setdefault('inbounds',[]) if i.get('tag')=='usage-meter-api-in']
    desired={'tag':'usage-meter-api-in','listen':'127.0.0.1','port':port,'protocol':'dokodemo-door','settings':{'address':'127.0.0.1'}}
    if entries:
        if len(entries)!=1 or entries[0]!=desired:raise ValueError('api inbound conflict')
    else:
        if any(i.get('port')==port for i in cfg['inbounds']):raise ValueError('port conflict')
        cfg['inbounds'].append(desired)
    rule={'type':'field','inboundTag':['usage-meter-api-in'],'outboundTag':'usage-meter-api'}
    rules=cfg.setdefault('routing',{}).setdefault('rules',[])
    if rule not in rules:rules.insert(0,rule)
    assert original_clients==[i.get('settings',{}).get('clients') for i in cfg['inbounds'][:len(original_clients)]]
    return cfg


IPLIMIT_OUTBOUND_TAG='iplimit-block'


def add_iplimit_routing(cfg):
    """为 Reality 设备上限补齐运行时路由能力：回环 API 加 RoutingService，加一个黑洞出站。

    规则本身由 portal 在运行时用 `xray api adrules -append` 下发，不落盘；
    这里只保证接口和出站存在。黑洞必须排在已有出站之后，否则会变成默认出站。
    """
    api=cfg.get('api') or {}
    if api.get('tag')!='usage-meter-api':raise ValueError('iplimit routing requires usage-meter api')
    services=list(api.get('services') or [])
    if 'RoutingService' not in services:services.append('RoutingService')
    api['services']=services
    outbounds=cfg.setdefault('outbounds',[])
    if not outbounds:outbounds.append({'protocol':'freedom','tag':'direct'})
    if not any(o.get('tag')==IPLIMIT_OUTBOUND_TAG for o in outbounds):
        outbounds.append({'protocol':'blackhole','tag':IPLIMIT_OUTBOUND_TAG})
    return cfg


def iplimit_routing_ready(cfg):
    return ('RoutingService' in ((cfg.get('api') or {}).get('services') or []) and
            any(o.get('tag')==IPLIMIT_OUTBOUND_TAG and o.get('protocol')=='blackhole' for o in cfg.get('outbounds',[])[1:]))


def enable_iplimit_routing(config_path, binary, address):
    """幂等开启设备上限所需的路由接口；预检后替换，重启失败恢复原配置。不触碰 clients。"""
    if not address.startswith('127.0.0.1:'):raise ValueError('stats endpoint must be loopback')
    path=pathlib.Path(config_path)
    original=path.read_bytes()
    config=json.loads(original)
    if iplimit_routing_ready(config):
        return {'enabled':True,'changed':False,'backup':None}
    clients=[copy.deepcopy(i.get('settings',{}).get('clients')) for i in config.get('inbounds',[])]
    add_iplimit_routing(config)
    assert clients==[i.get('settings',{}).get('clients') for i in config.get('inbounds',[])]
    candidate=path.with_name(path.name+'.iplimit-candidate.json')
    backup=path.with_name(path.name+'.before-iplimit-'+str(time.time_ns()))
    replaced=False
    try:
        candidate.write_text(json.dumps(config,indent=2),encoding='utf-8')
        os.chmod(candidate,path.stat().st_mode & 0o777)
        subprocess.run([binary,'run','-test','-c',str(candidate)],capture_output=True,timeout=10,check=True)
        if path.read_bytes()!=original:raise RuntimeError('xray config changed concurrently')
        shutil.copy2(path,backup)
        os.replace(candidate,path);replaced=True
        subprocess.run(['systemctl','restart','xray'],capture_output=True,timeout=15,check=True)
        subprocess.run(['systemctl','is-active','--quiet','xray'],capture_output=True,timeout=5,check=True)
        for _ in range(10):
            probe=subprocess.run([binary,'api','lsrules','--server='+address],capture_output=True,timeout=5)
            if probe.returncode==0:break
            time.sleep(0.5)
        else:raise RuntimeError('routing api unavailable after restart')
    except Exception:
        if replaced:
            candidate.write_bytes(original)
            os.replace(candidate,path)
            subprocess.run(['systemctl','restart','xray'],capture_output=True,timeout=15)
        raise
    finally:
        candidate.unlink(missing_ok=True)
    return {'enabled':True,'changed':True,'backup':str(backup)}


def enable_reality_online(config_path, binary, address):
    """只开启在线统计；校验后替换，失败恢复原配置，不触碰用户或 Reality 参数。"""
    path=pathlib.Path(config_path)
    original=path.read_bytes()
    config=json.loads(original)
    levels=config.setdefault('policy',{}).setdefault('levels',{})
    used={'0'}
    for inbound in config.get('inbounds',[]):
        for client in inbound.get('settings',{}).get('clients',[]) or []:
            used.add(str(client.get('level',0)))
    changed=any(not levels.get(level,{}).get('statsUserOnline',False) for level in used)
    for level in used:levels.setdefault(level,{})['statsUserOnline']=True
    candidate=path.with_name(path.name+'.online-candidate.json')
    backup=path.with_name(path.name+'.before-reality-online-'+str(time.time_ns()))
    if not address.startswith('127.0.0.1:'):raise ValueError('stats endpoint must be loopback')
    if changed:
        candidate.write_text(json.dumps(config,indent=2),encoding='utf-8')
        os.chmod(candidate,path.stat().st_mode & 0o777)
        try:
            subprocess.run([binary,'run','-test','-c',str(candidate)],capture_output=True,timeout=10,check=True)
            shutil.copy2(path,backup)
            os.replace(candidate,path)
            subprocess.run(['systemctl','restart','xray'],capture_output=True,timeout=15,check=True)
            subprocess.run(['systemctl','is-active','--quiet','xray'],capture_output=True,timeout=5,check=True)
            subprocess.run([binary,'api','statsgetallonlineusers','--server='+address],capture_output=True,timeout=5,check=True)
        except Exception:
            if backup.exists():
                candidate.write_bytes(original)
                os.replace(candidate,path)
                subprocess.run(['systemctl','restart','xray'],capture_output=True,timeout=15,check=True)
            raise
        finally:
            candidate.unlink(missing_ok=True)
    else:
        subprocess.run(['systemctl','is-active','--quiet','xray'],capture_output=True,timeout=5,check=True)
        subprocess.run([binary,'api','statsgetallonlineusers','--server='+address],capture_output=True,timeout=5,check=True)
    return {'enabled':True,'changed':changed,'backup':str(backup) if changed else None}
