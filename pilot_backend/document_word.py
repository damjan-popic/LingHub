"""Apply a complete draft to the captured Word paragraphs without flattening it.

ZIP members and XML markup remain in place. Only text-token content in changed
paragraphs is replaced. Run properties survive, although translated word order
cannot guarantee the original association of every inline style with a word.
"""
from copy import copy
from difflib import SequenceMatcher
import io
import re
from xml.parsers import expat
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZipFile

from . import document_files as files
from .security import Failure

_W = files.W[1:]
_CONTROLS = '\t\r\n\u2011\u00ad'


def _unavailable():
    raise Failure('document_draft_unavailable', 409)


def _utf8_xml(data):
    try:
        text = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff'))
                           else 'utf-8-sig')
    except UnicodeError:
        _unavailable()
    # Expat offsets refer to this UTF-8 representation. Retain all original
    # namespace prefixes/markup; a changed UTF-16 part gets an explicit UTF-8
    # declaration rather than an incompatible declaration over new bytes.
    text = re.sub(r'(<\?xml\b[^?]*\bencoding\s*=\s*)([\'"])[^\'"]+\2',
                  lambda m: m[1] + m[2] + 'UTF-8' + m[2], text, count=1, flags=re.I)
    return text.encode('utf-8')


def _tag_end(data, start):
    quote = None
    for index in range(start, len(data)):
        byte = data[index]
        if quote is not None:
            if byte == quote:
                quote = None
        elif byte in (34, 39):
            quote = byte
        elif byte == 62:
            return index + 1
    _unavailable()


def _paragraphs(data, part):
    """Map the same native locations as document_files to exact XML byte spans."""
    data = _utf8_xml(data)
    parser = expat.ParserCreate(namespace_separator='}')
    stack, paragraphs, paragraph = [], {}, None
    text_node = None

    def start(name, attributes):
        nonlocal paragraph, text_node
        if text_node is not None:
            _unavailable()  # A Word text token cannot contain nested markup.
        local = name.rsplit('}', 1)[-1]
        if stack:
            parent = stack[-1]
            number = parent['counts'].get(local, 0) + 1
            parent['counts'][local] = number
            location = parent['location'] + '/' + local + '[' + str(number) + ']'
        else:
            location = part
        stack.append({'name': name, 'location': location, 'counts': {}})
        if name == _W + 'p':
            if paragraph is not None:
                _unavailable()
            paragraph = {'location': location, 'nodes': [], 'tokens': []}
            paragraphs[location] = paragraph
        if paragraph is None:
            return
        if name == _W + 't':
            begin = parser.CurrentByteIndex
            end = _tag_end(data, begin)
            tag = data[begin:end]
            text_node = {'start': begin, 'open_end': end, 'value': '',
                         'self_closing': bool(re.search(rb'/\s*>$', tag)),
                         'name': re.match(rb'<\s*([^\s/>]+)', tag).group(1)}
        elif name in {_W + 'tab', _W + 'br', _W + 'cr',
                      _W + 'noBreakHyphen', _W + 'softHyphen'}:
            value = '\t' if name == _W + 'tab' else '\n' if name in {
                _W + 'br', _W + 'cr'} else '\u2011' if name == _W + 'noBreakHyphen' else '\u00ad'
            paragraph['tokens'].append((value, None))

    def end(name):
        nonlocal paragraph, text_node
        if name == _W + 't':
            if paragraph is None or text_node is None:
                _unavailable()
            text_node['close_start'] = text_node['open_end'] if text_node['self_closing'] else parser.CurrentByteIndex
            index = len(paragraph['nodes'])
            paragraph['nodes'].append(text_node)
            paragraph['tokens'].append((text_node['value'], index))
            text_node = None
        if name == _W + 'p':
            paragraph = None
        stack.pop()

    def content(value):
        if text_node is not None:
            text_node['value'] += value

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = content
    parser.ExternalEntityRefHandler = lambda *args: 0
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, IndexError, AttributeError):
        _unavailable()
    return data, paragraphs


def _assign(source, target, owners, output):
    if not source:
        if target:
            _unavailable()
        return
    if not target:
        return
    # Avoid an unbounded quadratic diff on a very long, repeated paragraph.
    # A coarse replacement retains the first source run's formatting and all
    # surrounding paragraph/table markup; it does not flatten the document.
    if len(set(owners)) == 1 or len(source) * len(target) > 4_000_000:
        output[owners[0]].append(target)
        return
    for operation, a, b, x, y in SequenceMatcher(None, source, target, autojunk=False).get_opcodes():
        if operation == 'equal':
            for index in range(a, b):
                output[owners[index]].append(source[index])
        elif operation == 'replace':
            output[owners[a]].append(target[x:y])
        elif operation == 'insert':
            anchor = owners[a - 1] if a else owners[0]
            output[anchor].append(target[x:y])


