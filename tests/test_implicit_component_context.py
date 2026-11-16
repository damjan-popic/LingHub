"""Full original context versus lookup candidates, using one whole saved row.

Every original observation below is mechanical, not analyzer output. The
native row is retained from Q2, not newly discovered for these examples.
"""
from copy import deepcopy
import gzip, hashlib, json, os
import importlib.util
from pathlib import Path

import pytest
# CORE has a test module with the same basename. Load this WEB fixture helper
# by its exact path rather than depending on sys.path ordering.
_spec = importlib.util.spec_from_file_location('_component_shared_implicit_controls',
    Path(__file__).with_name('test_implicit_consultation.py'))
controls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(controls)
from tools.zrc_reference.contextual_lexical_scope import supporting_lookups as core_supporting
from tools.zrc_reference.pilot_contextual_consultation import IMPLICIT_VERSION
from tools.zrc_reference.pilot_tool_evidence import tool_passages
from tools.zrc_reference.request_interpretation import authoritative_request_plan

READER_SHA = '2b7b97fdcbb17ec95d796114215bb418ef2950c0e35569106680c5270d6767f5'

def retained():
    data = gzip.decompress((Path(__file__).parent / 'fixtures/implicit_component_context_reader_v1.json.gz').read_bytes())
    assert hashlib.sha256(data).hexdigest() == READER_SHA
    return json.loads(data)

def value(text, words, *, question=None):
    question, context = (text, '') if question is None else (question, text)
    full = question + ('\n' + context if context else '')
    prefix = [('Pogodba', 'pogodba', 'NOUN'), ('o', 'o', 'ADP'),
              ('zaposlitvi', 'zaposlitev', 'NOUN'), ('ostaja', 'ostajati', 'VERB')] if context else []
    return {'question': question, 'context': context,
        'request_plan': authoritative_request_plan(question, context=context, input_language='sl', locale='sl'),
        'analysis': controls.mechanical_analysis(full, prefix + words)}

def delivery(monkeypatch, observed):
    original_saved = controls.saved
    native = json.loads(retained()['body']['excerpt'])
    def saved(case):
        return [{'original_json': deepcopy(native)}] if case == 'COLL-P' else original_saved(case)
    monkeypatch.setattr(controls, 'saved', saved)
    _, capture, calls, analyses, _ = controls.mock_profile(monkeypatch, observed)
    passages = tool_passages(capture)
    items = []
    for i, source in enumerate(passages):
        eid = 'E' + str(i + 1)
        items.append({**deepcopy(source), 'evidence_id': eid, 'supports': [{
            'support_id': eid + '.S1', 'text': source['text'], 'char_start': 0,
            'char_end': len(source['text']), 'source_role': 'source_passage'}]})
    payload = controls._plan_runtime_payload(observed['question'], items, passages,
        request_plan=observed['request_plan'])
    payload['context'] = observed['context']
    payload['tool_evidence'] = capture
    runtime = controls._plan_runtime()
    registry = runtime._answer_registry(payload, 'answer')
    units = [u for u in registry.units if u['payload'].get('version') == IMPLICIT_VERSION]
    return capture, calls, analyses, passages, payload, runtime, registry, units

@pytest.mark.parametrize('text,words', [
    ('Pogodba o zaposlitvi ostaja.', [('Pogodba','pogodba','NOUN'),('o','o','ADP'),('zaposlitvi','zaposlitev','NOUN'),('ostaja','ostajati','VERB')]),
    ('Pogodba govori o zaposlitvi.', [('Pogodba','pogodba','NOUN'),('govori','govoriti','VERB'),('o','o','ADP'),('zaposlitvi','zaposlitev','NOUN')]),
    ('Pogodba ostaja; o zaposlitvi govorimo.', [('Pogodba','pogodba','NOUN'),('ostaja','ostajati','VERB'),('o','o','ADP'),('zaposlitvi','zaposlitev','NOUN'),('govorimo','govoriti','VERB')]),
])
def test_complete_retained_fact_uses_observed_function_word_without_lookup_or_dependency(monkeypatch, text, words):
    observed = value(text, words)
    capture, calls, analyses, passages, payload, runtime, registry, units = delivery(monkeypatch, observed)
    report = capture['captures'][0]['response']['interpretation']['supporting_lookups']
    assert report == core_supporting(observed['request_plan'], 'sl', observed['analysis'])
    assert observed['request_plan']['target_spans'] == [] and registry.required_unit_ids == []
    assert analyses == [True] and len(calls) == len(set(calls)) <= 8
    assert not any(lemma == 'o' for _, lemma in calls)
    assert all(t['supporting_origin']['lemma'] != 'o' for t in report['targets'])
    assert all(g['lemma'] != 'o' for g in report['observed_occurrences'])
    context, = [g for g in report['context_observations'] if g['lemma'] == 'o']
    assert context['upos'] == 'ADP'
    occurrence, = context['occurrences']
    assert observed['analysis']['text'][occurrence['analysis_start']:occurrence['analysis_end']] == occurrence['text']
    assert occurrence['predictions'] == [{'engine':'mechanical','word_id':words.index(next(w for w in words if w[1]=='o')),
        'lemma':'o','upos':'ADP','feats':{}}]
    assert len(units) == 1 and units[0]['consultation_support'] is True
    source, = passages
    native = json.loads(retained()['body']['excerpt'])
    assert source['original_json']['fields'] == native['fields']
    assert source['original_json']['provenance'] == native['provenance']
    assert source['original_json']['entry_id'] == native['entry_id'] == '2762284'
    assert [m['entry_id'] for m in source['original_json']['source_memberships']] == ['2762284']
    record, = units[0]['payload']['records']
    assert record['native_passage'] == source
    assert record['occurrence_basis'] == 'retained_original_lexical_observation_not_dependency'
    result = controls.selected_output(payload, runtime, registry, units)
    answer = result['answer_md']
    assert 'Vir Kolokacije izpričuje zvezo »pogodba o zaposlitvi«' in answer
    assert 'status postopka v viru: manual' in answer and text in answer
    assert 'Ločena analiza odvisnosti izvirnika ni na voljo' in answer
    assert 'Analiza napoveduje zvezo' not in answer and 'popravi' not in answer
    assert len(result['citations']) == 1
    output = os.environ.get('KLIKAI_COMPONENT_RECEIPTS')
    if output and text == 'Pogodba o zaposlitvi ostaja.':
        receipt = {'provenance': {'reader_sha256': READER_SHA,
            'source': 'complete saved Q2 reader3, mechanically reused; not new discovery',
            'original_observations': 'mechanical lemma/POS/offset fixture, not analyzer output',
            'transports': 'mocked', 'selection': 'manual', 'syntax': 'absent', 'actual_operations': 0},
            'supporting_lookups': report, 'mocked_reader_calls': calls,
            'native_source': source, 'registry': registry.model_dump(), 'answer': result}
        Path(output, 'mechanical-component-consumer.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')

