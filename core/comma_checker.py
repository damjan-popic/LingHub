"""Optional CJVT comma predictions using the observed LORIS token contract.

Only ``check`` dispatches text. The caller owns selected-target extraction,
external-processing consent and access-controlled storage of this observation.
No source assertion, writer invocation or new grammatical verdict is produced.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import stat
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


SCHEMA = 'linghub.comma-prediction.v1'
ENDPOINT = 'https://gpu-proc1.cjvt.si/popravljalnik-api/postavi_vejice'
OFFSET_CONVENTION = 'unicode_codepoint_half_open'
_SPACING = ' \t\u00a0'


@dataclass(frozen=True)
class CommaCheckerConfig:
    endpoint: str = ENDPOINT
    key_file: str | Path | None = None
    timeout_seconds: float = 15.0
    max_chars: int = 3000  # KLIKai application bound; not a claim about the API.
    response_max_bytes: int = 1024 * 1024
    protected_spans: tuple[tuple[int, int], ...] = ()
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False, compare=False)


class AlignmentFailure(ValueError):
    pass


class MalformedResponse(ValueError):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _observation(text: str, config: CommaCheckerConfig) -> dict:
    return {
        'schema': SCHEMA, 'status': 'unavailable', 'reason': None,
        'provider': 'cjvt_vejice', 'provider_label': 'CJVT Vejice',
        'endpoint': config.endpoint,
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'reported_model_version': None, 'reported_api_version': None,
        'reported_confidence': None,
        'input_text': text, 'input_sha256': _sha(text),
        'proposed_text': None, 'edits': [],
        'checked_extent': {'start': 0, 'end': len(text),
            'offset_convention': OFFSET_CONVENTION, 'complete': False},
        'truncated': False, 'application_limit_characters': config.max_chars,
        'execution': {'invocation_attempted': False, 'invocation_acknowledged': False,
            'response_received': False, 'http_status': None,
            'neural_prediction_received': False, 'inference_performed': None},
        'response_sha256': None, 'raw_result': None,
    }


def _config(value: CommaCheckerConfig | Mapping[str, Any]) -> CommaCheckerConfig:
    result = value if isinstance(value, CommaCheckerConfig) else CommaCheckerConfig(**dict(value))
    parsed = urlsplit(result.endpoint)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or not isinstance(result.max_chars, int) or isinstance(result.max_chars, bool)
            or not 1 <= result.max_chars <= 100000
            or isinstance(result.timeout_seconds, bool) or not isinstance(result.timeout_seconds, (int, float))
            or not math.isfinite(result.timeout_seconds) or not 0 < result.timeout_seconds <= 60
            or type(result.response_max_bytes) is not int
            or not 1024 <= result.response_max_bytes <= 8 * 1024 * 1024
            or not isinstance(result.protected_spans, (tuple, list))):
        raise ValueError('Invalid checker configuration')
    return result


def _key(config: CommaCheckerConfig) -> str:
    if config.key_file is None:
        raise ValueError('Checker credentials are not configured')
    path = Path(config.key_file)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
        raise ValueError('Checker credential file is not private')
    key = path.read_text(encoding='utf-8').strip()
    if not key or '\n' in key or '\r' in key or '\x00' in key:
        raise ValueError('Invalid checker credentials')
    return key


def _tokens(raw: dict, name: str, *, identifiers: bool) -> list[dict]:
    values = raw.get(name)
    if not isinstance(values, list) or not values or len(values) > 20000:
        raise MalformedResponse('Missing token array')
    seen = set()
    for token in values:
        if not isinstance(token, dict) or not isinstance(token.get('text'), str):
            raise MalformedResponse('Invalid token')
        if identifiers or 'id' in token:
            identity = token.get('id')
            if (not isinstance(identity, str) or not identity or len(identity) > 128
                    or identity in seen):
                raise MalformedResponse('Invalid or repeated token identity')
            seen.add(identity)
    return values


def _source_positions(text: str, tokens: list[dict]) -> dict[str, dict]:
    """Align in order, crossing only whitespace, never searching past a word.

    Token-boundary whitespace can be normalized by this API. The exact input
    whitespace, including CRLF, stays authoritative in the derived proposal.
    """
    cursor = 0
    result = {}
    for token in tokens:
        core = token['text'].strip()
        if not core:
            if token['text'] and not token['text'].isspace():
                raise AlignmentFailure('Invalid empty token')
            result[token['id']] = {'start': cursor, 'end': cursor, 'core': '', 'raw': token['text']}
            continue
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if not text.startswith(core, cursor):
            raise AlignmentFailure('Source tokens differ from the exact input')
        result[token['id']] = {'start': cursor, 'end': cursor+len(core),
                              'core': core, 'raw': token['text']}
        cursor += len(core)
    if text[cursor:].strip():
        raise AlignmentFailure('Source tokens do not cover the exact input')
    return result


def _signature(text: str) -> list[str]:
    return re.findall(r'\w+|[^\w\s]', text, re.UNICODE)


def _protected(text: str, explicit) -> list[tuple[int, int]]:
    ranges = []
    # A selected quotation is input, not automatically protected material.
    # The caller may explicitly protect unselected/source quotation ranges.
    patterns = (
        r'```[\s\S]*?```', r'`[^`\r\n]*`',
        r'https?://[^\s<>"«»]+', r'www\.[^\s<>"«»]+',
        r'<(?:code|pre)\b[^>]*>[\s\S]*?</(?:code|pre)\s*>', r'<[^>\r\n]*>',
    )
    for pattern in patterns:
        ranges.extend(match.span() for match in re.finditer(pattern, text, re.I))
    for pair in explicit:
        if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                or any(type(value) is not int for value in pair)
                or not 0 <= pair[0] < pair[1] <= len(text)):
            raise ValueError('Invalid protected input range')
        ranges.append((pair[0], pair[1]))
    return ranges


def _edits(start: int, original: str, proposed: str) -> list[dict]:
    values = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, original, proposed, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        old, new = original[a:b], proposed[c:d]
        if (any(char not in ','+_SPACING for char in old+new)
                or any(char in '\r\n' for char in old+new)
                or sum(char in _SPACING for char in old) > 2
                or sum(char in _SPACING for char in new) > 2):
            raise AlignmentFailure('The proposal changes material other than commas or adjacent spacing')
        if ',' not in old+new:
            old_near = original[max(0, a-1):min(len(original), b+1)]
            new_near = proposed[max(0, c-1):min(len(proposed), d+1)]
            if ',' not in old_near+new_near:
                raise AlignmentFailure('The proposal changes unrelated spacing')
        values.append({'start': start+a, 'end': start+b,
            'original': old, 'replacement': new,
            'kind': ('comma_addition' if old == '' and ',' in new else
                     'comma_removal' if new == '' and ',' in old else 'comma_and_spacing')})
    return values


def _apply(text: str, edits: list[dict]) -> str:
    cursor = 0
    pieces = []
    for edit in edits:
        if edit['start'] < cursor or text[edit['start']:edit['end']] != edit['original']:
            raise AlignmentFailure('Overlapping or inconsistent source edits')
        pieces.extend((text[cursor:edit['start']], edit['replacement']))
        cursor = edit['end']
    return ''.join(pieces) + text[cursor:]


def align(text: str, raw: dict, *, protected_spans=()) -> tuple[str, list[dict]]:
    """Use source token identities and verify the complete target token stream."""
    source = _tokens(raw, 'source_tokens', identifiers=True)
    target = _tokens(raw, 'target_tokens', identifiers=False)
    positions = _source_positions(text, source)
    corrections = raw.get('corrections')
    if not isinstance(corrections, (dict, list)):
        raise MalformedResponse('Missing correction groups')
    groups = corrections.values() if isinstance(corrections, dict) else corrections
    changes = {}
    for group in groups:
        if (not isinstance(group, dict) or not isinstance(group.get('source_start'), str)
                or group['source_start'] not in positions
                or not isinstance(group.get('corrections'), list)):
            raise MalformedResponse('Invalid correction group')
        for correction in group['corrections']:
            if (not isinstance(correction, dict) or not isinstance(correction.get('source_id'), str)
                    or correction['source_id'] not in positions
                    or not isinstance(correction.get('source_text'), str)
                    or not isinstance(correction.get('text'), str)):
                raise MalformedResponse('Invalid token correction')
            identity = correction['source_id']
            original = positions[identity]
            if correction['source_text'] != original['raw']:
                raise AlignmentFailure('Correction source differs from the observed source token')
            proposed = correction['text'].strip()
            if identity in changes and changes[identity] != proposed:
                raise AlignmentFailure('Competing corrections for one source token')
            changes[identity] = proposed
    edits = []
    for identity, proposed in changes.items():
        original = positions[identity]
        if not original['core']:
            if proposed:
                raise AlignmentFailure('A whitespace token cannot anchor a comma proposal')
            continue
        edits.extend(_edits(original['start'], original['core'], proposed))
    edits.sort(key=lambda edit: (edit['start'], edit['end']))
    protected = _protected(text, protected_spans)
    for edit in edits:
        left, right = edit['start'], edit['end']
        if any((a < left < b if left == right else a < right and left < b) for a, b in protected):
            raise AlignmentFailure('A proposed edit touches protected input')
        # Both deletion and insertion between digits can change a decimal or
        # numeric grouping; neither is accepted as a sentence-comma edit.
        for offset, char in enumerate(edit['original']):
            pos = left+offset
            if char == ',' and 0 < pos < len(text)-1 and text[pos-1].isdigit() and text[pos+1].isdigit():
                raise AlignmentFailure('A proposed edit changes a numeric comma')
        if (',' in edit['replacement'] and left > 0 and right < len(text)
                and text[left-1].isdigit() and text[right].isdigit()):
            raise AlignmentFailure('A proposed edit inserts a numeric comma')
    proposal = _apply(text, edits)
    if ([piece for piece in _signature(text) if piece != ',']
            != [piece for piece in _signature(proposal) if piece != ',']):
        raise AlignmentFailure('The proposal splits or joins words or numbers')
    # Comparing token streams, rather than concatenating unspaced API words,
    # preserves word boundaries and detects changed words, case and punctuation.
    expected = [piece for token in target for piece in _signature(token['text'])]
    if _signature(proposal) != expected:
        raise AlignmentFailure('Comma corrections do not reproduce the target token stream')
    return proposal, edits


def _metadata(raw: dict) -> dict:
    def version(value):
        return value if isinstance(value, str) and 0 < len(value) <= 200 else None
    model = raw.get('model') if isinstance(raw.get('model'), dict) else {}
    confidence = raw.get('confidence')
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)):
        confidence = None
    return {'reported_model_version': version(raw.get('model_version', model.get('version'))),
            'reported_api_version': version(raw.get('api_version')),
            'reported_confidence': confidence}


def _json(raw_bytes: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MalformedResponse('Duplicate response key')
            result[key] = value
        return result
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise MalformedResponse('Non-finite response number')
        return number
    try:
        value = json.loads(raw_bytes, object_pairs_hook=unique,
                           parse_float=finite_float,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError) as error:
        raise MalformedResponse('Invalid JSON response') from error
    if not isinstance(value, dict):
        raise MalformedResponse('Invalid response root')
    return value


async def check(text: str, config: CommaCheckerConfig | Mapping[str, Any]) -> dict:
    """Return one private saved prediction observation; no retry or fallback."""
    if not isinstance(text, str):
        raise TypeError('Comma checker input must be a string')
    try:
        config = _config(config)
    except (ValueError, TypeError, AttributeError):
        result = _observation(text, CommaCheckerConfig())
        result['reason'] = 'invalid_configuration'
        return result
    result = _observation(text, config)
    if len(text) > config.max_chars:
        result.update(status='too_long', reason='application_character_limit')
        return result
    if not text.strip():
        result.update(status='alignment_failed', reason='empty_target')
        return result
    try:
        _protected(text, config.protected_spans)
        key = _key(config)
    except (ValueError, OSError, UnicodeError):
        result['reason'] = 'not_configured_or_invalid_target_scope'
        return result
    try:
        async with asyncio.timeout(config.timeout_seconds):
            async with httpx.AsyncClient(timeout=config.timeout_seconds, trust_env=False,
                    follow_redirects=False, transport=config.transport) as client:
                result['execution']['invocation_attempted'] = True
                async with client.stream('POST', config.endpoint,
                        headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                 'X-API-KEY': key}, json={'vhodna_poved': text}) as response:
                    result['execution'].update(response_received=True, http_status=response.status_code,
                        invocation_acknowledged=200 <= response.status_code < 300)
                    if not 200 <= response.status_code < 300:
                        result['reason'] = 'provider_unavailable'
                        return result
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > config.response_max_bytes:
                            result.update(status='malformed_response', reason='application_response_limit')
                            return result
                    result['response_sha256'] = hashlib.sha256(data).hexdigest()
        raw = _json(bytes(data))
        result['raw_result'] = raw  # Private owner-controlled result only.
        result.update(_metadata(raw))
        proposal, edits = align(text, raw, protected_spans=config.protected_spans)
        result.update(status='ok', proposed_text=proposal, edits=edits, reason=None)
        result['checked_extent']['complete'] = True
        result['execution']['neural_prediction_received'] = True
        if type(raw.get('inference_performed')) is bool:
            result['execution']['inference_performed'] = raw['inference_performed']
        return result
    except (TimeoutError, httpx.TimeoutException):
        result.update(status='timeout', reason='provider_timeout')
    except AlignmentFailure:
        result['execution']['neural_prediction_received'] = True
        result.update(status='alignment_failed', reason='unalignable_or_protected_prediction')
    except MalformedResponse:
        result.update(status='malformed_response', reason='invalid_provider_contract')
    except httpx.HTTPError:
        result.update(status='unavailable', reason='provider_unavailable')
    return result


def public_proposal(observation: dict) -> dict:
    """Closed reader projection; raw provider material and endpoint stay private."""
    statuses = {'ok', 'too_long', 'unavailable', 'timeout', 'alignment_failed', 'malformed_response'}
    if (not isinstance(observation, dict) or observation.get('schema') != SCHEMA
            or observation.get('status') not in statuses
            or observation.get('provider') != 'cjvt_vejice'
            or not isinstance(observation.get('input_text'), str)):
        raise ValueError('Invalid comma prediction observation')
    text = observation['input_text']
    if observation.get('input_sha256') != _sha(text):
        raise ValueError('Comma prediction input identity differs')
    extent = observation.get('checked_extent')
    if (not isinstance(extent, dict) or set(extent) != {'start', 'end', 'offset_convention', 'complete'}
            or extent['start'] != 0 or extent['end'] != len(text)
            or extent['offset_convention'] != OFFSET_CONVENTION
            or type(extent['complete']) is not bool
            or extent['complete'] != (observation['status'] == 'ok')):
        raise ValueError('Comma prediction checked extent differs')
    edits = observation.get('edits')
    if not isinstance(edits, list):
        raise ValueError('Invalid comma prediction edits')
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {'start', 'end', 'original', 'replacement', 'kind'}
                or type(edit['start']) is not int or type(edit['end']) is not int
                or not 0 <= edit['start'] <= edit['end'] <= len(text)
                or not isinstance(edit['original'], str) or not isinstance(edit['replacement'], str)
                or edit['kind'] not in {'comma_addition', 'comma_removal', 'comma_and_spacing'}):
            raise ValueError('Invalid comma prediction edit')
    if observation['status'] == 'ok':
        if _apply(text, edits) != observation.get('proposed_text'):
            raise ValueError('Comma prediction edits differ from the saved proposal')
    elif observation.get('proposed_text') is not None or edits:
        raise ValueError('Failed comma prediction contains an applicable proposal')
    execution = observation.get('execution')
    if (not isinstance(execution, dict) or set(execution) != {
            'invocation_attempted', 'invocation_acknowledged', 'response_received', 'http_status',
            'neural_prediction_received', 'inference_performed'}
            or any(type(execution[key]) is not bool for key in (
                'invocation_attempted', 'invocation_acknowledged', 'response_received', 'neural_prediction_received'))
            or execution['inference_performed'] is not None and type(execution['inference_performed']) is not bool):
        raise ValueError('Invalid comma prediction execution observation')
    result = {key: copy.deepcopy(observation[key]) for key in (
        'status', 'reason', 'provider', 'provider_label', 'observed_at',
        'reported_model_version', 'reported_api_version', 'reported_confidence',
        'proposed_text', 'edits', 'checked_extent', 'execution',
        'truncated', 'application_limit_characters')}
    result.update(schema='linghub.comma-proposal.v1', label='Predlog postavitve vejic · CJVT Vejice',
                  original_text=text, original_sha256=observation['input_sha256'])
    return result
