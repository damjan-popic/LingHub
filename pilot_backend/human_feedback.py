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
VERSION_FIELDS=('evidence_packet_hash','source_snapshot_ids','vector_snapshot_id','policy_version','generation_profile_id','family_basis_hash','model_identity','execution','application_release')
PROVIDER_SCOPES={'shared','own'}
PROVIDER_SELECTIONS={'auto','qwen','gemma'}
PROVIDER_ROUTE_REASONS={'shared_ready','shared_busy_own','shared_unavailable_own','shared_incompatible_own','shared_terminal_failure_own','own_gpu_ready','own_cpu_fallback'}
PROVIDER_FAILURE_CODES={'ambiguous_remote_generation','shared_response_identity_mismatch','provider_terminal_rejection','selected_provider_unavailable','malformed_provider_response','invalid_generated_content','invalid_application_composition','provider_identity_verification_failed','generation_waiting_capacity','provider_identity_missing','provider_selection_mismatch','PILOT_BUSY','PILOT_TIMEOUT','PILOT_FROZEN_RESOURCE_UNAVAILABLE','PILOT_INVALID_FROZEN_REQUEST'}
PROVIDER_FAILURE_STAGES={'writer_request','generation','post_identity','response_shape','hidden_reasoning','completion_stop','response_json','correction','usage','token_budget','source_verification'}
PROVIDER_FAILURE_REASONS={'writer_request_failed','generation_request_failed','response_model_identity_mismatch','post_generation_identity_unavailable','post_generation_identity_mismatch','cpu_execution_identity_mismatch','cpu_execution_allowance_mismatch','invalid_response_shape','invalid_response_json','invalid_execution_shape','invalid_content_type','unexpected_hidden_reasoning','completion_not_stopped','invalid_final_json','correction_without_parent','parent_consistency_failed','invalid_usage_shape','invalid_reasoning_token_accounting','final_token_accounting_unavailable','total_output_budget_exceeded','final_output_budget_exceeded','reasoning_token_budget_exceeded','frozen_source_verification_failed'}
PROVIDER_FAILURE_TYPES={'PilotUnavailable','ValueError','KeyError','TypeError','JSONDecodeError','OSError','TimeoutError','PermissionError','FileNotFoundError','ConnectionError','ConnectionResetError','HTTPError','SharedUnavailable','SharedRejected','SharedNotAdmitted','SharedCompletionInvalid','OtherError'}
VALIDATION_STATUSES={'invalid_composition_envelope','all_claims_withheld','bounded_checks_completed','bounded_checks_passed','consistent_abstention'}
VALIDATION_ERROR_FIELDS={'answer_language','claims','claim_id','text','support_ids','section','paragraph_id','depends_on','limitations',
    'code','scope','claim_ids','no_sufficient_evidence','correction_required'}
VALIDATION_ERROR_TYPES={'model_type','missing','extra_forbidden','string_type','string_too_short','string_too_long',
    'string_pattern_mismatch','list_type','too_long','too_short','literal_error','bool_type','bool_parsing',
    'value_error','int_type','int_parsing'}
VALIDATION_SCOPE_REASONS={'claim_limitation_missing_claim_ids','answer_limitation_has_claim_ids'}
VALIDATION_FAILURE_REASONS={'invalid_composition_envelope','tier_claim_bound_exceeded','duplicate_claim_ids',
    'requested_locale_differs','abstention_contradicts_claims','empty_supported_answer',
    'limitation_names_unknown_claims','no_evidence_caveat_contradicts_claims','support_bytes_differ'}
VALIDATION_REASONS={
    'invalid_independent_claim_schema','invalid_final_prose','unresolved_support_id','unresolved_inline_citation_alias','dependent_claim_not_retained',
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
    'pattern_normality_not_established','frequency_scope_not_established','frequency_numbers_not_in_selected_support','scoped_abstention_not_factual_claim',
    'irrelevant_source_commentary','population_comprehension_requires_attribution','geographical_attestation_is_not_exclusivity',
    'language_is_not_country','per_form_recommendation_not_preserved','notification_is_not_permit',
    'claim_case_not_in_supported_agreement','curated_recommendation_direction_reversed'}
