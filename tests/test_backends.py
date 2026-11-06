import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from book_illustrator.backends import ComfyBackend, TextBackend
from book_illustrator.core import write_json


class BackendTests(unittest.TestCase):
    def test_runtime_mislabeled_json_is_opt_in_and_must_be_complete(self):
        backend = TextBackend({'endpoint': 'http://localhost:1/v1', 'model': 'test',
                               'allow_json_in_reasoning_channel': True})
        response = {'choices': [{'finish_reason': 'stop', 'message': {
            'content': '', 'reasoning_content': '{"name":"Иван"}'}}]}
        with patch('book_illustrator.backends.request_json', return_value=response):
            self.assertEqual(backend.complete('test', {}), {'name': 'Иван'})
            backend.config['allow_json_in_reasoning_channel'] = False
            with self.assertRaises(ValueError):
                backend.complete('test', {})
            backend.config['allow_json_in_reasoning_channel'] = True
            response['choices'][0]['message']['reasoning_content'] = 'I think: {"name":"Иван"}'
            with self.assertRaises(ValueError):
                backend.complete('test', {})

    def test_truncated_json_never_accepted(self):
        backend = TextBackend({'endpoint': 'http://localhost:1/v1', 'model': 'test'})
        response = {'choices': [{'finish_reason': 'length', 'message': {'content': '{"ok":true}'}}]}
        with patch('book_illustrator.backends.request_json', return_value=response):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                backend.complete('test', {})

    def test_comfy_rejects_wrong_graph_binding_and_reference_count_before_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            graph = {'1': {'class_type': 'Text', 'inputs': {'text': ''}},
                     '2': {'class_type': 'Image', 'inputs': {'image': 'old.png'}}}
            write_json(root/'workflow.json', graph)
            config = {'endpoint': 'http://localhost:1', 'workflow': 'workflow.json',
                      'bindings': {'prompt': ['1', 'text'], 'references': [['2', 'image']]},
                      'output_node': '2'}
            backend = ComfyBackend(config, root)
            with patch('book_illustrator.backends.request_json', return_value={'Text': {}, 'Image': {}}) as http:
                with self.assertRaisesRegex(ValueError, 'exactly'):
                    backend.generate('prompt', [], 1, root/'image.png')
                self.assertEqual(http.call_count, 1)  # object_info only; no generation job
                config['bindings']['prompt'] = ['9', 'text']
                with self.assertRaisesRegex(ValueError, 'binding'):
                    backend.validate_workflow()
            write_json(root/'workflow.json', {'nodes': [], 'links': []})
            with self.assertRaisesRegex(ValueError, 'API format'):
                backend.validate_workflow()


if __name__ == '__main__':
    unittest.main()
