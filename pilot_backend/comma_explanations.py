"""Link saved comma proposals to already admitted, cited applications.

No linguistic reason is inferred from a prediction or from answer prose. A
source excerpt without an owned application remains related guidance. The
caller supplies the normal validated CORE result and its private selected-unit
receipts; no source retrieval, model call or answer-text mutation occurs here.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import re


SCHEMA = 'klikai.comma-explanations.v1'
OFFSET = 'unicode_codepoint_half_open'
RELATIONS = {'supports_proposal', 'disagrees_with_proposal', 'conditional_support'}


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _punctuation(text):
    """Exact noncomma tokens plus comma boundaries in original code points.

    Whitespace is used only to recognize an existing example. Word boundaries,
    case, digits, negation and every other punctuation token remain distinct.
    """
    basis, commas = [], {}
    for match in re.finditer(r'\w+|[^\w\s]', text, re.UNICODE):
        if match.group() == ',':
            commas[match.start()] = len(basis)
        else:
            basis.append(match.group())
    return tuple(basis), commas, Counter(commas.values())


def _apply(text, edits):
    pieces, cursor = [], 0
    for edit in edits:
        left, right = edit.get('start'), edit.get('end')
        if (type(left) is not int or type(right) is not int
                or not cursor <= left <= right <= len(text)
                or not isinstance(edit.get('replacement'), str)
                or edit.get('original') != text[left:right]):
            raise ValueError('Proposal edit is not bound to the exact target')
        pieces.extend((text[cursor:left], edit['replacement'])); cursor = right
    return ''.join(pieces) + text[cursor:]


def _proposal(proposal, task):
    target = task.get('target') if isinstance(task, dict) else None
    text = proposal.get('original_text') if isinstance(proposal, dict) else None
    edits = proposal.get('edits') if isinstance(proposal, dict) else None
    if (proposal.get('status') != 'ok' or not isinstance(text, str)
            or not isinstance(edits, list) or not isinstance(target, dict)
            or target.get('text') != text or target.get('scope') not in {'question', 'context'}
            or type(target.get('start')) is not int or type(target.get('end')) is not int
            or not 0 <= target['start'] < target['end']
            or target['end']-target['start'] != len(text)
            or proposal.get('offset_convention', OFFSET) != OFFSET
            or _apply(text, edits) != proposal.get('proposed_text')
            or _punctuation(text)[0] != _punctuation(proposal['proposed_text'])[0]):
        raise ValueError('Proposal and interpreted target differ')
    return target, text, edits


def _citation_matches(binding, citation):
    passage = citation.get('evidence_id') or citation.get('passage_id') or citation.get('citation_id')
    if (binding.get('support_id') != citation.get('support_id')
            or binding.get('passage_id') != passage
            or binding.get('source_slug') != (citation.get('source_key') or citation.get('source_slug'))
            or binding.get('char_start') != citation.get('char_start')
            or binding.get('char_end') != citation.get('char_end')
            or not isinstance(citation.get('snippet'), str)
            or binding.get('text_sha256') != _sha(citation['snippet'])):
        return False
    return all(binding.get(key) == citation.get(key) for key in ('source_id', 'source_revision') if key in binding)


def _graph(explained, receipt):
    identity = receipt.get('unit_id')
    kind = receipt.get('unit_type', receipt.get('type'))
    if ('unit_type' in receipt and 'type' in receipt and receipt['unit_type'] != receipt['type']
            or kind not in {'construction_application', 'source_advice'}
            or not isinstance(receipt.get('payload'), dict)
            or not isinstance(receipt.get('bindings'), list) or not receipt['bindings']):
        return None
    claims = [row for row in explained.get('claims', []) if row.get('unit_id') == identity]
    blocks = [row for row in explained.get('display_blocks', []) if row.get('unit_id') == identity]
    if len(claims) != 1 or len(blocks) != 1:
        return None
    claim, block = claims[0], blocks[0]
    if (block.get('unit_type') != kind or block.get('claim_id') != claim.get('claim_id')
            or claim.get('text') != block.get('text')
            or claim.get('paragraph_id') != block.get('paragraph_id')):
        return None
    citations = [(index, row) for index, row in enumerate(explained.get('citations', []))
                 if row.get('unit_id') == identity and row.get('claim_id') == claim.get('claim_id')
                 and row.get('block_id') == block.get('block_id')]
    bindings = receipt['bindings']
    if (len(bindings) != len(citations)
            or any(sum(_citation_matches(binding, row) for _, row in citations) != 1 for binding in bindings)
            or any(sum(_citation_matches(binding, row) for binding in bindings) != 1 for _, row in citations)
            or set(claim.get('support_ids', [])) != {row.get('support_id') for row in bindings}
            or set(block.get('support_ids', [])) != set(claim.get('support_ids', []))):
        return None
    graph = {'unit_id': identity, 'unit_type': kind, 'claim_id': claim['claim_id'],
        'block_id': block['block_id'], 'citation_indexes': [index for index, _ in citations],
        'evidence_ids': list(dict.fromkeys(row['evidence_id'] for _, row in citations))}
    return graph, [(index, row['evidence_id'], row['snippet']) for index, row in citations]


def _candidates(receipt, target, text, source_quotes):
    payload = receipt['payload']; kind = receipt.get('unit_type', receipt.get('type'))
    if kind == 'source_advice':
        application = payload.get('application')
        if not isinstance(application, dict):
            return []
        span = application.get('input_span')
        source = application.get('input_text')
        if (not isinstance(span, dict) or not isinstance(source, str) or not source
                or span.get('scope') != target['scope']
                or type(span.get('start')) is not int or type(span.get('end')) is not int
                or not target['start'] <= span['start'] < span['end'] <= target['end']
                or span.get('text_sha256') != _sha(source)):
            return []
        left, right = span['start']-target['start'], span['end']-target['start']
        if text[left:right] != source:
            return []
        outputs = application.get('outputs')
        changes = application.get('edits')
        if (not isinstance(outputs, list) or not outputs
                or not isinstance(changes, list) or len(outputs) != len(changes)):
            return []
        for output, edit in zip(outputs, changes):
            if (not isinstance(edit, dict) or type(edit.get('start')) is not int
                    or type(edit.get('end')) is not int
                    or not 0 <= edit['start'] <= edit['end'] <= len(source)
                    or not isinstance(edit.get('replacement'), str)
                    or output != source[:edit['start']]+edit['replacement']+source[edit['end']:]):
                return []
        # Existing source applications also include lexical rewrites. They
        # cannot explain punctuation merely because a comma occurs nearby.
        if any(not isinstance(value, str) or _punctuation(value)[0] != _punctuation(source)[0]
               for value in outputs):
            return []
        return [{'start': left, 'end': right, 'basis': 'owned_source_application',
                 'construction': application.get('kind'), 'references': list(dict.fromkeys(outputs))}]
    if payload.get('construction') == 'finite_subordinate_clause_boundary':
        span=payload.get('input_span',{}); boundary=payload.get('boundary',{})
        if (payload.get('input_text')!=text or payload.get('input_sha256')!=_sha(text)
                or span.get('scope')!=target['scope'] or span.get('start')!=target['start']
                or span.get('end')!=target['end'] or span.get('text_sha256')!=_sha(text)
                or type(boundary.get('insert_at')) is not int
                or not 0<=boundary['insert_at']<=len(text)
                or not isinstance(payload.get('output_text'),str)):
            return []
        parts=payload.get('source_record',{}).get('parts',[])
        quotes=[(index,identity) for index,identity,quote in source_quotes
                if any(part.get('binding',{}).get('text_sha256')==_sha(quote)
                       for part in parts)]
        if not quotes:return []
        key=len(_punctuation(text[:boundary['insert_at']])[0])
        return [{'start':0,'end':len(text),'basis':'syntax_bound_source_application',
            'construction':payload['construction'],'references':[payload['output_text']],
            'boundary_keys':{key},'citation_indexes':[i for i,_ in quotes],
            'evidence_ids':list(dict.fromkeys(identity for _,identity in quotes))}]
    if payload.get('construction') != 'coordination_punctuation':
        return []
    examples = payload.get('examples')
    if not isinstance(examples, dict):
        return []
    candidates, cited = [], []
    for value in examples.values():
        values = value if isinstance(value, list) else [value]
        for example in values:
            matches = ([(index, identity) for index, identity, quote in source_quotes
                        if ' '.join(example.split()) in ' '.join(quote.split())]
                       if isinstance(example, str) else [])
            if (matches and _punctuation(example)[0] == _punctuation(text)[0]):
                candidates.append(example)
                cited.extend(pair for pair in matches if pair not in cited)
    if not candidates:
        return []
    return [{'start': 0, 'end': len(text), 'basis': 'source_attested_example',
             'construction': payload['construction'], 'references': list(dict.fromkeys(candidates)),
             'citation_indexes': [index for index, _ in cited],
             'evidence_ids': list(dict.fromkeys(identity for _, identity in cited))}]


def _relation(counts, references, boundaries=None):
    expected = [_punctuation(value)[2] for value in references]
    keys = set(counts) | set().union(*(set(row) for row in expected)) if boundaries is None else boundaries
    decisions = [all(counts[key] == row[key] for key in keys) for row in expected]
    return ('supports_proposal' if all(decisions) else
            'conditional_support' if any(decisions) else 'disagrees_with_proposal')


def _application(candidate, text, edits, graph):
    graph = dict(graph)
    for key in ('citation_indexes', 'evidence_ids'):
        if key in candidate:
            graph[key] = candidate[key]
    left, right = candidate['start'], candidate['end']; local = []
    for index, edit in enumerate(edits):
        if left <= edit['start'] <= edit['end'] <= right:
            local.append((index, dict(edit, start=edit['start']-left, end=edit['end']-left)))
        elif edit['start'] < right and left < edit['end']:
            return None, []
    original = text[left:right]
    proposed = _apply(original, [edit for _, edit in local])
    basis, old_positions, old_counts = _punctuation(original)
    new_basis, new_positions, new_counts = _punctuation(proposed)
    if basis != new_basis:
        return None, []
    application = dict(graph, target_start=left, target_end=right, target_sha256=_sha(original),
        basis=candidate['basis'], construction=candidate['construction'],
        reference_texts=deepcopy(candidate['references']),
        relation=_relation(new_counts, candidate['references'],candidate.get('boundary_keys')))
    links, shift = [], 0
    changed = {key for key in set(old_counts) | set(new_counts) if old_counts[key] != new_counts[key]}
    for index, edit in local:
        start, end = edit['start'], edit['end']
        proposed_start = start + shift
        keys = {key for pos, key in old_positions.items() if start <= pos < end}
        keys |= {key for pos, key in new_positions.items()
                 if proposed_start <= pos < proposed_start+len(edit['replacement'])}
        keys &= changed
        if candidate.get('boundary_keys') is not None:keys &= candidate['boundary_keys']
        if keys:
            links.append(dict(graph, edit_index=index, edit_start=left+start, edit_end=left+end,
                relation=_relation(new_counts, candidate['references'], keys)))
        shift += len(edit['replacement'])-(end-start)
    return application, links


def _related(explained):
    result = []
    for block in explained.get('display_blocks', []):
        identity, claim = block.get('unit_id'), block.get('claim_id')
        rows = [(index, row) for index, row in enumerate(explained.get('citations', []))
                if row.get('unit_id') == identity and row.get('claim_id') == claim
                and row.get('block_id') == block.get('block_id')]
        if identity and claim and rows:
            result.append({'unit_id': identity, 'unit_type': block.get('unit_type'),
                'claim_id': claim, 'block_id': block.get('block_id'),
                'citation_indexes': [index for index, _ in rows],
                'evidence_ids': list(dict.fromkeys(row['evidence_id'] for _, row in rows))})
    return result


def connect(proposal, task, explained, application_units):
    result = {'schema': SCHEMA, 'status': 'unsupported', 'target_sha256': None,
        'offset_convention': OFFSET, 'coverage': 'matched_applications_only',
        'links': [], 'applications': [], 'related_guidance': [], 'unexplained_edit_indexes': []}
    try:
        target, text, edits = _proposal(proposal, task)
    except (ValueError, TypeError, KeyError, AttributeError):
        return result
    result['target_sha256'] = _sha(text)
    result['unexplained_edit_indexes'] = list(range(len(edits)))
    if not isinstance(explained, dict):
        return result
    related = _related(explained)
    result['related_guidance'] = related
    receipts = application_units if isinstance(application_units, list) else []
    seen = set()
    for receipt in receipts[:128]:
        if not isinstance(receipt, dict) or receipt.get('unit_id') in seen:
            continue
        seen.add(receipt.get('unit_id'))
        try:
            proof = _graph(explained, receipt)
            if proof is None:
                continue
            graph, source_quotes = proof
            for candidate in _candidates(receipt, target, text, source_quotes):
                application, links = _application(candidate, text, edits, graph)
                if application is not None:
                    result['applications'].append(application); result['links'].extend(links)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    applied_ids = {row['unit_id'] for row in result['applications']}
    result['related_guidance'] = [row for row in related if row['unit_id'] not in applied_ids]
    linked = {row['edit_index'] for row in result['links']}
    result['unexplained_edit_indexes'] = [index for index in range(len(edits)) if index not in linked]
    result['status'] = ('applied' if result['applications'] else 'related_guidance' if related else 'unsupported')
    return result


def reader(value):
    """Project saved links without returning private selected-unit receipts."""
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or value.get('status') not in {'applied', 'related_guidance', 'unsupported'}):
        return None
    fields = {'unit_id', 'unit_type', 'claim_id', 'block_id', 'citation_indexes', 'evidence_ids'}
    result = {key: deepcopy(value.get(key)) for key in (
        'schema', 'status', 'target_sha256', 'offset_convention', 'coverage', 'unexplained_edit_indexes')}
    for key, allowed in [
        ('links', fields | {'edit_index', 'edit_start', 'edit_end', 'relation'}),
        ('applications', fields | {'target_start', 'target_end', 'target_sha256', 'basis',
                                  'construction', 'reference_texts', 'relation'}),
        ('related_guidance', fields),
    ]:
        rows = value.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return None
        result[key] = [{k: deepcopy(v) for k, v in row.items() if k in allowed} for row in rows]
    return result
