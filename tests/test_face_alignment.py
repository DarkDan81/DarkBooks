import unittest
try:
    import numpy as np
    from book_illustrator.face_alignment import fit_scale_translation,align_crop
    AVAILABLE=True
except ImportError:
    AVAILABLE=False

@unittest.skipUnless(AVAILABLE,'Run alignment tests with runtime/.venv/Scripts/python.exe')
class FaceAlignmentTests(unittest.TestCase):
    def test_recovers_known_scale_and_translation_without_rotation(self):
        source=np.array([[20,30],[60,30],[40,50],[25,70],[55,70]],float)
        target=source*1.4+[18,-6]
        matrix,error=fit_scale_translation(source,target)
        self.assertAlmostEqual(matrix[0,0],1.4)
        np.testing.assert_allclose(matrix[:,2],[18,-6])
        self.assertLess(error,1e-8)
        self.assertEqual(matrix[0,1],0)

    def test_different_pose_is_rejected_instead_of_rotating_head(self):
        source=np.array([[20,30],[60,30],[40,50],[25,70],[55,70]],float)
        target=source@np.array([[0,1],[-1,0]])+[100,10]
        image=np.full((160,160,3),180,np.uint8)
        aligned,mask,result=align_crop(image,{'landmarks':source}, {'landmarks':target,'box':[10,20,110,120]})
        self.assertFalse(result['accepted']);self.assertIsNone(aligned)

    def test_mask_keeps_background_untouched(self):
        points=np.array([[50,50],[90,50],[70,70],[55,90],[85,90]],float)
        image=np.full((160,160,3),120,np.uint8)
        aligned,mask,result=align_crop(image,{'landmarks':points},{'landmarks':points,'box':[35,30,105,110]})
        self.assertTrue(result['accepted'])
        self.assertEqual(int(mask[0,0]),0)
        self.assertGreater(mask[70,70],200)
