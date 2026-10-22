"""Complete retained originals, not regenerated or executable publisher markup."""
from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from pilot_backend import unified
from pilot_backend.original_projection import checked
from pilot_backend.presentation import tester_view as public_view
from pilot_backend.security import Failure
from pilot_backend.source_reader import source_view
from test_pilot_unified import UnifiedGateway, ORIGIN, app_for, ask, login


def projection():
    return {'schema': 'klikai.epravopis-original-projection.v1',
        'source_slug': 'epravopis-slovar', 'record_id': 'epravopis:dictionary:spt:000001',
        'stable_id': '000001', 'headword': 'izraz', 'homograph_marker': '2',
        'inventory_homograph_markers': ['2'], 'homograph_discrepancy': False,
        'status_label': 'Predlog', 'entry_html_sha256': 'a'*64,
        'tables': [{'ordinal': 1, 'html_sha256': 'b'*64, 'rows': [
            [{'kind': 'th', 'text': 'Sklon', 'rowspan': 0, 'colspan': 1, 'scope': 'col'},
             {'kind': 'th', 'text': 'Ednina', 'rowspan': 1, 'colspan': 2, 'scope': 'colgroup'}],
            [{'kind': 'td', 'text': 'rodilnik', 'rowspan': 1, 'colspan': 1},
             {'kind': 'td', 'text': 'prva\nali druga <oblika>', 'rowspan': 1, 'colspan': 1}]]}],
        'tooltips': [{'node_ordinal': 1, 'scope_text': 'ali', 'label': 'Enakovredna različica.',
                      'scope_html_sha256': 'c'*64, 'target': '#opomba'}],
        'citation_original': ['ePravopis, dostop 22. 10. 2026.'],
        'citation_access_dates': ['2026-10-22'], 'source_date': None,
        'source_date_role': 'publication_date_not_established',
        'coverage': {'captured_entries': 11709, 'expected_observed_entries': 11746,
                     'accepted_missing_count': 37, 'cumulative_complete': False}}


def hit():
    p = projection()
    return {'source_slug': p['source_slug'], 'resource_id': p['record_id'],
        'title': 'izraz 2 — ePravopis (predlog)', 'rights_mode': 'restricted',
        'snippet': 'Kratek izsek.', 'original_text': 'Celotno izvirno besedilo. Izjema in vse oblike.',
        'source_status': 'Predlog', 'source_revision': 'd'*64, 'source_date': None,
        'normative_status': 'proposal', 'coverage_note': '11.709 / 11.746; 37 dokumentiranih vrzeli.',
        'original_projection': p, 'score': 4.0, 'url': 'https://fran.si/ext/SPT/000001'}


def test_reader_preserves_all_table_cells_notes_dates_and_original_without_mutation():
    source = {'source_label': 'ePravopis — spletni slovar (predlog)',
              'excerpt': 'Celotno izvirno besedilo.', 'original_projection': projection()}
    before = deepcopy(source)
    view = source_view(source)
    assert source == before and view['excerpt'] == source['excerpt']
    table = next(v for v in view['reader_blocks'] if v.get('kind') == 'table')
    assert table['rows'] == source['original_projection']['tables'][0]['rows']
    shown = json.dumps(view['reader_blocks'], ensure_ascii=False)
    assert 'Enakovredna različica.' in shown and 'Predlog' in shown
    assert all(s in shown for s in ['11.709', '11.746', '37', 'Ni potrjeno, da seznam zajema celoten slovar'])
    assert '2026-10-22' in shown and 'niso datumi objave' in shown
    assert 'Za ta sestavek ni ugotovljen.' in shown
    assert 'original_projection' not in view and 'html_sha256' not in shown
    public = public_view(view, 'owner', 'secret')
    assert public['reader_blocks'] == view['reader_blocks']
    italian = str(source_view(source, 'it')['reader_blocks'])
    assert 'non date di pubblicazione' in italian and 'Non accertata' in italian


@pytest.mark.parametrize('change', ['script', 'event', 'identity', 'scope', 'span', 'date', 'complete', 'oversized'])
def test_invalid_projection_is_rejected_not_clipped_or_rendered(change):
    p = projection()
    if change == 'script': p['tables'][0]['html'] = '<script>steal()</script>'
    if change == 'event': p['tables'][0]['rows'][0][0]['onclick'] = 'steal()'
    if change == 'identity': p['record_id'] = 'epravopis:dictionary:spt:000002'
    if change == 'scope': p['tables'][0]['rows'][0][0]['scope'] = 'onload'
    if change == 'span': p['tables'][0]['rows'][0][0]['colspan'] = -1
    if change == 'date': p['source_date'] = '2026-10-22'
    if change == 'complete': p['coverage']['cumulative_complete'] = True
    if change == 'oversized': p['tables'][0]['rows'][0][0]['text'] = 'x'*(2*1024*1024)
    with pytest.raises(Failure): checked(p)


def test_projection_requires_matching_source_and_native_record_identity():
    for slug, rid in [('epravopis', projection()['record_id']), ('epravopis-slovar', 'wrong')]:
        with pytest.raises(Failure): checked(projection(), source_slug=slug, record_id=rid)


