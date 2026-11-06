"""Recover from response truncation without accepting partial JSON."""
from copy import deepcopy

class ModelCapacityError(ValueError):
    pass

def capacity_error(error):
    message=str(error).lower()
    return any(s in message for s in ('context length','context window','context size','exceeds the available context','too many tokens','maximum context','model output incomplete: length','n_keep','context overflow'))

def adaptive_chat(request,url,body,*,context_length=32768,timeout=600,headers=None):
    body=deepcopy(body);attempts=[]
    # Output allowance expands only when the server reports truncation.
    ceiling=context_length-512
    while True:
        from .control import check_pause
        check_pause()
        body['max_tokens']=min(body.get('max_tokens',5000),ceiling)
        try:result=request(url,body,timeout=timeout,headers=headers)
        except RuntimeError as error:
            if capacity_error(error):raise ModelCapacityError(str(error)) from error
            raise
        choice=result['choices'][0]
        attempts.append({'max_tokens':body['max_tokens'],'finish_reason':choice.get('finish_reason'),'usage':result.get('usage')})
        if choice.get('finish_reason')!='length':
            result['_adaptive_attempts']=attempts
            return result
        prompt_tokens=(result.get('usage') or {}).get('prompt_tokens')
        available=min(ceiling,context_length-prompt_tokens-256) if prompt_tokens else ceiling
        next_budget=min(body['max_tokens']*2,available)
        if next_budget<=body['max_tokens']:
            raise ModelCapacityError('Model output incomplete: length; exhausted available context, split the request')
        body['max_tokens']=next_budget

def split_payload(stage,payload):
    """Split independent work units, retaining source data in both cached children."""
    def halves(path):
        value=payload
        for key in path:value=value[key]
        if len(value)<2:return None
        output=[]
        for part in (value[:len(value)//2],value[len(value)//2:]):
            child=deepcopy(payload);target=child
            for key in path[:-1]:target=target[key]
            target[path[-1]]=part;output.append(child)
        return output
    if stage.endswith('02-atomic-appearance'):
        return halves(['characters']) or (halves(['characters',0,'sentences']) if len(payload['characters'])==1 else None)
    if stage.endswith('01-visual-rank'):return halves(['paragraphs'])
    if stage.endswith('selected-prompts'):return halves(['episodes'])
    for key in ('scenes','names','items'):
        if isinstance(payload.get(key),list):
            result=halves([key])
            if result:return result
    if isinstance(payload.get('target'),dict) and 'paragraphs' in payload['target']:
        return halves(['target','paragraphs'])
    return None

def merge_responses(left,right):
    """Merge independent results; named people retain the union of cited facts."""
    if left==right:return deepcopy(left)
    if isinstance(left,dict) and isinstance(right,dict):
        return {k:merge_responses(left[k],right[k]) for k in left}
    if isinstance(left,list) and isinstance(right,list):
        result=deepcopy(left)
        for item in right:
            if item in result:continue
            # Only registry records are joined by name; episodes/issues remain separate.
            match=next((i for i,old in enumerate(result) if isinstance(item,dict) and isinstance(old,dict)
                        and item.get('name') and item.get('name')==old.get('name')),None)
            if match is None:result.append(deepcopy(item))
            else:result[match]=merge_responses(result[match],item)
        return result
    if left=='unknown':return deepcopy(right)
    if right=='unknown':return deepcopy(left)
    if left in ('male','female') and right in ('male','female'):return 'unknown'
    raise ValueError('Cannot safely merge conflicting scalar model answers')

def vision_face_batches(request,url,body,indices,*,context_length=8192):
    """Label small independent face sets; subdivide again on context exhaustion."""
    import json
    def visit(group):
        scoped=deepcopy(body)
        scoped['messages'][0]['content'][0]['text']+=' Return records ONLY for these numbered face_index values: '+json.dumps(group)
        try:
            result=adaptive_chat(request,url,scoped,context_length=context_length,timeout=600)
        except ModelCapacityError:
            if len(group)<2:raise
            middle=len(group)//2
            a,aa=visit(group[:middle]);b,bb=visit(group[middle:])
            return a+b,aa+bb
        choice=result['choices'][0]
        if choice.get('finish_reason') not in ('stop','eos',None):
            raise ValueError('Incomplete vision output: '+str(choice.get('finish_reason')))
        message=choice['message']
        rows=json.loads(message.get('content') or message.get('reasoning_content'))['faces']
        return [r for r in rows if r['face_index'] in group],result['_adaptive_attempts']
    rows=[];attempts=[]
    for start in range(0,len(indices),3):
        batch,events=visit(indices[start:start+3]);rows.extend(batch);attempts.extend(events)
    return rows,attempts
