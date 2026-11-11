"""Document capture is lossless, bounded and never follows package content."""
import io
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_BZIP2
from xml.etree import ElementTree as ET

import pytest

from pilot_backend import document_files as files
from pilot_backend.security import Failure


def package(body, extras=None):
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        for name, data in (extras or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def failure(data, name, code):
    with pytest.raises(Failure) as raised:
        files.extract(data, name)
    assert raised.value.code == code


def test_exact_unicode_newlines_and_splitting():
    text = '  Začetek 🧑‍🏫 človek.\r\n\r\n' + 'žaba \t' * 700 + '  Konec\r\n'
    captured = files.extract(text.encode(), 'pismo.TXT')
    assert ''.join(b['text'] for b in captured['blocks']) == text
    assert all(0 < len(b['text']) <= files.MAX_SECTION for b in captured['blocks'])
    offset = 0
    for index, block in enumerate(captured['blocks']):
        assert block['index'] == index
        assert block['location'] == f'text@{offset}:{offset + len(block["text"])}'
        offset += len(block['text'])
    assert len(captured['sha256']) == 64


def test_operation_slices_prefer_complete_sentence_and_keep_boundary_whitespace():
    paragraph=('Una comunicazione completa per le famiglie. '*30+
               'La didattica a distanza rimane disponibile. Le famiglie riceveranno una sintesi. '*22)
    parts=list(files._split(paragraph))
    assert ''.join(parts)==paragraph
    assert len(parts)>1 and all(len(part)<=files.SECTION_TARGET+1 for part in parts)
    assert all(part.rstrip().endswith('.')for part in parts[:-1])
    assert not any(part.endswith('Le ')for part in parts[:-1])
    text='A '*749+'\r\n'+'B '*900
    parts=list(files._split(text))
    assert ''.join(parts)==text
    assert all(not(part.endswith('\r')and following.startswith('\n'))for part,following in zip(parts,parts[1:]))


@pytest.mark.parametrize('locale',['sl','it'])
def test_reports_have_human_locations_localized_status_and_exact_support(locale):
    original='Če dežuje ostanemo doma.';proposed='Če dežuje, ostanemo doma.'
    edit={'original':original,'replacement':proposed,'explanation':'Existing supported explanation.',
          'evidence_ids':['rule'],'support_quote':'Exact governing source passage.'}
    report={'name':'school.docx','mode':'review','source_language':'sl','target_language':None,'locale':locale,
        'sections':[{'index':0,'location':'word/document.xml/body[1]/tbl[1]/tr[2]/tc[1]/p[1]@0:24',
          'text':original,'state':'complete','document_result':{'status':'draft','proposed_text':proposed,'notes':[], 'edits':[edit]},
          'sources':[{'evidence_id':'rule','local_number':1,'title':'  A recorded\n  rule  ','url':'https://example.org/rule'}]}]}
    rendered=files.report_text(report).decode()
    assert 'word/document.xml'not in rendered and 'None'not in rendered and 'Stanje: complete'not in rendered
    assert ('tabela 1 · vrstica 2 · celica 1'if locale=='sl'else'tabella 1 · riga 2 · cella 1')in rendered
    assert proposed in rendered and edit['support_quote']in rendered and '[1] A recorded rule'in rendered
    report['sections'][0]['document_result']={'status':'not_reviewed','proposed_text':original,'notes':[],'edits':[]}
    assert ('Ni pregledano'if locale=='sl'else'Non revisionato')in files.report_text(report).decode()


def test_docx_paragraph_table_header_notes_and_unchanged_boundaries():
    body = '<w:p><w:r><w:t xml:space="preserve"> A 🧑‍🏫 č </w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell 1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>cell 2</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    header = '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>header</w:t></w:r></w:p></w:hdr>'
    footnotes = '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1"><w:p><w:r><w:t>footnote</w:t></w:r></w:p></w:footnote></w:footnotes>'
    captured = files.extract(package(body, {'word/header1.xml': header, 'word/footnotes.xml': footnotes}), 'pismo.docx')
    assert [b['text'] for b in captured['blocks']] == [' A 🧑‍🏫 č \tB\nC', 'cell 1', 'cell 2', 'footnote', 'header']
    assert '/tbl[1]/tr[1]/tc[1]/p[1]' in captured['blocks'][1]['location']
    assert '/tc[2]/' in captured['blocks'][2]['location']
    assert captured['warnings'] == []


@pytest.mark.parametrize('name', ['../x.txt', 'folder\\x.docx', '\0.txt', 'x\n.txt', '', 'x' * 181])
def test_reject_path_or_bad_names(name):
    failure(b'x', name, 'document_invalid')


@pytest.mark.parametrize('data,name,code', [
    (b'abc', 'file.pdf', 'document_unsupported'),
    (b'abc', 'file.doc', 'document_unsupported'),
    (b'not a zip', 'file.docx', 'document_invalid'),
    (b'\xff\xff', 'file.txt', 'document_invalid'),
    (b'a\0b', 'file.txt', 'document_invalid'),
    (b' \n\t', 'file.txt', 'document_empty'),
    (b'a' * (files.MAX_UPLOAD_BYTES + 1), 'file.txt', 'document_too_large'),
    (b'a' * (files.MAX_TEXT + 1), 'file.txt', 'document_too_large'),
])
def test_invalid_empty_and_bounded(data, name, code):
    failure(data, name, code)


def test_many_word_paragraphs_reject_instead_of_truncate():
    failure(package('<w:p><w:r><w:t>x</w:t></w:r></w:p>' * 101), 'file.docx', 'document_too_large')


@pytest.mark.parametrize('body', [
    '<w:p><w:ins><w:r><w:t>insertion</w:t></w:r></w:ins></w:p>',
    '<w:p><w:r><w:fldChar/></w:r></w:p>',
    '<w:p><w:r><w:instrText>DATE</w:instrText></w:r></w:p>',
    '<w:altChunk/>',
    '<w:p><w:r><w:drawing><w:txbxContent><w:p><w:r><w:t>text box</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r></w:p>',
])
def test_unsupported_visible_constructs_never_silently_dropped(body):
    failure(package(body), 'file.docx', 'document_unsupported')


def test_images_are_explicit_warning_and_external_hyperlinks_never_fetched():
    body = '<w:p><w:hyperlink><w:r><w:t>visible link</w:t></w:r></w:hyperlink><w:r><w:drawing/></w:r></w:p>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="hyperlink" Target="http://127.0.0.1/private" TargetMode="External"/></Relationships>'
    result = files.extract(package(body, {'word/_rels/document.xml.rels': rels}), 'file.docx')
    assert result['blocks'][0]['text'] == 'visible link'
    assert result['warnings'] == ['document_images_not_reviewed']


@pytest.mark.parametrize('extra', [
    {'../outside': 'anything'},
    {'word/vbaProject.bin': 'anything'},
    {'word/embeddings/a.bin': 'anything'},
])
def test_malicious_package_parts_rejected(extra):
    with pytest.raises(Failure):
        files.extract(package('<w:p><w:r><w:t>x</w:t></w:r></w:p>', extra), 'file.docx')


def test_duplicate_members_rejected():
    output = io.BytesIO(package('<w:p/>'))
    with ZipFile(output, 'a') as archive:
        with pytest.warns(UserWarning):
            archive.writestr('word/document.xml', '<x/>')
    failure(output.getvalue(), 'file.docx', 'document_invalid')


def test_unbounded_compression_codecs_rejected_without_reading_content():
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_BZIP2) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<x/>')
    failure(output.getvalue(), 'file.docx', 'document_unsupported')


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16le', 'utf-16be'])
def test_entity_declarations_rejected_before_xml_expansion(encoding):
    xml = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "EXPANDED">]><x>&a;</x>'
    failure(package('<w:p/>', {'word/header1.xml': xml.encode(encoding)}), 'file.docx', 'document_invalid')


