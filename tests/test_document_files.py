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
    assert any('https://example.org/vir' in b['text'] for b in captured['blocks'])
    assert any('Ne ohranja postavitve' in b['text'] for b in captured['blocks'])
    assert report['sections'][0]['text'] == original


def test_clean_drafts_require_every_section_and_rejoin_split_source_paragraphs():
    report={'state':'complete','sections':[
        {'location':'text@0:4','state':'complete','document_result':{'status':'draft','proposed_text':' A\r\n'}},
        {'location':'text@4:6','state':'complete','document_result':{'status':'draft','proposed_text':'B '}}]}
    assert files.draft_text(report).decode()==' A\r\nB '
    assert files.extract(files.draft_docx(report),'draft.docx')['blocks'][0]['text']==' A\nB '
    report['sections'][1]['document_result']['status']='not_reviewed'
    with pytest.raises(Failure,match='document_draft_unavailable'):files.draft_docx(report)
