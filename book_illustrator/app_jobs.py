"""Portable project storage and subprocess worker, independent of any particular book."""
from pathlib import Path
import os,uuid,subprocess,sys,time,json,shutil
from urllib.parse import urlparse
from .core import Book,read_json,write_json

ROOT=Path(__file__).resolve().parents[1]
RESOURCES=Path(__file__).resolve().parent/'resources'
DEFAULT_DATA=Path.home()/'DarkBooks' if ROOT.name in ('site-packages','dist-packages') else ROOT/'data'
DATA=Path(os.environ.get('DARKBOOKS_DATA',os.environ.get('BOOK_ILLUSTRATOR_DATA',str(DEFAULT_DATA)))).resolve()
DEFAULT_STYLE='Black and white book illustration, expressive ink drawing and restrained cross-hatching, readable silhouettes, light paper background. No panels or text.'

def defaults():
    return {'text':{'endpoint':'http://127.0.0.1:1234/v1','model':'','api_key':'',
                    'context_length':32768,'max_tokens':5000,'structured_output':'json_schema','manage_lms':False,'load_key':'qwen3.5-9b'},
            'image':{'backend':'comfyui','endpoint':'http://127.0.0.1:8188','model':'qwen_image_2.1_turbo_int8_convrot.safetensors',
                     'encoder':'qwen3vl_8b_w4a8.safetensors','vae':'qwen_image_2.1_vae_bf16.safetensors','api_key':'','size':'1024x1024'},
            'style':DEFAULT_STYLE,'setting':''}

def settings(private=False):
    path=DATA/'settings.json';value=read_json(path) if path.exists() else defaults()
    if not private:
        for name in ('text','image'):
            value[name]['has_key']=bool(value[name].get('api_key'));value[name].pop('api_key',None)
    return value

def save_settings(value):
    previous=settings(True);clean=defaults()
    for section in ('text','image'):
        for key in clean[section]:
            if key in value.get(section,{}):clean[section][key]=value[section][key]
        if not value.get(section,{}).get('api_key') and not value.get(section,{}).get('clear_key'):
            clean[section]['api_key']=previous[section].get('api_key','')
        parsed=urlparse(clean[section]['endpoint'])
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username:
            raise ValueError('Use an HTTP(S) model server URL without credentials in the URL')
    if clean['image']['backend'] not in ('comfyui','compatible_images'):raise ValueError('Unknown image provider')
    if clean['text']['structured_output'] not in ('json_schema','json_object'):raise ValueError('Unknown structured output mode')
    for key in ('context_length','max_tokens'):
        clean['text'][key]=int(clean['text'][key])
        if clean['text'][key]<=0:raise ValueError('Token budgets must be positive')
    for key in ('style','setting'):clean[key]=str(value.get(key,previous.get(key,'')))
    write_json(DATA/'settings.json',clean)
    return settings()

def project_path(identifier):
    if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('Invalid project ID')
    return DATA/'projects'/identifier

def upload(payload,filename):
    identifier=uuid.uuid4().hex;path=project_path(identifier);path.mkdir(parents=True)
    (path/'book.fb2').write_bytes(payload)
    try:
        book=Book(path/'book.fb2');inventory=book.inventory()
        chapters=[{k:v for k,v in c.items() if k!='paragraphs'} for c in inventory['chapters']]
        state={'id':identifier,'filename':Path(filename.replace('\\','/')).name,'title':inventory.get('title',''),
               'state':'draft','created_at':time.time(),'source_sha256':book.sha,'chapters':chapters}
        write_json(path/'project.json',state);return state
    except Exception:
        (path/'book.fb2').unlink();path.rmdir();raise

def projects():
    return sorted([read_json(p) for p in (DATA/'projects').glob('*/project.json')],key=lambda p:p['created_at'],reverse=True)

def public_project(identifier):
    path=project_path(identifier);state=read_json(path/'project.json')
    plan=path/'run/scenes.json';renders=path/'run/renders-scenes.json'
    scenes=read_json(plan)['scenes'] if plan.exists() else []
    images=read_json(renders)['images'] if renders.exists() else []
    state.update(scene_count=len(scenes),rendered=len(images),
        analyzed_chapters=len(list((path/'run/chapters').glob('*/analysis-reviewed.json'))),
        gallery=[{'id':r['scene_id'],'url':f'/api/projects/{identifier}/images/{r["scene_id"]}'} for r in images],
        book_ready=(path/'result/book.fb2').exists(),options=read_json(path/'options.json') if (path/'options.json').exists() else None)
    log=path/'worker.log'
    if log.exists():
        with log.open('rb') as handle:
            handle.seek(max(0,log.stat().st_size-16000));state['log']=handle.read().decode('utf-8',errors='replace')
    return state

