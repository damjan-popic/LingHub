"""The actual profile producer retains requested whole nominal paradigms."""
import copy,gzip,json,os
from pathlib import Path
import pytest
import test_source_advice_morphology as producer
from core.lexical_identity import source_occurrence_key,morphology_key
from core.lexical_profile import full_paradigm_occurrences

F=Path(__file__).parent/'fixtures'
ENTRIES=sum([json.loads(gzip.decompress((F/name).read_bytes()))['lexemes']for name in
    ['language_choice_sloleks.json.gz','generic_paradigm_sloleks.json.gz']],[])

@pytest.fixture
def core(monkeypatch):
    root=Path(os.environ['KLIKAI_PINNED_CORE_ROOT'])
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setattr(producer,'ENTRIES',ENTRIES)
    return root

@pytest.mark.parametrize('name',['Luka','Miha','Aljoša','Marko','Marka','Nina'])
@pytest.mark.parametrize('mixed',[False,True])
def test_whole_name_request_keeps_all_recorded_slots_per_occurrence(core,monkeypatch,name,mixed):
    from tools.zrc_reference.request_interpretation import response
    from tools.zrc_reference.pilot_tool_evidence import tool_passages,validate_capture_set
    from tools.zrc_reference.common import value_hash
    question='Kako se sklanja ime '+name+(' in kako se prevaja v italijanščino?'if mixed else'?')
    plan=response(question,'','sl','sl')['request_plan'];before=copy.deepcopy(plan)
    value,_=producer.profile(monkeypatch,question,None,plan)
    assert value['interpretation']['request_plan']==plan==before
    selected=[le for le in ENTRIES if le['lemma']==name and le['grammatical_type']=='noun'
        and le['grammatical_features'].get('type')=='proper']
    assert selected
    for le in selected:
        own=[u for u in value['evidence']if u.get('source_occurrence_key')==source_occurrence_key(le['evidence'])]
        assert own
        header,=[u for u in own if 'slot'not in u['fields']]
        assert header['fields']['paradigm_slot_count']==len(le['slots'])
        assert {u['fields']['slot']['slot_locator']:u['fields']['slot']for u in own if 'slot'in u['fields']}=={s['slot_locator']:s for s in le['slots']}
    cap={'operation':'lexical_profile','request':value['query'],'response':value,'response_sha256':value_hash(value),'duration_ms':0}
    capture={'version':'klikai.lexical-collation.v1','plan':{'status':'ready','routing_kind':'automatic_lexical_profile','focus':question},'captures':[cap],'capture_set_sha256':value_hash([cap])}
    validate_capture_set(capture)
    assert len(tool_passages(capture))>=sum(len(le['slots'])+1 for le in selected)

def test_explicit_gender_and_class_limits_preserve_other_occurrences_without_expanding_them():
    lexemes=copy.deepcopy([le for le in ENTRIES if le['lemma']=='Marka'])
    assert lexemes
    base={'requested_facets':['inflection'],'explicit_lexemes':[{'text':'Marka','lexical_kind':'proper_name','gender':'feminine'}]}
    expanded=full_paradigm_occurrences(base,lexemes)
    assert expanded=={morphology_key(le)for le in lexemes if le['grammatical_type']=='noun'
        and le['grammatical_features'].get('gender')=='feminine'and le['grammatical_features'].get('type')=='proper'}
    assert not full_paradigm_occurrences(dict(base,slot_focus={'case':'genitive'}),lexemes)
    assert not full_paradigm_occurrences(dict(base,requested_grammar_slots=[{'case':'dative'}]),lexemes)
    assert not full_paradigm_occurrences(dict(base,requested_facets=['translation']),lexemes)
    assert not full_paradigm_occurrences(dict(base,explicit_lexemes=[]),lexemes)

@pytest.mark.parametrize('lemma',['otrok','vojvoda'])
def test_explicit_common_noun_role_also_expands_without_name_assumption(lemma):
    lexemes=[le for le in ENTRIES if le['lemma']==lemma]
    p={'requested_facets':['inflection'],'explicit_lexemes':[{'text':lemma,'lexical_kind':'common_noun'}]}
    assert full_paradigm_occurrences(p,lexemes)=={morphology_key(le)for le in lexemes if le['grammatical_type']=='noun'}
