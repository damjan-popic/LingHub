"""Request-local syntax observation; no service, source authority or implicit call.

The caller chooses an explanation request and its exact target. Original
question/context scope stays with that caller; offsets here address only text.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from .syntax_worker import MODEL_PACKAGE, MODEL_VERSION, MODEL_HASHES, RESOURCES_SHA256

MAX_CHARACTERS = 3000
TIMEOUT_SECONDS = 45
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


def collect(text, config):
    """Load the fixed local parser once, only when explicitly called by a task."""
    started = time.monotonic()
    observation = {'schema': 'linghub.syntax-observation.v1', 'status': 'invalid_input',
                   'input_text': text if isinstance(text, str) else None,
                   'input_sha256': None, 'analysis': None,
                   'observed_at': datetime.now(timezone.utc).isoformat(),
                   'execution': {'attempted': False, 'process_started': False,
                                 'response_received': False, 'inference_performed': None}}

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
    python = config.get('syntax_python') if isinstance(config, dict) else None
    if not isinstance(python, str) or not Path(python).is_absolute() or not os.access(python, os.X_OK):
        return finish('unavailable', 'syntax_python_unavailable')
    resources = config.get('syntax_resources')
    if not isinstance(resources, str) or not Path(resources).is_absolute() or not Path(resources).is_dir():
        return finish('unavailable', 'syntax_resources_unavailable')
    worker = Path(__file__).with_name('syntax_worker.py')
    # Isolated Python ignores inherited PYTHONPATH and disables bytecode writes.
    argv = [python, '-I', '-B', str(worker), '--resources', resources]
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR')
           if key in os.environ}
    env.update(CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS='2', BLIS_NUM_THREADS='2')
    observation['execution']['attempted'] = True
    try:
        result = subprocess.run(argv, input=json.dumps({'text': text}, ensure_ascii=False).encode('utf-8'),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT_SECONDS,
                                env=env, check=False)
        observation['execution']['process_started'] = True
        observation['execution']['returncode'] = result.returncode
        if result.returncode:
            return finish('unavailable', 'syntax_worker_failed')
        observation['execution']['response_received'] = True
        if len(result.stdout) > 4 * 1024 * 1024:
            return finish('malformed_response', 'syntax_response_bound')
        observation['analysis'] = _analysis(json.loads(result.stdout), text)
        observation['execution']['inference_performed'] = True
        return finish('ok')
    except subprocess.TimeoutExpired:
        observation['execution']['process_started'] = True
        return finish('timeout', 'syntax_timeout')
    except OSError:
        return finish('unavailable', 'syntax_process_unavailable')
    except (ValueError, TypeError, KeyError):
        return finish('malformed_response', 'syntax_response_invalid')
