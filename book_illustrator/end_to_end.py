"""One-command, isolated cold pipeline: source text -> scenes -> crop identities -> A edits -> FB2."""
from pathlib import Path
from urllib.parse import urlparse
import subprocess,sys,time,json,shutil
from .core import read_json,write_json,fingerprint
from .backends import request_json,ComfyBackend
from .production import ProductionRunner

class EndToEndRunner:
    def __init__(self,pipeline,delivery):
        self.p=pipeline;self.delivery=Path(delivery).resolve();self.server=None;self.handles=[]
        self.resumable=bool(self.p.config.get('resumable'))
        self.report={'variant':self.p.config['run_variant'],'source_sha256':self.p.book.sha,
            'cold_run':True,'manual_scene_selection':False,'manual_prompt_edits':False,
            'prior_images_or_assignments_reused':False,'portrait_synthesis':False,
            'face_prompt_mode':self.p.config['face_refinement']['prompt_mode'],'stages':[]}

    def save(self):write_json(self.p.run/'end-to-end-report.json',self.report)

    def stage(self,name,operation):
        from .control import check_pause
        check_pause()
        durable={'text_analysis','scene_generation','face_detection_and_identity','select_scene_crops','face_edit_A','head_mask_compositing','fb2_export_and_previews'}
        if self.resumable and name in durable and any(r['name']==name and r['state']=='complete' for r in self.report['stages']):
            print('RESUME skip',name,flush=True)
            if name=='scene_generation':return read_json(self.p.run/'renders-qwen-scenes.json')['images']
            return None
        print('STAGE',name,flush=True);started=time.perf_counter();row={'name':name,'state':'running'}
        self.report['stages'].append(row);self.save()
        try:
            value=operation();row['state']='complete';return value
        except Exception as error:
            row['state']='failed';row['error']=str(error);raise
        finally:row['wall_seconds']=time.perf_counter()-started;self.save()

    def start_comfy(self):
        url=self.p.config['images']['qwen-scenes']['endpoint'];port=urlparse(url).port
        try:
            request_json(url+'/system_stats',timeout=2)
        except RuntimeError:pass
        else:raise RuntimeError('Cold pipeline requires an unused private Comfy port: '+url)
        root=self.p.root;runtime=root/'runtime'
        args=[str(runtime/'.venv/Scripts/python.exe'),'-u','main.py','--listen','127.0.0.1','--port',str(port),
            '--disable-auto-launch','--disable-all-custom-nodes','--disable-api-nodes',
            '--output-directory',str(root/'runs/comfy-outputs'),'--input-directory',str(root/'runs/comfy-inputs'),
            '--user-directory',str(runtime/'comfy-user'),'--reserve-vram','1.5','--preview-method','none']
        model_paths=Path(self.p.config.get('production_runtime',{}).get('extra_model_paths',root.parent/'Data/Packages/ComfyUI/extra_model_paths.yaml'))
        if model_paths.is_file():args.extend(['--extra-model-paths-config',str(model_paths)])
        self.handles=[open(self.p.run/'comfy.log','w',encoding='utf-8'),open(self.p.run/'comfy-errors.log','w',encoding='utf-8')]
        self.server=subprocess.Popen(args,cwd=runtime/'ComfyUI',stdout=self.handles[0],stderr=self.handles[1],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.report['private_comfy']={'pid':self.server.pid,'endpoint':url,'fresh_process':True}
        deadline=time.monotonic()+90
        while True:
            from .control import check_pause
            check_pause()
            if time.monotonic()>=deadline:
                print('Still waiting for owned Comfy server startup',flush=True)
                deadline=time.monotonic()+90
            if self.server.poll() is not None:raise RuntimeError('Private Comfy exited; see run/comfy-errors.log')
            try:request_json(url+'/system_stats',timeout=2);return
            except RuntimeError:time.sleep(1)

    def release_comfy(self):
        url=self.p.config['images']['qwen-scenes']['endpoint'];q=request_json(url+'/queue')
        if q['queue_running'] or q['queue_pending']:raise RuntimeError('Refusing to unload active Comfy jobs')
        request_json(url+'/free',{'unload_models':True,'free_memory':True})

    def load_text(self,identifier,context):
        subprocess.run(['lms','server','start','--port','1234'],check=False,capture_output=True)
        subprocess.run(['lms','load',self.p.config['production_runtime']['text_model_key'],'--identifier',identifier,
            '--context-length',str(context),'--parallel','1','--gpu','max','-y'],check=True)

    def unload_text(self,identifier):subprocess.run(['lms','unload',identifier],check=False,capture_output=True)

    def faces(self,phase,briefs='reference-briefs.json'):
        subprocess.run([sys.executable,str(self.p.root/'scripts/run_production_faces.py'),phase,
            '--config',str(self.p.run/'run-config.json'),'--run-path',str(self.p.run),
            '--pass-name','face-pass','--portrait-briefs',briefs,'--delivery',str(self.delivery)],check=True,cwd=self.p.root)

    def select_crops(self):
        subprocess.run([sys.executable,str(self.p.root/'scripts/prepare_scene_crop_references.py'),
            '--run-path',str(self.p.run),'--mapping-pass','face-pass','--face-pass','face-pass',
            '--delivery',str(self.delivery)],check=True,cwd=self.p.root)

    def finish_report(self,total_seconds):
        result=read_json(self.p.run/'face-pass/report.json');refs=read_json(self.p.run/'reference-briefs-scene-crops.json')
        images=read_json(self.p.run/'renders-qwen-scenes.json')['images'];events=list(read_json(self.p.run/'text-timings.json').values())
        if not self.resumable:assert all(not e['cached_this_invocation'] for e in events)
        assert all(i['sampler_executed'] for i in images)
        assert all(e['edit_generation']['sampler_executed'] for r in result['scenes'] for e in r['edits'])
        assert all(r['outside_mask_pixels_changed']==0 for r in result['scenes'])
        assert result['export']['text_and_original_links_preserved'] and result['export']['output_xsd']['valid']
        book=result['export'].get('path',str(self.delivery/'eisenhorn-qwen-facefix-first3.fb2'))
        assert Path(book).is_file()
        self.report.update(state='complete',total_wall_seconds=total_seconds,
            text=read_json(self.p.run/'text-phase.json'),scene_count=len(images),reference_count=len(refs['designs']),
            reference_sources=[{'character':r['character'],'scene':r['source_scene_id']} for r in refs['designs']],
            unavailable_references=refs['unavailable_characters'],edited_faces=result['edited_faces'],
            edited_scenes=result['edited_scenes'],book=book,validation=result['export'])
        self.save();shutil.copy2(self.p.run/'end-to-end-report.json',self.delivery/'end-to-end-report.json')
        for name in ['scenes.json','analysis.json','run-config.json']:shutil.copy2(self.p.run/name,self.delivery/name)

    def run(self):
        if not self.resumable and ((self.p.run/'end-to-end-report.json').exists() or self.p.store.db.execute('select count(*) from jobs').fetchone()[0]):
            raise ValueError('Cold run already contains work; choose a NEW run_variant, no cache silently reused')
        self.delivery.mkdir(parents=True,exist_ok=True);started=time.perf_counter()
        if self.resumable and (self.p.run/'run-config.json').exists():
            if read_json(self.p.run/'run-config.json')!=self.p.config:raise ValueError('Resume config changed; use a new run variant')
            self.report=read_json(self.p.run/'end-to-end-report.json')
            if self.report.get('state')=='complete':return
            self.report['cold_run']=False
            self.report['prior_images_or_assignments_reused']=True
        self.report.pop('error',None)
        self.report['state']='running'
        write_json(self.p.run/'run-config.json',self.p.config);self.save()
        textid=self.p.config['text']['model'];mapper='book-face-mapper'
        try:
            self.stage('start_private_comfy',self.start_comfy)
            self.stage('load_text_model',lambda:self.load_text(textid,self.p.config['production_runtime']['context_length']))
            if self.p.config.get('text_analysis_mode')=='optimized':
                from .optimized import OptimizedRunner
                runner=OptimizedRunner(self.p)
            else:runner=ProductionRunner(self.p)
            self.stage('text_analysis',runner.analyze)
            if not self.resumable:assert all(not e['cached_this_invocation'] for e in runner.events.values())
            self.stage('unload_text_model',lambda:self.unload_text(textid))
            images=self.stage('scene_generation',lambda:self.p.render('qwen-scenes'))
            assert len(images)==len(read_json(self.p.run/'scenes.json')['scenes']), 'Rendered images must match the actual selected scene plan'
            assert all(i['sampler_executed'] for i in images)
            self.stage('release_scene_models',self.release_comfy)
            self.stage('load_vision_model',lambda:self.load_text(mapper,8192))
            self.stage('face_detection_and_identity',lambda:self.faces('map'))
            self.stage('unload_vision_model',lambda:self.unload_text(mapper))
            self.stage('select_scene_crops',self.select_crops)
            self.stage('face_edit_A',lambda:self.faces('edit','reference-briefs-scene-crops.json'))
            self.stage('head_mask_compositing',lambda:self.faces('reblend','reference-briefs-scene-crops.json'))
            self.stage('fb2_export_and_previews',lambda:self.faces('package','reference-briefs-scene-crops.json'))
            self.finish_report(time.perf_counter()-started)
            print('COMPLETE',self.p.config['run_variant'],'seconds',round(self.report['total_wall_seconds'],1),flush=True)
        except Exception as error:
            self.report.update(state='paused' if __import__('os').environ.get('BOOK_ILLUSTRATOR_STOP_FILE') and Path(__import__('os').environ['BOOK_ILLUSTRATOR_STOP_FILE']).exists() else 'failed',error=str(error),total_wall_seconds=time.perf_counter()-started);self.save();raise
        finally:
            self.unload_text(textid);self.unload_text(mapper)
            if self.server is not None and self.server.poll() is None:
                # Only the process tree launched and owned by THIS invocation.
                subprocess.run(['taskkill','/PID',str(self.server.pid),'/T','/F'],capture_output=True)
            for handle in self.handles:handle.close()
