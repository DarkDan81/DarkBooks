from pathlib import Path
import tempfile
import unittest
from PIL import Image,ImageChops
from book_illustrator.face_refinement import refine_face


class FaceRefinementTests(unittest.TestCase):
    def test_rotated_edits_preserve_every_pixel_outside_saved_mask(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'scene.png';portrait=root/'portrait.png'
            Image.new('RGB',(512,512),'#777777').save(source)
            Image.new('RGB',(128,128),'white').save(portrait)
            prompts=[]
            class Backend:
                def generate(self,prompt,references,seed,destination):
                    prompts.append(prompt)
                    Image.new('RGB',(512,512),'black').save(destination)
                    return {'path':str(destination)}
            for rotation in [0,0.5,1,2]:
                result=refine_face(Backend(),source,{'box':[200,190,270,280],'rotation':rotation},
                                   portrait,'A female character','side view, eyes closed',1,root/str(rotation),True)
                mask=Image.open(result['mask']).convert('L')
                diff=ImageChops.difference(Image.open(source).convert('L'),Image.open(result['path']))
                self.assertGreater(max(diff.tobytes()),0)
                self.assertTrue(all(d==0 for d,m in zip(diff.tobytes(),mask.tobytes()) if not m))
                self.assertEqual(result['outside_mask_pixels_changed'],0)
            self.assertTrue(all('identity of the person' in p for p in prompts))
            self.assertTrue(all('side view, eyes closed' in p for p in prompts))
            result=refine_face(Backend(),source,{'box':[200,190,270,280],'rotation':0},
                portrait,'Gregor Eisenhorn','looking down',1,root/'geometry',True,'scene_crop','preserve_geometry')
            self.assertEqual(result['prompt_mode'],'preserve_geometry')
            self.assertIn('Image 1 is the authoritative geometry',prompts[-1])
            self.assertNotIn('substantial face replacement',prompts[-1])
            self.assertNotIn('Remove ALL beard',prompts[-1])
            for mode in ['balanced_identity','balanced_identity_strong']:
                result=refine_face(Backend(),source,{'box':[200,190,270,280],'rotation':0},
                    portrait,'Gregor Eisenhorn','looking down',1,root/mode,True,'scene_crop',mode)
                self.assertEqual(result['prompt_mode'],mode)
                self.assertIn('Image 2 controls identity',prompts[-1])
                self.assertNotIn('subtly adapt',prompts[-1])
                self.assertNotIn('Remove ALL beard',prompts[-1])
