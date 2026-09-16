"""Additive, server-derived human rating observations; no changes to old events."""
import json
import hashlib
import csv
import io
import re
from pathlib import Path
from .store import canonical,digest

SCALE='answer-quality-1-5-v1'
DISPLAY_VERSION='pilot-paragraph-display-v1'
VERSION_FIELDS=('evidence_packet_hash','source_snapshot_ids','vector_snapshot_id','policy_version','generation_profile_id','family_basis_hash','model_identity','execution')
PROVIDER_SCOPES={'shared','own'}
PROVIDER_SELECTIONS={'auto','qwen','gemma'}
PROVIDER_ROUTE_REASONS={'shared_ready','shared_busy_own','shared_unavailable_own','shared_incompatible_own','shared_terminal_failure_own','own_gpu_ready','own_cpu_fallback'}
PROVIDER_FAILURE_CODES={'ambiguous_remote_generation','shared_response_identity_mismatch','provider_terminal_rejection','selected_provider_unavailable','generation_waiting_capacity','provider_identity_missing','provider_selection_mismatch','PILOT_BUSY','PILOT_TIMEOUT','PILOT_FROZEN_RESOURCE_UNAVAILABLE','PILOT_INVALID_FROZEN_REQUEST'}
PROVIDER_FAILURE_STAGES={'writer_request','generation','post_identity','response_shape','hidden_reasoning','completion_stop','response_json','correction','usage','token_budget','source_verification'}
PROVIDER_FAILURE_REASONS={'writer_request_failed','generation_request_failed','response_model_identity_mismatch','post_generation_identity_unavailable','post_generation_identity_mismatch','cpu_execution_identity_mismatch','cpu_execution_allowance_mismatch','invalid_response_shape','unexpected_hidden_reasoning','completion_not_stopped','invalid_final_json','correction_without_parent','parent_consistency_failed','invalid_usage_shape','invalid_reasoning_token_accounting','final_token_accounting_unavailable','total_output_budget_exceeded','final_output_budget_exceeded','reasoning_token_budget_exceeded','frozen_source_verification_failed'}
PROVIDER_FAILURE_TYPES={'PilotUnavailable','ValueError','KeyError','TypeError','JSONDecodeError','OSError','TimeoutError','PermissionError','FileNotFoundError','ConnectionError','ConnectionResetError','HTTPError','SharedUnavailable','SharedRejected','SharedNotAdmitted','SharedCompletionInvalid','OtherError'}
VALIDATION_STATUSES={'invalid_composition_envelope','all_claims_withheld','bounded_checks_completed','bounded_checks_passed'}
VALIDATION_ERROR_FIELDS={'answer_language','claims','claim_id','text','support_ids','section','limitations',
    'code','scope','claim_ids','no_sufficient_evidence','correction_required'}
VALIDATION_ERROR_TYPES={'model_type','missing','extra_forbidden','string_type','string_too_short','string_too_long',
    'string_pattern_mismatch','list_type','too_long','too_short','literal_error','bool_type','bool_parsing',
    'value_error','int_type','int_parsing'}
VALIDATION_SCOPE_REASONS={'claim_limitation_missing_claim_ids','answer_limitation_has_claim_ids'}
VALIDATION_REASONS={
    'invalid_independent_claim_schema','invalid_final_prose','unresolved_support_id','unresolved_inline_citation_alias',
    'generated_bracketed_form_differs_from_original','silent_letter_interpretation_not_explicit_in_selected_support',
    'mentioned_name_form_not_in_selected_support','example_does_not_establish_adjective_definition',
    'entry_presence_does_not_establish_publication_change','uncertain_causal_support_became_unqualified',
    'syntactic_category_not_explicit_in_selected_support','unsupported_notation_interpretation',
    'visible_word_budget_exceeded','duplicate_supported_content','competing_specialist_concepts',
    'specialist_concept_inside_quotation','specialist_translation_subject_not_established','specialist_word_count_translation',
    'explicit_gender_number_form_not_in_complete_source_table','applicable_dictionary_entry_occurrence_ambiguous',
    'comparative_polarity_changed','frequency_domain_not_preserved','frequency_predicate_negation_changed',
    'measurement_date_not_established','historical_current_norm_not_attributed','source_uncertainty_not_preserved',
    'qualified_domain_not_preserved','attached_exception_lost','literal_sense_transferred_to_term',
    'pattern_normality_not_established','frequency_scope_not_established','frequency_numbers_not_in_selected_support'}
