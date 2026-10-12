import unittest,json,importlib.util
from pathlib import Path
s=importlib.util.spec_from_file_location('live',Path(__file__).resolve().parents[1] / 'usage-meter-readers-20261007-v1.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def row(event,addr='203.0.113.8:1234',uid='user',time=1,binary=False):
 msg='2026-10-12T00:00:00Z\t\x1b[34mINFO\x1b[0m\t'+event+'\t'+json.dumps({'addr':addr,'id':uid})
 return {'MESSAGE':list(msg.encode()) if binary else msg,'__MONOTONIC_TIMESTAMP':str(time)}
class LiveTests(unittest.TestCase):
 def test_long_connection_does_not_expire(self):self.assertEqual(m.parse_events([row('client connected')]),{('user','203.0.113.8:1234'):'203.0.113.8'})
 def test_core_zero_removes_ghost(self):self.assertEqual(m.confirmed_ips({('user','203.0.113.8:1'):'203.0.113.8'},'user',0),[])
 def test_count_mismatch_unknown(self):self.assertIsNone(m.confirmed_ips({},'user',1))
 def test_deduplicate_same_ip(self):self.assertEqual(m.confirmed_ips({('user','a'):'203.0.113.8',('user','b'):'203.0.113.8'},'user',2),['203.0.113.8'])
 def test_disconnect_removes(self):self.assertEqual(m.parse_events([row('client connected'),row('client disconnected',time=2)]),{})
 def test_reversed_binary_journal(self):self.assertEqual(m.parse_events([row('client disconnected',time=2,binary=True),row('client connected',binary=True)]),{})
 def test_same_ip_multiple_instances(self):self.assertEqual(len(m.parse_events([row('client connected'),row('client connected',addr='203.0.113.8:5678')])),2)
 def test_ipv6(self):self.assertEqual(list(m.parse_events([row('client connected',addr='[2001:db8::1]:1234')]).values()),['2001:db8::1'])
 def test_invalid_rejected(self):
  with self.assertRaises(ValueError):m.parse_events([row('client connected',addr='not-ip:1')])
 def test_users_isolated(self):self.assertEqual(len(m.parse_events([row('client connected'),row('client connected',uid='other')])),2)
 def test_structured_logs(self):self.assertEqual(len(m.parse_events([{'MESSAGE':json.dumps({'msg':'client connected','id':'u','addr':'203.0.113.7:1'})}])),1)
if __name__=='__main__':unittest.main()
