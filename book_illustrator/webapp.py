"""Loopback-only web UI; workers survive browser closure and persist their progress."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse,unquote
from http.cookies import SimpleCookie
import argparse,secrets,hmac,json,threading,subprocess,sys,os,time,mimetypes,webbrowser,zipfile
from . import app_jobs as jobs
from .core import read_json,write_json
from .backends import TextBackend,request_json

def locked(path):
    from .job_lock import FileLock
    try:
        with FileLock(path):return False
    except BlockingIOError:return True

class Manager:
    def __init__(self):self.guard=threading.Lock();self.children={};self.python=sys.executable
    def status(self,identifier):
        state=jobs.public_project(identifier);path=jobs.project_path(identifier)
        child=self.children.get(identifier)
        if state['state']=='running' and not locked(path/'worker.lock') and (child is None or child.poll() is not None):
            state.update(state='paused',error='The task was interrupted. You can resume from the saved progress.')
            stored=read_json(path/'project.json');stored.update(state='paused',error=state['error']);write_json(path/'project.json',stored)
        return state
    def start(self,identifier,options=None):
        with self.guard:
            if any(c.poll() is None for c in self.children.values()) or locked(jobs.DATA/'worker.lock'):
                raise ValueError('A book is already being illustrated. Pause it before starting another.')
            path=jobs.project_path(identifier);state=read_json(path/'project.json');providers=jobs.settings(True)
            if locked(path/'worker.lock'):raise ValueError('This book is already being illustrated.')
            if (path/'config.json').exists():
                if options is not None and options!=read_json(path/'options.json'):
                    raise ValueError('This edition has already started. Create a new edition to change its settings.')
                config=read_json(path/'config.json')
                for section in ('text','image'):
                    saved=config['text'] if section=='text' else config['images']['scenes']
                    if any(saved[k]!=providers[section][k] for k in ('endpoint','model')):
                        raise ValueError('To resume, reconnect the original model and server, or create a new edition.')
                if state['state']=='complete':return self.status(identifier)
            else:
                if options is None:raise ValueError('Choose chapters and illustration settings first')
                config=jobs.make_config(identifier,options,providers)
                write_json(path/'options.json',options);write_json(path/'config.json',config)
            stop=path/'STOP'
            if stop.exists():stop.unlink()
            env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['BOOK_ILLUSTRATOR_DATA']=str(jobs.DATA);env['DARKBOOKS_DATA']=str(jobs.DATA)
            env['BOOK_ILLUSTRATOR_PROVIDERS']=json.dumps(providers,ensure_ascii=False)
            state.update(state='running',stage='starting');state.pop('error',None);write_json(path/'project.json',state)
            with (path/'worker.log').open('ab') as log:
                command=[self.python,'-u',str(jobs.ROOT/'launch.py'),'--worker',identifier] if (jobs.ROOT/'launch.py').exists() else [self.python,'-u','-m','book_illustrator.app_jobs',identifier]
                child=subprocess.Popen(command,
                    cwd=jobs.ROOT,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.children[identifier]=child;return self.status(identifier)
    def pause(self,identifier):
        path=jobs.project_path(identifier);state=read_json(path/'project.json')
        if state['state']=='running':(path/'STOP').touch()
        return {'requested':True,'message':'The current step will finish and save before pausing.'}

def make_server(port=8765):
    manager=Manager();token=secrets.token_hex(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def authenticated(self):
            if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
                return False
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):return False
            supplied=self.headers.get('X-App-Token','')
            if self.command=='GET' and not supplied:
                cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));cookie_name='session'+str(self.server.server_port)
                supplied=cookie[cookie_name].value if cookie_name in cookie else ''
            return hmac.compare_digest(supplied,token)
        def send(self,data,status=200,content_type='application/json; charset=utf-8'):
            payload=json.dumps(data,ensure_ascii=False).encode() if not isinstance(data,bytes) else data
            self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(payload)
        def file(self,path,download=False):
            path=Path(path)
            if not path.is_file():return self.send({'error':'File not available'},404)
            self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(path.stat().st_size));self.send_header('X-Content-Type-Options','nosniff')
            if download:self.send_header('Content-Disposition','attachment; filename="'+(download if isinstance(download,str) else 'book.fb2')+'"')
            self.end_headers()
            with path.open('rb') as handle:
                while chunk:=handle.read(1024*1024):self.wfile.write(chunk)
        def do_GET(self):
            try:
                route=urlparse(self.path).path
                if route=='/':
                    if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):return self.send({'error':'Forbidden'},403)
                    payload=(Path(__file__).parent/'web/index.html').read_text(encoding='utf-8').replace('__APP_TOKEN__',token).encode()
                    self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(payload)))
                    self.send_header('Set-Cookie',f'session{self.server.server_port}={token}; HttpOnly; SameSite=Strict; Path=/')
                    self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; frame-ancestors 'none'")
                    self.end_headers();self.wfile.write(payload);return
                if route.startswith('/assets/'):
                    name=route.removeprefix('/assets/')
                    if name not in {'app.js','ui.css','strings.json','mark.svg'}:return self.send({'error':'Not found'},404)
                    return self.file(Path(__file__).parent/'web'/name)
                if not self.authenticated():return self.send({'error':'Forbidden'},403)
                if route=='/api/settings':return self.send(jobs.settings())
                if route=='/api/projects':return self.send([manager.status(p['id']) for p in jobs.projects()])
                parts=route.strip('/').split('/')
                if len(parts)>=3 and parts[:2]==['api','projects']:
                    identifier=parts[2];path=jobs.project_path(identifier)
                    if len(parts)==3:return self.send(manager.status(identifier))
                    if parts[3:] == ['book']:return self.file(path/'result/book.fb2',True)
                    if parts[3:]==['archive']:
                        records=read_json(path/'run/renders-scenes.json')['images']
                        from .core import fingerprint
                        archive=path/'result'/('images-'+fingerprint(records)[:12]+'.zip');archive.parent.mkdir(exist_ok=True)
                        with manager.guard:
                            if not archive.exists():
                                temporary=archive.with_suffix('.zip.tmp')
                                with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_STORED) as bundle:
                                    for r in records:bundle.write(r['path'],r['scene_id']+'.png')
                                temporary.replace(archive)
                        return self.file(archive,'images.zip')
                    if len(parts)==5 and parts[3]=='images':
                        records=read_json(path/'run/renders-scenes.json')['images'];record=next(r for r in records if r['scene_id']==parts[4])
                        return self.file(record['path'])
                self.send({'error':'Not found'},404)
            except (ValueError,KeyError,FileNotFoundError,StopIteration):self.send({'error':'Project or file not found'},404)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_POST(self):
            if not self.authenticated():return self.send({'error':'Forbidden'},403)
            try:
                route=urlparse(self.path).path
                length=int(self.headers.get('Content-Length','0'))
                if length<0:raise ValueError('Invalid content length')
                body=self.rfile.read(length)
                if route=='/api/upload':return self.send(jobs.upload(body,unquote(self.headers.get('X-Filename','book.fb2'))))
                data=json.loads(body or b'{}')
                if route=='/api/sample':return self.send(jobs.upload((jobs.RESOURCES/'examples/lantern-keeper.fb2').read_bytes(),'the-lantern-keeper.fb2'))
                if route=='/api/settings':return self.send(jobs.save_settings(data))
                if route=='/api/check':
                    setting=jobs.settings(True);section=data['section'];config=setting[section]
                    if section=='text':
                        # Use a per-call backend key without putting it into global server environment.
                        backend=TextBackend(config);backend.headers={'Authorization':'Bearer '+config['api_key']} if config.get('api_key') else {}
                        result=backend.models();return self.send({'models':[m['id'] for m in result.get('data',[])],'message':'Connected to your text model server.'})
                    if config['backend']=='comfyui':
                        stats=request_json(config['endpoint'].rstrip('/')+'/system_stats',timeout=5)
                        nodes=request_json(config['endpoint'].rstrip('/')+'/object_info',timeout=30)
                        def choices(node,key):
                            value=nodes.get(node,{}).get('input',{}).get('required',{}).get(key,[[]])[0]
                            return value if isinstance(value,list) else []
                        return self.send({'message':'Connected to ComfyUI.','models':choices('UNETLoader','unet_name'),
                            'encoders':choices('CLIPLoader','clip_name'),'vaes':choices('VAELoader','vae_name'),
                            'qwen_node': 'TextEncodeQwenImage21' in nodes})
                    return self.send({'message':'Connection settings saved. Try a sample chapter to check image generation.'})
                parts=route.strip('/').split('/')
                if len(parts)==4 and parts[:2]==['api','projects']:
                    identifier=parts[2];path=jobs.project_path(identifier)
                    if parts[3]=='clone':
                        clone=jobs.upload((path/'book.fb2').read_bytes(),read_json(path/'project.json')['filename'])
                        clone['trial_of']=identifier;write_json(jobs.project_path(clone['id'])/'project.json',clone)
                        return self.send(clone)
                    if parts[3]=='start':return self.send(manager.start(identifier,data.get('options')))
                    if parts[3]=='pause':return self.send(manager.pause(identifier))
                    if parts[3]=='workflow':
                        if (path/'config.json').exists():raise ValueError('Workflow can be changed only before first start')
                        workflow=data['workflow'];bindings=data['bindings']
                        if not isinstance(workflow,dict) or 'nodes' in workflow:raise ValueError('Use a ComfyUI API-format workflow, not the editor format')
                        write_json(path/'custom-workflow.json',workflow);write_json(path/'workflow-bindings.json',bindings)
                        return self.send({'saved':True})
                self.send({'error':'Not found'},404)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as error:
                message=str(error)
                for section in ('text','image'):
                    key=jobs.settings(True)[section].get('api_key','')
                    if key:message=message.replace(key,'[redacted]')
                self.send({'error':message},400)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.manager=manager;server.app_token=token
    return server

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    try:server=make_server(args.port)
    except OSError:
        if args.port==0:raise
        server=make_server(0)
    url=f'http://127.0.0.1:{server.server_port}/';print('DarkBooks: '+url,flush=True)
    if not args.no_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
