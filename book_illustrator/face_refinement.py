"""Local Qwen identity editing with pixel-preserving compositing outside the head mask."""
from pathlib import Path
from time import perf_counter
from PIL import Image,ImageDraw,ImageFilter

def refine_face(backend,image_path,face,portrait,identity,pose,seed,output_dir,strong_identity=False,reference_kind='portrait',prompt_mode='default',align_geometry=False):
    started=perf_counter();output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    original=Image.open(image_path).convert('RGB')
    x1,y1,x2,y2=face['box'];size=max(224,min(512,int(max(x2-x1,y2-y1)*2.2)))
    left=max(0,min(original.width-size,round((x1+x2-size)/2)))
    top=max(0,min(original.height-size,round((y1+y2-size)/2)))
    box=(left,top,left+size,top+size);crop=original.crop(box)
    angle=face['rotation']*90
    if angle:crop=crop.rotate(angle,resample=Image.Resampling.BICUBIC,fillcolor='white')
    crop_path=output_dir/'input.png';crop.resize((512,512),Image.Resampling.LANCZOS).save(crop_path)
    prompt=('Image 1 is a crop from an existing ink book illustration. Image 2 is the identity portrait of '
        +identity+'. Replace only the head and facial features in image 1 with this identity. '
        'Preserve EXACTLY the original head turn, gaze direction, expression and head position from image 1. '
        'Do not copy the frontal portrait pose or make the character look at the viewer. '
        'Transfer the eye shape, brow, nose, lips and jaw identity from image 2. '
        'Preserve the original body, clothes, action, lighting and ink hatching of image 1. '
        'Scene pose: '+pose+'. Output the edited image 1 only, no panels or labels.')
    if strong_identity:
        prompt=('Replace the facial identity in image 1 with the identity of the person in image 2. '
                'This is a substantial face replacement, NOT an upscale or restoration of the old face. '
                'Transfer the distinctive eye shape, brow, nose, lips, jaw proportions and hair from image 2. '
                'The resulting person must look like image 2, not the original person in image 1. '
                'Adapt the reference identity to the head angle and pose already in image 1; do not copy '
                'the frontal reference pose. Preserve gaze direction into the scene rather than at the viewer. '
                'Scene pose: '+pose+'. Keep the head in the same location and size. '
                'Preserve the original clothes, body, background, ink drawing style and lighting. '
                'Output only the edited image 1, no panels or comparison layout.')
    if prompt_mode=='preserve_geometry':
        prompt=('Edit image 1 in place. Image 1 is the authoritative geometry and pose. '
                'Keep EXACTLY its head tilt, yaw, pitch, face silhouette, nose position and pointing direction, '
                'ear visibility, eye positions, gaze and expression. Keep the same perspective and foreshortening. '
                'Do not rotate or straighten the head, reveal the hidden cheek, move the nose, or look toward the viewer. '
                'Image 2 supplies only distinguishing identity features: subtly adapt brows, eye shape, nose details, '
                'mouth and skin character to the EXISTING facial geometry in image 1. Do not copy its head angle, '
                'expression, silhouette, hair outline or lighting. Preserve the original hair outline and ears. '
                'Target scene pose: '+pose+'. Preserve rough ink lines, shadows, hatching, clothes and background '
                'from image 1. No beautification, smooth skin or studio portrait styling. Output edited image 1 only.')
    if prompt_mode in ['balanced_identity','balanced_identity_strong']:
        prompt=('Recast the face in image 1 as the SAME PERSON shown in image 2, photographed from the angle '
                'already present in image 1. Image 1 controls head rotation, downward tilt, perspective, gaze, '
                'expression and lighting. Image 2 controls identity: brow shape, eye sockets, nose shape, cheekbones, '
                'mouth, chin, age, skin character and facial hair. Change those identity features clearly; do not '
                'merely sharpen or restore the original face. Adapt the reference features into the target perspective. '
                'Keep the target nose pointing in its original direction, preserve which ear and cheek are visible '
                'and the existing head silhouette. Never borrow the reference frontal pose or look at the viewer. '
                'Target pose: '+pose+'. Retain the rough ink drawing, hatching, shadows and original clothes. '
                'Output image 1 only, no panels, labels or studio portrait treatment.')
        if prompt_mode=='balanced_identity_strong':
            prompt+=' Prioritize a recognizable match to the reference eyes, brow, cheekbones and mouth while keeping the target head rotation unchanged. Match reference hair texture and facial hair within the existing silhouette; retain its weathered age and asymmetries.'
    if reference_kind=='scene_crop' and prompt_mode=='default':
        prompt+=' Image 2 is an unretouched face crop from an earlier scene, NOT a polished studio portrait. Preserve its rugged facial character, age, irregularities, hair and facial hair as the identity. Retain rough ink hatching and the target scene shadows. Do not beautify, groom, smooth skin or turn this into a fashion portrait.'
    result=backend.generate(prompt,[str(crop_path),str(portrait)],seed,output_dir/'edited-crop.png')
    edited=Image.open(result['path']).convert('RGB').resize((size,size),Image.Resampling.LANCZOS)
    if angle:edited=edited.rotate(-angle,resample=Image.Resampling.BICUBIC,fillcolor='white')
    mask=Image.new('L',(size,size),0);draw=ImageDraw.Draw(mask)
    dx,dy=(x2-x1)*(0.25 if strong_identity else 0.04),(y2-y1)*(0.24 if strong_identity else 0.04)
    extra=(y2-y1)*0.35 if angle==0 else 0
    draw.ellipse((x1-left-dx,y1-top-dy-extra,x2-left+dx,y2-top+dy),fill=255)
    mask=mask.filter(ImageFilter.GaussianBlur(2.5))
    fullmask=Image.new('L',original.size,0);fullmask.paste(mask,(left,top))
    final=original.copy();final.paste(edited,(left,top),mask)
    final=final.convert('L');path=output_dir/'composited.png';final.save(path)
    mask_path=output_dir/'mask.png';fullmask.save(mask_path)
    changed=sum(a!=b for a,b,m in zip(original.convert('L').tobytes(),final.tobytes(),fullmask.tobytes()) if m==0)
    if changed:raise ValueError('Pixels outside mask changed')
    result_data={'path':str(path),'mask':str(mask_path),'seconds':perf_counter()-started,
            'face':face,'crop_box':box,'identity':identity,'portrait':str(portrait),'prompt':prompt,
            'strong_identity':strong_identity,'reference_kind':reference_kind,'prompt_mode':prompt_mode,
            'edit_generation':result,'outside_mask_pixels_changed':changed}

    if align_geometry:
        import subprocess
        from .core import write_json,read_json
        root=Path(__file__).resolve().parents[1]
        write_json(output_dir/'raw-edit.json',result_data)
        subprocess.run([str(root/'runtime/.venv/Scripts/python.exe'),str(root/'scripts/align_face_crop.py'),
            str(output_dir/'raw-edit.json'),str(image_path),str(output_dir/'alignment')],check=True,capture_output=True,cwd=root)
        alignment=read_json(output_dir/'alignment/alignment.json')
        result_data.update(path=alignment['path'],mask=alignment['mask'],geometry_alignment=alignment,
                           mask_policy='Landmark-aligned facial region; preserve original hair and background')
    return result_data
