import unittest,json,importlib.util,threading
from http.server import HTTPServer,BaseHTTPRequestHandler
from unittest.mock import patch
from pathlib import Path
s=importlib.util.spec_from_file_location('live',Path(__file__).resolve().parents[1] / 'usage-meter-readers-20261007-v1.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def row(event,addr='203.0.113.8:1234',uid='user',time=1,binary=False):
 msg='2026-10-12T00:00:00Z\t\x1b[34mINFO\x1b[0m\t'+event+'\t'+json.dumps({'addr':addr,'id':uid})
 return {'MESSAGE':list(msg.encode()) if binary else msg,'__MONOTONIC_TIMESTAMP':str(time)}
class LiveTests(unittest.TestCase):
 def test_authoritative_core_without_ssh_or_journal(self):
  class Handler(BaseHTTPRequestHandler):
   def do_GET(self):
    self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'revision':'teyir-live-ips-v1','users':{'user':{'connections':2,'ips':['203.0.113.8']}}}).encode())
   def log_message(self,*args):pass
  server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  cfg={'hysteria':{'url':'http://127.0.0.1:'+str(server.server_port),'secret':'test-secret'}}
  def read(path,*args,**kwargs):return json.dumps(cfg if path.name=='usage-meter-config.json' else {'users':{'user':{},'offline':{}}})
  try:
   with patch.object(m.pathlib.Path,'read_text',read),patch.object(m,'cmd',side_effect=AssertionError('must not use journal for authoritative API')):
    result=m.read_live_connections()['users']
   self.assertTrue(result['user']['hysteria_ok']);self.assertEqual(result['user']['hysteria_count'],2)
   self.assertEqual(result['user']['ips'][0]['ip'],'203.0.113.8');self.assertTrue(result['offline']['hysteria_ok']);self.assertEqual(result['offline']['ips'],[])
  finally:server.shutdown();server.server_close();thread.join()
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
