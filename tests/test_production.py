from pathlib import Path
import tempfile
import unittest
from test_pipeline import fixture
from book_illustrator.core import write_json
from book_illustrator.pipeline import Pipeline,obj,S
from book_illustrator.production import ProductionRunner,portrait_traits,portrait_prompt

class ProductionTests(unittest.TestCase):
    def test_english_character_name_does_not_require_russian_spelling(self):
        from book_illustrator.production import ProductionAnalyzer
        chapter={'id':'english','paragraphs':[{'id':'p1','text':'Mara lifted the copper lantern.'}]}
        analyzer=ProductionAnalyzer(self.runner,chapter,[])
        ranked={'characters':[{'name':'Mara','aliases':[],'appearance_paragraph_ids':[]}],'episodes':[]}
        result=analyzer.canonicalize_ranked(ranked,chapter['paragraphs'])
        self.assertEqual(result['characters'][0]['name'],'Mara')

    def test_identity_unsupported_clues_are_logged_and_valid_clues_survive(self):
        from book_illustrator.core import read_json
        chapter=self.p.book.chapters[0];pid=chapter['paragraphs'][0]['id']
        self.p.config['pilot']={'chapter_ids':[chapter['id']]}
        self.p.config['identity_pass_mode']='individual'
        for gender,ids in [('male',[]),('female',['foreign']),('female',[pid])]:
            with self.subTest(gender=gender,ids=ids):
                def call(*args):
                    return {'gender':gender,'gender_source_ids':ids,
                            'face_accessories':[{'description_en':'Silver eye implant','source_paragraph_id':pid},
                                                {'description_en':'Invented mask','source_paragraph_id':'foreign'}]},{}
                self.runner.text_call=call
                analysis={'characters':[{'name':'Иван','aliases':[],'appearance':[{'paragraph_id':pid,'trait':'Dark eyes'}]}]}
                output=self.runner.complete_identity(analysis)
                traits=[f['trait'] for f in output['characters'][0]['appearance']]
                self.assertIn('Silver eye implant',traits)
                self.assertNotIn('Invented mask',traits)
                self.assertEqual('A woman' in traits,ids==[pid])
                clean=read_json(self.p.run/'identity-completion.json')['people'][0]
                self.assertEqual(clean['gender'],gender if ids==[pid] else 'unknown')
                self.assertEqual(len(clean['face_accessories']),1)
                rejected=read_json(self.p.run/'identity-completion-rejections.json')
                self.assertEqual(len(rejected),1 if ids==[pid] else 2)

    def test_source_glossary_reaches_scene_writer_and_checker(self):
        from book_illustrator.core import write_json
        write_json(self.root/'setting.json',{'translation_glossary':[{'source_term':'боевой катер','preferred_en':'armoured gun-cutter aircraft','avoid_en':['battlecat']}]})
        self.p.config['setting_profile']='setting.json';calls=[]
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):calls.append((system,payload));return {'issues':[]}
        self.runner.backend=Backend()
        self.runner.text_call('ch1/04-source-audit','Check source',{'scenes':[]},obj({'issues':{'type':'array','items':S}}))
        self.assertIn('translation equivalents',calls[0][0])
        self.assertEqual(calls[0][1]['source_term_glossary'][0]['preferred_en'],'armoured gun-cutter aircraft')
    def test_portrait_uses_shared_setting_without_changing_traits(self):
        prompt=portrait_prompt('Character','Dark eyes; Clean-shaven','Warhammer 40,000 Inquisition novel illustration.')
        self.assertTrue(prompt.startswith('Warhammer 40,000 Inquisition novel illustration.\n'))
        self.assertIn('Dark eyes; Clean-shaven',prompt)
        self.assertIn('head-and-shoulders',prompt)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        fixture(self.root/'source.fb2')
        write_json(self.root/'config.json',{'book':'source.fb2','text':{'endpoint':'http://localhost:1','model':'test'}})
        self.p=Pipeline(self.root,self.root/'config.json');self.runner=ProductionRunner(self.p)
    def tearDown(self):
        self.p.store.close();self.temp.cleanup()

    def test_stage_cache_reuses_only_identical_model_request(self):
        calls=[]
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):calls.append(payload);return {'value':'ok'}
        self.runner.backend=Backend();schema=obj({'value':S})
        a,first=self.runner.text_call('stage','system',{'x':1},schema)
        b,second=self.runner.text_call('stage','system',{'x':1},schema)
        self.assertEqual(a,b);self.assertEqual(len(calls),1)
        self.assertFalse(first['cached_this_invocation']);self.assertTrue(second['cached_this_invocation'])
        self.runner.text_call('stage','system',{'x':2},schema)
        self.assertEqual(len(calls),2)

    def test_portraits_exclude_injuries_and_outfits_but_keep_grooming(self):
        person={'appearance':[{'trait':trait,'category':category,'state':state}
            for trait,category,state in [('Dark eyes','eyes','permanent'),('Clean-shaven','facial_hair','temporary'),
                ('Bloody wound','injury','permanent'),('Green robe','clothing','permanent')]]}
        self.assertEqual([f['trait'] for f in portrait_traits(person)],['Dark eyes','Clean-shaven'])

    def test_atomic_character_scope_mismatch_is_retried_automatically(self):
        calls=[]
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):
                calls.append(payload)
                return {'characters':[{'name':n,'facts':[]} for n in (['Иван','Лишний'] if len(calls)==1 else ['Иван'])]}
        self.runner.backend=Backend()
        schema=obj({'characters':{'type':'array','items':obj({'name':S,'facts':{'type':'array','items':S}})}})
        data,event=self.runner.text_call('ch1/02-atomic-appearance','system',{'characters':[{'name':'Иван'}]},schema)
        self.assertEqual([x['name'] for x in data['characters']],['Иван'])
        self.assertEqual(event['attempts'],2)
        self.assertIn('correction_required',calls[1])

    def test_repeated_extra_person_is_logged_and_cannot_leak_into_target_facts(self):
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):
                return {'characters':[{'name':'Иван','facts':['Grey hair']},{'name':'Лишний','facts':['Blue eyes']}]}
        self.runner.backend=Backend()
        schema=obj({'characters':{'type':'array','items':obj({'name':S,'facts':{'type':'array','items':S}})}})
        data,event=self.runner.text_call('ch1/02-atomic-appearance','system',{'characters':[{'name':'Иван'}]},schema)
        self.assertEqual(data,{'characters':[{'name':'Иван','facts':['Grey hair']}]})
        self.assertEqual(event['normalizations'][0]['out_of_scope_names'],['Лишний'])
        self.assertEqual(len(event['normalizations'][0]['raw_response']['characters']),2)

    def test_audit_corrects_only_flagged_scene_and_rechecks_it(self):
        chapter=self.p.book.chapters[1];paras=chapter['paragraphs'];calls=[]
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):
                calls.append(payload)
                if len(calls)==1:return {'issues':[{'scene_id':'0',
                    'problem':'Wrong pose','source_paragraph_ids':[paras[0]['id']]}]}
                if len(calls)==2:return {'description':'Corrected','image_prompt':'Fixed pose','characters':[],
                                         'applied_fixes':['Corrected pose']}
                return {'issues':[]}
        self.runner.backend=Backend()
        scenes=[{'after_paragraph_id':p['id'],'source_start_paragraph_id':p['id'],
                 'image_prompt':'Original '+str(i),'characters':[]} for i,p in enumerate(paras[:2])]
        data={'characters':[],'chapters':[{'results':[{'scene_candidates':scenes}]}]}
        result=self.runner.audit(chapter,data)
        updated=result['chapters'][0]['results'][0]['scene_candidates']
        self.assertEqual(updated[0]['image_prompt'],'Fixed pose')
        self.assertEqual(updated[1]['image_prompt'],'Original 1')
        self.assertEqual(len(calls),3);self.assertEqual(len(calls[2]['scenes']),1)
