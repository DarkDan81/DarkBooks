import unittest,tempfile,threading,json,time,base64,urllib.request,urllib.error,os
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from unittest.mock import patch
from book_illustrator import app_jobs
from book_illustrator.webapp import make_server
from book_illustrator.core import read_json,write_json,Book
from book_illustrator.cli import test_card
from test_pipeline import fixture

class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.data_patch=patch.object(app_jobs,'DATA',self.folder/'data');self.data_patch.start()
        self.root_patch=None
        if os.environ.get('BOOK_TEST_PORTABLE_ROOT'):
            self.root_patch=patch.object(app_jobs,'ROOT',Path(os.environ['BOOK_TEST_PORTABLE_ROOT']));self.root_patch.start()
        self.server=make_server(0)
        if self.root_patch:self.server.manager.python=str(app_jobs.ROOT/'runtime/python/python.exe')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}';self.token=self.server.app_token
        fixture(self.folder/'book.fb2')
    def tearDown(self):
        for child in self.server.manager.children.values():
            if child.poll() is None:child.wait(timeout=15)
        self.server.shutdown();self.server.server_close();self.thread.join();self.data_patch.stop()
        if self.root_patch:self.root_patch.stop()
        self.temp.cleanup()
    def request(self,path,data=None,raw=None,headers=None):
        body=raw if raw is not None else json.dumps(data).encode() if data is not None else None
        req=urllib.request.Request(self.url+path,body,headers={'X-App-Token':self.token,**(headers or {})})
        try:response=urllib.request.urlopen(req,timeout=15)
        except urllib.error.HTTPError as error:
            error.read();error.close();raise
        with response:
            payload=response.read()
            return json.loads(payload) if response.headers['Content-Type'].startswith('application/json') else payload
    def test_auth_secrets_and_untrusted_book(self):
        with self.assertRaises(urllib.error.HTTPError) as blocked:urllib.request.urlopen(self.url+'/api/settings')
        self.assertEqual(blocked.exception.code,403)
        blocked.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as blocked:
            self.request('/api/settings',{},headers={'Origin':'https://untrusted.example'})
        self.assertEqual(blocked.exception.code,403)
        settings=app_jobs.defaults();settings['text']['api_key']='private-key';self.request('/api/settings',settings)
        self.assertNotIn('private-key',json.dumps(self.request('/api/settings')))
        public=self.request('/api/settings');self.assertTrue(public['text']['has_key'])
        self.request('/api/settings',public);self.assertEqual(app_jobs.settings(True)['text']['api_key'],'private-key')
        public['text']['clear_key']=True;self.request('/api/settings',public);self.assertFalse(app_jobs.settings(True)['text']['api_key'])
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/upload',raw=b'<!DOCTYPE x><x/>')
        project=self.request('/api/upload',raw=(self.folder/'book.fb2').read_bytes(),headers={'X-Filename':'../../secret.fb2'})
        self.assertEqual(project['filename'],'secret.fb2')
        self.assertEqual((app_jobs.project_path(project['id'])/'book.fb2').read_bytes(),(self.folder/'book.fb2').read_bytes())
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/projects/not-a-project')

    def test_real_worker_pause_resume_and_export_via_fake_model_apis(self):
        self.integration('compatible_images')

    def test_comfy_worker_pause_resume_and_export(self):
        self.integration('comfyui')

    def integration(self,backend):
        card=self.folder/'card.png';test_card(card);png=card.read_bytes()
        calls={'images':0,'text':0};pause={'project':None,'enabled':True}
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(handler):
                if handler.path.endswith('/object_info'):
                    workflow=read_json(app_jobs.RESOURCES/'workflows/qwen21-0-reference-api.json');result={n['class_type']:{} for n in workflow.values()}
                elif '/history/' in handler.path:
                    job=handler.path.rsplit('/',1)[-1];result={job:{'outputs':{'9':{'images':[{'filename':'image.png'}]}},'status':{'completed':True}}}
                else:
                    handler.send_response(200);handler.send_header('Content-Length',str(len(png)));handler.end_headers();handler.wfile.write(png);return
                data=json.dumps(result).encode();handler.send_response(200);handler.send_header('Content-Length',str(len(data)));handler.end_headers();handler.wfile.write(data)
            def do_POST(handler):
                body=json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
                if handler.path.endswith(('/images/generations','/prompt')):
                    calls['images']+=1;result={'prompt_id':'job-'+str(calls['images'])} if handler.path.endswith('/prompt') else {'data':[{'b64_json':base64.b64encode(png).decode()}]}
                    if pause['enabled']:
                        (app_jobs.project_path(pause['project'])/'STOP').touch();pause['enabled']=False
                else:
                    calls['text']+=1;payload=json.loads(body['messages'][1]['content'])
                    if 'paragraphs' in payload:
                        paragraphs=payload['paragraphs'];answer={'characters':[],'episodes':[
                            {'start_paragraph_id':p['id'],'end_paragraph_id':p['id'],'title':'Room '+str(i),
                             'summary':'Visible room '+str(i),'kind':'environment','characters':[],
                             'visible_moment':5,'visual_interest':5,'narrative_importance':4,'novelty':5}
                            for i,p in enumerate(paragraphs)]}
                    elif 'episodes' in payload:
                        answer={'scenes':[{'episode_id':e['episode_id'],'description':'A room','image_prompt':'An empty room in ink','characters':[]} for e in payload['episodes']]}
                    elif 'scenes' in payload:answer={'issues':[]}
                    else:answer={'characters':[]}
                    result={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
                            'usage':{'prompt_tokens':200,'completion_tokens':200}}
                data=json.dumps(result).encode();handler.send_response(200);handler.send_header('Content-Length',str(len(data)));handler.end_headers();handler.wfile.write(data)
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider);thread=threading.Thread(target=provider.serve_forever,daemon=True);thread.start()
        try:
            endpoint=f'http://127.0.0.1:{provider.server_port}/v1';settings=app_jobs.defaults()
            settings['text'].update(endpoint=endpoint,model='test-text',api_key='test-secret')
            settings['image'].update(endpoint=endpoint,model='test-image',backend=backend,api_key='image-secret')
            self.request('/api/settings',settings)
            project=self.request('/api/upload',raw=(self.folder/'book.fb2').read_bytes());identifier=project['id'];pause['project']=identifier
            chapter=next(c for c in Book(self.folder/'book.fb2').chapters if len(c['paragraphs'])>=2)
            options={'chapter_ids':[chapter['id']],'illustrations':2,'setting':'A fictional city','style':'Ink','narrator':'','seed':42}
            self.request(f'/api/projects/{identifier}/start',{'options':options})
            def wait_for(state):
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    current=self.request(f'/api/projects/{identifier}')
                    if current['state']==state:return current
                    if current['state']=='failed':self.fail(current.get('error','failed')+'\n'+current.get('log',''))
                    time.sleep(.1)
                self.fail(str(current))
            paused=wait_for('paused');self.assertEqual(paused['rendered'],1);text_calls=calls['text']
            self.server.manager.children[identifier].wait(timeout=10)
            self.request(f'/api/projects/{identifier}/start',{})
            done=wait_for('complete');self.assertEqual(done['rendered'],2)
            self.assertEqual(calls['images'],2);self.assertEqual(calls['text'],text_calls)
            book=self.request(f'/api/projects/{identifier}/book');self.assertIn(b'FictionBook',book)
            verification=read_json(app_jobs.project_path(identifier)/'result/verification.json')
            self.assertTrue(verification['output_xsd']['valid']);self.assertTrue(verification['text_and_original_links_preserved'])
            for path in (app_jobs.project_path(identifier)).rglob('*.json'):
                self.assertNotIn('test-secret',path.read_text(encoding='utf-8'))
                self.assertNotIn('image-secret',path.read_text(encoding='utf-8'))
            self.assertEqual(self.request(f'/api/projects/{identifier}/archive')[:2],b'PK')
        finally:provider.shutdown();provider.server_close();thread.join()
