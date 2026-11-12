"""Server-owned identity for the optional private document translation lane."""
from copy import deepcopy
from .security import Failure
from .store import digest

PROVIDER = 'translategemma_sl_it'
GUIDED_PROFILE = 'translategemma_sl_it_guided'
RESOURCE = 'document_translation_cpu'
DEFAULT_LIMITS = {'max_sections': 4, 'waiting_documents': 2,
                  'queue_seconds': 1800, 'document_seconds': 1500}


def profile_checked(value):
    # CORE owns the complete profile schema and rechecks its exact configured
    # identity. WEB records it, never permits browser-authored authority.
    if (not isinstance(value, dict) or value.get('profile_id') not in {PROVIDER,GUIDED_PROFILE}
            or len(str(value)) > 30000 or value.get('profile_sha256') !=
            digest({k:v for k,v in value.items() if k!='profile_sha256'})):
        raise Failure('document_translation_unavailable', 503)
    if guided(value):
        import re
        if (value.get('request_contract')!='klikai.qualified-guidance-translation.v1'
                or not re.fullmatch(r'[0-9a-f]{64}',str(value.get('base_profile_sha256','')))
                or not re.fullmatch(r'[0-9a-f]{64}',str(value.get('guidance_renderer_sha256','')))
                or value.get('custom_rendering')is not True
                or value.get('capabilities',{}).get('source_consultation')is not True
                or value.get('capabilities',{}).get('glossary_enforcement')is not False):
            raise Failure('document_translation_unavailable',503)
    return deepcopy(value)


def guided(profile):
    return (profile or{}).get('profile_id')==GUIDED_PROFILE


def native_references(packet):
    """Mirror the pinned custom renderer's lossless identity-wrapper reduction."""
    references=[]
    for index,item in enumerate(packet['sources'],1):
        row=deepcopy(item['model_source']);row.pop('passage_id')
        if row.get('source_label'):row.pop('source_slug',None)
        if row.get('title')and row.get('text','').startswith(row['title']):row.pop('title')
        if row.get('edition')is None:row.pop('edition',None)
        if row.get('source_status')=='captured_original_record':row.pop('source_status')
        if row.get('representation',{}).get('selection_status')=='no_inspected_plan':
            row.pop('representation')
        row['reference']='K'+str(index);references.append(row)
    return references


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
