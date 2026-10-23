"""Browser comparison depth is conditional on the same complete frozen input."""
from copy import deepcopy

import pytest

from pilot_backend.lexical import overview


def bundle(kind='translation', single_source=False):
    task={'version':'task-evidence-needs-v1','kind':kind,'comparison':True,
          'targets':['source expression','option one','option two']if kind=='translation'else['option one','option two'],
          'requested_facets':['translation']if kind=='translation'else[]}
    rows=[{'passage_id':'sense:one','text':'Complete first sense and qualification.',
           'supports':[{'support_id':'E1.S1','char_start':0,'char_end':39}]}]
    if not single_source:
        rows.append({'passage_id':'sense:two','text':'Complete second sense and qualification.',
                     'supports':[{'support_id':'E2.S1','char_start':0,'char_end':40}]})
    packing=[{'passage_ids':[r['passage_id']],'role':'core'}for r in rows]
    short={'model_evidence':deepcopy(rows),'input_profile':{'selection_task':deepcopy(task)}}
    wide={'model_evidence':deepcopy(rows),'input_profile':{'selection_task':deepcopy(task),'packing_order':packing},
          'token_budget':{'status':'verified'},'source_view_reason':None}
    profile={'interpretation':{'query_kind':'question','requested_facets':['translation']if kind=='translation'else[],
             'targets':[{'text':t,'basis':'proposed translation alternative; original surface'}for t in task['targets']]},
             'evidence':[]}
    return {'query':'How should I translate this: option one or option two?',
            'analysis':{'version':'klikai.lexical-collation.v1','captures':[{'response':profile}]},
            'gateway_bundle':{'payload':{'question':'How should I translate this: option one or option two?',
             'packets':{'short':short,'lexical_overview':wide}}}}


@pytest.mark.parametrize('kind,single_source',[('translation',False),('comparison',False),('comparison',True),('construction',True)])
def test_fully_preserved_comparison_uses_existing_wider_allowance(kind,single_source):
    value=bundle(kind,single_source);before=deepcopy(value)
    assert overview(value)is True
    assert value==before


@pytest.mark.parametrize('damage',['task_mismatch','not_comparison','one_target','no_short_evidence','no_wide_evidence',
    'dropped_sense','changed_text','changed_support','duplicate_identity','incomplete_group',
    'only_optional','missing_packing','unverified','source_view','incomplete_homographs'])
def test_wider_output_cannot_substitute_or_weaken_selected_source_evidence(damage):
    value=bundle();packets=value['gateway_bundle']['payload']['packets']
    short=packets['short'];wide=packets['lexical_overview']
    if damage=='task_mismatch':wide['input_profile']['selection_task']['targets']=['another source','another option']
    if damage=='not_comparison':
        for p in packets.values():p['input_profile']['selection_task']['comparison']=False
    if damage=='one_target':
        for p in packets.values():p['input_profile']['selection_task']['targets']=['only one']
    if damage=='no_short_evidence':short['model_evidence']=[]
    if damage=='no_wide_evidence':wide['model_evidence']=[]
    if damage=='dropped_sense':wide['model_evidence'].pop()
    if damage=='changed_text':wide['model_evidence'][0]['text']='A narrower excerpt.'
    if damage=='changed_support':wide['model_evidence'][0]['supports'][0]['char_end']-=1
    if damage=='duplicate_identity':wide['model_evidence'].append(deepcopy(wide['model_evidence'][0]))
    if damage=='incomplete_group':wide['input_profile']['packing_order'][0]['passage_ids'].append('missing:qualification')
    if damage=='only_optional':
        for g in wide['input_profile']['packing_order']:g['role']='optional'
    if damage=='missing_packing':wide['input_profile'].pop('packing_order')
    if damage=='unverified':wide['token_budget']['status']='not_needed_source_view'
    if damage=='source_view':wide['source_view_reason']='incomplete_evidence'
    if damage=='incomplete_homographs':wide['input_profile']['dictionary_alternatives']={'complete':False}
    assert overview(value)is False


@pytest.mark.parametrize('phrase',['Na kratko','KRATKO PROSIM','brevemente','in breve','briefly','in short'])
def test_explicit_brevity_keeps_short_browser_profile(phrase):
    value=bundle();value['gateway_bundle']['payload']['question']=phrase+': '+value['query']
    assert overview(value)is False


def test_brevity_marker_requires_word_boundaries():
    value=bundle();value['gateway_bundle']['payload']['question']='Discuss the options in shorthand.'
    assert overview(value)is True


@pytest.mark.parametrize('requested,brief,expected',[
    ('short',False,'lexical_overview'),('short',True,'short'),('long',False,'long'),
])
def test_browser_dispatch_preserves_explicit_depth_and_uses_only_existing_tiers(requested,brief,expected):
    from pilot_backend import unified
    from pilot_backend.store import digest
    value=bundle()
    if brief:value['gateway_bundle']['payload']['question']='Na kratko: '+value['query']
    value.update(contract=unified.CONTRACT,matches=[{'evidence_id':'sense:one'}])
    value['snapshot_sha256']=digest(value)
    calls=[]
    class CapturedRequest(RuntimeError):pass
    class Gateway:
        def request(self,path,payload):
            calls.append((path,payload))
            raise CapturedRequest('Synthetic transport; never dispatch a model call')
    with pytest.raises(CapturedRequest):unified._generate(Gateway(),value,requested,'qwen')
    assert len(calls)==1 and calls[0][0]=='/internal/pilot/generate'
    assert calls[0][1]['tier']==expected
