"""Server-owned identity for the optional private document translation lane."""
from copy import deepcopy
from .security import Failure
from .store import digest

PROVIDER = 'translategemma_sl_it'
RESOURCE = 'document_translation_cpu'
DEFAULT_LIMITS = {'max_sections': 4, 'waiting_documents': 2,
                  'queue_seconds': 1800, 'document_seconds': 1500}


def profile_checked(value):
    # CORE owns the complete profile schema and rechecks its exact configured
    # identity. WEB records it, never permits browser-authored authority.
    if (not isinstance(value, dict) or value.get('profile_id') != PROVIDER
            or len(str(value)) > 30000 or value.get('profile_sha256') !=
            digest({k:v for k,v in value.items() if k!='profile_sha256'})):
        raise Failure('document_translation_unavailable', 503)
    return deepcopy(value)


def selected(options):
    return options.get('translation_provider') == PROVIDER


def task_profile(task):
    value = task.get('translation_profile')
    if value is None:
        if 'translation_request_id'in task:raise Failure('invalid_request')
        return None
    profile = profile_checked(value)
    if (task.get('mode') != 'translate' or task.get('source_language') != 'sl'
            or task.get('target_language') != 'it' or task.get('inline_spans')
            or task.get('translation_context') or not isinstance(task.get('translation_request_id'),str)
            or not task['translation_request_id'].startswith('document-')
            or len(task['translation_request_id'])>160):
        raise Failure('invalid_request')
    return profile


def profile_hash(profile):
    return profile['profile_sha256']
