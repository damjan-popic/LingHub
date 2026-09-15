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
