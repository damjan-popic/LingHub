"""Apply a complete draft to the captured Word paragraphs without flattening it.

ZIP members and XML markup remain in place. Only text-token content in changed
paragraphs is replaced. Run properties survive, although translated word order
cannot guarantee the original association of every inline style with a word.
"""
from copy import copy
from difflib import SequenceMatcher
import io
import hashlib
import re
import unicodedata
from xml.parsers import expat
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZipFile

from . import document_files as files
from .security import Failure

_W = files.W[1:]
_CONTROLS = '\t\r\n\u2011\u00ad'


def _word_char(value):
    return bool(value)and(value.isalnum()or value=='_'or unicodedata.category(value).startswith('M'))


def _whole_expression(text,start,end):
    if start==end:return True
    return not((start and _word_char(text[start-1])and _word_char(text[start]))
        or(end<len(text)and _word_char(text[end-1])and _word_char(text[end])))


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
    run,hyperlink=None,None

    def style():
        return {'hyperlink':hyperlink,'emphasis':tuple(sorted((run or{}).items()))}

    def start(name, attributes):
        nonlocal paragraph, text_node,run,hyperlink
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
            paragraph = {'location': location, 'nodes': [], 'tokens': [],'token_styles':[]}
            paragraphs[location] = paragraph
        if paragraph is None:
            return
        if name==_W+'r':
            if run is not None:_unavailable()
            run={}
        elif name==_W+'hyperlink':
            if hyperlink is not None:_unavailable()
            hyperlink=location
        elif run is not None and any(row['name']==_W+'rPr'for row in stack):
            if local in {'b','bCs','i','iCs','strike','dstrike','caps','smallCaps'}:
                if attributes.get(_W+'val','true').lower()not in {'0','false','off'}:run[local]='true'
            elif local=='u':
                value=attributes.get(_W+'val','single')
                if value not in {'none','0','false','off'}:run[local]=value
            elif local in {'rStyle','highlight'}:
                value=attributes.get(_W+'val','')
                if value and value not in {'none','default'}:run[local]=value
            elif local=='color':
                value=attributes.get(_W+'val','auto')
                if value.lower()not in {'auto','000000'}:run[local]=value
        if name == _W + 't':
            begin = parser.CurrentByteIndex
            end = _tag_end(data, begin)
            tag = data[begin:end]
            text_node = {'start': begin, 'open_end': end, 'value': '',
                         'self_closing': bool(re.search(rb'/\s*>$', tag)),
                         'name': re.match(rb'<\s*([^\s/>]+)', tag).group(1),'style':style()}
        elif name in {_W + 'tab', _W + 'br', _W + 'cr',
                      _W + 'noBreakHyphen', _W + 'softHyphen'}:
            value = '\t' if name == _W + 'tab' else '\n' if name in {
                _W + 'br', _W + 'cr'} else '\u2011' if name == _W + 'noBreakHyphen' else '\u00ad'
            paragraph['tokens'].append((value, None))
            paragraph['token_styles'].append(style())

    def end(name):
        nonlocal paragraph, text_node,run,hyperlink
        if name == _W + 't':
            if paragraph is None or text_node is None:
                _unavailable()
            text_node['close_start'] = text_node['open_end'] if text_node['self_closing'] else parser.CurrentByteIndex
            index = len(paragraph['nodes'])
            paragraph['nodes'].append(text_node)
            paragraph['tokens'].append((text_node['value'], index))
            paragraph['token_styles'].append(text_node['style'])
            text_node = None
        if name==_W+'r':run=None
        if name==_W+'hyperlink':hyperlink=None
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


def _original_paragraphs(data):
    if not isinstance(data,bytes)or len(data)>files.MAX_UPLOAD_BYTES:_unavailable()
    captured,_=files._word_blocks(data)
    if (not captured or sum(len(value)for _,value in captured)>files.MAX_TEXT
            or not any(value.strip()for _,value in captured)):_unavailable()
    return captured


