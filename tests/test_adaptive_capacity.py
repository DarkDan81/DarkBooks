import json,tempfile,unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from book_illustrator.adaptive import adaptive_chat,ModelCapacityError,merge_responses,vision_face_batches
from book_illustrator.backends import ComfyBackend
import test_production

class AdaptiveCapacityTests(unittest.TestCase):
    def test_truncated_response_expands_then_accepts_complete_document(self):
        calls=[]
        def request(url,body,**kwargs):
            calls.append(deepcopy(body))
            return {'choices':[{'finish_reason':'length' if len(calls)==1 else 'stop','message':{'content':'{}'}}],
                    'usage':{'prompt_tokens':100}}
        original={'max_tokens':100,'messages':[]}
        result=adaptive_chat(request,'url',original,context_length=2000)
        self.assertEqual([b['max_tokens'] for b in calls],[100,200])
        self.assertEqual(len(result['_adaptive_attempts']),2)
        self.assertEqual(original['max_tokens'],100)

    def test_no_room_for_output_requests_split(self):
        def request(*args,**kwargs):
            return {'choices':[{'finish_reason':'length'}],'usage':{'prompt_tokens':1800}}
        with self.assertRaises(ModelCapacityError):
            adaptive_chat(request,'url',{'max_tokens':100},context_length=2000)

    def test_named_facts_merge_without_losing_citations(self):
        self.assertEqual(merge_responses({'characters':[{'name':'A','facts':['one']}]},
                                        {'characters':[{'name':'A','facts':['two']}]}),
                         {'characters':[{'name':'A','facts':['one','two']}]})

    def test_vision_context_overflow_splits_face_sets(self):
        calls=[]
        def request(url,body,**kwargs):
            ids=json.loads(body['messages'][0]['content'][0]['text'].rsplit(': ',1)[1]);calls.append(ids)
            if len(ids)>1:raise RuntimeError('HTTP 400: context length exceeded')
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'faces':[{'face_index':ids[0]}]})}}]}
        body={'max_tokens':100,'messages':[{'content':[{'type':'text','text':'Identify'}]}]}
        rows,_=vision_face_batches(request,'url',body,[0,1,2,3])
        self.assertEqual([r['face_index'] for r in rows],[0,1,2,3])
        self.assertIn([0,1,2],calls)

    def test_live_comfy_job_survives_elapsed_deadline_without_resubmission(self):
        with tempfile.TemporaryDirectory() as tmp:
            config={'endpoint':'http://local','bindings':{'prompt':['6','prompt'],'seed':['3','seed']},'output_node':'9','timeout_seconds':1}
            backend=ComfyBackend(config,tmp)
            workflow={'6':{'inputs':{'prompt':''}},'3':{'inputs':{'seed':1}}}
            calls=[]
            def request(url,data=None,**kwargs):
                calls.append(url)
                if url.endswith('/prompt'):return {'prompt_id':'job'}
                if url.endswith('/queue'):return {'queue_running':[[0,'job']], 'queue_pending':[]}
                return {'job':{'outputs':{'9':{'images':[{'filename':'image.png'}]}},'status':{}}}
            class Response:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self):return b'\x89PNG\r\n\x1a\n'+b'fixture'
            with patch.object(backend,'validate_workflow',return_value=workflow),patch('book_illustrator.backends.request_json',side_effect=request),patch('book_illustrator.backends.time.monotonic',side_effect=[0,2,2]),patch('book_illustrator.backends.urllib.request.urlopen',return_value=Response()):
                result=backend.generate('scene',[],1,Path(tmp)/'result.png')
            self.assertEqual(result['prompt_id'],'job')
            self.assertEqual(calls.count('http://local/prompt'),1)

class CapacityCheckpointTests(unittest.TestCase):
    setUp=test_production.ProductionTests.setUp
    tearDown=test_production.ProductionTests.tearDown
    def test_capacity_parts_are_saved_and_reused(self):
        calls=[]
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):
                calls.append(payload)
                if len(payload['characters'])>1:raise ModelCapacityError('context length exceeded')
                return {'characters':[{'name':payload['characters'][0]['name'],'facts':[]}]}
        self.runner.backend=Backend()
        from book_illustrator.visual_analysis import FACT_SCHEMA
        payload={'characters':[{'name':'A','sentences':[]},{'name':'B','sentences':[]}]}
        data,_=self.runner.text_call('ch/02-atomic-appearance','extract',payload,FACT_SCHEMA)
        count=len(calls)
        again,event=self.runner.text_call('ch/02-atomic-appearance','extract',payload,FACT_SCHEMA)
        self.assertEqual(data,again);self.assertEqual(len(calls),count)
        self.assertTrue(event['cached_this_invocation'])
        self.assertEqual([p['name'] for p in data['characters']],['A','B'])
