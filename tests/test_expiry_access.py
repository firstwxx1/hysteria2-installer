import sys,unittest,json,tempfile,pathlib,subprocess,ast,threading,re
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]));import portal
class Resp:
 def __init__(self,obj):self.obj=obj
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def read(self):return json.dumps(self.obj).encode()
class ExpiryTests(unittest.TestCase):
 def test_deadline_status_quota(self):
  good={'status':'active','expires_at':200,'limit_bytes':100,'used_bytes':99}
  self.assertTrue(portal.user_access_allowed(good,100))
  for diff in [{'expires_at':100},{'expires_at':99},{'status':'disabled'},{'used_bytes':100}]:self.assertFalse(portal.user_access_allowed(dict(good,**diff),100))
 def test_reality_filters_expired_disabled_and_exhausted(self):
  d={'users':{'expired':{'status':'active','expires_at':1},'active':{'expires_at':2085974400},'disabled':{'status':'disabled','expires_at':2085974400},'quota':{'expires_at':2085974400,'limit_bytes':1,'used_bytes':1}}}
  self.assertEqual([x['email'] for x in portal.reality_clients(d)],['active'])
 def test_kick_and_confirm_all_sessions(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'usage-meter-config.json').write_text(json.dumps({'hysteria':{'url':'http://127.0.0.1:19996','secret':'fixture'}}))
   with patch.object(portal.urllib.request,'urlopen',side_effect=[Resp({'expired':2}),Resp({}),Resp({'expired':1}),Resp({})]),patch.object(portal.subprocess,'run') as run,patch.object(portal.time,'sleep'):
    self.assertTrue(portal.enforce_node_access(p,{'users':{'expired':{'expires_at':1}}}))
   self.assertTrue(any(x.args[0]==['systemctl','restart','hysteria-server'] for x in run.call_args_list))
 def test_failed_kick_never_acknowledges(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'usage-meter-config.json').write_text(json.dumps({'hysteria':{'url':'http://127.0.0.1:19996','secret':'fixture'}}))
   with patch.object(portal.urllib.request,'urlopen',side_effect=OSError('offline')):
    with self.assertRaises(OSError):portal.enforce_node_access(p,{'users':{}},['deleted'])
 def test_delete_failure_returns_retryable_503(self):
  source=pathlib.Path(portal.__file__).read_text(encoding='utf-8');tree=ast.parse(source)
  branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and isinstance(n.test,ast.Compare) and any(isinstance(v,ast.Constant) and v.value=='users/delete' for v in n.test.comparators))
  f=ast.parse('def delete(self,params):\n pass').body[0];f.body=branch.body
  state={'users':{'probe':{'status':'active'}}};env={'data':state,'data_lock':threading.RLock(),'regenerate_page':lambda:None,'reality_registry':portal.reality_registry,'VALID_USER_ID_RE':re.compile('^[a-z]+$'),'MASTER_USER_ID':'admin_master','ip_tracker':{},'apply_access':lambda ids:False}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[f],type_ignores=[])),'branch','exec'),env)
  class Fake:
   def reply_json(self,status,body):return status,body
  self.assertEqual(env['delete'](Fake(),{'user_id':'probe'})[0],503)
  self.assertEqual(state['_pending_revocations'],['probe'])
if __name__=='__main__':unittest.main()
