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
    'unsupported_interpretation', 'comparison_coverage_incomplete',
    'comparison_explanation_incomplete'})

EVIDENCE_STATES = frozenset({'source_present_unresolved_interpretation',
    'source_present_not_admitted_for_answer', 'no_selected_evidence'})

ACCOUNTING_FIELDS = frozenset({'primary_provider_dispatches',
    'corrective_provider_dispatches', 'completed_primary_calls',
    'completed_corrective_calls', 'unknown_provider_outcomes'})


def reader_generation_accounting(value):
    """Expose only bounded dispatch counts, never provider attempt details."""
    if not isinstance(value,dict)or set(value)!=ACCOUNTING_FIELDS:return None
    if any(type(value[key])is not int or value[key]<0 for key in ACCOUNTING_FIELDS):return None
    primary=value['primary_provider_dispatches'];corrective=value['corrective_provider_dispatches']
    completed_primary=value['completed_primary_calls']
    completed_corrective=value['completed_corrective_calls'];unknown=value['unknown_provider_outcomes']
    total=primary+corrective
    if (primary>2 or corrective>1 or total>3
            or completed_primary>primary or completed_corrective>corrective
            or unknown>total or completed_primary+completed_corrective+unknown>total):return None
    return {key:value[key]for key in ACCOUNTING_FIELDS}|{'provider_dispatches_total':total}


def reader_route(value):
    """Expose fixed release-routing labels only when dispatch accounting is valid."""
    if not isinstance(value,dict):return None
    out={}
    if value.get('selection')in {'auto','qwen','gemma'}:out['selection']=value['selection']
    if value.get('provider_scope')in {'shared','own'}:out['provider_scope']=value['provider_scope']
    return out or None


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
        if row['code']=='unsupported_interpretation'and scope!='answer':continue
        item={'code':row['code'],'scope':scope,'claim_ids':list(ids)}
        if item not in rows:rows.append(item)
    return rows


def reader_evidence_state(value, visible_evidence):
    """Expose only a consistent closed distinction, never unit diagnostics."""
    raw=value.get('evidence_state')if isinstance(value,dict)else None
    state=raw.get('status')if isinstance(raw,dict)else None
    if state not in EVIDENCE_STATES:return None
    has_evidence=any(isinstance(row,dict)and isinstance(
        row.get('evidence_id')or row.get('citation_id'),str)and bool(
        row.get('evidence_id')or row.get('citation_id'))for row in visible_evidence)
    expected={
        'source_present_unresolved_interpretation':('validated','source_only','returned_processing_limitation',False,True),
        'source_present_not_admitted_for_answer':('validated','source_only','returned_abstention',False,True),
        'no_selected_evidence':('no_hit','source_only','returned_abstention',True,False),
    }[state]
    no_hit=raw.get('no_hit')if type(raw.get('no_hit'))is bool else None
    actual=(value.get('status'),value.get('answer_kind'),value.get('generation_status'),
        no_hit,has_evidence)
    return {'status':state}if actual==expected else None


def delivery_token(secret, owner, run_id, display_hash):
    value='\0'.join((owner,run_id,display_hash)).encode()
    return base64.urlsafe_b64encode(hmac.new(secret.encode(),value,hashlib.sha256).digest()).decode().rstrip('=')


class ServerSourceBinding(str):
    """Opaque provenance capability created only from authenticated server state."""


