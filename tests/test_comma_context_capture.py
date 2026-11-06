"""A separately frozen comma context on the existing syntax/source path."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core import comma_checker, syntax_analysis
from pilot_backend import unified
from test_comma_checker import response
from test_pilot_unified import core_test_root


def native_observations():
    root = core_test_root()
    data = json.loads((root / 'tests/zrc/fixtures/classla_clause_context_20261106.json').read_text())
    return {row['input_text']: row for row in data['observations']}


def checker_observation(tmp_path, original, proposed):
    key = tmp_path / 'synthetic-key'; key.write_text('synthetic-key'); key.chmod(0o600)
    config = comma_checker.CommaCheckerConfig(key_file=key,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response(original, proposed))))
    return asyncio.run(comma_checker.check(original, config))


def family(original):
    question = 'Preveri vejice: ' + original
    return {'question': question, 'request_plan': {'comma_check': {'status': 'ready', 'explain': True,
        'target': {'scope': 'question', 'start': len(question)-len(original),
                   'end': len(question), 'text': original}}}}


@pytest.mark.parametrize('with_candidate', [False, True])
def test_original_and_candidate_keep_separate_exact_inputs_and_native_annotations(tmp_path, monkeypatch, with_candidate):
    original = 'Ker je mraz zakurimo.'; proposed = 'Ker je mraz, zakurimo.'
    observed = native_observations(); calls = []
    def collect(text, config):
        calls.append(text); return deepcopy(observed[text])
    monkeypatch.setattr(syntax_analysis, 'collect', collect)
    candidate = checker_observation(tmp_path, original, proposed) if with_candidate else None
    gateway = SimpleNamespace(comma_checker={'enabled': True, 'syntax_url': 'http://127.0.0.1:18316'})
    captured, status = unified.collect_tools(gateway, family(original), comma_observation=candidate)
    assert status['tool_outcome'] == 'matched' and calls == ([original, proposed] if with_candidate else [original])
    frozen = captured['captures'][0]['response']
    assert frozen['input_text'] == original and frozen['analysis'] == observed[original]['analysis']
    if with_candidate:
        context = frozen['punctuation_context']
        assert context['original_input_sha256'] == hashlib.sha256(original.encode()).hexdigest()
        assert context['observation']['input_text'] == proposed
        assert context['observation']['analysis'] == observed[proposed]['analysis']
        assert frozen['analysis']['words'][1]['upos'] == 'PRON'
        assert context['observation']['analysis']['words'][1]['upos'] == 'AUX'
    else:
        assert 'punctuation_context' not in frozen


def test_unavailable_context_does_not_erase_successful_original_analysis(tmp_path, monkeypatch):
    original = 'Če ne pride ne grem.'; proposed = 'Če ne pride, ne grem.'
    observed = native_observations()
    monkeypatch.setattr(syntax_analysis, 'collect', lambda text, config:
        deepcopy(observed[text]) if text == original else {'status': 'unavailable', 'analysis': None,
            'input_text': text, 'execution': {'inference_performed': None}})
    candidate = checker_observation(tmp_path, original, proposed)
    captured, status = unified.collect_tools(SimpleNamespace(comma_checker={'enabled': True}),
        family(original), comma_observation=candidate)
    assert captured['captures'][0]['response']['analysis'] == observed[original]['analysis']
    assert status['tool_outcome'] == 'matched'


def test_different_original_cannot_supply_a_context_to_this_request(tmp_path, monkeypatch):
    original = 'Ker je mraz zakurimo.'; observed = native_observations(); calls = []
    monkeypatch.setattr(syntax_analysis, 'collect', lambda text, config:
        (calls.append(text) or deepcopy(observed[text])))
    other = checker_observation(tmp_path, 'Če ne pride ne grem.', 'Če ne pride, ne grem.')
    captured, _ = unified.collect_tools(SimpleNamespace(comma_checker={'enabled': True}),
        family(original), comma_observation=other)
    assert calls == [original] and 'punctuation_context' not in captured['captures'][0]['response']