VALIDATION_ACTIONS={'accepted','withhold','omit','source_supported_partial','mixed_generated_and_source_partial',
    'supported_claim_fragment','deterministic_repair','deterministic_glossary_repair','deterministic_temporal_attribution','normalize_abstention_envelope','deterministic_typed_scope_repair'}
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
        'gateway_generation_status':('generation_status',{'generated','not_generated','source_supported_partial','mixed_generated_and_source_partial','returned_abstention'}),
        'synthesis_mode':('synthesis_mode',{'generated','source_supported_partial','mixed_generated_and_source_partial','abstention'})}
    for key,(original,allowed)in fields.items():
        value=raw.get(key if nested else original)
        if isinstance(value,str)and value in allowed:out[key]=value
    validation=raw.get('validation')
    if isinstance(validation,dict):
        safe={}
        if isinstance(validation.get('status'),str)and validation['status']in VALIDATION_STATUSES:safe['status']=validation['status']
        if validation.get('semantic_entailment_certified')is False:safe['semantic_entailment_certified']=False
        if validation.get('error_type')in ('ValueError','KeyError','TypeError','ValidationError'):safe['error_type']=validation['error_type']
        reason=validation.get('reason')
        if isinstance(reason,str)and reason in VALIDATION_FAILURE_REASONS:safe['reason']=reason
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
        status=failure.get('http_status')
        if type(status)is int and 100<=status<=599:out['provider_failure']['http_status']=status
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
    previous=None
    for block in result.get('blocks',[]):
        if not isinstance(block,dict)or not isinstance(block.get('text'),str):continue
        pid=block.get('paragraph_id')
        if isinstance(pid,str)and re.fullmatch(r'P(?:[1-9]|1[0-4])',pid)and pid==previous:
            paragraphs[-1]+=' '+block['text']
        else:paragraphs.append(block['text'])
        previous=pid
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
    submission=db.execute('SELECT snapshot FROM run_observations WHERE run_id=?',(run['id'],)).fetchone()
    submission=json.loads(submission[0]) if submission else None
    return {'schema':'klikai.pilot.feedback-observation.v1','participant':account,
            'original_prompt':original[0]if original else family['question'],
            'original_prompt_exact':bool(original),'context':family['context'],
            'displayed_answer': '\n\n'.join(paragraphs),'display_paragraphs':paragraphs,
            'display_sha256':run['display_hash'],'display_version':('pilot-scoped-paragraph-display-v2' if any(b.get('paragraph_id')for b in result.get('blocks',[])) else DISPLAY_VERSION),
            'display_text_sha256':hashlib.sha256('\n\n'.join(paragraphs).encode()).hexdigest(),'display_locale':display_locale,
            'family_id':family['id'],'run_id':run['id'],'answer_revision':run['revision'],
            'timestamp':created,'selected_model':run['model'],
            'actual_model':result.get('model_identity'),
            'execution':result['execution']if isinstance(result.get('execution'),dict)else{'device':'cpu'if family['task']!='reference'else'unknown'},
            'answer_language':family['locale'],'input_language':family['input_language'],
            'tier':run['tier'],'task':family['task'],'answer_method':result.get('answer_method'),'versions':versions,
            'human_eligible':account['origin']=='human' and account['username']!='tehno' and (submission or {}).get('participant',{}).get('origin')!='automated_test',
            'submission_observation':{k:submission[k] for k in ('participant','versions')} if submission else None}

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

# One small additive record per existing run, in the same private/backed-up DB.
# Families retain their frozen sources; results and revisioned feedback are joined,
# never copied into a second feedback store.
def participant_at_submission(db, owner, automated=False):
    account=dict(db.execute('SELECT id,username,origin,cohort FROM accounts WHERE id=?',(owner,)).fetchone())
    group=account['username'] if account['username'] in {'cu','slori','tehno'} else 'unknown'
    origin=('automated_test' if automated or account['origin'] in {'test','synthetic'} else
            'technical_manual' if group=='tehno' else 'project_use' if group in {'cu','slori'} else 'unknown')
    return {**account,'group':group,'account_origin':account['origin'],'origin':origin,'group_basis':'authenticated_account_at_submission'}