def test_disagreeing_publisher_homograph_markers_remain_separate_without_inventing_an_entry_marker():
    p = projection(); p.update(homograph_marker=None, inventory_homograph_markers=['1'], homograph_discrepancy=True)
    source = {'excerpt': 'Izvirni sestavek brez številčne oznake.', 'original_projection': p}
    blocks = source_view(source)['reader_blocks']
    text = next(row['text'] for row in blocks if row.get('heading') == 'Oznake enakopisnic')
    assert 'sestavku: je ni' in text and 'izdajatelja: 1' in text
    assert p['homograph_marker'] is None
    p['homograph_discrepancy'] = False
    with pytest.raises(Failure): checked(p)


class DictionaryGateway(UnifiedGateway):
    def __init__(self):
        super().__init__(provider_failure=True)
        self.entry = hit()
        self.drop_projection = False

    def request(self, path, payload, base=None):
        assert not self.blocked, 'Historical browsing must not reread current sources'
        if path == '/api/sources':
            return {'sources': [{'slug': 'epravopis-slovar', 'adapter_key': 'official_reference_jsonl',
                'display_name': 'ePravopis — spletni slovar (predlog)', 'rights_mode': 'restricted',
                'source_type': 'documentary', 'is_active': True}]}
        if urlsplit(path).path == '/api/search':
            params = parse_qs(urlsplit(path).query)
            return {'query': params['q'][0], 'sources': ['epravopis-slovar'], 'count': 1,
                    'hits': [deepcopy(self.entry)]}
        out = super().request(path, payload, base)
        if path == '/internal/pilot/freeze' and not self.drop_projection:
            for row in out['matches']:
                if row['source_slug'] == 'epravopis-slovar':
                    row['source']['original_projection'] = deepcopy(self.entry['original_projection'])
        return out


def test_owned_frozen_original_tables_survive_source_changes_and_other_accounts_cannot_read(tmp_path):
    gateway = DictionaryGateway()
    app = app_for(tmp_path, gateway)
    with TestClient(app, base_url=ORIGIN) as client:
        fid, family = ask(app, client, login(client), question='Kaj pomeni izraz?')
        route = '/api/pilot/families/' + fid
        page = client.get(route + '/matches?source=epravopis-slovar').json()
        row = page['items'][0]
        assert row['status'] == 'Predlog' and row['source_slug'] == 'epravopis-slovar'
        original_route = route + '/sources/' + row['evidence_id']
        response = client.get(original_route)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert next(r for r in saved['reader_blocks'] if r.get('kind') == 'table')['rows'] == projection()['tables'][0]['rows']
        assert 'Celotno izvirno besedilo.' in saved['excerpt']
        assert saved['bibliography']['coverage_note'] == gateway.entry['coverage_note']
        gateway.entry['original_projection']['tables'][0]['rows'][1][1]['text'] = 'CHANGED'
        gateway.blocked = True
        assert client.get(original_route).json() == saved
        login(client, 'unified-other')
        assert client.get(original_route).status_code == 404


def test_gateway_cannot_silently_drop_projection_from_bound_capture():
    gateway = DictionaryGateway(); gateway.drop_projection = True
    result = unified.freeze(gateway, {'question': 'izraz', 'locale': 'sl'}, {})
    # Existing failure path preserves local discovery and its full original;
    # the malformed remote bundle is never accepted for generation.
    assert result['bundle']['gateway_bundle'] is None
    entry = next(x for x in result['bundle']['matches'] if x['source_slug'] == 'epravopis-slovar')
    assert entry['source']['original_projection'] == projection()


def test_historical_plain_original_unchanged_without_new_projection():
    source = {'source_label': 'SP2001', 'excerpt': 'Izvirnik in vse izjeme.'}
    assert source_view(source) == dict(source, reader_blocks=[{'heading': '', 'text': source['excerpt']}])


def test_real_core_gateway_admits_bound_original_and_roundtrips_complete_reader_contract():
    producer = pytest.importorskip('tools.zrc_reference.pilot_unified_evidence')
    pytest.importorskip('klikai_api.retrieval.original_projection')

    class RealProjectionGateway(DictionaryGateway):
        def request(self, path, payload, base=None):
            if path != '/internal/pilot/freeze':
                return super().request(path, payload, base)
            packet, policy = producer.validate_capture_set(payload['unified_evidence'], {
                'unified_policy': {'policy_id': 'synthetic', 'sources': ['epravopis-slovar'],
                                   'editorial_operator_policy_id': 'unused'}})
            matches = []
            for capture in packet['matches']:
                eid = producer.evidence_id(capture)
                view = producer.source_details({'citation_id': eid, 'bibliographic': {},
                    'title': capture['title'], 'source_slug': capture['source_slug']}, packet)
                assert 'original_json' not in view
                matches.append({**capture, 'evidence_id': eid, 'source': view})
            return {'bundle': {'fixture': packet}, 'matches': matches}

    gateway = RealProjectionGateway()
    frozen = unified.freeze(gateway, {'question': 'izraz', 'locale': 'sl'}, {})['bundle']
    assert frozen['gateway_bundle'] is not None
    match = frozen['matches'][0]
    original = unified.source(frozen, match['evidence_id'])
    assert original['original_projection'] == projection()
    assert gateway.entry['original_text'] in original['excerpt']
    assert 'tables' not in original['excerpt']  # Never dump SearchHit JSON as source prose.
    reader = source_view(original)
    assert next(x for x in reader['reader_blocks'] if x.get('kind') == 'table')['rows'] == projection()['tables'][0]['rows']
