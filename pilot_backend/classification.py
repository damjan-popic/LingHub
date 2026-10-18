"""Classify retained output methods without editing historical answer bodies."""
VERSION='pilot-output-method-v2'
MODES={'generated','mixed_generated_and_source_partial','source_supported_partial','deterministic_supported_application'}

def method(result):
    if not isinstance(result,dict):return 'unknown'
    if result.get('answer_kind')in {'capability_unavailable','unavailable'}:return 'capability_unavailable'
    if result.get('status')=='failed':return 'failed'
    mode=result.get('synthesis_mode')
    if mode in MODES:return mode
    kind=result.get('answer_kind')
    if kind=='mixed':return 'mixed_generated_and_source_partial'
    if kind in {'clarification','clarification_required'}:return 'clarification_required'
    if result.get('status')in {'no_hit','no_answer'}:return 'no_hit'
    if kind=='deterministic_guidance':return 'clarification_required' if result.get('answer_support')=='clarification' else 'deterministic'
    if kind in {'generated','source_only','deterministic'}:return kind
    return 'unknown'

def contains_generated_text(result):
    return method(result)in {'generated','mixed_generated_and_source_partial'}
