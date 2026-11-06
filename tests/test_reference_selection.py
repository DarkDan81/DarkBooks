from copy import deepcopy
from pathlib import Path
import unittest
from book_illustrator.core import read_json
from book_illustrator.reference_selection import reference_rejection_reason
from book_illustrator.pipeline import compile_prompt,normalize_scene_terms


class ReferenceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.face={'box':[100,100,180,210],'confidence':0.95}
        self.assignment={'confidence':0.95,'editable':True,'pose':'Three-quarter view, looking forward.',
                         'reference_headgear':'none','reference_face_visibility':'clear'}

    def test_open_helmet_is_rejected_even_when_editable_and_pose_omits_headgear(self):
        a={**self.assignment,'reference_headgear':'helmet'}
        self.assertTrue(a['editable'])
        self.assertIn('headgear',reference_rejection_reason(a,self.face))

    def test_masks_hoods_and_uncertain_screening_cannot_become_references(self):
        for kind in ['mask','other','unknown']:
            with self.subTest(kind=kind):
                self.assertIsNotNone(reference_rejection_reason({**self.assignment,'reference_headgear':kind},self.face))
        legacy={k:v for k,v in self.assignment.items() if not k.startswith('reference_')}
        self.assertIsNotNone(reference_rejection_reason(legacy,self.face))

    def test_clear_bare_face_is_accepted_but_occluded_face_is_not(self):
        self.assertIsNone(reference_rejection_reason(self.assignment,self.face))
        self.assertIsNotNone(reference_rejection_reason({**self.assignment,'reference_face_visibility':'occluded'},self.face))
        self.assertIsNone(reference_rejection_reason({**self.assignment,'pose':'Clear face, no mask or helmet.'},self.face))

    def test_first_eligible_candidate_skips_helmet_then_keeps_bare_face(self):
        candidates=[{**self.assignment,'reference_headgear':'helmet'},self.assignment]
        index=next(i for i,a in enumerate(candidates) if reference_rejection_reason(a,self.face) is None)
        self.assertEqual(index,1)


class SceneTerminologyTests(unittest.TestCase):
    def setUp(self):
        self.setting={'prompt':'Fictional science-fiction world.', 'translation_glossary':[{'preferred_en':'armoured gun-cutter aircraft','avoid_en':['battlecat','battlecats','battle cat','battle cats','battle-cat','battle-cats']}]}

    def test_all_known_battlecat_mistranslations_are_corrected_in_final_prompt(self):
        for term in self.setting['translation_glossary'][0]['avoid_en']:
            scene={'image_prompt':'A massive '+term.upper()+' lands on the platform.',
                   'profiles_at_scene':[],'style':'Ink.','setting':self.setting}
            original=deepcopy(scene);prompt=compile_prompt(scene)
            self.assertIn('armoured gun-cutter aircraft lands',prompt)
            self.assertNotIn(term.upper(),prompt)
            self.assertEqual(scene,original)

    def test_other_words_and_settings_are_not_rewritten(self):
        text='A cat watches a gun-cutter aircraft. A battlecatcher stands nearby.'
        self.assertEqual(normalize_scene_terms(text,self.setting),text)
        self.assertEqual(normalize_scene_terms('A battlecat lands.',{}),'A battlecat lands.')
