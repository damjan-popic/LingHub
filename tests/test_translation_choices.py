"""Translation requests keep the source separate from offered whole terms."""
import pytest

from core.lexical_planner import plan, translation_choice_spans


@pytest.mark.parametrize('question,expected,destination', [
    ('Kako prevedemo didattica: didaktika ali poučevanje?', ['didattica', 'didaktika', 'poučevanje'], None),
    ('Kako prevedemo didattica: poučevanje ali didaktika?', ['didattica', 'poučevanje', 'didaktika'], None),
    ('Kako prevedemo »didattica«: didaktika ali poučevanje?', ['didattica', 'didaktika', 'poučevanje'], None),
    ('Kako prevedemo "didattica": "didaktika" ali "poučevanje"?', ['didattica', 'didaktika', 'poučevanje'], None),
    ('Kako se v slovenščino prevede tempo pieno: polni čas ali celodnevni pouk?', ['tempo pieno', 'polni čas', 'celodnevni pouk'], 'sl'),
    ('Kako prevedemo v slovenščino didattica a distanza: pouk na daljavo ali izobraževanje na daljavo?', ['didattica a distanza', 'pouk na daljavo', 'izobraževanje na daljavo'], 'sl'),
    ('Kako prevedemo didattica a distanza v slovenščino: pouk na daljavo ali izobraževanje na daljavo?', ['didattica a distanza', 'pouk na daljavo', 'izobraževanje na daljavo'], 'sl'),
    ('Kako prevedemo polni čas: tempo pieno ali tempo completo v italijanščino?', ['polni čas', 'tempo pieno', 'tempo completo'], 'it'),
    ('Come si traduce tempo pieno in sloveno: polni čas oppure celodnevni pouk?', ['tempo pieno', 'polni čas', 'celodnevni pouk'], 'sl'),
    ('Qual è la traduzione di pouk na daljavo: didattica a distanza o insegnamento a distanza?', ['pouk na daljavo', 'didattica a distanza', 'insegnamento a distanza'], None),
])
def test_source_and_offered_translations_are_exact_separate_spans(question, expected, destination):
    result = plan(question, '', 'sl', {'raw_engines': {}})
    request = result['request_plan']
    assert [t['text'] for t in result['targets']] == expected
    assert request['operation'] == 'translation_help'
    assert request['translation_language'] == destination
    assert result['query_kind'] == 'comparison'
    assert request['target_spans'][0]['basis'] == 'translation source expression; original surface'
    assert all(t['basis'] == 'proposed translation alternative; original surface' for t in request['target_spans'][1:])
    assert all(question[t['start']:t['end']] == t['text'] for t in request['target_spans'])
    # Existing live lexical service can consume this portable plan verbatim;
    # it need not infer the new boundaries itself or change a language verdict.
    replay = plan(question, '', 'sl', {'raw_engines': {}}, request_plan=request)
    assert replay['request_plan']['target_spans'] == request['target_spans']
    assert bool(request['ambiguities']) == (destination is None)


@pytest.mark.parametrize('question', [
    'Kako prevedemo »naslov: A ali B«?',
    'Kako prevedemo naslov: A ali B ali C?',
    'Kako prevedemo naslov: A: B ali C?',
    'Kako prevedemo izraz: A ali A?',
    'Je prav didattica: didaktika ali poučevanje?',
    'Kako prevedemo didattica a distanza?',
])
def test_no_split_for_literal_quoted_punctuation_or_ambiguous_choice_lists(question):
    assert translation_choice_spans(question) == []


def test_quoted_choice_words_inside_one_term_do_not_create_an_extra_alternative():
    question = 'Kako prevedemo "A ali B": "prva ali druga možnost" ali "izbira"?'
    assert [t['text'] for t in plan(question, '', 'sl', {'raw_engines': {}})['targets']] == [
        'A ali B', 'prva ali druga možnost', 'izbira']


def test_a_literal_translated_title_with_colon_remains_whole():
    question = 'Kako prevedemo »naslov: A ali B«?'
    assert [t['text'] for t in plan(question, '', 'sl', {'raw_engines': {}})['targets']] == ['naslov: A ali B']