def make_config(identifier,options,provider_settings):
    path=project_path(identifier);book=Book(path/'book.fb2');chosen=options['chapter_ids']
    if not chosen or len(set(chosen))!=len(chosen) or set(chosen)-{c['id'] for c in book.chapters}:raise ValueError('Select existing chapters')
    count=int(options.get('illustrations',5))
    if count<1:raise ValueError('Illustration target must be positive')
    if not provider_settings['text']['model'].strip():raise ValueError('Choose a text model in Connections first.')
    if not provider_settings['image']['model'].strip():raise ValueError('Choose an illustration model in Connections first.')
    text={k:provider_settings['text'][k] for k in ('endpoint','model','context_length','max_tokens','structured_output')}
    text.update(backend='openai_compatible',api_key_env='BOOK_TEXT_API_KEY',temperature=.1,
                allow_json_in_reasoning_channel=True,timeout_seconds=600)
    image={k:provider_settings['image'][k] for k in ('backend','endpoint','model','size')}
    image.update(api_key_env='BOOK_IMAGE_API_KEY',conditioning='text_only',require_character_references=False,timeout_seconds=1800)
    if image['backend']=='comfyui':
        workflow=read_json(path/'custom-workflow.json' if (path/'custom-workflow.json').exists() else RESOURCES/'workflows/qwen21-0-reference-api.json')
        if (path/'custom-workflow.json').exists():
            bindings=read_json(path/'workflow-bindings.json');image.update(bindings=bindings['bindings'],output_node=str(bindings['output_node']))
        else:
            workflow['1']['inputs']['unet_name']=image['model'];workflow['2']['inputs']['clip_name']=provider_settings['image']['encoder']
            workflow['4']['inputs']['vae_name']=provider_settings['image']['vae']
            image.update(bindings={'references':[],'prompt':['6','prompt'],'seed':['3','seed']},output_node='9')
        write_json(path/'workflow.json',workflow);image['workflow']=str(path/'workflow.json')
    write_json(path/'setting.json',{'prompt':options.get('setting',''),'translation_glossary':[]})
    narrator=options.get('narrator','').strip()
    config={'book':str(path/'book.fb2'),'run_directory':str(path/'run'),'resumable':True,'text':text,'images':{'scenes':image},
        'pilot':{'chapter_ids':chosen,'illustrations_per_chapter':count,'max_chunk_chars':5000,'seed':int(options.get('seed',42841)),
                 'allow_partial_evidence':True,'scene_selection':'distributed'},
        'style':options.get('style',DEFAULT_STYLE),'setting_profile':str(path/'setting.json'),
        'identity_aliases':{'Я':narrator,'I':narrator} if narrator else {},
        'resolve_unique_short_names':True,'named_character_references_only':True,'scene_identity_policy':'batch_intrinsic',
        'stable_character_references':True,'complete_portrait_identity':False}
    return config

def worker(identifier):
    from .pipeline import Pipeline
    from .production import ProductionRunner
    from .control import check_pause,PauseRequested
    from .finishing import finish_grayscale,xsd_report
    path=project_path(identifier);state=read_json(path/'project.json');config=read_json(path/'config.json')
    providers=json.loads(os.environ.get('BOOK_ILLUSTRATOR_PROVIDERS','null')) or settings(True);os.environ['BOOK_ILLUSTRATOR_STOP_FILE']=str(path/'STOP')
    for section,name in [('text','BOOK_TEXT_API_KEY'),('image','BOOK_IMAGE_API_KEY')]:os.environ[name]=providers[section].get('api_key','')
    def stage(name):
        state.update(state='running',stage=name,updated_at=time.time());state.pop('error',None);write_json(path/'project.json',state)
    p=Pipeline(ROOT,path/'config.json');owned_lms=False
    try:
        check_pause()
        if not (p.run/'scenes.json').exists():
            stage('text')
            if providers['text'].get('manage_lms'):
                if urlparse(config['text']['endpoint']).hostname not in ('127.0.0.1','localhost'):raise ValueError('LM Studio automatic loading requires a local server.')
                port=urlparse(config['text']['endpoint']).port or 1234
                subprocess.run(['lms','server','start','--port',str(port)],check=True)
                subprocess.run(['lms','load',providers['text']['load_key'],'--identifier',config['text']['model'],
                                '--context-length',str(config['text']['context_length']),'--parallel','1','--gpu','max','-y'],check=True)
                owned_lms=True
            ProductionRunner(p).analyze()
        if owned_lms:
            subprocess.run(['lms','unload',config['text']['model']],check=True);owned_lms=False
        check_pause();stage('images');images=p.render('scenes')
        check_pause();stage('export');finished=[]
        for image in images:
            check_pause();destination=path/'result/images'/(image['scene_id']+'.png')
            finish_grayscale(image['path'],destination)
            finished.append({**image,'path':str(destination)})
        output=path/'result/book.fb2'
        verification=p.book.export(finished,output,normalize_publisher_headers=True)
        verification['output_xsd']=xsd_report(output,RESOURCES/'schemas/FictionBook.xsd')
        if not verification['output_xsd']['valid'] or not verification['text_and_original_links_preserved']:raise ValueError('FB2 validation failed')
        write_json(path/'result/verification.json',verification)
        state.update(state='complete',stage='complete',finished_at=time.time());write_json(path/'project.json',state)
    except Exception as error:
        # Key material never enters a browser response or persisted error.
        message=str(error)
        for section in ('text','image'):
            key=providers[section].get('api_key','')
            if key:message=message.replace(key,'[redacted]')
        state.update(state='paused' if isinstance(error,PauseRequested) or (path/'STOP').exists() else 'failed')
        if isinstance(error,PauseRequested):state.pop('error',None)
        else:state['error']=message
        write_json(path/'project.json',state);print(message,flush=True)
    finally:
        if owned_lms:subprocess.run(['lms','unload',config['text']['model']],check=False,capture_output=True)
        p.store.close()

def worker_main(identifier):
    from .job_lock import FileLock
    try:
        with FileLock(DATA/'worker.lock'),FileLock(project_path(identifier)/'worker.lock'):
            worker(identifier)
    except BlockingIOError:
        state=read_json(project_path(identifier)/'project.json');state.update(state='paused',error='Another project is already running.')
        write_json(project_path(identifier)/'project.json',state)

if __name__=='__main__':worker_main(sys.argv[1])
