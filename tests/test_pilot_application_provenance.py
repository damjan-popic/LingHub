"""A source-derived repair must not be counted as unaltered model prose."""
from pilot_backend.classification import method,contains_generated_text
from pilot_backend.human_feedback import generation_validation_diagnostics


def test_application_method_and_safe_diagnostics_preserve_the_distinction():
    result={'status':'validated','answer_kind':'generated','generation_status':'generated',
            'synthesis_mode':'deterministic_supported_application',
            'quality_actions':[{'claim_id':'C1','action':'deterministic_supported_application',
                'application':{'private_source_text':'not for bounded diagnostics'},
                'issues':[{'code':'construction_application_rendered','action':'source_bound_derivation'}]}]}
    assert method(result)=='deterministic_supported_application'
    assert not contains_generated_text(result)
    diagnostic=generation_validation_diagnostics(result)
    assert diagnostic['synthesis_mode']=='deterministic_supported_application'
    assert diagnostic['quality_actions'][0]['action']=='deterministic_supported_application'
    assert 'private_source_text'not in str(diagnostic)