def accepted(db, run_id, owner, *, automated=False, release=None):
    row=db.execute('SELECT parent_run_id FROM runs WHERE id=?',(run_id,)).fetchone()
    parent=db.execute('SELECT snapshot FROM run_observations WHERE run_id=?',(row[0],)).fetchone() if row[0] else None
    value={'schema':'klikai.pilot.run-observation.v1', 'participant':participant_at_submission(db,owner,automated),
        'versions':{'application_release':release},'stages':{},'trace_incomplete':False,'missing_fields':[]}
    if parent and json.loads(parent[0])['participant']['origin']=='automated_test':value['participant']['origin']='automated_test'
    db.execute('INSERT INTO run_observations VALUES(?,?)',(run_id,canonical(value)))


def record_stage(store, run_id, stage, value):
    """Optional extended capture must not turn an answer into a failure.

    Missing stage markers are detected independently by export even if the DB
    itself could not accept the diagnostic failure marker.
    """
    try:
        encoded=canonical(value)
        if len(encoded.encode())>4*1024*1024:raise ValueError('diagnostic_capture_bound')
        with store.tx() as db:
            row=db.execute('SELECT snapshot FROM run_observations WHERE run_id=?',(run_id,)).fetchone()
            if row is None:return False
            snapshot=json.loads(row[0]);snapshot['stages'][stage]=json.loads(encoded)
            db.execute('UPDATE run_observations SET snapshot=? WHERE run_id=?',(canonical(snapshot),run_id))
        return True
    except Exception as error:
        try:
            with store.tx() as db:
                row=db.execute('SELECT snapshot FROM run_observations WHERE run_id=?',(run_id,)).fetchone()
                if row:
                    snapshot=json.loads(row[0]);snapshot['trace_incomplete']=True
                    snapshot['missing_fields'].append({'stage':stage,'cause':safe_exception(error)})
                    db.execute('UPDATE run_observations SET snapshot=? WHERE run_id=?',(canonical(snapshot),run_id))
        except Exception:pass
        return False


