import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from book_illustrator.control import check_pause,PauseRequested
from book_illustrator.end_to_end import EndToEndRunner
from book_illustrator.core import write_json

class CollectionResumeTests(unittest.TestCase):
    def test_pause_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'STOP'
            with patch.dict(os.environ,BOOK_ILLUSTRATOR_STOP_FILE=str(marker)):
                check_pause()
                marker.touch()
                with self.assertRaises(PauseRequested):check_pause()
                marker.unlink();check_pause()

    def test_completed_durable_stage_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            runner=EndToEndRunner.__new__(EndToEndRunner)
            runner.resumable=True
            runner.p=SimpleNamespace(run=run)
            runner.report={'stages':[{'name':'scene_generation','state':'complete'}]}
            write_json(run/'renders-qwen-scenes.json',{'images':[{'scene_id':'saved'}]})
            with patch.dict(os.environ,BOOK_ILLUSTRATOR_STOP_FILE=str(run/'STOP')):
                result=runner.stage('scene_generation',lambda:self.fail('Repeated generation'))
            self.assertEqual(result,[{'scene_id':'saved'}])

    def test_failed_stage_is_retried_and_checkpointed(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner=EndToEndRunner.__new__(EndToEndRunner)
            runner.resumable=True;runner.p=SimpleNamespace(run=Path(tmp))
            runner.report={'stages':[{'name':'text_analysis','state':'failed'}]}
            with patch.dict(os.environ,BOOK_ILLUSTRATOR_STOP_FILE=str(Path(tmp)/'STOP')):
                self.assertEqual(runner.stage('text_analysis',lambda:42),42)
            self.assertEqual(runner.report['stages'][-1]['state'],'complete')
