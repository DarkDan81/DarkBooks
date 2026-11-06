from pathlib import Path
import tempfile
import unittest
from book_illustrator.core import Book
from book_illustrator.two_pass import TwoPassAnalyzer,check_episode
from test_pipeline import fixture

class TwoPassTests(unittest.TestCase):
    def test_only_selected_episodes_expanded_and_evidence_copied_from_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root/'book.fb2');book=Book(root/'book.fb2')
            chapter=book.chapters[1];paragraphs=chapter['paragraphs'];payloads=[]
            class Backend:
                last_usage={'completion_tokens':10}
                def complete(self,system,payload,schema):
                    payloads.append(payload)
                    if 'paragraphs' in payload:
                        return {'episodes':[{'start_paragraph_id':p['id'],'end_paragraph_id':p['id'],
                            'title':str(i),'summary':'A distinct event','kind':'action',
                            'characters':['Я'],'importance':3} for i,p in enumerate(paragraphs)],
                            'characters':[{'name':'Я','aliases':[],'appearance':[
                                {'description_en':'Grey hair','paragraph_id':paragraphs[0]['id'],'state':'permanent'}]}]}
                    return {'scenes':[{'episode_id':e['episode_id'],'description':'A traveller pauses',
                        'image_prompt':'A traveller with grey hair pauses near the door.','characters':['Я']}
                        for e in payload['episodes']]}
            run=root/'run';run.mkdir()
            analysis,summary=TwoPassAnalyzer(Backend(),run,{'я':'Иван'}).analyze(book,chapter,2)
            self.assertEqual(len(payloads),2)
            self.assertEqual(len(payloads[1]['episodes']),2)
            self.assertEqual(summary['candidates'],3)
            self.assertEqual(analysis['characters'][0]['name'],'Иван')
            self.assertEqual(analysis['characters'][0]['appearance'][0]['quote'],paragraphs[0]['text'])
            for scene in analysis['chapters'][0]['results'][0]['scene_candidates']:
                source=next(p['text'] for p in paragraphs if p['id']==scene['after_paragraph_id'])
                self.assertEqual(scene['quote'],source)
                self.assertEqual(scene['characters'],['Иван'])

    def test_episode_bounds_must_exist_and_be_in_source_order(self):
        paragraphs=[{'id':'p1','text':'First'},{'id':'p2','text':'Second'}]
        episode={'start_paragraph_id':'p2','end_paragraph_id':'p1','title':'x','summary':'x',
                 'kind':'action','characters':[],'importance':3}
        with self.assertRaisesRegex(ValueError,'reversed'):check_episode(episode,paragraphs)
        episode['end_paragraph_id']='p999'
        with self.assertRaisesRegex(ValueError,'outside'):check_episode(episode,paragraphs)
