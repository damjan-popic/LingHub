from copy import deepcopy
import json
import pytest
from pilot_backend.terminology import record_text


def hit():
    return {'title':'atto → akt','source_display_name':'Zvezek','original_terms':['atto'],
            'standardized_terms':['akt','dejanje'],'proposals':[],'rationale':[],
            'categories':[],'subcategories':[], 'term_entries':[
                {'term':'atto','lang':'it'}, {'term':'akt','lang':'sl','notes':'Samo v tem pomenu.'},
                {'term':'dejanje','lang':'sl','usage':'neuradno','usageStatus':'priporočeno',
                 'geographicalUsage':['FJK'],'usageField':'krajevni urad','context':'To je dejanje.'}]}


@pytest.mark.parametrize('locale',['sl','it'])
@pytest.mark.parametrize('status',['normirano','potrjeno','predlog',None])
def test_new_source_reader_preserves_form_and_record_axes(locale,status):
    row=hit();row['record_status']=status
    row['record_status_source']=[{'label':'Zapisnik','url':'https://example.test/source'}]
    if status=='predlog':row['proposals']=row.pop('standardized_terms');row['standardized_terms']=[]
    before=deepcopy(row);text=record_text(row,locale)
    for value in ['neuradno','priporočeno','FJK','Samo v tem pomenu.','krajevni urad','To je dejanje.','Zapisnik']:
        assert value in text
    assert ('ni naveden' if locale=='sl' else 'non indicato')in text if status is None else status in text
    assert 'Standardizirane oblike:' not in text and 'Forme standardizzate:' not in text
    assert row==before


def test_legacy_record_text_stays_byte_identical():
    row=hit()
    expected=('atto → akt\n\nIzvirne oblike:\natto\n\nStandardizirane oblike:\nakt\ndejanje\n\n'
              'Izvirne oblike z oznakami rabe (manjkajoča oznaka ne pomeni priporočila):\n'
              +json.dumps(row['term_entries'],ensure_ascii=False,indent=2)+'\n\nVir: Zvezek')
    assert record_text(row,'sl')==expected