VALIDATION_ACTIONS={'accepted','withhold','omit','source_supported_partial','mixed_generated_and_source_partial',
    'supported_claim_fragment','deterministic_repair','deterministic_glossary_repair','deterministic_temporal_attribution'}
VALIDATION_LIMITATION_REASONS={'model_found_insufficient_evidence','no_supported_generated_claims',
    'invalid_or_inconsistently_scoped_composition','native_tokenizer_unavailable','empty_indexed_evidence',
    'selected_model_or_frozen_profile_unavailable','generation_failed_or_truncated','source_search_requested'}


def generation_validation_diagnostics(result):
    """Technical composition outcomes only; model prose and source spans are excluded."""
    if not isinstance(result,dict):return {}
    nested=isinstance(result.get('generation_validation'),dict)
    raw=result['generation_validation']if nested else result
    out={}
    fields={
        'gateway_status':('status',{'validated','partial','failed','no_hit','source_only','rejected','no_answer'}),
        'gateway_answer_kind':('answer_kind',{'generated','mixed','source_only','clarification'}),
        'gateway_generation_status':('generation_status',{'generated','not_generated','source_supported_partial','mixed_generated_and_source_partial'}),
        'synthesis_mode':('synthesis_mode',{'generated','source_supported_partial','mixed_generated_and_source_partial'})}
    for key,(original,allowed)in fields.items():
        value=raw.get(key if nested else original)
        if isinstance(value,str)and value in allowed:out[key]=value
    validation=raw.get('validation')
    if isinstance(validation,dict):
        safe={}
        if isinstance(validation.get('status'),str)and validation['status']in VALIDATION_STATUSES:safe['status']=validation['status']
        if validation.get('semantic_entailment_certified')is False:safe['semantic_entailment_certified']=False
        if validation.get('error_type')in ('ValueError','KeyError','TypeError','ValidationError'):safe['error_type']=validation['error_type']
        errors=validation.get('pydantic_errors')
        if isinstance(errors,list):
            safe['pydantic_errors']=[]
            for row in errors[:32]:
                if not isinstance(row,dict)or not isinstance(row.get('type'),str)or row['type']not in VALIDATION_ERROR_TYPES:continue
                loc=row.get('loc')
                if not isinstance(loc,list):continue
                detail={'type':row['type'],'loc':[
                    value if type(value)is int and 0<=value<=1024 or isinstance(value,str)and value in VALIDATION_ERROR_FIELDS
                    else 'other_field'for value in loc[:12]]}
                reason=row.get('reason')
                if (row['type']=='value_error'and len(loc)==2 and loc[0]=='limitations'and type(loc[1])is int
                        and 0<=loc[1]<=1024 and isinstance(reason,str)and reason in VALIDATION_SCOPE_REASONS):detail['reason']=reason
                safe['pydantic_errors'].append(detail)
        if safe:out['validation']=safe
    def bounded_rows(value):return value[:14]if isinstance(value,list)else []
    def claim_identity(value):
        safe={};cid=value.get('claim_id');index=value.get('claim_index')
        if isinstance(cid,str)and re.fullmatch(r'C(?:[1-9]|1[0-4])',cid):safe['claim_id']=cid
        if type(index)is int and 0<=index<14:safe['claim_index']=index
        return safe
    omitted=[]
    for row in bounded_rows(raw.get('omitted_claims')):
        if not isinstance(row,dict):continue
        reason=row.get('reason')
        if isinstance(reason,str)and reason in VALIDATION_REASONS:omitted.append({**claim_identity(row),'reason':reason})
    if omitted:out['omitted_claims']=omitted
    actions=[]
    for row in bounded_rows(raw.get('quality_actions')):
        if not isinstance(row,dict):continue
        safe=claim_identity(row);action=row.get('action')
        if isinstance(action,str)and action in VALIDATION_ACTIONS:safe['action']=action
        issues=[]
        for issue in bounded_rows(row.get('issues')):
            if not isinstance(issue,dict):continue
            code=issue.get('code')
            if not isinstance(code,str)or code not in VALIDATION_REASONS:continue
            item={'code':code};action=issue.get('action');index=issue.get('part_index')
            if isinstance(action,str)and action in VALIDATION_ACTIONS:item['action']=action
            if type(index)is int and 0<=index<1024:item['part_index']=index
            issues.append(item)
        if issues:safe['issues']=issues
        for key in ('original_part_count','retained_part_count'):
            value=row.get(key)
            if type(value)is int and 0<=value<=1024:safe[key]=value
        if 'action'in safe or issues:actions.append(safe)
    if actions:out['quality_actions']=actions
    reasons=raw.get('limitation_reasons')if nested else [row.get('reason')for row in bounded_rows(raw.get('limitations'))if isinstance(row,dict)]
    reasons=[value for value in bounded_rows(reasons)if isinstance(value,str)and value in VALIDATION_LIMITATION_REASONS]
    if reasons:out['limitation_reasons']=list(dict.fromkeys(reasons))
    return out

