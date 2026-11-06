import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from book_illustrator.cli import test_card
from book_illustrator.core import Book, FB, Store, verify_export, write_json
from book_illustrator.pipeline import Pipeline, validate_analysis, plan_fingerprint


def fixture(path):
    path.write_text('''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info><genre>sf</genre><author><first-name>Test</first-name><last-name>Author</last-name></author>
<book-title>Тестовая книга</book-title><lang>ru</lang></title-info><document-info><author><nickname>test</nickname></author>
<date>2026</date><id>fixture</id><version>1</version></document-info></description>
<body><section><title><p>Предисловие</p></title><p>О книге.</p></section>
<section><title><p>Часть первая</p></title><section><title><p>Глава 1</p></title>
<p>Иван вошёл. У него седые волосы.</p><p>Он поднял <emphasis>меч</emphasis>.<a l:href="#n1" type="note">1</a></p>
<cite><p>Вложенная цитата.</p></cite></section>
<section><title><p>Глава 2</p></title><p>На Иване теперь красный плащ.</p><p>Он вышел.</p></section></section></body>
<body name="notes"><section id="n1"><title><p>1</p></title><p>Примечание.</p></section></body>
</FictionBook>''', encoding='utf-8')


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.fb2'
        fixture(self.source)

    def tearDown(self):
        self.tmp.cleanup()

    def test_setting_prompt_keeps_source_priority_and_scene_details(self):
        from book_illustrator.pipeline import compile_prompt
        scene = {'profiles_at_scene': [], 'image_prompt': 'A naval officer', 'style': 'Ink',
                 'setting': {'name': 'Imperium', 'visual_context': 'Gothic machinery'},
                 'source_detail_context': {'description': 'Ivory grip with a rose'}}
        prompt = compile_prompt(scene)
        self.assertIn('Gothic machinery', prompt)
        self.assertIn('Explicit book details and character identity override', prompt)
        self.assertIn('Ivory grip with a rose', prompt)
        scene['setting'] = {'prompt': 'Warhammer 40,000. Human-scale investigation.'}
        short_prompt = compile_prompt(scene)
        self.assertTrue(short_prompt.startswith(scene['setting']['prompt'] + '\n'))
        self.assertNotIn('VISUAL WORLD CONTEXT', short_prompt)
        self.assertNotIn('PRIORITY:', short_prompt)
        self.assertIn('Ivory grip with a rose', short_prompt)

    def test_variant_isolated_and_path_traversal_rejected(self):
        write_json(self.root/'config.json', {'book': 'source.fb2', 'run_variant': 'setting-v1'})
        pipeline = Pipeline(self.root, self.root/'config.json')
        try:
            self.assertEqual(pipeline.run, self.root.resolve()/'runs'/pipeline.book.sha[:16]/'variants'/'setting-v1')
        finally:
            pipeline.store.close()
        write_json(self.root/'config.json', {'book': 'source.fb2', 'run_variant': '../../other'})
        with self.assertRaisesRegex(ValueError, 'simple directory name'):
            Pipeline(self.root, self.root/'config.json')

    def test_reference_library_reuses_identity_and_limits_reference_count(self):
        write_json(self.root/'config.json', {'book': 'source.fb2', 'style': 'Ink',
                   'reference_library': 'library.json', 'max_character_references': 1})
        image = self.root/'known.png'
        test_card(image)
        write_json(self.root/'library.json', {'designs': [
            {'id': 'known', 'character': 'Иван', 'image_path': str(image)}]})
        pipeline = Pipeline(self.root, self.root/'config.json')
        try:
            write_json(pipeline.run/'scenes.json', {'scenes': [
                {'id': 's1', 'characters': ['Иван', 'Пётр'], 'profiles_at_scene': [
                    {'name': 'Иван', 'appearance': []}, {'name': 'Пётр', 'appearance': []}]}]})
            briefs = pipeline.references()
            self.assertEqual(len(briefs['designs']), 1)
            self.assertEqual(briefs['designs'][0]['image_path'], str(image))
            self.assertEqual(briefs['designs'][0]['reused_identity_from'], 'known')
            plan = pipeline.attach_references()
            self.assertEqual(plan['scenes'][0]['references'], [str(image)])
        finally:
            pipeline.store.close()

    def test_stable_portrait_shared_across_scenes_without_importing_later_traits(self):
        write_json(self.root/'config.json', {'book': 'source.fb2', 'style': 'Ink',
                   'stable_character_references': True})
        pipeline = Pipeline(self.root, self.root/'config.json')
        later = {'trait': 'Scar revealed later', 'paragraph_id': 'p99', 'quote': 'scar', 'state': 'permanent'}
        try:
            write_json(pipeline.run/'scenes.json', {'scenes': [
                {'id': 'early', 'characters': ['Иван'], 'profiles_at_scene': [{'name': 'Иван', 'appearance': []}]},
                {'id': 'later', 'characters': ['Иван'], 'profiles_at_scene': [{'name': 'Иван', 'appearance': [later]}]}]})
            briefs = pipeline.references()
            self.assertEqual(len(briefs['designs']), 1)
            self.assertEqual(briefs['designs'][0]['scene_ids'], ['early', 'later'])
            self.assertEqual(briefs['designs'][0]['evidence'], [])
            self.assertNotIn('Scar revealed later', briefs['designs'][0]['prompt'])
        finally:
            pipeline.store.close()

    def test_portrait_translation_uses_source_quote_instead_of_category_label(self):
        write_json(self.root/'config.json', {'book':'source.fb2','style':'Ink',
                   'text':{'endpoint':'http://localhost:1'}})
        pipeline = Pipeline(self.root, self.root/'config.json')
        pid = pipeline.book.chapters[1]['paragraphs'][0]['id']
        fact = {'trait':'седые волосы','paragraph_id':pid,'quote':'седые волосы','state':'permanent'}
        try:
            write_json(pipeline.run/'analysis.json', {'characters':[
                {'name':'Иван','aliases':[],'appearance':[fact]}], 'chapters':[]})
            write_json(pipeline.run/'reference-briefs.json', {'designs':[
                {'id':'a','character':'Иван','evidence':[],'scene_ids':['s1'],'image_path':None}]})
            bad = {'characters':[{'name':'Иван','aliases':[],'appearance':[{**fact,'trait':'hair'}]}]}
            good = {'english_descriptions':['Grey hair']}
            with patch('book_illustrator.pipeline.TextBackend.complete', side_effect=[bad,good]) as complete:
                briefs = pipeline.prepare_reference_descriptions()
            self.assertEqual(complete.call_count,2)
            self.assertIn('Grey hair',briefs['designs'][0]['prompt'])
            self.assertEqual(briefs['designs'][0]['evidence'][0]['quote'],'седые волосы')
        finally:
            pipeline.store.close()

    def test_prior_analysis_is_source_checked_before_inference(self):
        write_json(self.root/'config.json', {'book': 'source.fb2',
                   'pilot': {'chapter_ids': ['ch0003']},
                   'text': {'endpoint': 'http://localhost:1'}, 'prior_analysis': 'prior.json'})
        book = Book(self.source)
        early = book.chapters[1]['paragraphs'][0]['id']
        write_json(self.root/'prior.json', {'source_sha256': book.sha, 'characters': [
            {'name': 'Иван', 'aliases': [], 'appearance': [
                {'paragraph_id': early, 'quote': 'выдуманная цитата',
                 'trait': 'Unsupported', 'state': 'permanent'}]}]})
        pipeline = Pipeline(self.root, self.root/'config.json')
        try:
            with patch('book_illustrator.pipeline.TextBackend.complete') as complete:
                with self.assertRaisesRegex(ValueError, 'Unsupported'):
                    pipeline.analyze()
                complete.assert_not_called()
        finally:
            pipeline.store.close()

    def test_explicit_narrator_alias_merges_registry_but_preserves_source_and_prompt(self):
        from book_illustrator.pipeline import normalize_identities
        fact = {'paragraph_id': 'p1', 'quote': 'Я высок', 'trait': 'Tall', 'state': 'permanent'}
        raw = {'characters': [
            {'name': 'Я', 'aliases': [], 'appearance': [fact]},
            {'name': 'Эйзенхорн', 'aliases': [], 'appearance': []}],
            'chapters': [{'results': [{'scene_candidates': [{'characters': ['Я', 'Эйзенхорн'],
                        'quote': 'Я высок', 'image_prompt': 'A man says Я'}]}]}]}
        result = normalize_identities(raw, {'я': 'Грегор Эйзенхорн', 'эйзенхорн': 'Грегор Эйзенхорн'})
        self.assertEqual(len(result['characters']), 1)
        self.assertEqual(result['characters'][0]['name'], 'Грегор Эйзенхорн')
        scene = result['chapters'][0]['results'][0]['scene_candidates'][0]
        self.assertEqual(scene['characters'], ['Грегор Эйзенхорн'])
        self.assertEqual(scene['quote'], 'Я высок')
        self.assertEqual(scene['image_prompt'], 'A man says Я')
        self.assertEqual(raw['characters'][0]['name'], 'Я')

    def test_distributed_selection_covers_chapter_instead_of_high_score_cluster(self):
        from book_illustrator.pipeline import select_distributed_scenes
        paragraphs = [{'id': f'p{i:03}', 'text': 'x'*100} for i in range(20)]
        candidates = [{'after_paragraph_id': p['id'], 'importance': 5 if i<5 else 3,
                       'title': 'Repeated meeting' if i<5 else f'Event {i}',
                       'description': 'Same audience with aristocrat' if i<5 else f'Action number {i}'}
                      for i,p in enumerate(paragraphs)]
        chosen = select_distributed_scenes(candidates, paragraphs, 5)
        self.assertEqual(len(chosen), 5)
        self.assertEqual(len({min(int((int(s['after_paragraph_id'][1:])+1)/20*5),4) for s in chosen}),5)
        self.assertEqual([s['after_paragraph_id'] for s in chosen], sorted(s['after_paragraph_id'] for s in chosen))

    def test_unique_surname_resolved_but_shared_family_name_stays_ambiguous(self):
        from book_illustrator.pipeline import normalize_identities
        def person(name):
            return {'name':name,'aliases':[],'appearance':[]}
        data = {'characters':[person('Эйклон'),person('Мурдин Эйклон')]}
        result = normalize_identities(data,{},True)
        self.assertEqual(len(result['characters']),1)
        self.assertEqual(result['characters'][0]['name'],'Мурдин Эйклон')
        data['characters'].append(person('Другой Эйклон'))
        result = normalize_identities(data,{},True)
        self.assertEqual(len(result['characters']),3)

    def test_distributed_selection_fills_empty_regions_without_duplicates(self):
        from book_illustrator.pipeline import select_distributed_scenes
        paragraphs = [{'id': f'p{i:03}', 'text': 'x'*100} for i in range(20)]
        candidates = [{'after_paragraph_id': paragraphs[i]['id'], 'importance': 4,
                       'title': f'Scene {i}', 'description': f'Event {i}'} for i in [0,1,2,17,18,19]]
        chosen = select_distributed_scenes(candidates, paragraphs, 5)
        self.assertEqual(len({s['after_paragraph_id'] for s in chosen}),5)

    def test_text_only_profile_ignores_portrait_inputs_and_reference_instructions(self):
        write_json(self.root/'config.json', {'book': 'source.fb2', 'pilot': {'seed': 7},
            'images': {'plain': {'endpoint': 'http://localhost:1', 'workflow': 'workflow.json', 'conditioning': 'text_only',
                                'require_character_references': False}}})
        write_json(self.root/'workflow.json', {})
        pipeline = Pipeline(self.root, self.root/'config.json')
        try:
            write_json(pipeline.run/'scenes.json', {'scenes': [
                {'id': 's1', 'title': 'Scene', 'description': 'A man', 'quote': 'Иван',
                 'approved': False, 'after_paragraph_id': 'p000005', 'characters': ['Иван'],
                 'profiles_at_scene': [{'name': 'Иван', 'appearance': []}],
                 'references': ['missing-reference.png'], 'image_prompt': 'A man', 'style': 'Ink'}]})
            def generate(_self, prompt, refs, seed, destination):
                self.assertEqual(refs, [])
                self.assertNotIn('Reference image', prompt)
                test_card(destination)
                return {'path': str(destination), 'seed': seed, 'prompt_id': 'test'}
            with patch('book_illustrator.pipeline.ComfyBackend.validate_workflow', return_value={}), \
                 patch('book_illustrator.pipeline.ComfyBackend.generate', generate):
                results = pipeline.render('plain')
            self.assertEqual(results[0]['reference_count'], 0)
        finally:
            pipeline.store.close()

    def test_nearest_section_and_notes(self):
        book = Book(self.source)
        self.assertEqual([c['title'] for c in book.chapters], ['Предисловие', 'Глава 1', 'Глава 2'])
        self.assertEqual(len(book.chapters[1]['paragraphs']), 3)
        self.assertEqual(book.chapters[1]['parents'], ['Часть первая'])

    def test_export_preserves_mixed_markup_notes_and_embedded_images(self):
        book = Book(self.source)
        image = self.root / 'card.png'
        test_card(image)
        source_bytes = self.source.read_bytes()
        pid = book.chapters[1]['paragraphs'][-1]['id']  # a p nested inside cite
        output = self.root / 'out.fb2'
        report = book.export([{'path': str(image), 'after_paragraph_id': pid}], output)
        self.assertTrue(report['original_structure_preserved'])
        result = Book(output)
        inserted = next(result.root.iter(f'{{{FB}}}image'))
        self.assertEqual(result.parents[inserted].tag, f'{{{FB}}}section')
        self.assertEqual(self.source.read_bytes(), source_bytes)
        verify_export(self.source, output, 1)

    def test_repeated_exports_do_not_accumulate_another_models_illustrations(self):
        book = Book(self.source)
        image = self.root/'card.png'
        test_card(image)
        entries = [{'path':str(image),'after_paragraph_id':book.chapters[1]['paragraphs'][-1]['id']}]
        first = book.export(entries,self.root/'model-a.fb2')
        second = book.export(entries,self.root/'model-b.fb2')
        self.assertEqual(first['added_illustrations'],1)
        self.assertEqual(second['added_illustrations'],1)
        self.assertEqual(len(list(book.root.iter(f'{{{FB}}}image'))),0)

    def test_preserves_existing_binary(self):
        image = self.root / 'card.png'
        test_card(image)
        from xml.etree import ElementTree as ET
        book = Book(self.source)
        blob = ET.SubElement(book.root, f'{{{FB}}}binary', {'id': 'old-cover', 'content-type': 'image/png'})
        blob.text = base64.b64encode(image.read_bytes()).decode()
        ET.ElementTree(book.root).write(self.source, encoding='utf-8', xml_declaration=True)
        book = Book(self.source)
        output = self.root / 'output.fb2'
        book.export([{'path': str(image), 'after_paragraph_id': book.chapters[1]['paragraphs'][0]['id']}], output)
        self.assertEqual(len(Book(output).root.findall(f'{{{FB}}}binary')), 2)

    def test_export_detects_modified_text(self):
        book = Book(self.source)
        output = self.root / 'out.fb2'
        book.export([], output)
        output.write_text(output.read_text(encoding='utf-8').replace('седые', 'чёрные'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed original'):
            verify_export(self.source, output)

    def test_vendor_wrapper_normalization_keeps_paragraphs_and_source_untouched(self):
        from xml.etree import ElementTree as ET
        book = Book(self.source)
        body = book.root.find(f'{{{FB}}}body')
        header = ET.Element(f'{{{FB}}}myheader')
        ET.SubElement(header, f'{{{FB}}}p').text = 'Publisher header'
        body.insert(0, header)
        title = ET.Element(f'{{{FB}}}title')
        ET.SubElement(title, f'{{{FB}}}p').text = 'Book title'
        body.insert(1, title)
        footer = ET.SubElement(body, f'{{{FB}}}myfooter')
        ET.SubElement(footer, f'{{{FB}}}p').text = 'Publisher footer'
        ET.ElementTree(book.root).write(self.source, encoding='utf-8', xml_declaration=True)
        original = self.source.read_bytes()
        output = self.root/'normalized.fb2'
        report = Book(self.source).export([], output, normalize_publisher_headers=True)
        self.assertTrue(report['text_and_original_links_preserved'])
        self.assertFalse(report['original_structure_preserved'])
        self.assertEqual(report['declared_markup_normalization'],
                         {'publisher_headers': 1, 'publisher_footers': 1, 'body_titles_wrapped': 1})
        self.assertEqual(self.source.read_bytes(), original)
        self.assertNotIn('myheader', output.read_text(encoding='utf8'))

    def test_source_overwrite_and_bad_anchor_rejected(self):
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            Book(self.source).export([], self.source)
        image = self.root / 'card.png'
        test_card(image)
        with self.assertRaisesRegex(ValueError, 'Unknown paragraph'):
            Book(self.source).export([{'path': str(image), 'after_paragraph_id': 'bad'}], self.root/'out.fb2')

    def test_quote_must_be_verbatim_and_in_cited_paragraph(self):
        paragraph = {'id': 'p000001', 'text': 'У него седые волосы.'}
        data = {'characters': [{'name': 'Иван', 'aliases': [], 'appearance': [
            {'trait': 'Grey hair', 'paragraph_id': 'p000001', 'quote': 'седые волосы', 'state': 'permanent'}]}],
                'locations': [], 'scene_candidates': []}
        validate_analysis(data, [paragraph])
        data['characters'][0]['appearance'][0]['quote'] = 'синие глаза'
        with self.assertRaisesRegex(ValueError, 'non-verbatim'):
            validate_analysis(data, [paragraph])

    def test_cache_invalidates_on_inputs_and_recovers_failed_job(self):
        store = Store(self.root/'run')
        calls = []
        def operation():
            calls.append(True)
            return {'ok': True}
        try:
            store.run('a', {'model': 'one'}, operation)
            store.run('a', {'model': 'one'}, operation)
            store.run('a', {'model': 'two'}, operation)
            self.assertEqual(len(calls), 2)
            def fail():
                raise ValueError('interrupted')
            with self.assertRaises(ValueError):
                store.run('b', {}, fail)
            store.run('b', {}, operation)
            self.assertTrue(all(row['state'] == 'done' for row in store.status()))
        finally:
            store.close()

    def test_quote_punctuation_repair_copies_unique_source_span_and_records_it(self):
        from book_illustrator.pipeline import validate_quote
        source = {'p1': 'Он сказал: ты ведь уже труп, как и все они.'}
        fact = {'paragraph_id': 'p1', 'quote': 'Ты ведь уже труп.'}
        repair = validate_quote(fact, source)
        self.assertEqual(fact['quote'], 'ты ведь уже труп')
        self.assertIn(fact['quote'], source['p1'])
        self.assertEqual(repair['model_quote'], 'Ты ведь уже труп.')
        with self.assertRaises(ValueError):
            validate_quote({'paragraph_id': 'p1', 'quote': 'Ты ведь уже мёртв.'}, source)
        with self.assertRaises(ValueError):
            validate_quote({'paragraph_id': 'p2', 'quote': 'Ты ведь уже труп.'}, source)
        fact = {'paragraph_id': 'p1', 'quote': '— Знаешь, Грегор'}
        validate_quote(fact, {'p1': '—\u00a0Знаешь, Грегор, ты ведь уже труп.'})
        self.assertEqual(fact['quote'], '—\u00a0Знаешь, Грегор')

    def test_prior_character_evidence_allowed_but_scene_anchor_must_be_current(self):
        earlier = [{'id': 'p000001', 'text': 'У Ивана тёмные волосы.'}]
        current = [{'id': 'p000010', 'text': 'Иван открыл дверь.'}]
        data = {'characters': [{'name': 'Иван', 'aliases': [], 'appearance': [
            {'trait': 'Dark hair', 'paragraph_id': 'p000001', 'quote': 'тёмные волосы', 'state': 'permanent'}]}],
            'locations': [], 'scene_candidates': [{'title': 'Door', 'after_paragraph_id': 'p000010',
            'quote': 'открыл дверь', 'characters': ['Иван'], 'description': 'Opens door',
            'image_prompt': 'A man opens a door', 'importance': 3}]}
        validate_analysis(deepcopy(data), current, earlier)
        with self.assertRaises(ValueError):
            validate_analysis(deepcopy(data), current)  # no unchecked full-book access
        data['scene_candidates'][0]['after_paragraph_id'] = 'p000001'
        data['scene_candidates'][0]['quote'] = 'тёмные волосы'
        with self.assertRaises(ValueError):
            validate_analysis(data, current, earlier)

    def test_partial_evidence_quarantines_unsupported_words_instead_of_accepting_them(self):
        from book_illustrator.pipeline import quarantine_invalid_items
        data = {'characters': [{'name': 'Иван', 'aliases': [], 'appearance': [
            {'trait': 'Dark hair', 'paragraph_id': 'p1', 'quote': 'тёмные волосы', 'state': 'permanent'},
            {'trait': 'Blue eyes', 'paragraph_id': 'p1', 'quote': 'синие глаза', 'state': 'permanent'}]}],
            'locations': [], 'scene_candidates': []}
        result = quarantine_invalid_items(data, [{'id': 'p1', 'text': 'У Ивана тёмные волосы.'}], [])
        self.assertEqual(len(result['characters'][0]['appearance']), 1)
        self.assertEqual(len(result['_rejected_items']), 1)
        self.assertTrue(result['_needs_review'])

    def test_end_to_end_mock_backend_no_future_appearance_and_approval(self):
        config = {'book': 'source.fb2', 'pilot': {'chapter_ids': ['ch0002', 'ch0003'],
                  'illustrations_per_chapter': 1, 'max_chunk_chars': 16000, 'seed': 10},
                  'text': {'endpoint': 'http://localhost:1/v1', 'model': 'test'}, 'style': 'Ink',
                  'images': {'test': {'endpoint': 'http://localhost:2', 'workflow': 'workflow.json',
                             'bindings': {'prompt': ['1', 'text'], 'references': []},
                             'require_character_references': False, 'output_node': '1'}}}
        write_json(self.root/'config.json', config)
        write_json(self.root/'workflow.json', {'1': {'class_type': 'test', 'inputs': {'text': ''}}})
        def answer(_self, _system, payload, _schema):
            paragraphs = payload['paragraphs']
            first, last = paragraphs[0], paragraphs[-1]
            return {'characters': [{'name': 'Иван', 'aliases': [], 'appearance': [
                {'trait': 'Grey hair' if 'седые' in first['text'] else 'Red coat',
                 'paragraph_id': first['id'], 'quote': first['text'],
                 'state': 'permanent' if 'седые' in first['text'] else 'temporary'}]}],
                 'locations': [], 'scene_candidates': [{'title': 'Scene', 'after_paragraph_id': last['id'],
                 'quote': last['text'], 'characters': ['Иван'], 'description': 'A scene',
                 'image_prompt': 'A traveller', 'importance': 4}]}
        def generate(_self, prompt, refs, seed, destination):
            test_card(destination)
            return {'path': str(destination), 'seed': seed, 'prompt_id': 'test-only'}
        pipeline = Pipeline(self.root, self.root/'config.json')
        try:
            with patch('book_illustrator.pipeline.TextBackend.complete', answer):
                pipeline.analyze()
            plan = pipeline.plan()
            self.assertNotIn('Red coat', json.dumps(plan['scenes'][0]['profiles_at_scene']))
            with patch('book_illustrator.pipeline.ComfyBackend.validate_workflow', return_value={}), \
                 patch('book_illustrator.pipeline.ComfyBackend.generate', generate):
                pipeline.render('test')
            with self.assertRaisesRegex(ValueError, 'Review images'):
                pipeline.export('test')
            before = plan_fingerprint(plan)
            rendered = json.loads((pipeline.run/'renders-test.json').read_text(encoding='utf8'))
            hashes = {item['scene_id']: item['image_sha256'] for item in rendered['images']}
            for scene in plan['scenes']:
                scene['approved'] = True
                scene['approved_image_sha256'] = hashes[scene['id']]
            self.assertEqual(before, plan_fingerprint(plan))
            write_json(pipeline.run/'scenes.json', plan)
            report = pipeline.export('test')
            self.assertEqual(report['added_illustrations'], 2)
            plan['scenes'][0]['approved_image_sha256'] = 'another-image-revision'
            write_json(pipeline.run/'scenes.json', plan)
            with self.assertRaisesRegex(ValueError, 'image revision'):
                pipeline.export('test')
        finally:
            pipeline.store.close()


if __name__ == '__main__':
    unittest.main()
