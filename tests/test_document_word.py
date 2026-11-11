"""Word drafts keep source package structure and exact section identities."""
from copy import deepcopy
import io
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED
from xml.sax.saxutils import escape

import pytest

from pilot_backend import document_files as files
from pilot_backend.document_word import rewrite,source_anchors
from pilot_backend.security import Failure

W = files.W


def package(body, extras=None):
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.comment = b'original package metadata'
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:preserve="urn:unused-but-retained"><w:body>' + body + '</w:body></w:document>')
        for name, data in (extras or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def sections(data, changes=None):
    blocks = files.extract(data, 'source.docx')['blocks']
    return [{**block, 'state': 'complete', 'document_result': {'status': 'draft',
        'proposed_text': (changes or {}).get(block['index'], block['text'])}} for block in blocks]


def texts(data):
    return [block['text'] for block in files.extract(data, 'draft.docx')['blocks']]


def test_unicode_review_preserves_bold_runs_paragraph_style_and_package_bytes():
    body = '<!-- retain comment --><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Ker 🧑‍🏫 </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>dežuje</w:t></w:r><w:r><w:t> ostanemo doma.</w:t></w:r></w:p>'
    data = package(body, {'word/styles.xml': '<styles>exact stylesheet bytes</styles>', 'docProps/custom.xml': b'exact custom metadata'})
    before = bytes(data)
    draft = rewrite(data, sections(data, {0: 'Ker 🧑‍🏫 dežuje, ostanemo doma.'}))
    assert data == before
    assert texts(draft) == ['Ker 🧑‍🏫 dežuje, ostanemo doma.']
    with ZipFile(io.BytesIO(data)) as original, ZipFile(io.BytesIO(draft)) as output:
        assert output.namelist() == original.namelist()
        assert output.comment == original.comment
        for name in original.namelist():
            if name != 'word/document.xml':
                assert output.read(name) == original.read(name)
        xml = output.read('word/document.xml')
        assert b'xmlns:preserve="urn:unused-but-retained"' in xml
        assert b'<!-- retain comment -->' in xml
        assert b'<w:pStyle w:val="Heading1"/>' in xml
        assert b'<w:rPr><w:b/></w:rPr>' in xml
        assert all(a.date_time == b.date_time and a.compress_type == b.compress_type
                   for a, b in zip(original.infolist(), output.infolist()))


def test_translation_keeps_tables_headers_footers_images_and_relationships():
    body = '<w:p><w:r><w:drawing xmlns:a="urn:drawing"><a:graphic/></w:drawing><w:t>Gentili,</w:t></w:r></w:p><w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr><w:tr><w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>Nome</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Anno 2026</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    extras = {'word/header1.xml': '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Scuola</w:t></w:r></w:p></w:hdr>',
        'word/footer1.xml': '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Contatti</w:t></w:r></w:p></w:ftr>',
        'word/media/image1.png': b'original image payload',
        'word/_rels/document.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="image" Target="media/image1.png"/></Relationships>'}
    data = package(body, extras)
    source = sections(data)
    changes = {'Gentili,': 'Spoštovani,', 'Nome': 'Ime', 'Anno 2026': 'Leto 2026',
               'Scuola': 'Šola', 'Contatti': 'Stiki'}
    for section in source:
        section['document_result']['proposed_text'] = changes[section['text']]
    draft = rewrite(data, source)
    assert texts(draft) == ['Spoštovani,', 'Ime', 'Leto 2026', 'Stiki', 'Šola']
    with ZipFile(io.BytesIO(draft)) as output:
        assert output.read('word/media/image1.png') == extras['word/media/image1.png']
        assert output.read('word/_rels/document.xml.rels').decode() == extras['word/_rels/document.xml.rels']
        root = ET.fromstring(output.read('word/document.xml'))
        assert len(list(root.iter(W + 'tbl'))) == 1
        assert len(list(root.iter(W + 'tc'))) == 2
        assert len(list(root.iter(W + 'drawing'))) == 1
        assert root.find('.//' + W + 'tcW').get(W + 'w') == '2400'