def _paragraph_anchors(paragraph):
    original=''.join(value for value,_ in paragraph['tokens'])
    ranges=[];offset=0
    for (value,_),style in zip(paragraph['tokens'],paragraph['token_styles']):
        if value:
            ranges.append({'start':offset,'end':offset+len(value),'link':style['hyperlink'],
                           'emphasis':style['emphasis']})
        offset+=len(value)
    links={}
    for row in ranges:
        if row['link']:
            value=links.setdefault(row['link'],{'start':row['start'],'end':row['end'],'styles':set()})
            value['end']=row['end'];value['styles'].add(row['emphasis'])
    selected=[]
    for row in links.values():
        if row['start']==0 and row['end']==len(original):continue
        # One model mapping cannot preserve a smaller emphasis range inside a
        # hyperlink. Do not discard that nested semantic range silently.
        if len(row['styles'])!=1:_unavailable()
        selected.append({'kind':'hyperlink','start':row['start'],'end':row['end']})
    emphasis=[]
    for row in ranges:
        if not row['emphasis']or any(a['start']<=row['start']and row['end']<=a['end']for a in selected):
            continue
        if (emphasis and emphasis[-1]['end']==row['start']
                and emphasis[-1]['signature']==row['emphasis']):
            emphasis[-1]['end']=row['end']
        else:emphasis.append({'kind':'emphasis','start':row['start'],'end':row['end'],'signature':row['emphasis']})
    selected.extend({'kind':'emphasis','start':row['start'],'end':row['end']}for row in emphasis
                    if not(row['start']==0 and row['end']==len(original)))
    selected.sort(key=lambda row:(row['start'],row['end']))
    last=0
    for row in selected:
        if row['start']<last:_unavailable()
        row['text']=original[row['start']:row['end']]
        if any(char in _CONTROLS for char in row['text']):_unavailable()
        if not _whole_expression(original,row['start'],row['end']):_unavailable()
        identity='\0'.join((paragraph['location'],row['kind'],str(row['start']),str(row['end']),row['text']))
        row['id']='wi1-'+hashlib.sha256(identity.encode('utf-8')).hexdigest()
        last=row['end']
    return selected


def source_anchors(original_docx_bytes):
    """Return disjoint partial-paragraph semantic source spans without URLs.

    Unsupported overlapping or nested marks raise document_draft_unavailable;
    callers may expose that as an unsupported Word structure at upload.
    """
    try:
        captured=_original_paragraphs(original_docx_bytes)
        wanted={location for location,_ in captured};result={};count=0
        with files._package(original_docx_bytes)as archive:
            for part in {location.partition('.xml')[0]+'.xml'for location in wanted}:
                _,paragraphs=_paragraphs(archive.read(part),part)
                for location,paragraph in paragraphs.items():
                    if location in wanted:
                        rows=_paragraph_anchors(paragraph)
                        if rows:result[location]=rows;count+=len(rows)
                        if count>128:_unavailable()
        return result
    except Failure as error:
        if error.code=='document_draft_unavailable':raise
        _unavailable()
    except (BadZipFile,OSError,RuntimeError,ValueError,KeyError,TypeError,
            NotImplementedError,UnicodeError,RecursionError):_unavailable()


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


def _slice_tokens(paragraph,start,end):
    tokens=[];offset=0
    for value,owner in paragraph['tokens']:
        a,b=max(start-offset,0),min(end-offset,len(value))
        if a<b:tokens.append((value[a:b],owner))
        offset+=len(value)
    return {'nodes':paragraph['nodes'],'tokens':tokens}


def _mapped_node_values(paragraph,proposed,anchors,mappings):
    original=''.join(value for value,_ in paragraph['tokens'])
    if (not isinstance(mappings,list)or len(mappings)!=len(anchors)
            or [row.get('id')for row in mappings if isinstance(row,dict)]!=[row['id']for row in anchors]):
        _unavailable()
    output=[''for _ in paragraph['nodes']];source_end=target_end=0
    segments=[]
    for anchor,mapping in zip(anchors,mappings):
        if not isinstance(mapping,dict)or set(mapping)!={'id','target_start','target_end','target_text'}:_unavailable()
        a,b=mapping['target_start'],mapping['target_end']
        if (type(a)is not int or type(b)is not int or not target_end<=a<=b<=len(proposed)
                or not isinstance(mapping['target_text'],str)or proposed[a:b]!=mapping['target_text']
                or not _whole_expression(proposed,a,b)):_unavailable()
        segments.extend([(source_end,anchor['start'],target_end,a),
                         (anchor['start'],anchor['end'],a,b)])
        source_end,target_end=anchor['end'],b
    segments.append((source_end,len(original),target_end,len(proposed)))
    for a,b,x,y in segments:
        assigned=_node_values(_slice_tokens(paragraph,a,b),proposed[x:y])
        for index,value in enumerate(assigned):output[index]+=value
    recreated=''.join(value if owner is None else output[owner]for value,owner in paragraph['tokens'])
    if recreated!=proposed:_unavailable()
    return output