def provider_response_observation(value):
    """Keep technical response shape and counts; never model text or reasoning."""
    if not isinstance(value,dict):return {}
    out={}
    enums={'finish_reason':{'stop','length','tool_calls','function_call','content_filter','missing','null','other'},
           'content_type':{'missing','null','string','object','array','boolean','number','other'}}
    counts={'choices_count','prompt_tokens','completion_tokens','total_tokens','reasoning_tokens','json_error_position'}
    for key,item in value.items():
        if key in enums and isinstance(item,str)and item in enums[key]:out[key]=item
        elif key in counts and type(item)is int and 0<=item<=2**63-1:out[key]=item
        elif key=='reasoning_present'and type(item)is bool:out[key]=item
    return out

def public_routing(execution):
    """Only fixed routing labels, never endpoint or authentication metadata."""
    if not isinstance(execution,dict):return {}
    allowed={'provider_scope':PROVIDER_SCOPES,'selection':PROVIDER_SELECTIONS,'route_reason':PROVIDER_ROUTE_REASONS}
    return {key:value for key,value in execution.items()if key in allowed and isinstance(value,str)and value in allowed[key]}

def provider_diagnostics(result):
    """Bounded attempt accounting; response bodies and credentials are excluded."""
    out={};attempts=result.get('provider_attempts')
    if isinstance(attempts,list)and len(attempts)<=2:
        safe=[]
        for attempt in attempts:
            if not isinstance(attempt,dict):continue
            row={key:value for key,value in attempt.items()if
                 (key=='route'and isinstance(value,str)and value in {'shared','own_cpu','own_gpu'})or
                 (key=='status'and isinstance(value,str)and value in {'started','completed','rejected','unknown'})or
                 (key=='http_code'and type(value)is int and 100<=value<=599)or
                 (key=='stage'and isinstance(value,str)and value in {'generation'})or
                 (key=='model'and isinstance(value,str)and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. /-]{0,299}',value))}
            if row:safe.append(row)
        out['provider_attempts']=safe
    failure=result.get('provider_failure')
    if isinstance(failure,dict)and isinstance(failure.get('code'),str)and failure.get('code')in PROVIDER_FAILURE_CODES:
        out['provider_failure']={'code':failure['code']}
        if isinstance(failure.get('selection'),str)and failure.get('selection')in PROVIDER_SELECTIONS:out['provider_failure']['selection']=failure['selection']
        for key,allowed in (('stage',PROVIDER_FAILURE_STAGES),('reason',PROVIDER_FAILURE_REASONS),('error_type',PROVIDER_FAILURE_TYPES)):
            if isinstance(failure.get(key),str)and failure[key]in allowed:out['provider_failure'][key]=failure[key]
        response=provider_response_observation(failure.get('response'))
        if response:out['provider_failure']['response']=response
    if isinstance(result.get('generation_validation'),dict):
        validation=generation_validation_diagnostics(result)
        if validation:out['generation_validation']=validation
    return out

def display_paragraphs(result,tier,locale):
    paragraphs=[]
    if tier=='long'and (result.get('correction')or{}).get('required'):
        messages=json.loads((Path(__file__).resolve().parent.parent/'pilot_ui'/'i18n'/(locale+'.json')).read_text())
        paragraphs.append(messages['long_correction_notice'])
    paragraphs.extend(b['text']for b in result.get('blocks',[])if isinstance(b,dict)and isinstance(b.get('text'),str))
    return paragraphs

def observation(db,owner,run,result,created,display_locale):
    family=dict(db.execute('SELECT * FROM families WHERE id=?',(run['family_id'],)).fetchone())
    account=dict(db.execute('SELECT id,username,origin,cohort FROM accounts WHERE id=?',(owner,)).fetchone())
    original=db.execute('SELECT original_question FROM family_inputs WHERE family_id=?',(run['family_id'],)).fetchone()
    paragraphs=display_paragraphs(result,run['tier'],display_locale)
    # Frozen identity is assigned by our adapter, never by a browser. It contains
    # source/model/resource identities, never the signed packet or credentials.
    versions={k:result[k]for k in VERSION_FIELDS if k in result}
    versions.update(policy_id=family['policy_id'],family_identity=json.loads(family['identity']or'{}'))
    versions['evidence']=[{k:e[k]for k in ('evidence_id','citation_id','passage_id','source_slug','external_id','source_id','source_revision','snapshot_sha256','edition','publication_scope','locator','tool_provenance')if k in e}for e in result.get('evidence',[])if isinstance(e,dict)]
    if result.get('answer_pin'):versions['answer_pin_sha256']=digest(result['answer_pin'])
    ui=Path(__file__).resolve().parent.parent/'pilot_ui'
    versions['ui_assets_sha256']={name:hashlib.sha256((ui/name).read_bytes()).hexdigest()for name in ('pilot.mjs','core.mjs','pilot.css','i18n/'+display_locale+'.json')}
    versions['feedback_implementation_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return {'schema':'klikai.pilot.feedback-observation.v1','participant':account,
            'original_prompt':original[0]if original else family['question'],
            'original_prompt_exact':bool(original),'context':family['context'],
            'displayed_answer': '\n\n'.join(paragraphs),'display_paragraphs':paragraphs,
            'display_sha256':run['display_hash'],'display_version':DISPLAY_VERSION,
            'display_text_sha256':hashlib.sha256('\n\n'.join(paragraphs).encode()).hexdigest(),'display_locale':display_locale,
            'family_id':family['id'],'run_id':run['id'],'answer_revision':run['revision'],
            'timestamp':created,'selected_model':run['model'],
            'actual_model':result.get('model_identity'),
            'execution':result['execution']if isinstance(result.get('execution'),dict)else{'device':'cpu'if family['task']!='reference'else'unknown'},
            'answer_language':family['locale'],'input_language':family['input_language'],
            'tier':run['tier'],'task':family['task'],'answer_method':result.get('answer_method'),'versions':versions,
            'human_eligible':account['origin']=='human'}

def attach(db,event):
    row=db.execute('SELECT quality_score,snapshot FROM feedback_observations WHERE event_id=?',(event['id'],)).fetchone()
    if row:
        event.update(quality_score=row['quality_score'],quality_scale=SCALE,observation=json.loads(row['snapshot']))
    return event

def public_execution(result):
    """Display actual model/backend without exposing private runtime configuration."""
    out={};identity=result.get('model_identity')
    if isinstance(identity,dict):
        out['model_identity']={k:v for k,v in identity.items()if k in {'repository','model_id','revision'}and isinstance(v,str)and len(v)<=300}
    execution=result.get('execution')
    if isinstance(execution,dict):
        out['execution']={k:v for k,v in execution.items()if (k=='device'and isinstance(v,str)and v in {'cpu','gpu'})or(k in {'dtype','runtime','revision'}and isinstance(v,str)and len(v)<=100)}
        out['execution'].update(public_routing(execution))
    return out

def csv_export(data):
    """Spreadsheet-safe CSV, with JSON preserving literal unescaped strings."""
    columns=['event_id','feedback_id','revision','participant_id','participant','origin','timestamp','original_prompt','original_prompt_exact','displayed_answer','quality_score','quality_scale','legacy_rating','comment','model','actual_model','backend','answer_language','tier','display_sha256','versions']
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
    def safe(value):
        if isinstance(value,(dict,list)):value=canonical(value)
        if isinstance(value,str)and value.lstrip().startswith(('=','+','-','@','\t','\r')):return "'"+value
        return value
    for account in data['accounts']:
        for event in account['feedback_revisions']:
            o=event.get('observation',{});a=account['account']
            row={'event_id':event['id'],'feedback_id':event['feedback_id'],'revision':event['revision'],'participant_id':a['id'],'participant':a['username'],'origin':a['origin'],'timestamp':event['created'],'original_prompt':o.get('original_prompt',''),'original_prompt_exact':o.get('original_prompt_exact',False),'displayed_answer':o.get('displayed_answer',''),'quality_score':event.get('quality_score'),'quality_scale':event.get('quality_scale',''),'legacy_rating':event['rating'],'comment':event['comment'],'model':o.get('selected_model',''),'actual_model':o.get('actual_model',''),'backend':o.get('execution',{}).get('device','unknown'),'answer_language':o.get('answer_language',''),'tier':o.get('tier',''),'display_sha256':event['display_hash'],'versions':o.get('versions',{})}
            writer.writerow({key:safe(value)for key,value in row.items()})
    return stream.getvalue()
