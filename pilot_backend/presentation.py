"""Reader-facing beta projection; stored provenance and operator views stay intact."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re


def documentary_sources(value):
    """Validate release-pinned public collection identifiers, without a lookup.

    The deployment derives this list from enabled documentary collections.
    Keeping it in release configuration makes the legend independent of the
    latest query, stored matches and temporary source availability.
    """
    if (not isinstance(value,list)or len(value)>256
            or any(not isinstance(slug,str)or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}',slug)for slug in value)
            or len(set(value))!=len(value)):
        raise ValueError('Invalid documentary source presentation list')
    return list(value)


def packaged_documentary_sources():
    """Read the small enabled-collection snapshot shipped with this release."""
    path=Path(__file__).with_name('enabled-documentary-sources.json')
    return documentary_sources(json.loads(path.read_text(encoding='utf-8')))

SAFE = frozenset({
    'family', 'families', 'job', 'answer_family_id', 'family_id', 'run_id', 'job_id',
    'question', 'context', 'locale', 'input_language', 'task', 'created', 'short', 'long',
    'answer', 'answer_format', 'source_tier', 'single_answers',
    'state', 'status', 'parent_run_id', 'parent_relation', 'matches_available', 'unified_answers', 'focus',
    'short_answer', 'answer_md', 'blocks', 'block_id', 'text', 'paragraph_id', 'evidence_ids',
    'decision_sources', 'evidence', 'evidence_id', 'citation_id', 'citations', 'claim_id', 'local_number',
    'source_badges', 'source_slug', 'source_label', 'source_key', 'label', 'display_name',
    'title', 'snippet', 'excerpt', 'url', 'edition', 'source_status', 'bibliography',
    'current_original_lookup', 'original_view',
    'bibliographic', 'authors', 'date', 'source_date', 'coverage_note', 'normative_status', 'signed_date_text', 'publisher', 'publication_date',
    'publication_scope', 'annual_scope', 'answer_kind', 'generation_status', 'answer_support',
    'correction', 'clarification_code', 'progress', 'stage', 'cancel_requested', 'position',
    'acknowledged', 'feedback_id', 'feedback', 'rating', 'quality_score', 'tags', 'comment',
    'anchor', 'target', 'kind', 'quote', 'start', 'end', 'prefix', 'suffix',
    'sources', 'items', 'record_id', 'preview', 'offset', 'limit', 'returned_count',
    'next_offset', 'corpus_total', 'count_scope', 'usage_final', 'used_in_answer', 'answer_status',
    'reader_blocks', 'heading', 'rows', 'rowspan', 'colspan', 'scope', 'revision', 'correction', 'group', 'events',
    'started', 'finished', 'elapsed_seconds', 'followup',
})

LIMITATION_CODES = frozenset({'partial_evidence', 'no_answer_evidence', 'conflicting_evidence',
    'source_scope', 'proposal_status', 'uncertain_entry_boundary', 'unsupported_inference',
    'comparison_coverage_incomplete', 'comparison_explanation_incomplete'})


def reader_limitations(value):
    """Retain scoped reader qualifications, without private validation details.

    The interface localizes this closed set of meanings. Source edition, draft
    status and signed dates remain alongside evidence for more specific notes.
    Fallback string limitations repeat their already displayed status block.
    """
    rows=[]
    for row in value if isinstance(value,list)else []:
        if not isinstance(row,dict)or not isinstance(row.get('code'),str)or row['code']not in LIMITATION_CODES:continue
        scope=row.get('scope');ids=row.get('claim_ids')
        if not isinstance(scope,str)or scope not in {'answer','claim'}or not isinstance(ids,list)or any(not isinstance(cid,str)for cid in ids):continue
        if (scope=='answer'and ids)or(scope=='claim'and not ids):continue
        item={'code':row['code'],'scope':scope,'claim_ids':list(ids)}
        if item not in rows:rows.append(item)
    return rows


def delivery_token(secret, owner, run_id, display_hash):
    value='\0'.join((owner,run_id,display_hash)).encode()
    return base64.urlsafe_b64encode(hmac.new(secret.encode(),value,hashlib.sha256).digest()).decode().rstrip('=')


def tester_view(value, owner, secret):
    if isinstance(value,list):return [tester_view(v,owner,secret)for v in value]
    if not isinstance(value,dict):return value
    out={k:tester_view(v,owner,secret)for k,v in value.items()if k in SAFE}
    if isinstance(value.get('lexical_profile'),dict):
        # Produced by the closed reader projection, never raw analyzer diagnostics.
        out['lexical_profile']=value['lexical_profile']
    if 'limitations'in value:out['limitations']=reader_limitations(value['limitations'])
    if value.get('run_id')and value.get('display_sha256'):
        out['delivery_token']=delivery_token(secret,owner,value['run_id'],value['display_sha256'])
    if value.get('error_code'):out['error_code']='answer_unavailable'
    if isinstance(out.get('progress'),dict):
        stage=out['progress'].get('stage')
        out['progress']={'stage':stage if stage in {'retrieval','generation','validation','complete'}else'generation'}
    return out
