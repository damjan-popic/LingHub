"""WEB preserves accepted-normalization spans before the CORE guard checks them."""
import pytest
from pilot_backend import document_surface as surface, document_transform as document


@pytest.mark.parametrize('literal,text,expected', [
    ('(generalni) direktor','Vloga (generalni)  direktor.','(generalni)  direktor'),
    ('šolsko leto','Novo šolsko\nleto.','šolsko\nleto'),
    ('anno scolastico','Nuovo anno\u00a0scolastico.','anno\u00a0scolastico'),
    ('DDV','Vrednost ＤＤＶ.','ＤＤＶ'),
    ('čaj','Na voljo c\u030caj.','c\u030caj'),
])
def test_source_occurrences_keep_exact_original_boundaries(literal,text,expected):
    result=list(document._surface_occurrences(literal,text))
    assert len(result)==1 and result[0]['text']==expected
    assert text[result[0]['start']:result[0]['end']]==expected


def test_recorded_alias_is_a_language_bound_candidate_not_a_split_or_forged_headterm():
    capture={'term_entries':[{'term':'dottore','lang':'it','variantForms':['dott. / dott.ssa'],'usage':'Restricted scope'}]}
    assert list(document._native_term_surfaces(capture,'it'))==['dottore','dott. / dott.ssa']
    assert list(document._native_term_surfaces(capture,'sl'))==[]
    assert list(surface.occurrences('dott.','dott.ssa'))==[]
