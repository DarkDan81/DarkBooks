"""Image providers implement generate() and validate_workflow(); orchestration is shared."""
import base64,os,urllib.request
from pathlib import Path
from .backends import ComfyBackend,request_json
from .core import image_mime

class CompatibleImageBackend:
    def __init__(self,config,root):self.config=config
    def validate_workflow(self):return {}
    def generate(self,prompt,references,seed,destination):
        if references:raise ValueError('This image API adapter supports text-to-image only')
        config=self.config;key=os.environ.get(config.get('api_key_env','BOOK_IMAGE_API_KEY'),'')
        result=request_json(config['endpoint'].rstrip('/')+'/images/generations',
            {'model':config['model'],'prompt':prompt,'n':1,'size':config.get('size','1024x1024')},
            headers={'Authorization':'Bearer '+key} if key else {},timeout=config.get('timeout_seconds',600))
        record=result['data'][0]
        if record.get('b64_json'):payload=base64.b64decode(record['b64_json'],validate=True)
        elif record.get('url'):
            if not record['url'].startswith(('https://','http://')):raise ValueError('Unsupported image download URL')
            with urllib.request.urlopen(record['url'],timeout=120) as response:payload=response.read()
        else:raise ValueError('Image API returned neither b64_json nor url')
        image_mime(payload);path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        if image_mime(payload)=='image/png':temporary.write_bytes(payload)
        else:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(payload)) as image:image.save(temporary,format='PNG')
        temporary.replace(path)
        return {'path':str(path.resolve()),'provider':'compatible_images','seed_requested':seed,
                'seed_supported':False,'sampler_executed':True}

def image_backend(config,root):
    kind=config.get('backend','comfyui')
    if kind=='comfyui':return ComfyBackend(config,root)
    if kind=='compatible_images':return CompatibleImageBackend(config,root)
    raise ValueError('Unknown image adapter: '+kind)
