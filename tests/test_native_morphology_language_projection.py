"""Native label projection, without constructing an app or reading a source."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def actual_projection():
    namespace = {'copy': __import__('copy'), 'hashlib': hashlib, 'json': json}
    for relative, names in [
        ('core/platform_api.py', {'public'}),
        ('core/lexical_identity.py', {'source_occurrence_key', 'morphology_key'}),
        ('core/lexical_profile.py', {'digest', 'source_record', 'morphological_records'}),
    ]:
        source = (ROOT / relative).read_text()
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in nodes} == names
        exec(compile(ast.Module(body=nodes, type_ignores=[]), relative, 'exec'), namespace)
    return namespace['morphological_records']


@pytest.mark.parametrize('language', ['sl', 'it', None])
def test_native_language_is_preserved_in_complete_head_and_slot_only_when_present(language):
    lexeme = {
        'source_entry_id': 'synthetic-native:1', 'lemma': 'akt',
        'grammatical_type': 'noun', 'grammatical_features': {'gender': 'masculine'},
        'source_status': 'synthetic', 'frequency_observations': [],
        'submitted_form': 'akt', 'submitted_analyses': [], 'matched_by': 'exact_headword',
        'head_source': {'entry_id': 'synthetic-native:1'},
        'evidence': {'resource_id': 'synthetic-native', 'version': 'synthetic-release'},
        'slots': [{'slot_locator': 1, 'normalized_features': {'case': 'nominative'},
                   'variants': [{'form': 'akt'}]}],
    }
    if language is not None:
        lexeme['language'] = language
    before = deepcopy(lexeme)
    records = actual_projection()({'lexemes': [lexeme]}, 'akt', {})
    head, slot = records
    assert lexeme == before
    for record in records:
        assert record['kind'] == 'morphology'
        assert record['target'] == 'akt' and record['match_relation'] == 'recorded_target'
        assert record['provenance'] == lexeme['evidence']
        assert record['record_id'].startswith('lexical:')
        if language is None:
            assert 'language' not in record['fields']
        else:
            assert record['fields']['language'] == language
    assert head['fields']['lemma'] == 'akt' and head['fields']['paradigm_slot_count'] == 1
    assert head['fields']['head_source'] == lexeme['head_source']
    assert slot['fields']['slot'] == lexeme['slots'][0]
    assert slot['entry_id'] == 'synthetic-native:1:slot:1'
    assert slot['fields']['grammatical_features'] == lexeme['grammatical_features']