@pytest.mark.parametrize('text,words', [
    ('Pogodba zaposlitvi ostaja.', [('Pogodba','pogodba','NOUN'),('zaposlitvi','zaposlitev','NOUN'),('ostaja','ostajati','VERB')]),
    ('Pogodba za zaposlitvi ostaja.', [('Pogodba','pogodba','NOUN'),('za','za','ADP'),('zaposlitvi','zaposlitev','NOUN'),('ostaja','ostajati','VERB')]),
    ('Pogodba o delu ostaja.', [('Pogodba','pogodba','NOUN'),('o','o','ADP'),('delu','delo','NOUN'),('ostaja','ostajati','VERB')]),
])
def test_missing_or_different_component_is_not_admitted(monkeypatch, text, words):
    observed = value(text, words)
    capture, calls, _, passages, _, _, registry, units = delivery(monkeypatch, observed)
    assert len(passages) == 1 # the complete fact was captured, not silently removed by the mock
    assert units == [] and registry.required_unit_ids == []
    assert all(lemma not in {'o','za'} for _, lemma in calls)

@pytest.mark.parametrize('damage', ['lemma', 'class', 'segmentation'])
def test_ambiguous_function_word_context_does_not_use_first_reading(monkeypatch, damage):
    text = 'Pogodba o zaposlitvi ostaja.'
    observed = value(text, [('Pogodba','pogodba','NOUN'),('o','o','ADP'),('zaposlitvi','zaposlitev','NOUN'),('ostaja','ostajati','VERB')])
    other = deepcopy(observed['analysis']['raw_engines']['mechanical'])
    if damage == 'lemma':
        other['analysis']['words'][1]['lemma'] = 'ob'
    elif damage == 'class':
        other['analysis']['words'][1]['upos'] = 'AUX'
    else:
        other['analysis']['words'] = [dict(other['analysis']['words'][1],
            text='o zaposlitvi', end=text.index(' ostaja'), lemma='o zaposlitvi')]
    observed['analysis']['raw_engines']['other'] = other
    capture, _, _, passages, _, _, _, units = delivery(monkeypatch, observed)
    report = capture['captures'][0]['response']['interpretation']['supporting_lookups']
    assert len(passages) == 1 and units == []
    assert not any(g['lemma'] == 'o' for g in report['context_observations'])
    assert any(o['reason'] == ('ambiguous_original_segmentation' if damage == 'segmentation' else 'ambiguous_original_reading') for o in report['omissions'])

def test_component_outside_target_scope_does_not_fill_missing_context(monkeypatch):
    observed = value('Pogodba zaposlitvi ostaja.',
        [('Pogodba','pogodba','NOUN'),('zaposlitvi','zaposlitev','NOUN'),('ostaja','ostajati','VERB')],
        question='Pogodba o zaposlitvi ostaja.')
    capture, _, _, passages, _, _, _, units = delivery(monkeypatch, observed)
    report = capture['captures'][0]['response']['interpretation']['supporting_lookups']
    assert report['target_scope']['scope'] == 'context'
    assert len(passages) == 1 and units == []
    assert not any(g['lemma'] == 'o' for g in report['context_observations'])
    assert all(o['analysis_start'] >= len(observed['question']) + 1
        for g in report['context_observations'] for o in g['occurrences'])

def test_actual_Q3_names_and_function_classes_are_context_without_added_work():
    observed = controls.actual()
    report = core_supporting(observed['request_plan'], 'sl', observed['analysis'])
    assert {'PROPN','AUX','PRON','PART'} <= {g['upos'] for g in report['context_observations']}
    assert len(report['work_ids']) == 6
    assert {t['supporting_origin']['upos'] for t in report['targets']} <= {'NOUN','ADJ','VERB','ADV'}
    for group in report['context_observations']:
        for occurrence in group['occurrences']:
            a, b = occurrence['analysis_start'], occurrence['analysis_end']
            assert observed['analysis']['text'][a:b] == occurrence['text']
            for prediction in occurrence['predictions']:
                words = observed['analysis']['raw_engines'][prediction['engine']]['analysis']['words']
                word, = [w for w in words if w['id'] == prediction['word_id']]
                assert (word['start'], word['end'], word['lemma'], word['upos'], word['feats']) == (
                    a, b, group['lemma'], group['upos'], prediction['feats'])
