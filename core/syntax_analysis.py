"""Request-local observation from the existing bounded CLASSLA language worker.

The caller chooses an explanation request and its exact target. Original
question/context scope stays with that caller; offsets here address only text.
"""
from datetime import datetime, timezone
import hashlib
import json
import time
from urllib.parse import urlsplit

import httpx
from .syntax_worker import MODEL_PACKAGE, MODEL_VERSION, MODEL_HASHES, RESOURCES_SHA256

MAX_CHARACTERS = 3000
TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
OFFSET_CONVENTION = 'unicode_codepoint_half_open'


def _analysis(value, text):
    if (not isinstance(value, dict) or value.get('schema') != 'linghub.syntax-analysis.v1'
            or value.get('text') != text or value.get('offset_convention') != OFFSET_CONVENTION
            or value.get('head_convention') != 'one_based_0_root'):
        raise ValueError('analysis_identity')
    model = value.get('model')
    if (not isinstance(model, dict) or model.get('package') != MODEL_PACKAGE
            or model.get('package_version') != MODEL_VERSION
            or not isinstance(model.get('torch_version'), str)
            or model.get('resource_sha256') != MODEL_HASHES
            or model.get('resources_manifest_sha256') != RESOURCES_SHA256
            or not isinstance(model.get('pipeline'), list) or 'depparse' not in model['pipeline']):
        raise ValueError('model_identity')
    words = value.get('words')
    if not isinstance(words, list) or not words or value.get('tokens') != words:
        raise ValueError('word_records')
    previous = 0
    for index, word in enumerate(words, 1):
        if (not isinstance(word, dict) or type(word.get('id')) is not int or word['id'] != index
                or type(word.get('start')) is not int or type(word.get('end')) is not int
                or not previous <= word['start'] < word['end'] <= len(text)
                or word.get('text') != text[word['start']:word['end']]
                or not all(isinstance(word.get(key), str) for key in ('lemma', 'upos', 'deprel'))
                or not isinstance(word.get('feats'), dict)
                or any(not isinstance(key, str) or not isinstance(item, str)
                       for key, item in word['feats'].items())
                or type(word.get('head_id')) is not int or not 0 <= word['head_id'] <= len(words)
                or word['head_id'] == index
                or type(word.get('sentence_id')) is not int or word['sentence_id'] < 1):
            raise ValueError('word_binding')
        previous = word['end']
    return value


def collect(text, config, *, transport=None):
    """Observe one selected text; no retries, subprocess fallback or implicit call."""
    started = time.monotonic()
    observation = {'schema': 'linghub.syntax-observation.v1', 'status': 'invalid_input',
                   'input_text': text if isinstance(text, str) else None,
                   'input_sha256': None, 'analysis': None,
                   'observed_at': datetime.now(timezone.utc).isoformat(),
                   'execution': {'attempted': False, 'process_started': False,
                                 'response_received': False, 'inference_performed': None,
                                 'transport': 'existing_classla_worker'}}

    def finish(status, reason=None):
        observation.update(status=status, reason=reason,
                           elapsed_ms=round((time.monotonic() - started) * 1000, 3))
        return observation

    if not isinstance(text, str) or not text.strip() or '\0' in text:
        return finish('invalid_input', 'invalid_selected_text')
    try:
        encoded = text.encode('utf-8')
    except UnicodeError:
        return finish('invalid_input', 'invalid_selected_text')
    observation['input_sha256'] = hashlib.sha256(encoded).hexdigest()
    if len(text) > MAX_CHARACTERS:
        return finish('too_long', 'selected_text_limit')
    url = config.get('syntax_url') if isinstance(config, dict) else None
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (parsed is None or parsed.scheme != 'http'
                or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in {'', '/'}
                or not parsed.port):
            raise ValueError('syntax_url')
    except ValueError:
        return finish('unavailable', 'syntax_worker_unconfigured')
    endpoint = url.rstrip('/') + '/syntax'
    observation['execution']['attempted'] = True
    try:
        with httpx.Client(timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=2), trust_env=False,
                          follow_redirects=False, transport=transport) as client:
            with client.stream('POST', endpoint, json={'text': text}) as response:
                observation['execution'].update(response_received=True, http_status=response.status_code)
                if response.status_code == 504:
                    return finish('timeout', 'syntax_timeout')
                if response.status_code != 200:
                    return finish('unavailable', 'syntax_worker_unavailable')
                payload = bytearray()
                for chunk in response.iter_bytes():
                    if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                        return finish('malformed_response', 'syntax_response_bound')
                    payload.extend(chunk)
                    if time.monotonic() - started > TIMEOUT_SECONDS:
                        return finish('timeout', 'syntax_timeout')
        value = json.loads(payload)
        if not isinstance(value, dict) or value.get('status') != 'ok':
            raise ValueError('syntax_response_status')
        observation['analysis'] = _analysis(value.get('analysis'), text)
        observation['execution']['inference_performed'] = True
        return finish('ok')
    except httpx.TimeoutException:
        return finish('timeout', 'syntax_timeout')
    except httpx.HTTPError:
        return finish('unavailable', 'syntax_worker_unavailable')
    except (ValueError, TypeError, KeyError):
        return finish('malformed_response', 'syntax_response_invalid')
