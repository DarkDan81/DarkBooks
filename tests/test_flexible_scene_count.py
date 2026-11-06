import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from book_illustrator.pipeline import select_distributed_scenes
from book_illustrator.visual_analysis import visual_selection
from book_illustrator.production import ProductionRunner
from book_illustrator.core import read_json

class FlexibleSceneCountTests(unittest.TestCase):
    def test_selection_keeps_available_scenes_without_padding(self):
        paragraphs=[{'id':'p1','text':'A room.'}]
        candidates=[{'after_paragraph_id':'p1','title':'Room','description':'A room','importance':4}]
        self.assertEqual(select_distributed_scenes(candidates,paragraphs,5),candidates)
        self.assertEqual(select_distributed_scenes([],paragraphs,5),[])

    def test_exposition_only_chapter_returns_no_scenes(self):
        paragraphs=[{'id':'p1','text':'An abstract discussion.'}]
        episode=dict(start_paragraph_id='p1',end_paragraph_id='p1',title='Ideas',summary='Discussion',
                     kind='exposition_only',characters=[],visible_moment=1,visual_interest=1,narrative_importance=5,novelty=1)
        chosen,rejected=visual_selection([episode],paragraphs,5)
        self.assertEqual(chosen,[])
        self.assertEqual(len(rejected),1)

    def test_empty_chapter_skips_model_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner=ProductionRunner.__new__(ProductionRunner)
            runner.p=SimpleNamespace(run=Path(tmp))
            runner.text_call=lambda *args:self.fail('Empty chapter must not invoke audit model')
            data={'chapters':[{'results':[{'scene_candidates':[]}]}]}
            self.assertEqual(runner.audit({'id':'ch1','paragraphs':[]},data),data)
            self.assertEqual(read_json(Path(tmp)/'chapters/ch1/audit.json'),[])
