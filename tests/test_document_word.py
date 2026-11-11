"""Word drafts keep source package structure and exact section identities."""
from copy import deepcopy
import io
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

import pytest

from pilot_backend import document_files as files
from pilot_backend.document_word import rewrite
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


def test_combining_characters_across_different_runs_are_bound_as_codepoints():
    data = package('<w:p><w:r><w:t>c</w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>̌lovek</w:t></w:r><w:r><w:t> potrebuje pomoč.</w:t></w:r></w:p>')
    draft = rewrite(data, sections(data, {0: 'človek potrebuje pomoč.'}))
    assert texts(draft) == ['človek potrebuje pomoč.']
    with ZipFile(io.BytesIO(draft)) as output:
        assert b'<w:rPr><w:i/></w:rPr>' in output.read('word/document.xml')


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