def safe_exception(error):
    # Exception messages can contain a signed URL, token, headers or a full
    # provider response. Retain class, numeric errno, safe code, and code frames.
    import traceback
    value={'error_type':type(error).__name__,'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)[-6:]]}
    code=getattr(error,'code',None)
    if isinstance(code,str) and re.fullmatch('[a-zA-Z0-9_]{1,100}',code):value['error_code']=code
    if type(getattr(error,'errno',None)) is int:value['errno']=error.errno
    cause=error.__cause__ or error.__context__
    if cause is not None and cause is not error:value['cause_type']=type(cause).__name__
    return value


def failure_display(state, locale):
    message=('Zahteva je preklicana.' if state=='cancelled' else 'Odgovora ni bilo mogoče pripraviti. Poskusite znova.') if locale=='sl' else (
        'La richiesta è stata annullata.' if state=='cancelled' else 'Non è stato possibile preparare la risposta. Riprova.')
    return {'status':'failed','answer_kind':'failed','answer_md':message,'short_answer':message,
        'blocks':[{'block_id':'attempt-status','text':message}],'evidence':[],'citations':[],'source_badges':[]}


def terminal_display(db, run_id, state):
    locale=db.execute('SELECT f.locale FROM families f JOIN runs r ON r.family_id=f.id WHERE r.id=?',(run_id,)).fetchone()[0]
    result=failure_display(state,locale)
    return canonical(result),digest(result['blocks'])


def visible_result(run, locale):
    result=json.loads(run['result'] or '{}')
    if run['state'] in {'failed','cancelled'} and not result.get('blocks'):return failure_display(run['state'],locale)
    return result


def derived_attempt_analysis(observation, result, *, delivered=None):
    """Additive stage interpretation; never replace any recorded outcome/trace.

    A completion is observed from retained final text, independently of old
    writer.status or provider-failure labels. Unknown delivery remains unknown.
    """
    stages=observation.get('stages',{});generation=stages.get('generation',{})
    writer=generation.get('writer',{});raw=writer.get('raw_final_content')
    returned=isinstance(raw,str) and bool(raw.strip())
    invocation=('returned_completion' if returned else 'not_invoked' if writer.get('status')=='not_invoked'
        else 'attempted_without_retained_completion' if writer.get('requests') or writer.get('status') in {'attempted','failed'} else 'unknown')
    checks=generation.get('result_checks') or result.get('generation_validation') or result
    validation=checks.get('validation',{})
    content=None
    if returned:
        try:content=json.loads(raw)
        except (ValueError,TypeError):pass
    claims=content.get('claims',[]) if isinstance(content,dict) else []
    def refusal(text):
        return isinstance(text,str) and bool(re.search(r'(?:priložen\w*|predložen\w*|izbran\w*)\s+vir\w*',text,re.I) and
            re.search(r'ne\s+vsebujejo|ni\s+(?:podatk\w*|naveden\w*|na\s+voljo|dovolj)|ni\s+zajet\w*',text,re.I))
    refused=(isinstance(content,dict) and (content.get('no_sufficient_evidence') is True and claims==[] or
        bool(claims) and all(isinstance(c,dict) and refusal(c.get('text')) for c in claims)))
    reason=validation.get('reason');status=validation.get('status')
    if status=='invalid_composition_envelope' or reason=='abstention_contradicts_claims':stage='invalid_composition'
    elif status=='all_claims_withheld':stage='all_claims_withheld'
    elif status=='consistent_abstention' or refused:stage='returned_abstention'
    elif result.get('claims') and (checks.get('omitted_claims') or result.get('answer_support')=='partial'):stage='partial_supported_output'
    elif result.get('claims'):stage='supported_output'
    elif invocation=='not_invoked':stage='no_writer_invoked'
    elif not returned and invocation=='attempted_without_retained_completion':stage='writer_unavailable_or_incomplete'
    else:stage='unknown'
    delivery=('delivered' if delivered is not None else
        'delivery_failure' if stages.get('failure',{}).get('stage') in {'delivery','browser_delivery'} else 'not_observed')
    return {'schema':'attempt-stage-analysis-v1','writer_execution':invocation,
        'content_kind':'abstention' if refused or status=='consistent_abstention' else 'substantive_candidate' if claims else 'unknown',
        'validation_stage':stage,'delivery_stage':delivery,'semantic_correctness':'not_established',
        'historical_labels_preserved':True}


def attempt_case(db, run_id):
    """Offline-only joins over original persisted data. Never calls a provider."""
    run=dict(db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone())
    family=dict(db.execute('SELECT * FROM families WHERE id=?',(run['family_id'],)).fetchone())
    account=dict(db.execute('SELECT id,username,cohort,origin FROM accounts WHERE id=?',(family['owner'],)).fetchone())
    row=db.execute('SELECT snapshot FROM run_observations WHERE run_id=?',(run_id,)).fetchone()
    observed_at_submission=row is not None
    observation=json.loads(row[0]) if row else {'participant':{**account,'group':account['username'] if account['username'] in {'cu','slori','tehno'} else 'unknown','origin':'unknown','group_basis':'historical_owner_identity_only'},'stages':{},'versions':{},'missing_fields':['submission_observation_not_recorded_historically']}
    job=dict(db.execute('SELECT * FROM jobs WHERE run_id=?',(run_id,)).fetchone() or {})
    original=db.execute('SELECT original_question FROM family_inputs WHERE family_id=?',(family['id'],)).fetchone()
    bundle=json.loads(family['bundle'] or '{}');result=visible_result(run,family['locale'])
    # HMACs authorize internal operations, not analysis. Export the signed
    # payload's source bytes but never the signature or an answer bearer pin.
    def data_only(value):
        if isinstance(value,list):return [data_only(x) for x in value]
        if isinstance(value,dict):return {k:data_only(v) for k,v in value.items() if k not in {'signature','hmac','answer_pin','sig'}}
        return value
    feedback=[]
    for row in db.execute('SELECT e.*,f.id AS feedback_id FROM feedback_events e JOIN feedback f ON f.id=e.feedback_id WHERE f.run_id=? ORDER BY e.revision',(run_id,)):
        event=attach(db,dict(row))
        for key in ('tags','target','anchor'):event[key]=json.loads(event[key]) if event[key] else None
        feedback.append(event)
    reviews=[dict(row) for row in db.execute('SELECT t.* FROM feedback_triage t JOIN feedback f ON f.id=t.feedback_id WHERE f.run_id=? ORDER BY t.created',(run_id,))]
    missing=list(observation.get('missing_fields',[]));stages=observation['stages']
    if run['state'] in {'failed','cancelled'} and not json.loads(run['result'] or '{}').get('blocks'):missing.append('historical_failure_display_not_captured')
    if observed_at_submission and run['state']=='complete' and 'generation' not in stages:missing.append('generation_stage_capture')
    if job.get('started') and 'started' not in stages:missing.append('worker_start_capture')
    writer=stages.get('generation',{}).get('writer',{})
    missing.extend(writer.get('missing_fields',[]))
    frozen=bundle.get('gateway_bundle',bundle).get('payload',{}) if isinstance(bundle.get('gateway_bundle',bundle),dict) else {}
    if family['task']=='reference' and run['state']=='complete' and bundle.get('matches') and not frozen:missing.append('gateway_freeze_unavailable')
    missing.extend(frozen.get('retrieval',{}).get('missing_fields',[]))
    generation=result.get('generation',{})
    failure=stages.get('failure',{})
    timeout=(job.get('error_code') in {'source_timeout','answer_timeout','PILOT_TIMEOUT'} or
        failure.get('error_type') in {'TimeoutError','ReadTimeout','ConnectTimeout'} or
        failure.get('cause_type') in {'TimeoutError','ReadTimeout','ConnectTimeout'})
    outcome=('timed_out' if run['state']!='cancelled' and timeout else
        run['state'] if run['state']!='complete' else
        'no_support' if result.get('status')=='no_hit' or generation.get('status')=='no_support' else
        'failed' if generation.get('status') in {'failed','unavailable'} or result.get('status')=='failed' else
        'partial' if result.get('status')=='partial' else 'completed')
    retrieval=result.get('retrieval',bundle.get('retrieval',{}))
    degraded=bool(retrieval.get('status') in {'partial','unavailable'} or frozen.get('retrieval',{}).get('degraded'))
    latest=feedback[-1] if feedback else {}
    rating=latest.get('quality_score') or latest.get('rating') or 'unrated'
    summary={'run_id':run_id,'family_id':family['id'],'job_id':job.get('id'),'parent_run_id':run['parent_run_id'],
        'participant_id':account['id'],'participant':observation['participant']['username'],'group':observation['participant']['group'],
        'origin':observation['participant']['origin'],'timestamp':run['created'],'question':original[0] if original else family['question'],
        'release':result.get('application_release') or observation.get('versions',{}).get('application_release'),
        'actual_model':result.get('model_identity'),'provider':result.get('execution',{}).get('provider_scope'),
        'outcome':outcome,'degraded':degraded,'rating':rating,'feedback_revisions':len(feedback),
        'review_status':reviews[-1]['status'] if reviews else 'unreviewed','trace_incomplete':bool(missing or observation.get('trace_incomplete')),
        'display_sha256':run['display_hash'] or digest(result.get('blocks',[])),'tier':run['tier']}
    return {'summary':summary,'observation':data_only(observation),'missing_fields':missing,
        'derived_analysis':derived_attempt_analysis(observation,result,delivered=run['delivered']),
        'context':family['context'],'languages':{'input':family['input_language'],'input_basis':'explicit_input' if family['input_language']!='und' else 'not_supplied','answer':family['locale'],'answer_basis':'submitted_answer_locale'},
        'job':job,'frozen_sources':data_only(bundle),'family_identity':json.loads(family['identity'] or '{}'),
        'display':data_only(result),'display_paragraphs':display_paragraphs(result,run['tier'],family['locale']),
        'feedback_revisions':feedback,'human_reviews':reviews,
        'timing':{'accepted':run['created'],'started':job.get('started'),'server_ready':run['finished'],'browser_delivery':run['delivered']},
        'teaching_approval':'not_established_by_rating_or_group'}


def export_attempts(store, directory, *, groups=None, origin=None, since=None, until=None, release=None,
                    model=None, provider=None, outcome=None, degraded=None, rating=None, review_status=None, run_id=None):
    import os
    directory=Path(directory).absolute()
    if directory.exists() or directory.is_symlink():raise ValueError('Choose a new private export directory')
    directory.mkdir(parents=True,mode=0o700);os.chmod(directory,0o700)
    chosen=set(groups or ('cu','slori'))
    # Explicit TEHNO includes its ordinary technical traffic. Automated traffic
    # and historical unknown traffic require an explicit origin/all selection.
    default_origin={'project_use','technical_manual'} if groups else {'project_use'}
    cases=[]
    with store.connect() as db:
        ids=[r[0] for r in db.execute('SELECT id FROM runs ORDER BY created')]
        for rid in ids:
            if run_id and rid!=run_id:continue
            case=attempt_case(db,rid);row=case['summary']
            if not run_id:
                if 'all' not in chosen and row['group'] not in chosen:continue
                if origin!='all' and row['origin'] not in ({origin} if origin else default_origin):continue
            if since is not None and row['timestamp']<since or until is not None and row['timestamp']>until:continue
            filters={'release':release,'provider':provider,'outcome':outcome,'rating':rating,'review_status':review_status,'degraded':degraded}
            if any(v is not None and str(row[k])!=str(v) for k,v in filters.items()):continue
            if model and model not in canonical(row['actual_model']):continue
            cases.append(case)
    def write(name, content):
        fd=os.open(directory/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as out:out.write(content)
    write('attempts.jsonl',''.join(canonical(c['summary'])+'\n' for c in cases))
    stream=io.StringIO(newline='');columns=list(cases[0]['summary']) if cases else ['run_id','group','origin','outcome','rating']
    writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
    for case in cases:
        row={k:canonical(v) if isinstance(v,(dict,list)) else v for k,v in case['summary'].items()}
        row={k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in row.items()};writer.writerow(row)
        write(case['summary']['run_id']+'.json',json.dumps(case,ensure_ascii=False,indent=2)+'\n')
        summary=case['summary'];write(summary['run_id']+'.md',f"# Private testing case {summary['run_id']}\n\nGroup: {summary['group']}; origin: {summary['origin']}; outcome: {summary['outcome']}; rating: {summary['rating']}.\n\n## Original question\n\n{summary['question']}\n\n## Displayed answer\n\n"+'\n\n'.join(case['display_paragraphs'])+'\n\nFull persisted analysis, candidates, selection, writer request/output and feedback revisions are in the adjacent JSON file. No retrieval or generation was performed for this export.\n\nMissing fields: '+canonical(case['missing_fields'])+'\n')
    write('attempts.csv',stream.getvalue())
    counts={'attempts':len(cases),'unique_questions':len({digest([c['summary']['question'],c['context']]) for c in cases}),'rated_outputs':sum(c['summary']['rating']!='unrated' for c in cases),'incomplete_traces':sum(c['summary']['trace_incomplete'] for c in cases)}
    write('README.json',json.dumps({'counts':counts,'scope':'bounded returned candidates, no claim of exhaustive coverage','filters':{'groups':sorted(chosen),'origin':origin or sorted(default_origin)},'training':'No case is approved teaching material merely by inclusion or rating.'},indent=2)+'\n')
    return {'directory':str(directory),'counts':counts}
