import io,unittest
from unittest.mock import patch
from book_illustrator.backends import request_json

class RuntimeResponseTests(unittest.TestCase):
    def test_comfy_free_empty_success_body_is_valid(self):
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'')):
            self.assertEqual(request_json('http://localhost/free',{'unload_models':True}),{})

    def test_nonempty_json_response_is_still_decoded(self):
        with patch('urllib.request.urlopen',return_value=io.BytesIO(b'{"ok":true}')):
            self.assertEqual(request_json('http://localhost/test'),{'ok':True})