def exact_source_provenance(value):
    """Return the closed identity used for answer/source equality, never for display."""
    if not isinstance(value,dict):return None
    evidence_id=value.get('evidence_id',value.get('passage_id',value.get('citation_id')))
    source_id=value.get('source_id');revision=value.get('source_revision')
    locator=value.get('locator')
    if (not isinstance(evidence_id,str)or not evidence_id or len(evidence_id)>512
            or not isinstance(source_id,str)or not source_id or len(source_id)>512
            or not isinstance(revision,str)or not revision or len(revision)>512
            or not isinstance(locator,dict)or not locator):return None
    try:
        encoded=json.dumps(locator,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        normalized=json.loads(encoded)
    except (TypeError,ValueError,UnicodeError):return None
    if len(encoded)>8192:return None
    return {'evidence_id':evidence_id,'source_id':source_id,
        'source_revision':revision,'locator':normalized}


def source_record_sha256(value):
    """Hash the frozen reader record without exposing its private structure."""
    if not isinstance(value,dict):return None
    record={key:item for key,item in value.items()if key not in {
        'evidence_id','citation_id','passage_id','source_binding'}}
    try:encoded=json.dumps(record,ensure_ascii=False,sort_keys=True,
        separators=(',',':')).encode()
    except (TypeError,ValueError,UnicodeError):return None
    if not encoded or len(encoded)>4*1024*1024:return None
    return hashlib.sha256(encoded).hexdigest()


def source_citation(result,evidence_id):
    """Return the bounded answer locations that actually cite one evidence row."""
    if not isinstance(result,dict)or not isinstance(evidence_id,str):return None
    blocks=[]
    for index,row in enumerate(result.get('blocks',[])if isinstance(result.get('blocks'),list)else[]):
        if not isinstance(row,dict)or evidence_id not in row.get('evidence_ids',[]):continue
        item={'index':index}
        for key in ('block_id','paragraph_id','unit_id'):
            value=row.get(key)
            if isinstance(value,str)and 0<len(value)<=512:item[key]=value
        blocks.append(item)
    citations=[]
    for index,row in enumerate(result.get('citations',[])if isinstance(result.get('citations'),list)else[]):
        if not isinstance(row,dict):continue
        cited=row.get('evidence_id',row.get('passage_id',row.get('citation_id')))
        if cited!=evidence_id:continue
        item={'index':index}
        for key in ('claim_id','block_id','unit_id'):
            value=row.get(key)
            if isinstance(value,str)and 0<len(value)<=512:item[key]=value
        citations.append(item)
    return {'blocks':blocks,'citations':citations}if blocks or citations else None


def source_binding(secret,owner,family_id,run_id,job_id,question,tier,evidence,
                   citation,source_record):
    """Bind exact frozen provenance to its owned answer without exposing it."""
    provenance=exact_source_provenance(evidence)
    record_hash=source_record_sha256(source_record)
    values=(secret,owner,family_id,run_id,job_id,question,tier)
    if (provenance is None or record_hash is None
            or not isinstance(citation,dict)or not citation
            or any(not isinstance(value,str)or not value for value in values)):return None
    payload={'schema':'klikai.public-source-binding.v1','owner':owner,
        'family_id':family_id,'run_id':run_id,'job_id':job_id,
        'question_sha256':hashlib.sha256(question.encode()).hexdigest(),
        'tier':tier,'citation':citation,'provenance':provenance,
        'source_record_sha256':record_hash}
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    token=base64.urlsafe_b64encode(
        hmac.new(secret.encode(),encoded,hashlib.sha256).digest()).decode().rstrip('=')
    return ServerSourceBinding('psb1.'+token)


def bind_evidence(rows,secret,owner,family_id,run_id,job_id,question,tier,result,
                  source_records):
    """Replace any untrusted binding field with one derived from persisted state."""
    output=[]
    for row in rows if isinstance(rows,list)else []:
        if not isinstance(row,dict):continue
        item=dict(row);item.pop('source_binding',None)
        provenance=exact_source_provenance(item)
        citation=source_citation(result,provenance['evidence_id'])if provenance else None
        source_record=(source_records.get(provenance['evidence_id'])
            if provenance and isinstance(source_records,dict)else None)
        binding=source_binding(secret,owner,family_id,run_id,job_id,question,tier,
            item,citation,source_record)
        if binding is not None:item['source_binding']=binding
        output.append(item)
    return output


def tester_view(value, owner, secret):
    if isinstance(value,list):return [tester_view(v,owner,secret)for v in value]
    if not isinstance(value,dict):return value
    out={k:tester_view(v,owner,secret)for k,v in value.items()if k in SAFE}
    if isinstance(value.get('source_binding'),ServerSourceBinding):
        out['source_binding']=str(value['source_binding'])
    if isinstance(value.get('lexical_profile'),dict):
        # Produced by the closed reader projection, never raw analyzer diagnostics.
        out['lexical_profile']=value['lexical_profile']
    accounting=reader_generation_accounting(value.get('generation_accounting'))
    if accounting:
        out['generation_accounting']=accounting
        route=reader_route(value.get('execution'))
        if route:out['execution']=route
    visible=out.get('evidence')if isinstance(out.get('evidence'),list)else []
    evidence_state=reader_evidence_state(value,visible)
    if evidence_state:out['evidence_state']=evidence_state
    if 'limitations'in value:
        limitations=reader_limitations(value['limitations'])
        if evidence_state!={'status':'source_present_unresolved_interpretation'}:
            limitations=[row for row in limitations if row['code']!='unsupported_interpretation']
        out['limitations']=limitations
    if value.get('run_id')and value.get('display_sha256'):
        out['delivery_token']=delivery_token(secret,owner,value['run_id'],value['display_sha256'])
    if value.get('error_code'):out['error_code']='answer_unavailable'
    if isinstance(out.get('progress'),dict):
        stage=out['progress'].get('stage')
        out['progress']={'stage':stage if stage in {'retrieval','generation','validation','complete'}else'generation'}
    return out
