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