def test_zip_bomb_and_deep_xml_rejected():
    failure(package('<w:p><w:r><w:t>' + 'a' * (files.MAX_EXPANDED + 1) + '</w:t></w:r></w:p>'), 'file.docx', 'document_too_large')
    failure(package('<w:sdt>' * 70 + '<w:p/>' + '</w:sdt>' * 70), 'file.docx', 'document_too_large')


def test_export_reports_keep_originals_outputs_and_credits_and_escape_xml():
    original = '  🧑‍🏫 <zadeva> & č\tA\nB'
    report = {'name': 'Pismo.docx', 'mode': 'review', 'locale': 'sl', 'source_language': 'sl', 'target_language': 'sl',
              'sections': [{'index': 0, 'location': 'word/document.xml/body/p[1]',
                            'text': original, 'output': 'Predlog < & >', 'status': 'needs_review',
                            'sources': [{'title': 'Slovenski pravopis', 'url': 'https://example.org/vir'}]}]}
    txt = files.report_text(report).decode()
    assert original in txt and 'Predlog < & >' in txt and 'https://example.org/vir' in txt
    docx = files.report_docx(report)
    captured = files.extract(docx, 'porocilo.docx')
    assert original in [b['text'] for b in captured['blocks']]
    assert any(b['text'] == 'Predlog < & >' for b in captured['blocks'])
    assert any(b['text']=='Izvirna objava'for b in captured['blocks'])
    with ZipFile(io.BytesIO(docx))as package:
        assert b'Target="https://example.org/vir"'in package.read('word/_rels/document.xml.rels')
    assert any('Poročilo uporablja svojo postavitev' in b['text'] for b in captured['blocks'])
    assert report['sections'][0]['text'] == original


