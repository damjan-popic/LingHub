"""Complete saved answer display; no lookup or inference."""
from copy import deepcopy
import hashlib
import pytest
from pilot_backend.document_resource_context import VERSION,records,digest
from pilot_backend.source_reader import source_view

def fixture():
    text='Urejamo besedilo.';sha=hashlib.sha256(text.encode()).hexdigest()
    passages=[]
    for i,(a,b,body)in enumerate([(0,9,'Pogoj A. '),(7,19,'. Odgovor B.')]):
        passages.append({'passage_id':'saved:'+str(i),'source_id':'saved:answer','source_revision':'2'*64,
            'source_slug':'synthetic','role':'expert_answer','text':body,'rights_mode':'restricted',
            'context':{'question':'Katero besedilo?'},'span':{'post_id':'post','char_start':a,'char_end':b}})
    reference={'contract':'klikai.reference-source-context.v1','source_only':True,'passages':passages}
    value={'version':VERSION,'source_sha256':sha,'envelopes':[{'occurrence':{'start':0,'end':len(text),
        'text':text,'original_sha256':sha},'query':text,'language':'sl','source_passages':passages,
        'reference_response':{**reference,'question':text}}]}
    return value,text,sha

def project(value,text,sha):
    value['context_sha256']=digest({k:v for k,v in value.items()if k!='context_sha256'})
    return records(value,text,sha)

def test_complete_group_is_additive_to_requested_chunk():
    value,text,sha=fixture();rows=project(value,text,sha)
    for row in rows:
        source=row['source'];assert source['native_source_unit']['text']==row['native_passage']['text']
        group=source['frozen_native_group'];assert group['original_text']=='Pogoj A. Odgovor B.'
        assert group['body_sha256']==hashlib.sha256(group['original_text'].encode()).hexdigest()
        view=source_view(source);assert view['excerpt']==source['excerpt']
        assert view['reader_blocks'][-1]['text']==group['original_text']

@pytest.mark.parametrize('fault',['gap','overlap','partial','bad_length'])
def test_unproven_group_never_gains_a_complete_display(fault):
    value,text,sha=fixture();p=value['envelopes'][0]['source_passages'][1]
    if fault=='gap':p['span'].update(char_start=10,char_end=22)
    elif fault=='overlap':p['text']='X Odgovor B.'
    elif fault=='bad_length':p['span']['char_end']=20
    else:p.update(context_eligible=False,warnings=['incomplete_answer_group'])
    rows=project(value,text,sha)
    assert all('frozen_native_group'not in r['source']for r in rows)
