"""Fit scale + translation only; reject edits that require a different head pose."""
import numpy as np
import cv2

def fit_scale_translation(source,target):
    source=np.asarray(source,dtype=float);target=np.asarray(target,dtype=float)
    a=source-source.mean(axis=0);b=target-target.mean(axis=0)
    denominator=float((a*a).sum())
    if denominator<1e-6:raise ValueError('Degenerate face landmarks')
    scale=float((a*b).sum()/denominator)
    translation=target.mean(axis=0)-scale*source.mean(axis=0)
    matrix=np.array([[scale,0,translation[0]],[0,scale,translation[1]]])
    residual=float(np.sqrt(np.mean(np.sum((source*scale+translation-target)**2,axis=1))))
    return matrix,residual

def face_mask(points,shape):
    points=np.asarray(points,dtype=float)
    # YuNet order: two eyes, nose, two mouth corners; orientation is retained.
    eye=(points[0]+points[1])/2;mouth=(points[3]+points[4])/2
    horizontal=points[1]-points[0];vertical=mouth-eye
    vertices=np.vstack([points,eye-vertical*.65-horizontal*.72,eye-vertical*.65+horizontal*.72,
        eye+vertical*.45-horizontal*.85,eye+vertical*.45+horizontal*.85,
        mouth+vertical*.45-horizontal*.50,mouth+vertical*.45+horizontal*.50])
    mask=np.zeros(shape[:2],np.uint8)
    cv2.fillConvexPoly(mask,cv2.convexHull(np.round(vertices).astype(np.int32)),255)
    return cv2.GaussianBlur(mask,(0,0),max(1.,np.linalg.norm(horizontal)*.035))

def align_crop(edited,source_face,target_face):
    target=np.asarray(target_face['landmarks'],float)
    matrix,residual=fit_scale_translation(source_face['landmarks'],target)
    box=target_face['box'];diagonal=float(np.hypot(box[2]-box[0],box[3]-box[1]))
    normalized=residual/max(1,diagonal)
    report={'scale':float(matrix[0,0]),'translation':matrix[:,2].tolist(),
            'landmark_error_px':residual,'normalized_landmark_error':normalized,'matrix':matrix.tolist()}
    if not .5<=matrix[0,0]<=2 or normalized>.035:
        return None,None,{**report,'accepted':False,'reason':'Pose/geometry incompatible with scale and translation'}
    height,width=edited.shape[:2]
    aligned=cv2.warpAffine(edited,matrix,(width,height),flags=cv2.INTER_LANCZOS4,borderValue=(255,255,255))
    valid=cv2.warpAffine(np.full((height,width),255,np.uint8),matrix,(width,height),flags=cv2.INTER_LINEAR)
    mask=np.minimum(face_mask(target,edited.shape),valid)
    return aligned,mask,{**report,'accepted':True}