def _review_mappings(original,proposed,anchors,edits):
    result=[]
    for anchor in anchors:
        text=anchor['text']
        if proposed.count(text)==1:
            start=proposed.index(text);end=start+len(text)
        else:
            if not edits:_unavailable()
            # Exact source-language edits may shift an unchanged range or
            # replace text wholly inside it. A replacement crossing a mark's
            # edge has no proven inline attachment and must not be guessed.
            for a,b,replacement in edits:
                if (a<anchor['start']<b or a<anchor['end']<b):_unavailable()
            start=anchor['start']+sum(len(value)-(b-a)for a,b,value in edits if b<=anchor['start'])
            end=anchor['end']+sum(len(value)-(b-a)for a,b,value in edits if b<=anchor['end'])
            expected=[];last=anchor['start']
            for a,b,value in edits:
                if anchor['start']<=a and b<=anchor['end']:
                    expected.extend([original[last:a],value]);last=b
            expected.append(original[last:anchor['end']])
            if proposed[start:end]!=''.join(expected):_unavailable()
        result.append({'id':anchor['id'],'target_start':start,'target_end':end,'target_text':proposed[start:end]})
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
    # Saved section boundaries belong to the captured document. Reapplying the
    # newest sentence/chunk policy would invalidate older, otherwise exact
    # drafts. Bind their contiguous codepoint ranges to the native paragraphs.
    captured=_original_paragraphs(original_docx_bytes)
    anchor_map=source_anchors(original_docx_bytes)
    if not isinstance(sections,list)or not 1<=len(sections)<=files.MAX_BLOCKS:
        _unavailable()
    proposals = {}
    paragraph_mappings={};paragraph_modes={};paragraph_edits={}
    output_size = 0
    paragraph_index,offset=0,0
    for ordinal,section in enumerate(sections):
        result = section.get('document_result') if isinstance(section, dict) else None
        if (not isinstance(section,dict)or type(section.get('index'))is not int
                or section['index']!=ordinal or paragraph_index>=len(captured)
                or not isinstance(section.get('location'),str)or section.get('state')!='complete'
                or not isinstance(result, dict) or result.get('status') != 'draft'
                or not isinstance(result.get('proposed_text'), str)):
            _unavailable()
        location,original=captured[paragraph_index]
        base,separator,span=section['location'].rpartition('@')
        if base!=location or not separator or len(span)>20 or re.fullmatch(r'[0-9]+:[0-9]+',span)is None:
            _unavailable()
        a,b=map(int,span.split(':'))
        if (section['location']!=f'{location}@{a}:{b}'or a!=offset
                or not 0<=a<b<=len(original)or section.get('text')!=original[a:b]):
            _unavailable()
        raw_proposed=files._valid_text(result['proposed_text'])
        proposed,mapping_shift=files.draft_section_text(section)
        output_size += len(proposed)
        if output_size > files.MAX_TEXT * 4:
            _unavailable()
        old, new = proposals.get(location, ('', ''))
        mode=result.get('mode')
        if mode not in {None,'review','translate'}:_unavailable()
        paragraph_modes.setdefault(location,set()).add(mode)
        mappings=result.get('inline_mappings',[])
        if not isinstance(mappings,list)or len(mappings)>16:_unavailable()
        expected_anchors={row['id']:row for row in anchor_map.get(location,[])}
        for mapping in mappings:
            if (not isinstance(mapping,dict)or set(mapping)!={'id','target_start','target_end','target_text'}
                    or not isinstance(mapping.get('id'),str)or mapping['id']not in expected_anchors):_unavailable()
            native=expected_anchors[mapping['id']]
            x,y=mapping['target_start'],mapping['target_end']
            if (not a<=native['start']<native['end']<=b
                    or type(x)is not int or type(y)is not int or not 0<=x<y<=len(raw_proposed)
                    or raw_proposed[x:y]!=mapping.get('target_text')or raw_proposed.count(mapping['target_text'])!=1
                    or not 0<=x+mapping_shift<y+mapping_shift<=len(proposed)
                    or proposed[x+mapping_shift:y+mapping_shift]!=mapping['target_text']):_unavailable()
            paragraph_mappings.setdefault(location,[]).append({**mapping,'target_start':x+mapping_shift+len(new),'target_end':y+mapping_shift+len(new)})
        edits=result.get('edits',[])
        if mode=='review':
            if not isinstance(edits,list)or len(edits)>64:_unavailable()
            last=0;rendered=[]
            for edit in edits:
                if not isinstance(edit,dict):_unavailable()
                x,y,replacement=edit.get('start'),edit.get('end'),edit.get('replacement')
                if (type(x)is not int or type(y)is not int or not last<=x<y<=len(section['text'])
                        or section['text'][x:y]!=edit.get('original')or not isinstance(replacement,str)):_unavailable()
                rendered.extend([section['text'][last:x],replacement]);last=y
                paragraph_edits.setdefault(location,[]).append((x+a,y+a,replacement))
            rendered.append(section['text'][last:])
            if ''.join(rendered)!=raw_proposed:_unavailable()
        proposals[location] = (old + section['text'], new + proposed)
        offset=b
        if offset==len(original):
            paragraph_index+=1
            offset=0
    if paragraph_index!=len(captured)or offset:
        _unavailable()
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
                if original == proposed and not paragraph_mappings.get(location):
                    continue
                modes=paragraph_modes[location]
                if len(modes)!=1:_unavailable()
                anchors=anchor_map.get(location,[])
                if anchors:
                    mappings=paragraph_mappings.get(location,[])
                    if not mappings:
                        if 'translate'in modes:_unavailable()
                        mappings=_review_mappings(original,proposed,anchors,paragraph_edits.get(location,[]))
                    values=_mapped_node_values(paragraph,proposed,anchors,mappings)
                else:
                    if paragraph_mappings.get(location):_unavailable()
                    values=_node_values(paragraph, proposed)
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
    final,_=files._word_blocks(result)
    if not any(value.strip()for _,value in final):
        _unavailable()
    grouped=dict(final)
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