def test_split_source_paragraph_reassembles_exactly_and_preserves_combining_text():
    text = 'človek 🧑‍🏫 ' * 600
    data = package('<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>')
    source = sections(data)
    assert len(source) > 1
    expected = ''
    for section in source:
        proposed = section['text'].replace('človek', 'človek')
        section['document_result']['proposed_text'] = proposed
        expected += proposed
    assert ''.join(texts(rewrite(data, source))) == expected


def test_emphasis_cannot_be_moved_across_a_combining_grapheme_boundary():
    data = package('<w:p><w:r><w:t>c</w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>̌lovek</w:t></w:r><w:r><w:t> potrebuje pomoč.</w:t></w:r></w:p>')
    with pytest.raises(Failure,match='document_draft_unavailable'):
        from pilot_backend.document_word import source_anchors
        source_anchors(data)


def test_unformatted_combining_characters_across_runs_stay_bound_as_codepoints():
    data = package('<w:p><w:r><w:t>c</w:t></w:r><w:r><w:t>̌lovek</w:t></w:r><w:r><w:t> potrebuje pomoč.</w:t></w:r></w:p>')
    draft = rewrite(data, sections(data, {0: 'človek potrebuje pomoč.'}))
    assert texts(draft) == ['človek potrebuje pomoč.']


def test_native_tabs_breaks_hyphens_and_whitespace_remain_native():
    data = package('<w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t><w:noBreakHyphen/><w:t>D</w:t><w:softHyphen/><w:t>E</w:t></w:r></w:p>')
    proposed = ' Novi A\tNovi B\nNovi C\u2011Novi D\u00adNovi E '
    draft = rewrite(data, sections(data, {0: proposed}))
    assert texts(draft) == [proposed]
    with ZipFile(io.BytesIO(draft)) as output:
        root = ET.fromstring(output.read('word/document.xml'))
        for tag in ('tab', 'br', 'noBreakHyphen', 'softHyphen'):
            assert len(list(root.iter(W + tag))) == 1
        assert all(node.get(files.XML_SPACE) == 'preserve' for node in root.iter(W + 't'))


@pytest.mark.parametrize('proposed', ['A B', 'A\nB', 'A\t\tB'])
def test_structural_control_changes_fail_instead_of_flattening(proposed):
    data = package('<w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t></w:r></w:p>')
    with pytest.raises(Failure, match='document_draft_unavailable'):
        rewrite(data, sections(data, {0: proposed}))


@pytest.mark.parametrize('change', ['missing', 'text', 'location', 'index', 'state', 'status'])
def test_incomplete_or_changed_source_sections_are_rejected(change):
    data = package('<w:p><w:r><w:t>Original</w:t></w:r></w:p>')
    source = sections(data)
    if change == 'missing':
        source.clear()
    elif change == 'status':
        source[0]['document_result']['status'] = 'not_reviewed'
    else:
        source[0][change] = 'changed'
    with pytest.raises(Failure, match='document_draft_unavailable'):
        rewrite(data, source)


def test_unchanged_complete_draft_returns_identical_package():
    data = package('<w:p><w:r><w:t>Original</w:t></w:r></w:p>')
    source = sections(data)
    before = deepcopy(source)
    assert rewrite(data, source) == data
    assert source == before


def test_signed_packages_are_not_silently_invalidated():
    data = package('<w:p><w:r><w:t>Original</w:t></w:r></w:p>', {'_xmlsignatures/sig1.xml': b'<signature/>'})
    with pytest.raises(Failure, match='document_draft_unavailable'):
        rewrite(data, sections(data, {0: 'Draft'}))


def test_signature_relationship_cannot_hide_behind_an_unusual_part_name():
    data = package('<w:p><w:r><w:t>Original</w:t></w:r></w:p>', {
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="signature" Type="http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/origin" Target="custom/signature.bin"/></Relationships>',
        'custom/signature.bin': b'original signature'})
    with pytest.raises(Failure, match='document_draft_unavailable'):
        rewrite(data, sections(data, {0: 'Draft'}))


