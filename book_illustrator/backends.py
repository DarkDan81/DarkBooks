from __future__ import annotations

import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .core import image_mime
from .adaptive import adaptive_chat,ModelCapacityError


def request_json(url, data=None, *, timeout=120, headers=None):
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    request = urllib.request.Request(url, data=body,
                                    headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload=response.read()
            return json.loads(payload) if payload.strip() else {}
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode('utf-8', errors='replace')
        raise RuntimeError(f'HTTP {error.code} from model server: {detail}') from error
    except urllib.error.URLError as error:
        raise RuntimeError(f'Model server unavailable at {url}: {error.reason}') from error


class TextBackend:
    def __init__(self, config):
        self.config = config
        self.url = config['endpoint'].rstrip('/')
        key = os.environ.get(config.get('api_key_env', 'BOOK_LLM_API_KEY'), '')
        self.headers = {'Authorization': 'Bearer ' + key} if key else {}

    def models(self):
        return request_json(self.url + '/models', timeout=5, headers=self.headers)

    def complete(self, system, payload, schema=None):
        body = {'model': self.config['model'], 'temperature': self.config.get('temperature', 0.1),
                'max_tokens': self.config.get('max_tokens', 6000),
                'messages': [{'role': 'system', 'content': system},
                             {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]}
        mode = self.config.get('structured_output', 'json_schema')
        if mode == 'json_schema' and schema is not None:
            body['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': 'book_analysis', 'strict': True, 'schema': schema}}
        elif mode == 'json_object':
            body['response_format'] = {'type': 'json_object'}
        body.update(self.config.get('request_options', {}))
        result = adaptive_chat(request_json,self.url + '/chat/completions',body,
                              context_length=self.config.get('context_length',32768),
                              timeout=self.config.get('timeout_seconds',600),headers=self.headers)
        self.last_attempts=result.get('_adaptive_attempts',[])
        self.last_usage = result.get('usage')
        choice = result['choices'][0]
        if choice.get('finish_reason') not in {'stop', 'eos', None}:
            raise ValueError(f'Model output incomplete: {choice.get("finish_reason")}')
        content = choice['message'].get('content')
        if not content and self.config.get('allow_json_in_reasoning_channel', False):
            # Some local runtime templates mislabel schema-constrained JSON as reasoning.
            # Accept only a complete JSON document, never extract snippets from reasoning prose.
            alternate = choice['message'].get('reasoning_content', '').strip()
            if alternate.startswith('{') and alternate.endswith('}'):
                json.loads(alternate)
                content = alternate
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Model returned no text content')
        content = content.strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        # No substring recovery: a truncated or ambiguous response must be retried.
        return json.loads(content)


class ComfyBackend:
    """Explicit API-workflow bindings, never heuristic mutation of arbitrary nodes."""
    def __init__(self, config, root):
        self.config = config
        self.root = Path(root)
        self.url = config['endpoint'].rstrip('/')

    def health(self):
        return request_json(self.url + '/system_stats', timeout=5)

    def validate_workflow(self):
        workflow_path = self.root / self.config['workflow']
        if not workflow_path.is_file():
            raise ValueError(f'Export a tested ComfyUI API workflow to {workflow_path}')
        workflow = json.loads(workflow_path.read_text(encoding='utf-8-sig'))
        if 'nodes' in workflow or not all(isinstance(node, dict) and 'class_type' in node
                                          for node in workflow.values()):
            raise ValueError('Workflow must be ComfyUI API format, not the editor graph')
        nodes = request_json(self.url + '/object_info', timeout=30)
        missing = sorted({node['class_type'] for node in workflow.values()} - nodes.keys())
        if missing:
            raise ValueError('Missing ComfyUI node types: ' + ', '.join(missing))
        bindings = self.config['bindings']
        for binding in [bindings['prompt'], *bindings.get('references', [])]:
            node_id, input_name = binding
            if str(node_id) not in workflow or input_name not in workflow[str(node_id)].get('inputs', {}):
                raise ValueError(f'Invalid workflow binding: {binding}')
        if bindings.get('seed'):
            node_id, input_name = bindings['seed']
            if str(node_id) not in workflow or input_name not in workflow[str(node_id)].get('inputs', {}):
                raise ValueError('Invalid seed binding')
        if str(self.config['output_node']) not in workflow:
            raise ValueError('Configured output node is missing')
        return workflow

    @staticmethod
    def bind(workflow, binding, value):
        node_id, input_name = binding
        if str(node_id) not in workflow or input_name not in workflow[str(node_id)].get('inputs', {}):
            raise ValueError(f'Invalid workflow binding: {binding}')
        workflow[str(node_id)]['inputs'][input_name] = value

    def upload(self, path):
        path = Path(path)
        data = path.read_bytes()
        mime = image_mime(data)
        boundary = 'book-' + uuid.uuid4().hex
        name = uuid.uuid4().hex + ('.png' if mime == 'image/png' else '.jpg')
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"'
                f'\r\nContent-Type: {mime}\r\n\r\n').encode() + data
        body += f'\r\n--{boundary}--\r\n'.encode()
        req = urllib.request.Request(self.url + '/upload/image', data=body,
                                     headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
        with urllib.request.urlopen(req, timeout=120) as response:
            saved = json.load(response)
        return '/'.join(filter(None, [saved.get('subfolder'), saved['name']]))

    def generate(self, prompt, references, seed, destination):
        workflow = self.validate_workflow()
        bindings = self.config['bindings']
        self.bind(workflow, bindings['prompt'], prompt)
        if bindings.get('seed'):
            self.bind(workflow, bindings['seed'], seed)
        slots = bindings.get('references', [])
        if len(references) > len(slots):
            raise ValueError(f'Workflow has {len(slots)} reference slots, needs {len(references)}')
        # A reference-conditioned graph with stale unfilled inputs is unsafe.
        if len(references) != len(slots):
            raise ValueError('Use a workflow profile with exactly the needed reference slots')
        for slot, reference in zip(slots, references):
            self.bind(workflow, slot, self.upload(reference))
        result = request_json(self.url + '/prompt', {'prompt': workflow, 'client_id': uuid.uuid4().hex})
        if result.get('node_errors') or not result.get('prompt_id'):
            raise ValueError(f'ComfyUI rejected workflow: {result}')
        job_id = result['prompt_id']
        deadline = time.monotonic() + self.config.get('timeout_seconds', 1800)
        while True:
            if time.monotonic() >= deadline:
                queue=request_json(self.url+'/queue',timeout=30)
                active=queue.get('queue_running',[])+queue.get('queue_pending',[])
                if not any(len(row)>1 and row[1]==job_id for row in active):
                    # Final history check below handles completion at the boundary.
                    history=request_json(self.url+'/history/'+job_id,timeout=30).get(job_id)
                    if not history:raise TimeoutError(f'ComfyUI job {job_id} disappeared from queue and history')
                print('Still waiting for existing Comfy job',job_id,flush=True)
                deadline=time.monotonic()+self.config.get('timeout_seconds',1800)
            history = request_json(self.url + '/history/' + job_id, timeout=30).get(job_id)
            if history:
                status = history.get('status', {})
                if status.get('status_str') == 'error':
                    raise RuntimeError(f'ComfyUI job failed: {status.get("messages")}')
                output_node = str(self.config['output_node'])
                images = history.get('outputs', {}).get(output_node, {}).get('images', [])
                if images:
                    if len(images) != 1:
                        raise ValueError('Pilot workflow must return exactly one image')
                    query = urllib.parse.urlencode(images[0])
                    with urllib.request.urlopen(self.url + '/view?' + query, timeout=120) as response:
                        payload = response.read()
                    image_mime(payload)
                    destination = Path(destination)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temp = destination.with_name(destination.name + '.tmp')
                    temp.write_bytes(payload)
                    temp.replace(destination)
                    cached=[node for kind,data in status.get('messages',[]) if kind=='execution_cached' for node in data.get('nodes',[])]
                    sampler=str(bindings.get('seed',['3'])[0])
                    if self.config.get('require_fresh_sampler') and sampler in cached:
                        raise RuntimeError('Cold-run sampler was cached: '+job_id)
                    return {'prompt_id': job_id, 'path': str(destination.resolve()), 'seed': seed,
                            'cached_nodes':cached,'sampler_executed':sampler not in cached}
                if status.get('completed'):
                    raise ValueError('Completed workflow returned no image at configured output node')
            time.sleep(2)