def test_clean_drafts_require_every_section_and_rejoin_split_source_paragraphs():
    report={'state':'complete','sections':[
        {'location':'text@0:4','state':'complete','document_result':{'status':'draft','proposed_text':' A\r\n'}},
        {'location':'text@4:6','state':'complete','document_result':{'status':'draft','proposed_text':'B '}}]}
    assert files.draft_text(report).decode()==' A\r\nB '
    assert files.extract(files.draft_docx(report),'draft.docx')['blocks'][0]['text']==' A\nB '
    report['sections'][1]['document_result']['status']='not_reviewed'
    with pytest.raises(Failure,match='document_draft_unavailable'):files.draft_docx(report)


def test_translated_paragraph_replay_blocks_export_and_is_visible_in_report():
    result={'mode':'translate','status':'draft','source_language':'it','target_language':'sl',
            'proposed_text':'Prevod prejšnjega dela.','edits':[],'notes':[]}
    report={'name':'letter.txt','mode':'translate','state':'complete','locale':'sl',
        'source_language':'it','target_language':'sl','sections':[
        {'index':0,'location':'text@0:7','text':'Primo. ','state':'complete','document_result':dict(result)},
        {'index':1,'location':'text@7:15','text':'Secondo.','state':'complete','document_result':dict(result)}]}
    with pytest.raises(Failure,match='document_draft_unavailable'):files.draft_docx(report)
    rendered=files.report_text(report).decode()
    assert 'Wordov osnutek ni na voljo'in rendered
    assert rendered.count('Ponovljen osnutek drugega dela; prevod je treba preveriti.')==1
    assert 'Primo.'in rendered and'Secondo.'in rendered
    # Independent locations and genuinely repeated source text are legitimate.
    report['sections'][1]['location']='other@0:8'
    assert files.draft_docx(report)
    report['sections'][1].update(location='text@7:14',text='Primo. ')
    assert files.draft_docx(report)


def test_complete_export_distinguishes_failed_operation_from_partial_support():
    section={'index':0,'location':'text@0:8','text':'Izvirnik','state':'complete','answer_support':'partial',
        'limitations':[{'code':'document_transform_failed','scope':'answer'}],
        'document_result':{'mode':'review','status':'draft','proposed_text':'Predlog','edits':[],'notes':[]}}
    report={'name':'review.txt','mode':'review','state':'complete','source_language':'sl','locale':'sl','sections':[section]}
    with pytest.raises(Failure,match='document_draft_unavailable'):files.draft_docx(report)
    assert 'Del zahtevane obdelave ni uspel'in files.report_text(report).decode()
    section['limitations']=[{'code':'document_draft_requires_review','scope':'answer'}]
    assert files.draft_docx(report)  # Bounded partial support is not a failed operation.


def test_report_word_links_keep_exact_destinations_with_readable_labels():
    from xml.etree import ElementTree as ET
    url='https://klikai.example/api/pilot/families/f/sources/frozen%3Aid?tier=answer&v=1'
    report={'name':'source.txt','mode':'review','state':'complete','locale':'sl','source_language':'sl',
            'saved_result_url':url,'sections':[]}
    assert url in files.report_text(report).decode()
    data=files.report_docx(report)
    with ZipFile(io.BytesIO(data))as package:
        doc=ET.fromstring(package.read('word/document.xml'))
        serialized=package.read('word/_rels/document.xml.rels')
        rels=ET.fromstring(serialized)
        assert b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'in serialized
        assert b'ns0:'not in serialized
    link=doc.find('.//'+files.W+'hyperlink')
    rid=link.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
    reference=next(r for r in rels if r.attrib['Id']==rid)
    assert reference.attrib['Target']==url and reference.attrib['TargetMode']=='External'
    assert ''.join(link.itertext())=='Odpri shranjeni dokument'


def test_partial_review_keeps_valid_proposal_and_falls_back_without_changing_history():
    from copy import deepcopy
    report={'state':'failed','options':{'mode':'review'},'sections':[
        {'state':'complete','text':'Original A','location':'text@0:10',
         'limitations':[{'code':'document_transform_failed'}],
         'document_result':{'mode':'review','status':'draft','proposed_text':'Proposal A','edits':[]}},
        {'state':'failed','text':'Original B','location':'other@0:10','document_result':None}]}
    before=deepcopy(report);partial=files.partial_review_document(report)
    assert report==before
    assert files.draft_blocks(partial)==['Proposal A','Original B']
    report['options']['mode']='translate'
    with pytest.raises(Failure,match='document_draft_unavailable'):files.partial_review_document(report)