def test_utf16_part_is_reencoded_with_matching_declaration_and_namespace_markup():
    data = package('<w:p><w:r><w:t>Izvirnik</w:t></w:r></w:p>')
    out = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as archive, ZipFile(out, 'w', ZIP_DEFLATED) as dest:
        for item in archive.infolist():
            content = archive.read(item.filename)
            if item.filename == 'word/document.xml':
                content = content.decode().replace('encoding="UTF-8"', 'encoding="UTF-16"').encode('utf-16')
            dest.writestr(item.filename, content)
    data = out.getvalue()
    draft = rewrite(data, sections(data, {0: 'Predlog'}))
    assert texts(draft) == ['Predlog']
    with ZipFile(io.BytesIO(draft)) as archive:
        assert b'encoding="UTF-8"' in archive.read('word/document.xml')


def test_literal_control_references_remain_exact_inside_one_text_run():
    data = package('<w:p><w:r><w:t>A&#9;B&#10;C&#13;D</w:t></w:r></w:p>')
    proposed = 'AA\tBB\nCC\rDD'
    draft = rewrite(data, sections(data, {0: proposed}))
    assert texts(draft) == [proposed]


def test_damaged_unvisited_image_is_a_fixed_unavailable_error():
    initial = package('<w:p><w:r><w:t>Original</w:t><w:drawing/></w:r></w:p>')
    output = io.BytesIO()
    with ZipFile(io.BytesIO(initial)) as archive, ZipFile(output, 'w', ZIP_STORED) as dest:
        for item in archive.infolist():
            dest.writestr(item.filename, archive.read(item.filename))
        dest.writestr('word/media/image1.png', b'UNIQUE_IMAGE_PAYLOAD')
    data = output.getvalue().replace(b'UNIQUE_IMAGE_PAYLOAD', b'UNIQUE_IMAGE_PAYLOA?')
    source = sections(data, {0: 'Draft'})
    with pytest.raises(Failure, match='document_draft_unavailable'):
        rewrite(data, source)


def test_unknown_markup_inside_changed_text_is_not_silently_removed():
    data = package('<w:p><w:r><w:t>Ori<!-- text token fragment -->ginal</w:t></w:r></w:p>')
    with pytest.raises(Failure, match='document_draft_unavailable'):
        rewrite(data, sections(data, {0: 'Draft'}))


def historical_sections(data,cut):
    native,_=files._word_blocks(data)
    saved=[]
    for location,original in native:
        for start in range(0,len(original),cut):
            end=min(start+cut,len(original))
            text=original[start:end]
            saved.append({'index':len(saved),'location':f'{location}@{start}:{end}',
                'text':text,'state':'complete','document_result':{'status':'draft',
                'proposed_text':text.replace('A','Z')}})
    return saved


