import json,re,unittest
from pathlib import Path
from book_illustrator.app_jobs import RESOURCES
from book_illustrator.finishing import xsd_report

class LocalizationTests(unittest.TestCase):
    def test_every_ui_key_is_translated_in_both_languages(self):
        web=Path(__file__).resolve().parents[1]/'book_illustrator/web'
        strings=json.loads((web/'strings.json').read_text(encoding='utf-8'))
        self.assertEqual(set(strings['en']),set(strings['ru']))
        keys=set(re.findall(r'data-(?:t|placeholder)="([^"]+)"',(web/'index.html').read_text(encoding='utf-8')))
        keys.update(re.findall(r"\bt\('([A-Za-z]+)'",(web/'app.js').read_text(encoding='utf-8')))
        for language in ('en','ru'):
            self.assertFalse(keys-set(strings[language]))
            self.assertTrue(all(isinstance(v,str) and v for v in strings[language].values()))
        self.assertFalse(re.search('[А-Яа-яЁё]',json.dumps(strings['en'],ensure_ascii=False)))
        self.assertIn('<html lang="en">',(web/'index.html').read_text(encoding='utf-8'))

    def test_original_sample_book_is_valid_and_available(self):
        self.assertTrue(xsd_report(RESOURCES/'examples/lantern-keeper.fb2',RESOURCES/'schemas/FictionBook.xsd')['valid'])
