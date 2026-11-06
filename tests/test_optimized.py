from pathlib import Path
import tempfile,unittest
from test_pipeline import fixture
from book_illustrator.core import write_json
from book_illustrator.pipeline import Pipeline
from book_illustrator.optimized import OptimizedAnalyzer,OptimizedRunner,relevant_people,check_optimized_fact

class OptimizedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);fixture(self.root/'source.fb2')
        write_json(self.root/'config.json',{'book':'source.fb2','text':{'endpoint':'http://localhost:1','model':'test'}})
        self.p=Pipeline(self.root,self.root/'config.json');self.runner=OptimizedRunner(self.p)
    def tearDown(self):self.p.store.close();self.temp.cleanup()

    def test_combined_read_copies_exact_evidence_and_only_selected_scene_fragments(self):
        chapter=self.p.book.chapters[1];paras=chapter['paragraphs'];pid=paras[0]['id'];calls=[]
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):
                calls.append(payload)
                if len(calls)==1:return {'episodes':[{'start_paragraph_id':p['id'],'end_paragraph_id':p['id'],
                    'title':'Visible event','summary':'A visible event','kind':'action','characters':['Иван'],
                    'visible_moment':4,'visual_interest':4,'narrative_importance':3,'novelty':4} for p in paras],
                    'characters':[{'name':'Иван','aliases':[],'appearance_paragraph_ids':[pid+'.s02'],'facts':[
                    {'description_en':'Grey hair','category':'hair','state':'permanent','source_sentence_ids':[pid+'.s02']}]}]}
                return {'scenes':[{'episode_id':e['episode_id'],'description':'One event','image_prompt':'A man in a room','characters':['Иван']} for e in payload['episodes']]}
        self.runner.backend=Backend();analyzer=OptimizedAnalyzer(self.runner,chapter,[])
        data,summary=analyzer.analyze(self.p.book,chapter,2)
        self.assertEqual(len(calls),2)
        self.assertEqual(data['characters'][0]['appearance'][0]['quote'],'У него седые волосы.')
        self.assertEqual(len(calls[1]['episodes']),2)
        self.assertNotIn('paragraphs',calls[1])
        self.assertEqual(summary['fact_count'],1)

    def test_batch_correction_handles_two_flagged_scenes_in_one_call(self):
        chapter=self.p.book.chapters[1];paras=chapter['paragraphs'];calls=[]
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):
                calls.append(payload)
                if len(calls)==1:return {'issues':[{'scene_id':str(i),'problem':'Reversed action','source_paragraph_ids':[paras[i]['id']]} for i in range(2)]}
                if len(calls)==2:return {'scenes':[{'scene_id':str(i),'description':'Corrected','image_prompt':'Fixed '+str(i),'characters':[], 'applied_fixes':['Restored action']} for i in range(2)]}
                return {'issues':[]}
        self.runner.backend=Backend();folder=self.p.run/'chapters'/chapter['id'];folder.mkdir(parents=True)
        scenes=[{'source_start_paragraph_id':p['id'],'after_paragraph_id':p['id'],'image_prompt':'Original','characters':[]} for p in paras[:2]]
        result=self.runner.audit(chapter,{'characters':[],'chapters':[{'results':[{'scene_candidates':scenes}]}]})
        self.assertEqual(len(calls),3)
        self.assertEqual(len(calls[1]['scenes']),2)
        self.assertEqual([s['image_prompt'] for s in scenes],['Fixed 0','Fixed 1'])

    def test_relevant_registry_keeps_explicit_and_narrator_but_drops_other_people(self):
        people=[{'name':n,'aliases':[]} for n in ['Иван','Грегор Эйзенхорн','Тутрон','Невидимый']]
        chosen=relevant_people(people,'В комнате Тутрон осматривала тело',['Иван'],'Грегор Эйзенхорн')
        self.assertEqual({p['name'] for p in chosen},{'Иван','Грегор Эйзенхорн','Тутрон'})

    def test_gender_facts_cannot_reference_another_source_scope(self):
        fact={'description_en':'A woman','category':'gender','state':'permanent','source_sentence_ids':['p1.s01']}
        with self.assertRaisesRegex(ValueError,'source'):check_optimized_fact(fact,{})

    def test_unnamed_unsupported_role_is_logged_without_poisoning_named_registry(self):
        chapter=self.p.book.chapters[1]
        class Backend:
            last_usage=None
            def complete(self,system,payload,schema):
                return {'names':[{'input_name':'Женщина-жертва','canonical_russian':'Женщина-жертва','source_aliases':[]}]}
        self.runner.backend=Backend();analyzer=OptimizedAnalyzer(self.runner,chapter,[])
        ranked={'characters':[{'name':n,'aliases':[],'appearance_paragraph_ids':[]} for n in ['Иван','Женщина-жертва']],
                'episodes':[{'characters':['Иван','Женщина-жертва']}]}
        result=analyzer.canonicalize_ranked(ranked,chapter['paragraphs'])
        self.assertEqual([p['name'] for p in result['characters']],['Иван'])
        self.assertEqual(result['episodes'][0]['characters'],['Иван'])
