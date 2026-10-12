"""门户计量运行时：真实累计同步到 used_bytes；旧值另存 legacy_auth_value，不混入新窗口。"""
import copy
import importlib.util
import json
import pathlib
import threading
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent

def load(name, filename):
    spec=importlib.util.spec_from_file_location(name, ROOT / filename)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

core=load('usage_meter_core', 'usage-meter-core-20261007-v1.py')
readers=load('usage_meter_readers', 'usage-meter-readers-20261007-v1.py')

class PortalMeter:
    def __init__(self, data, lock, save, config, read=None, epoch=None, on_speed=None):
        self.data, self.lock, self.save, self.config=data,lock,save,config
        self.read=read or self._read
        self.epoch=epoch or readers.service_epoch
        self.stop=threading.Event()
        # 速率采样回调（2026-10-08）：on_speed(uid, tx, rx, elapsed, source)
        # 由 portal.py 注入，把本轮真实字节增量写进 speed_tracker。
        # 之前 speed_tracker 没有写入侧，/traffic-speed 长期返回 0。
        # 参数是单用户增量、实际区间及来源；不是整个 deltas 字典。
        # 每个来源每个用户各调一次。
        self.on_speed=on_speed
        self.sample_times={}

    def enabled_sources(self):
        sources=[s for s in ("hysteria", "reality") if self.config.get(s)]
        # 默认安装会写 Reality 配置，但未安装 xray 时不应阻塞 Hy2 计量。
        cfg=self.config.get("reality", {})
        if self.read == self._read and "reality" in sources and not pathlib.Path(cfg.get("binary", "")).is_file():
            sources.remove("reality")
        return sources

    def _read(self, source, cfg):
        if source=='hysteria': return readers.read_hysteria(cfg['url'],cfg['secret'])
        return readers.read_xray(cfg['binary'],cfg['address'])

    def poll(self):
        for source in self.enabled_sources():
            cfg=self.config.get(source)
            now=datetime.now(timezone.utc).isoformat()
            try:
                if not cfg: raise RuntimeError('source_not_configured')
                service='hysteria-server' if source=='hysteria' else 'xray'
                before=self.epoch(service)
                counters=self.read(source,cfg)
                if self.epoch(service)!=before: raise RuntimeError('service_changed_during_read')
                with self.lock:
                    meter=copy.deepcopy(self.data.get('usage_meter',{}))
                    # 每个账户首次基线：不将已存在、未归属新窗口的进程计数混入。
                    registered=meter.setdefault('registered_users', list(self.data.get('users',{})))
                    # 已存在客户在后续才第一次出现在计数器中时，core 从零累加是有效的：
                    # 当前服务为所有用户保持单调累计，且初始窗口无计数表示0。
                    outcome = core.apply_sample(meter,source,before,counters,now)
                    meter.setdefault('health',{})[source]={'ok':True,'sampled_at':now,'error':''}
                    meter.setdefault('started_at',now)
                    old=self.data.get('usage_meter')
                    old_users=copy.deepcopy(self.data.get('users', {}))
                    for uid, user in self.data.get('users', {}).items():
                        user.setdefault('legacy_auth_value', user.get('used_bytes', 0))
                        user['used_bytes']=core.total_bytes(meter, uid)
                    self.data['usage_meter']=meter
                    try: self.save()
                    except Exception:
                        self.data['users']=old_users
                        if old is None: self.data.pop('usage_meter',None)
                        else: self.data['usage_meter']=old
                        raise
                    sampled=time.monotonic()
                    previous=self.sample_times.get(source)
                    self.sample_times[source]=sampled
                    if self.on_speed is not None and previous is not None and sampled > previous:
                        for uid, pair in outcome.get('deltas', {}).items():
                            try:
                                self.on_speed(uid, pair[0], pair[1], sampled-previous, source)
                            except Exception:
                                pass
            except Exception as exc:
                with self.lock:
                    old=copy.deepcopy(self.data.get('usage_meter'))
                    self.data.setdefault('usage_meter',{}).setdefault('health',{})[source]={
                        'ok':False,'sampled_at':now,'error':type(exc).__name__}
                    try: self.save()
                    except Exception:
                        if old is None: self.data.pop('usage_meter',None)
                        else: self.data['usage_meter']=old

    def summary(self, uid):
        with self.lock:
            m=self.data.get('usage_meter',{})
            health=m.get('health',{})
            enabled=self.enabled_sources()
            fresh=bool(enabled) and all(health.get(s,{}).get('ok',False) and
                time.time()-datetime.fromisoformat(health[s]['sampled_at']).timestamp()<30
                for s in enabled)
            initialized=bool(enabled) and all(s in m.get('sources',{}) for s in enabled)
            return {'measured_bytes':core.total_bytes(m,uid) if initialized else None,
                'measurement_started_at':m.get('started_at'),
                'measurement_ok':initialized and fresh and not m.get('gaps'),
                'measurement_scope':'since_measurement_started',
                'measurement_sources':copy.deepcopy(health),
                'measurement_gaps':copy.deepcopy(m.get('gaps',[])),
                'legacy_auth_value':self.data.get('users',{}).get(uid,{}).get('legacy_auth_value', self.data.get('users',{}).get(uid,{}).get('used_bytes',0))}

    def run(self):
        while not self.stop.is_set():
            self.poll(); self.stop.wait(5)

    def start(self):
        thread=threading.Thread(target=self.run,name='portal-real-usage-meter',daemon=True)
        thread.start(); return thread


def from_file(data,lock,save,path,on_speed=None):
    config=json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    return PortalMeter(data,lock,save,config,on_speed=on_speed)