def _node_values(paragraph, proposed):
    original = ''.join(value for value, _ in paragraph['tokens'])
    if [c for c in original if c in _CONTROLS] != [c for c in proposed if c in _CONTROLS]:
        _unavailable()
    target_parts = re.split('([' + _CONTROLS + '])', proposed)
    groups, controls, chars, owners = [], [], [], []
    for value, owner in paragraph['tokens']:
        for char in value:
            if char in _CONTROLS:
                groups.append((''.join(chars), owners))
                controls.append((char, owner))
                chars, owners = [], []
            else:
                if owner is None:
                    _unavailable()
                chars.append(char)
                owners.append(owner)
    groups.append((''.join(chars), owners))
    output = [[] for _ in paragraph['nodes']]
    for index, (source, source_owners) in enumerate(groups):
        _assign(source, target_parts[index * 2], source_owners, output)
        if index < len(controls):
            char, owner = controls[index]
            if owner is not None:
                output[owner].append(char)
    result = [''.join(chunks) for chunks in output]
    # Prove that retained structural tokens and replacement text reconstruct
    # exactly the proposed paragraph, before changing any package bytes.
    recreated = ''.join(value if owner is None else result[owner]
                        for value, owner in paragraph['tokens'])
    if recreated != proposed:
        _unavailable()
    return result


def _xml_text(value):
    return escape(value).replace('\r', '&#13;').replace('\n', '&#10;').replace('\t', '&#9;').encode('utf-8')


def _replacement(data, node, value):
    tag = data[node['start']:node['open_end']]
    if re.search(rb'\bxml:space\s*=', tag):
        tag = re.sub(rb'\bxml:space\s*=\s*([\'"])[^\'"]*\1', b'xml:space="preserve"', tag)
    else:
        tag = re.sub(rb'\s*/?\s*>$', lambda m: b' xml:space="preserve"' + (b'/>' if node['self_closing'] else b'>'), tag)
    if node['self_closing']:
        tag = re.sub(rb'/\s*>$', b'>', tag)
        return node['start'], node['open_end'], tag + _xml_text(value) + b'</' + node['name'] + b'>'
    if b'<' in data[node['open_end']:node['close_start']]:
        _unavailable()
    return node['start'], node['close_start'], tag + _xml_text(value)


def _rewrite(original_docx_bytes, sections):
    """Return an edited DOCX only when every source section has a bound draft.

    The input bytes are never modified. Unsupported structural changes fail
    closed; callers can still offer the separate source-linked report.
    """
    captured = files.extract(original_docx_bytes, 'original.docx')['blocks']
    if not isinstance(sections, list) or len(sections) != len(captured):
        _unavailable()
    proposals = {}
    output_size = 0
    for expected, section in zip(captured, sections):
        result = section.get('document_result') if isinstance(section, dict) else None
        if (not isinstance(section, dict) or any(section.get(key) != expected[key]
                for key in ('index', 'location', 'text')) or section.get('state') != 'complete'
                or not isinstance(result, dict) or result.get('status') != 'draft'
                or not isinstance(result.get('proposed_text'), str)):
            _unavailable()
        proposed = files._valid_text(result['proposed_text'])
        output_size += len(proposed)
        if output_size > files.MAX_TEXT * 4:
            _unavailable()
        location = expected['location'].rsplit('@', 1)[0]
        old, new = proposals.get(location, ('', ''))
        proposals[location] = (old + expected['text'], new + proposed)
    changed = {}
    with files._package(original_docx_bytes) as archive:
        if any(name.lower().startswith('_xmlsignatures/') or name.lower().endswith('.sigs') for name in archive.namelist()):
            _unavailable()
        for name in archive.namelist():
            if name.endswith('.rels') and any('digital-signature' in node.get('Type', '').lower()
                    for node in files._xml(archive.read(name))):
                _unavailable()
        targets = {location.partition('.xml')[0] + '.xml' for location in proposals}
        for part in targets:
            raw, paragraphs = _paragraphs(archive.read(part), part)
            replacements = []
            for location, paragraph in paragraphs.items():
                if location not in proposals:
                    continue
                original, proposed = proposals[location]
                if ''.join(value for value, _ in paragraph['tokens']) != original:
                    _unavailable()
                if original == proposed:
                    continue
                values = _node_values(paragraph, proposed)
                for node, value in zip(paragraph['nodes'], values):
                    if value != node['value']:
                        replacements.append(_replacement(raw, node, value))
            if any(location not in paragraphs for location in proposals if location.startswith(part + '/')):
                _unavailable()
            if replacements:
                for start, end, value in sorted(replacements, reverse=True):
                    raw = raw[:start] + value + raw[end:]
                # Reparse the changed part under the same bounded XML contract.
                files._xml(raw)
                changed[part] = raw
        if not changed:
            return original_docx_bytes
        expanded = sum(len(changed.get(info.filename, b'')) if info.filename in changed
                       else info.file_size for info in archive.infolist())
        if expanded > files.MAX_EXPANDED:
            _unavailable()
        output = io.BytesIO()
        with ZipFile(output, 'w') as destination:
            destination.comment = archive.comment
            for info in archive.infolist():
                destination.writestr(copy(info), changed.get(info.filename, archive.read(info.filename)))
    result = output.getvalue()
    final = files.extract(result, 'draft.docx')['blocks']
    grouped = {}
    for block in final:
        location = block['location'].rsplit('@', 1)[0]
        grouped[location] = grouped.get(location, '') + block['text']
    if any(grouped.get(location, '') != proposed for location, (_, proposed) in proposals.items()):
        _unavailable()
    return result


def rewrite(original_docx_bytes, sections):
    """Fail closed when a complete Word draft cannot retain its source structure."""
    try:
        return _rewrite(original_docx_bytes, sections)
    except Failure as error:
        if error.code == 'document_draft_unavailable':
            raise
        _unavailable()
    except (BadZipFile, OSError, RuntimeError, ValueError, KeyError, TypeError,
            NotImplementedError, UnicodeError, RecursionError):
        _unavailable()
