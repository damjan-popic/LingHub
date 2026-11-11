"""Bounded document text capture and source-linked report exports.

The source package is never unpacked, executed, fetched or rewritten. A report
is a new document and does not claim to preserve the source's Word layout.
"""
from __future__ import annotations

import hashlib
import io
import re
import stat
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from .security import Failure

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_TEXT = 200_000
MAX_BLOCKS = 100
MAX_SECTION = 3000
MAX_MEMBERS = 512
MAX_EXPANDED = 16 * 1024 * 1024
MAX_XML = 4 * 1024 * 1024
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
CONTENT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def _invalid(code='document_invalid', status=400):
    raise Failure(code, status)


def _xml(data):
    if len(data) > MAX_XML:
        _invalid('document_too_large', 413)
    try:
        decoded = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff'))
                              else 'utf-8-sig')
    except UnicodeError:
        _invalid()
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', decoded, re.I):
        _invalid()
    try:
        # Parse exactly the encoding whose declarations were checked above.
        # XML's automatic UTF-16 detection must not bypass the DTD guard.
        root = ET.fromstring(decoded)
    except (ET.ParseError, ValueError, RecursionError):
        _invalid()
    # Limit nesting before any subsequent tree walk uses recursive traversal.
    stack = [(root, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 64 or count > 200_000:
            _invalid('document_too_large', 413)
        stack.extend((child, depth + 1) for child in node)
    return root


def _valid_text(value):
    # Reject illegal XML/control values consistently for both input formats.
    if any(ord(c) < 32 and c not in '\n\r\t' or 0xD800 <= ord(c) <= 0xDFFF
           or ord(c) in {0xFFFE, 0xFFFF} for c in value):
        _invalid()
    return value


def _split(value):
    """Yield exact adjacent slices; joining them reproduces the original."""
    while len(value) > MAX_SECTION:
        cut = max((m.end() for m in re.finditer(r'\s+', value[:MAX_SECTION])), default=0)
        if cut == 0:
            cut = MAX_SECTION
        yield value[:cut]
        value = value[cut:]
    if value:
        yield value


def _package(data):
    archive = None
    try:
        archive = ZipFile(io.BytesIO(data))
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_EXPANDED:
            _invalid('document_too_large', 413)
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            _invalid()
        for info in infos:
            path = PurePosixPath(info.filename)
            # Deflate supports bounded decompression in ZipExtFile; reject the
            # less common codecs rather than trusting their declared sizes.
            if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                _invalid('document_unsupported')
            if (info.flag_bits & 1 or path.is_absolute() or '..' in path.parts
                    or '\\' in info.filename or '\0' in info.filename
                    or ':' in info.filename or stat.S_ISLNK(info.external_attr >> 16)):
                _invalid()
            if info.file_size > MAX_EXPANDED or (info.file_size > 1_000_000
                    and info.file_size > 200 * max(1, info.compress_size)):
                _invalid('document_too_large', 413)
        if '[Content_Types].xml' not in names or 'word/document.xml' not in names:
            _invalid()
        # Macro/embedded packages are unsupported even when their text looks ordinary.
        if any(re.search(r'(?:vbaProject|activeX|embeddings|altChunk)', name, re.I)
               for name in names):
            _invalid('document_unsupported')
        for name in names:
            if name.endswith('.rels') or name == '[Content_Types].xml':
                root = _xml(archive.read(name))
                if name == '[Content_Types].xml':
                    if root.tag != CONTENT + 'Types':
                        _invalid()
                    types = [e.get('ContentType', '') for e in root]
                    if any('macroEnabled' in t or 'vbaProject' in t for t in types):
                        _invalid('document_unsupported')
                    # Body, headers, footers, notes and comments are captured below;
                    # no unrecognized Word text part may be silently discarded.
                    for entry in root:
                        content_type = entry.get('ContentType', '')
                        part = entry.get('PartName', '').lstrip('/')
                        if 'wordprocessingml' in content_type and part.endswith('.xml'):
                            allowed = (part == 'word/document.xml'
                                or re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml', part)
                                or part in {'word/styles.xml', 'word/settings.xml', 'word/numbering.xml',
                                            'word/fontTable.xml', 'word/webSettings.xml'})
                            if not allowed:
                                _invalid('document_unsupported')
        return archive
    except Failure:
        if archive is not None:
            archive.close()
        raise
    except (BadZipFile, KeyError, RuntimeError, OSError, ValueError, NotImplementedError):
        if archive is not None:
            archive.close()
        _invalid()


def _paragraph_text(paragraph):
    output = []
    for node in paragraph.iter():
        if node.tag == W + 't':
            output.append(node.text or '')
        elif node.tag == W + 'tab':
            output.append('\t')
        elif node.tag in {W + 'br', W + 'cr'}:
            output.append('\n')
        elif node.tag == W + 'noBreakHyphen':
            output.append('\u2011')
        elif node.tag == W + 'softHyphen':
            output.append('\u00ad')
        elif node.tag in {W + 'sym', W + 'ptab', W + 'delText', W + 'instrText', W + 'fldChar', W + 'fldSimple',
                          W + 'del', W + 'ins', W + 'moveFrom', W + 'moveTo',
                          W + 'altChunk', W + 'txbxContent', W + 'object'}:
            _invalid('document_unsupported')
    return _valid_text(''.join(output))


def _word_blocks(data):
    blocks, warnings = [], []
    with _package(data) as archive:
        names = archive.namelist()
        parts = ['word/document.xml'] + sorted(n for n in names if re.fullmatch(
            r'word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml', n))
        for name in parts:
            root = _xml(archive.read(name))
            expected = ('document' if name == 'word/document.xml' else
                        'hdr' if '/header' in name else 'ftr' if '/footer' in name else
                        PurePosixPath(name).stem)
            if root.tag != W + expected or (expected == 'document'
                    and len(root.findall(W + 'body')) != 1):
                _invalid()
            # Reject constructs whose visible text cannot be captured faithfully.
            bad = {'altChunk', 'txbxContent', 'object', 'subDoc', 'del', 'ins',
                   'moveFrom', 'moveTo', 'fldSimple', 'fldChar', 'instrText', 'sym',
                   'ptab', 'delText'}
            if any(n.tag in {W + tag for tag in bad} for n in root.iter()):
                _invalid('document_unsupported')
            if any(n.tag.rsplit('}', 1)[-1] in {'oMath', 'oMathPara', 'AlternateContent'}
                   for n in root.iter()):
                _invalid('document_unsupported')
            # DrawingML/WordArt text needs a separate layout-aware capture path.
            if any(n.tag.rsplit('}', 1)[-1] == 't' and n.tag != W + 't'
                   for n in root.iter()):
                _invalid('document_unsupported')
            if any(n.tag in {W + 'drawing', W + 'pict'} for n in root.iter()):
                warnings.append('document_images_not_reviewed')

            def visit(node, path):
                if node.tag == W + 'p':
                    value = _paragraph_text(node)
                    if value:
                        blocks.append((path, value))
                    return
                counts = {}
                for child in node:
                    local = child.tag.rsplit('}', 1)[-1]
                    counts[local] = counts.get(local, 0) + 1
                    visit(child, f'{path}/{local}[{counts[local]}]')

            visit(root, name)
    return blocks, list(dict.fromkeys(warnings))


def extract(data: bytes, filename: str):
    """Capture TXT/DOCX into ordered, located sections without silent truncation."""
    if not isinstance(data, bytes) or not isinstance(filename, str):
        _invalid()
    if len(data) > MAX_UPLOAD_BYTES:
        _invalid('document_too_large', 413)
    if (not filename or len(filename) > 180 or any(c in filename for c in '/\\\0')
            or any(ord(c) < 32 for c in filename)):
        _invalid()
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == '.txt':
        try:
            value = data.decode('utf-8-sig')
        except UnicodeError:
            _invalid()
        # Keep even blank lines and exact CRLFs in the captured source text.
        source, warnings = [('text', _valid_text(value))], []
    elif suffix == '.docx':
        try:
            source, warnings = _word_blocks(data)
        except Failure:
            raise
        except (BadZipFile, KeyError, RuntimeError, OSError, ValueError, NotImplementedError):
            _invalid()
    else:
        _invalid('document_unsupported', 415)
    if sum(len(value) for _, value in source) > MAX_TEXT:
        _invalid('document_too_large', 413)
    if not any(value.strip() for _, value in source):
        _invalid('document_empty')
    blocks = []
    for location, value in source:
        offset = 0
        for part in _split(value):
            blocks.append({'index': len(blocks), 'location': f'{location}@{offset}:{offset + len(part)}',
                           'text': part})
            offset += len(part)
            if len(blocks) > MAX_BLOCKS:
                _invalid('document_too_large', 413)
    return {'name': filename, 'sha256': hashlib.sha256(data).hexdigest(),
            'blocks': blocks, 'warnings': warnings}


def _report_lines(report):
    it = report.get('locale') == 'it'
    yield ('Rapporto KLIKai: ' if it else 'Poročilo KLIKai: ') + str(report.get('name', ''))
    yield ('Questo rapporto contiene proposte e fonti. Non conserva l’impaginazione del file originale.'
           if it else 'Poročilo vsebuje predloge in vire. Ne ohranja postavitve izvirne datoteke.')
    yield f"{report.get('mode', '')}: {report.get('source_language', '')} → {report.get('target_language', '')}"
    for warning in report.get('warnings',[]):
        if warning=='document_images_not_reviewed':
            yield 'Le immagini non sono state elaborate.'if it else'Slike niso bile obdelane.'
    for section in report.get('sections', []):
        yield ''
        yield f"{int(section.get('index', 0)) + 1}. {section.get('location', '')}"
        yield ('Stato: ' if it else 'Stanje: ') + str(section.get('status', ''))
        yield ('Originale:' if it else 'Izvirnik:')
        yield str(section.get('text', ''))
        yield ('Risultato:' if it else 'Rezultat:')
        yield str(section.get('output', ''))
        sources = section.get('sources') or []
        numbers={source.get('evidence_id'):source.get('local_number',index+1)
                 for index,source in enumerate(sources)}
        for edit in (section.get('document_result')or{}).get('edits',[]):
            yield ('Modifica proposta:'if it else'Predlagana sprememba:')
            yield str(edit['original'])+' → '+str(edit['replacement'])
            yield str(edit['explanation'])
            yield ('Fonti: 'if it else'Viri: ')+', '.join('['+str(numbers[eid])+']'
                for eid in edit['evidence_ids']if eid in numbers)
        if sources:
            yield ('Fonti:' if it else 'Viri:')
            for index,source in enumerate(sources):
                yield '['+str(source.get('local_number',index+1))+'] '+' · '.join(
                    str(source.get(k, '')) for k in ('title', 'url') if source.get(k))


def report_text(report):
    return '\n'.join(_report_lines(report)).encode('utf-8')


def report_docx(report):
    """A new Word report, retaining exact original strings beside each result."""
    return _text_docx(_report_lines(report))


def _text_docx(lines):
    document = ET.Element(W + 'document')
    body = ET.SubElement(document, W + 'body')
    for line in lines:
        paragraph = ET.SubElement(body, W + 'p')
        run = ET.SubElement(paragraph, W + 'r')
        # Represent tabs/newlines as native Word tokens and preserve whitespace.
        for token in re.split(r'(\r\n|\r|\n|\t)', line):
            if token == '\t':
                ET.SubElement(run, W + 'tab')
            elif token in {'\r\n', '\n', '\r'}:
                ET.SubElement(run, W + 'br')
            elif token:
                ET.SubElement(run, W + 't', {XML_SPACE: 'preserve'}).text = token
    content_types = (b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        b'</Types>')
    relationships = (b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        b'</Relationships>')
    result = io.BytesIO()
    with ZipFile(result, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', relationships)
        archive.writestr('word/document.xml', ET.tostring(document, encoding='utf-8', xml_declaration=True))
    return result.getvalue()


def draft_blocks(report):
    """Return a complete proposed text, only after every section has a draft."""
    if report.get('state')!='complete' or not report.get('sections'):
        _invalid('document_draft_unavailable',409)
    blocks=[]
    for section in report['sections']:
        result=section.get('document_result')or{}
        if section.get('state')!='complete'or result.get('status')!='draft'or not isinstance(result.get('proposed_text'),str):
            _invalid('document_draft_unavailable',409)
        location=section['location'].rsplit('@',1)[0]
        if blocks and blocks[-1][0]==location:
            blocks[-1]=(location,blocks[-1][1]+result['proposed_text'])
        else:blocks.append((location,result['proposed_text']))
    return [text for _,text in blocks]


def draft_text(report):
    return '\n\n'.join(draft_blocks(report)).encode('utf-8')


def draft_docx(report):
    """New text-only Word draft; the original layout is never overwritten."""
    return _text_docx(draft_blocks(report))
