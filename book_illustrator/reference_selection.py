"""Fail-closed eligibility for reusable face identities, distinct from editability."""
REFERENCE_SCREENING_VERSION = 1


def reference_rejection_reason(assignment, face):
    x1, y1, x2, y2 = face['box']
    if min(x2-x1, y2-y1) < 48:
        return 'native face smaller than 48 pixels'
    if assignment['confidence'] < 0.9:
        return 'uncertain identity'
    if face['confidence'] < 0.3:
        return 'uncertain face detection'
    if assignment.get('reference_headgear', 'unknown') != 'none':
        return 'headgear, mask, or unknown headgear status; unsuitable identity reference'
    if assignment.get('reference_face_visibility', 'unknown') != 'clear':
        return 'face occluded or visibility unverified'
    if any(word in assignment.get('pose', '').lower() for word in
           ['back of head', 'hidden', 'eyes closed', 'lying down']):
        return 'unsuitable reference pose'
    return None