def test_valid_historical_chunk_boundaries_are_independent_of_current_split_policy(monkeypatch):
    text='A čebelica 🧑‍🏫 človek. '*12
    data=package('<w:p><w:r><w:t>'+text+'</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>A celica.</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    old=historical_sections(data,13)
    other=historical_sections(data,29)
    assert len(old)!=len(other)
    monkeypatch.setattr(files,'_split',lambda value:(_ for _ in()).throw(AssertionError('Current split policy must not participate in saved Word export')))
    first=rewrite(data,old)
    second=rewrite(data,other)
    assert files._word_blocks(first)[0]==files._word_blocks(second)[0]
    assert files._word_blocks(first)[0][0][1]==text.replace('A','Z')


@pytest.mark.parametrize('kind',['gap','overlap','order','path','text','uncovered_paragraph','extra_section','boolean_index'])
def test_historical_section_tampering_never_passes_binding(kind):
    data=package('<w:p><w:r><w:t>ABCD EFGH IJKL</w:t></w:r></w:p><w:p><w:r><w:t>Other paragraph</w:t></w:r></w:p>')
    source=historical_sections(data,4)
    if kind in {'gap','overlap'}:
        base=source[1]['location'].rsplit('@',1)[0]
        start=5 if kind=='gap'else 3
        source[1]['location']=f'{base}@{start}:8'
        source[1]['text']='ABCD EFGH IJKL'[start:8]
    elif kind=='order':
        source[0],source[1]=source[1],source[0]
        for index,section in enumerate(source):section['index']=index
    elif kind=='path':source[0]['location']=source[0]['location'].replace('/p[1]','/p[2]')
    elif kind=='text':source[0]['text']='not original'
    elif kind=='uncovered_paragraph':
        source=[section for section in source if '/p[2]'not in section['location']]
    elif kind=='extra_section':
        source.append(deepcopy(source[-1]));source[-1]['index']=len(source)-1
    else:source[1]['index']=True
    with pytest.raises(Failure,match='document_draft_unavailable'):
        rewrite(data,source)


def linked_paragraph(prefix,label,suffix,*,nested=False,same_range_bold=False):
    text=('<w:r><w:rPr><w:b/></w:rPr><w:t>'+escape(label[:2])+'</w:t></w:r><w:r><w:t>'+escape(label[2:])+'</w:t></w:r>'if nested else
        '<w:r><w:rPr><w:u w:val="single"/><w:color w:val="0563C1"/>'+('<w:b/>'if same_range_bold else'')+'</w:rPr><w:t>'+escape(label)+'</w:t></w:r>')
    return '<w:p><w:r><w:t xml:space="preserve">'+escape(prefix)+'</w:t></w:r><w:hyperlink xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId1">'+text+'</w:hyperlink><w:r><w:t xml:space="preserve">'+escape(suffix)+'</w:t></w:r></w:p>'


def with_link(body):
    return package(body,{'word/_rels/document.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="hyperlink" Target="https://example.org/pouk" TargetMode="External"/></Relationships>'})


def mapping(anchor,proposed,target):
    start=proposed.index(target)
    return {'id':anchor['id'],'target_start':start,'target_end':start+len(target),'target_text':target}


def test_actual_saved_translation_attaches_link_to_complete_expression_not_a_dista_fragment():
    # The observed first paragraph previously linked only "a dista". This is a
    # formatting-regression fixture, not approval of its linguistic wording.
    data=with_link(linked_paragraph('Spoštovani, prosim za informacije o ','pouku na daljavo',' in za potrdilo o vpisu za šolsko leto 2026/2027.'))
    proposed="Gentile, ti prego di fornirmi informazioni sul corso a distanza e sul certificato di iscrizione per l'anno scolastico 2026/2027."
    source=sections(data,{0:proposed});anchor=next(iter(source_anchors(data).values()))[0]
    source[0]['document_result'].update(mode='translate',inline_mappings=[mapping(anchor,proposed,'corso a distanza')])
    draft=rewrite(data,source)
    assert texts(draft)==[proposed]
    with ZipFile(io.BytesIO(draft))as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        links=[''.join(n.text or''for n in node.iter(W+'t'))for node in root.iter(W+'hyperlink')]
        assert links==['corso a distanza']
        assert b'https://example.org/pouk'in archive.read('word/_rels/document.xml.rels')


def test_old_translated_semantic_links_without_mappings_fail_closed():
    data=with_link(linked_paragraph('Informacije o ','pouku na daljavo',' so na voljo.'))
    source=sections(data,{0:'Informazioni sulla didattica a distanza sono disponibili.'})
    source[0]['document_result']['mode']='translate'
    with pytest.raises(Failure,match='document_draft_unavailable'):rewrite(data,source)


@pytest.mark.parametrize('bad',['unknown_id','fragment','duplicate','wrong_text','bad_offset'])
def test_invalid_semantic_target_mappings_are_rejected(bad):
    data=with_link(linked_paragraph('Informacije o ','pouku na daljavo',' so na voljo.'))
    proposed='Informazioni sulla didattica a distanza sono disponibili.'
    source=sections(data,{0:proposed});anchor=next(iter(source_anchors(data).values()))[0]
    value=mapping(anchor,proposed,'didattica a distanza')
    values=[value]
    if bad=='unknown_id':value['id']='unknown'
    elif bad=='fragment':value.update(mapping(anchor,proposed,'a dista'))
    elif bad=='duplicate':values.append(deepcopy(value))
    elif bad=='wrong_text':value['target_text']='different text'
    else:value['target_end']+=1
    source[0]['document_result'].update(mode='translate',inline_mappings=values)
    with pytest.raises(Failure,match='document_draft_unavailable'):rewrite(data,source)


def test_partial_nested_emphasis_inside_hyperlink_is_explicitly_unsupported():
    data=with_link(linked_paragraph('Informacije o ','pouku na daljavo',' so na voljo.',nested=True))
    with pytest.raises(Failure,match='document_draft_unavailable'):source_anchors(data)


def test_same_range_link_and_bold_share_one_mapping_and_keep_both_properties():
    data=with_link(linked_paragraph('Informacije o ','pouku na daljavo',' so na voljo.',same_range_bold=True))
    anchors=next(iter(source_anchors(data).values()));assert len(anchors)==1 and anchors[0]['kind']=='hyperlink'
    proposed='Informazioni sulla didattica a distanza sono disponibili.'
    source=sections(data,{0:proposed});source[0]['document_result'].update(mode='translate',inline_mappings=[mapping(anchors[0],proposed,'didattica a distanza')])
    draft=rewrite(data,source)
    with ZipFile(io.BytesIO(draft))as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        linked=next(root.iter(W+'hyperlink'))
        assert ''.join(t.text or''for t in linked.iter(W+'t'))=='didattica a distanza'
        assert linked.find(W+'r/'+W+'rPr/'+W+'b')is not None


def test_long_translated_emphasis_is_preserved_across_historical_sections():
    label='ne sme izgubiti možnosti sodelovanja';target='non deve perdere la possibilità di partecipare'
    original='Besedilo '*180+label+' Nadaljevanje.'*180
    data=package('<w:p><w:r><w:t xml:space="preserve">'+'Besedilo '*180+'</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>'+label+'</w:t></w:r><w:r><w:t xml:space="preserve">'+' Nadaljevanje.'*180+'</w:t></w:r></w:p>')
    anchor=next(iter(source_anchors(data).values()))[0]
    source=historical_sections(data,600);expected=[]
    for section in source:
        proposed=section['text'].replace('Besedilo','Testo').replace(label,target)
        section['document_result']={'status':'draft','mode':'translate','proposed_text':proposed}
        if label in section['text']:section['document_result']['inline_mappings']=[mapping(anchor,proposed,target)]
        expected.append(proposed)
    draft=rewrite(data,source)
    assert ''.join(texts(draft))==''.join(expected)
    with ZipFile(io.BytesIO(draft))as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        bold=[''.join(t.text or''for t in r.iter(W+'t'))for r in root.iter(W+'r')if r.find(W+'rPr/'+W+'b')is not None]
        assert bold==[target]
    assert len(original)>3000


def test_review_uses_exact_edits_to_shift_a_repeated_marked_expression():
    prefix='Če dežuje rok ostane. Drug ';label='rok';suffix=' je pozneje.'
    data=package('<w:p><w:r><w:t xml:space="preserve">'+prefix+'</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>'+label+'</w:t></w:r><w:r><w:t xml:space="preserve">'+suffix+'</w:t></w:r></w:p>')
    original=prefix+label+suffix;old='Če dežuje';new='Če dežuje,';proposed=original.replace(old,new)
    source=sections(data,{0:proposed});source[0]['document_result'].update(mode='review',edits=[{'start':0,'end':len(old),'original':old,'replacement':new}])
    draft=rewrite(data,source);out_anchor=next(iter(source_anchors(draft).values()))[0]
    assert out_anchor['text']=='rok'and out_anchor['start']==proposed.rindex('rok')


def test_review_edits_wholly_within_emphasis_keep_the_complete_new_expression():
    data=package('<w:p><w:r><w:t xml:space="preserve">Sestanek bo </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>čimprej</w:t></w:r><w:r><w:t>.</w:t></w:r></w:p>')
    original='Sestanek bo čimprej.';start=original.index('čimprej');proposed='Sestanek bo čim prej.'
    source=sections(data,{0:proposed});source[0]['document_result'].update(mode='review',edits=[{'start':start,'end':start+7,'original':'čimprej','replacement':'čim prej'}])
    draft=rewrite(data,source);anchor=next(iter(source_anchors(draft).values()))[0]
    assert anchor['text']=='čim prej'


def test_trimmed_translated_chunks_keep_source_separators_in_word_and_text_drafts():
    left,right='Prvi stavek. ','Drugi stavek.'
    data=package('<w:p><w:r><w:t>'+left+right+'</w:t></w:r></w:p>')
    location=files._word_blocks(data)[0][0][0]
    source=[{'index':0,'location':f'{location}@0:{len(left)}','text':left,'state':'complete',
             'document_result':{'status':'draft','mode':'translate','proposed_text':'Prima frase.'}},
            {'index':1,'location':f'{location}@{len(left)}:{len(left+right)}','text':right,'state':'complete',
             'document_result':{'status':'draft','mode':'translate','proposed_text':'Seconda frase.'}}]
    assert texts(rewrite(data,source))==['Prima frase. Seconda frase.']
    assert files.draft_blocks({'state':'complete','sections':source})==['Prima frase. Seconda frase.']


def test_boundary_whitespace_adjusts_inline_offsets_without_mutating_saved_proposal():
    data=with_link(linked_paragraph('  Informacije o ','pouku na daljavo',' so na voljo. '))
    proposed='Informazioni sulla didattica a distanza sono disponibili.'
    source=sections(data,{0:proposed});anchor=next(iter(source_anchors(data).values()))[0]
    source[0]['document_result'].update(mode='translate',inline_mappings=[mapping(anchor,proposed,'didattica a distanza')])
    before=deepcopy(source)
    draft=rewrite(data,source)
    assert texts(draft)==['  '+proposed+' ']
    out_anchor=next(iter(source_anchors(draft).values()))[0]
    assert out_anchor['text']=='didattica a distanza'
    assert out_anchor['start']==proposed.index('didattica a distanza')+2
    assert source==before


def test_mixed_source_bound_review_accepts_exact_comma_and_space_insertions():
    original='Če bo deževalo bomo sestanek čimprej izvedli na daljavo. Na daljavo je mogoče sodelovati tudi od doma.'
    proposed='Če bo deževalo, bomo sestanek čim prej izvedli na daljavo. Na daljavo je mogoče sodelovati tudi od doma.'
    data=package('<w:p><w:r><w:t>'+original+'</w:t></w:r></w:p>')
    comma=len('Če bo deževalo');space=original.index('čimprej')+3
    source=sections(data,{0:proposed});source[0]['document_result'].update(mode='review',edits=[
        {'start':comma,'end':comma,'original':'','replacement':','},
        {'start':space,'end':space,'original':'','replacement':' '}])
    assert texts(rewrite(data,source))==[proposed]


def test_review_insertion_inside_mark_keeps_the_whole_new_marked_phrase():
    data=package('<w:p><w:r><w:t xml:space="preserve">Sestanek bo </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>čimprej</w:t></w:r><w:r><w:t>.</w:t></w:r></w:p>')
    original='Sestanek bo čimprej.';position=original.index('čimprej')+3;proposed='Sestanek bo čim prej.'
    source=sections(data,{0:proposed});source[0]['document_result'].update(mode='review',edits=[
        {'start':position,'end':position,'original':'','replacement':' '}])
    draft=rewrite(data,source);anchor=next(iter(source_anchors(draft).values()))[0]
    assert anchor['text']=='čim prej'


@pytest.mark.parametrize('edge,replacement',[('start','nov '),('end',',')])
def test_review_boundary_insertions_remain_outside_repeated_marked_word(edge,replacement):
    prefix,label,suffix='Ta ','rok',' in drug rok.'
    original=prefix+label+suffix
    data=package('<w:p><w:r><w:t xml:space="preserve">'+prefix+'</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>'+label+'</w:t></w:r><w:r><w:t xml:space="preserve">'+suffix+'</w:t></w:r></w:p>')
    position=len(prefix)if edge=='start'else len(prefix+label)
    proposed=original[:position]+replacement+original[position:]
    source=sections(data,{0:proposed});source[0]['document_result'].update(mode='review',edits=[
        {'start':position,'end':position,'original':'','replacement':replacement}])
    draft=rewrite(data,source);anchor=next(iter(source_anchors(draft).values()))[0]
    assert anchor['text']=='rok'
    assert anchor['start']==proposed.index('rok')
