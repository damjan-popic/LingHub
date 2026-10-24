"""Original terminology readers preserve qualifications beyond lexical cards."""
import copy
import json

import pytest

from pilot_backend.source_reader import source_view


@pytest.mark.parametrize('locale', ['sl', 'it'])
@pytest.mark.parametrize('source_term,target_term,note', [
    ('didattica', 'poučevanje', 'intesa come attività educativa'),
    ('atto di indirizzo', 'usmeritveni akt', 'solo in questo ambito amministrativo'),
])
def test_tool_original_preserves_per_term_scope_and_full_multiword_forms(locale, source_term, target_term, note):
    record = {'kind': 'terminology', 'target': source_term,
        'selection': {'state': 'primary'}, 'record_id': 'captured-tool-id',
        'fields': {'title': source_term + ' → ' + target_term,
            'categories': ['Področje'], 'original_terms': [source_term],
            'standardized_terms': [target_term],
            'snippet': 'A shortened card must not replace the original.',
            'term_entries': [
                {'term': source_term, 'lang': 'it', 'notes': note, 'definition': 'First meaning'},
                {'term': target_term, 'lang': 'sl', 'usageNote': 'Samo v tem pomenu',
                 'status': 'predlog', 'context': 'Celotno izvirno sobesedilo.'}],
            'references': [{'label': 'Izvirno priporočilo', 'url': 'https://example.test/source'}]}}
    source = {'title': 'Retained original', 'excerpt': json.dumps(record, ensure_ascii=False)}
    before = copy.deepcopy(source)
    view = source_view(source, locale)
    blocks = view['reader_blocks']
    source_blocks = [block for block in blocks if note in block['text']]
    target_blocks = [block for block in blocks if 'Samo v tem pomenu' in block['text']]
    assert len(source_blocks) == len(target_blocks) == 1
    assert source_term in source_blocks[0]['text'] and 'First meaning' in source_blocks[0]['text']
    assert target_term in target_blocks[0]['text'] and 'predlog' in target_blocks[0]['text']
    assert note not in target_blocks[0]['text'] and 'predlog' not in source_blocks[0]['text']
    text = '\n'.join(block['text'] for block in blocks)
    assert 'Celotno izvirno sobesedilo.' in text and 'https://example.test/source' in text
    assert 'A shortened card' not in text and 'captured-tool-id' not in text
    assert source == before and view['excerpt'] == before['excerpt']


@pytest.mark.parametrize('locale', ['sl', 'it'])
def test_same_term_distinct_senses_keep_separate_notes_and_status(locale):
    record = {'kind': 'terminology', 'fields': {'term_entries': [
        {'term': 'forma', 'lang': 'it', 'definition': 'First recorded sense', 'notes': 'Scope one'},
        {'term': 'forma', 'lang': 'it', 'definition': 'Second recorded sense', 'notes': 'Scope two',
         'status': 'draft'},
        {'term': 'oblika', 'lang': 'sl', 'definition': 'First target'},
        {'term': 'obrazec', 'lang': 'sl', 'definition': 'Second target'}]}}
    view = source_view({'excerpt': json.dumps(record)}, locale)
    rows = view['reader_blocks']
    assert len(rows) == 4
    assert 'Scope one' in rows[0]['text'] and 'Scope two' not in rows[0]['text']
    assert 'Scope two' in rows[1]['text'] and 'draft' in rows[1]['text']
    assert 'Scope one' not in rows[1]['text'] and 'draft' not in rows[2]['text']


def test_legacy_tool_original_without_term_entries_still_has_readable_source_text():
    record = {'kind': 'terminology', 'fields': {'title': 'Legacy source', 'snippet': 'Complete retained wording.'}}
    view = source_view({'excerpt': json.dumps(record)})
    assert any(row['text'] == 'Complete retained wording.' for row in view['reader_blocks'])
