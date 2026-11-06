"""Optional saved comma predictions in the existing owned question workflow."""
import asyncio
from copy import deepcopy
import json
import re
from .security import Failure
from .store import digest

CONTRACT='klikai.comma-proposal.v1'
LABEL='Predlog postavitve vejic · CJVT Vejice'
TERMS='https://orodja.cjvt.si/vejice/about'
DISCLOSURE_SL=('Izbrano besedilo se pošlje API-ju CJVT (gpu-proc1.cjvt.si). '
    'Posebni pogoji te povezave niso potrjeni; javni pogoji CJVT dopuščajo '
    'hrambo do treh let za analizo in razvoj. Pošljite le besedilo, ki ga smete deliti.')
DISCLOSURE_IT=('Il testo selezionato viene inviato all’API CJVT (gpu-proc1.cjvt.si). '
    'Le condizioni specifiche di questa connessione non sono confermate; '
    'le condizioni pubbliche CJVT consentono la conservazione fino a tre anni '
    'per analisi e sviluppo. Inviate soltanto testo che potete condividere.')


def capability(config):
    enabled=isinstance(config,dict) and config.get('enabled') is True
    return {'configured':enabled,'provider_label':'CJVT Vejice',
        'disclosure_sl':DISCLOSURE_SL,'disclosure_it':DISCLOSURE_IT,'terms_url':TERMS}


def explanation_followup(question):
    return bool(re.search(r'\b(?:vejic\w*|virgol\w*)\b',question,re.I)and re.search(r'\b(?:zakaj|pojasn\w*|razlo[zž]\w*|perch[eé]|spiega\w*)\b',question,re.I))


def candidate(question,context=''):
    # Queue nomination only. CORE owns target interpretation and decides
    # whether this is actually a check; this never authorizes a provider call.
    return bool(re.search(r'\b(?:vejic\w*|virgol\w*)\b',question,re.I)
        and (re.search(r'\b(?:prever\w*|preglej\w*|poprav\w*|postav\w*|vstav\w*|dodaj\w*|odstran\w*|kje|kam|manjka\w*|odveč\w*|potrebn\w*|controll\w*|corregg\w*|dove)\b',question,re.I)
             or any(mark in question for mark in ('»','«','“','"'))
             or bool(context.strip() and explanation_followup(question))))


def _options(family):
    if isinstance(family.get('request_options'),dict):return family['request_options']
    try:
        identity=json.loads(family.get('identity') or '{}')
    except (ValueError,TypeError):
        identity={}
    return identity.get('request_options',{}) if isinstance(identity,dict) else {}


def freeze(gateway,family,policy):
    config=getattr(gateway,'comma_checker',None)
    if not capability(config)['configured'] or not candidate(family['question'],family.get('context','')):
        return None
    from . import unified
    question=family.get('original_question',family['question'])
    context=family.get('context','')
    interpreted=unified.shared_interpretation(gateway,question,context,
        family.get('input_language','und'),family.get('locale','sl'))
    task=interpreted['request_plan'].get('comma_check')
    if task is None:
        return None
    target=task.get('target')
    if target is not None:
        origin=question if target.get('scope')=='question' else context
        if (target.get('scope') not in {'question','context'}
                or type(target.get('start')) is not int or type(target.get('end')) is not int
                or not 0<=target['start']<target['end']<=len(origin)
                or origin[target['start']:target['end']]!=target.get('text')):
            raise Failure('invalid_request',422)
    status=task['status'];observation=None;reused_parent=None
    options=_options(family)
    consent=options.get('comma_checker_consent') is True
    if task.get('mode')=='explain_existing':
        parent=options.get('comma_parent')
        if not isinstance(parent,dict)or not isinstance(parent.get('observation'),dict):return None
        observation=deepcopy(parent['observation'])
        if target is None or observation.get('input_text')!=target['text']:raise Failure('invalid_request',422)
        reused_parent=parent['run_id'];status=observation['status']
    if status=='ready' and reused_parent is None and len(target['text'])>config.get('max_chars',3000):
        status='too_long'
    if status=='ready' and reused_parent is None and not consent:
        status='consent_required'
    if status=='ready' and reused_parent is None:
        from core.comma_checker import check
        client_config={k:config[k] for k in ('endpoint','key_file','timeout_seconds','max_chars') if k in config}
        observation=asyncio.run(check(target['text'],client_config))
        status=observation['status']
    explanation=None;explanation_status='not_requested'
    if task.get('explain') and status=='ok':
        # This is the existing source-grounded path. Its selected supports and
        # citations remain separate from the checker observation.
        try:
            explanation=unified.freeze(gateway,family,policy)['bundle']
            explanation_status='frozen'
        except Failure:
            explanation_status='unavailable'
    elif task.get('explain'):
        explanation_status='unavailable'
    body={'contract':CONTRACT,'question':question,'locale':family.get('locale','sl'),
        'task':deepcopy(task),'status':status,'observation':observation,'reused_parent_run_id':reused_parent,
        'application_limit_characters':config.get('max_chars',3000),
        'consent':{'accepted':consent,'disclosure_sha256':digest(capability(config)),
                   'terms_url':TERMS},'explanation':explanation,
        'explanation_status':explanation_status}
    bundle={**body,'snapshot_sha256':digest(body)}
    return {'bundle':bundle,'identity':{'contract':CONTRACT,'captured_response_sha256':digest(body)}}


def checked(bundle):
    if (bundle.get('contract')!=CONTRACT
            or bundle.get('snapshot_sha256')!=digest({k:v for k,v in bundle.items()if k!='snapshot_sha256'})):
        raise Failure('resource_changed',409)


def reader_proposal(value):
    if not isinstance(value,dict) or value.get('label')!=LABEL:
        return None
    allowed={'label','status','original_text','proposed_text','edits','offset_convention',
        'checked_extent','target_scope','provider_label','reported_model_version',
        'reported_api_version','reported_confidence','explanation_status','message',
        'tool_execution','application_limit_characters','truncated','explanation_links','unexplained_edit_indexes'}
    return {k:deepcopy(v) for k,v in value.items() if k in allowed}


def reader_execution(value):
    if not isinstance(value,dict):return None
    allowed={'provider','provider_label','attempted','acknowledged','response_received',
        'inference_performed','neural_prediction_received','status','writer_inference_performed',
        'reported_model_version','reported_api_version','observation_reused','source_run_id',
        'syntax_inference_performed'}
    result={k:deepcopy(v) for k,v in value.items() if k in allowed}
    for native,public in [('invocation_attempted','attempted'),('invocation_acknowledged','acknowledged')]:
        if native in value:result[public]=value[native]
    return result


def answer(gateway,bundle,tier,model,parent=None):
    checked(bundle)
    if tier not in {'short','answer'}:raise Failure('expansion_unavailable',409)
    task=bundle['task'];target=task.get('target');original=target['text'] if target else ''
    observation=bundle['observation']
    execution={'provider':'cjvt_vejice','provider_label':'CJVT Vejice','attempted':False,
        'acknowledged':False,'response_received':False,'inference_performed':None,
        'neural_prediction_received':False,'status':bundle['status'],'writer_inference_performed':False}
    proposal={'label':LABEL,'status':bundle['status'],'original_text':original,
        'proposed_text':None,'edits':[],'offset_convention':'unicode_codepoint_half_open',
        'checked_extent':{'start':0,'end':0,'complete':False,'offset_convention':'unicode_codepoint_half_open'},
        'provider_label':'CJVT Vejice','reported_model_version':None,'reported_confidence':None,
        'application_limit_characters':bundle.get('application_limit_characters',3000)}
    if observation is not None:
        from core.comma_checker import public_proposal
        proposal.update(public_proposal(observation))
        for edit in proposal['edits']:
            edit['kind']='remove'if edit['replacement'].count(',')<edit.get('original','').count(',')else'insert'
        proposal['offset_convention']='unicode_codepoint_half_open'
        execution.update(reader_execution(observation.get('execution',{})) or {})
    if bundle.get('reused_parent_run_id'):
        execution.update(attempted=False,acknowledged=False,response_received=False,
            neural_prediction_received=False,observation_reused=True,source_run_id=bundle['reused_parent_run_id'])
    syntax=((bundle.get('explanation')or{}).get('analysis')or{}).get('captures',[])
    syntax=[row.get('response',{}) for row in syntax if row.get('operation')=='syntax']
    if syntax:execution['syntax_inference_performed']=syntax[0].get('execution',{}).get('inference_performed')
    proposal['label']=LABEL
    proposal['target_scope']={k:target[k]for k in ('scope','start','end')}if target else None
    explanation_status=bundle['explanation_status']
    writer_execution={'provider_scope':'none','inference_performed':False,
        'writer_inference_performed':False,'inference_scope':'answer_writer'}
    result=None;explained=None
    if bundle.get('explanation'):
        from . import unified
        try:
            # A clause explanation needs both the whole syntax observation and
            # governing source. Reuse the ordinary answer allowance; the
            # lexical short input can drop one half before registry building.
            explained=unified.generate(gateway,bundle['explanation'],
                'answer' if tier=='short' else tier,model,parent)
            writer_execution=deepcopy(explained.get('execution',{}))
            if 'inference_performed'not in writer_execution:
                writer_execution['inference_performed']=(True if any(row.get('status')=='completed'for row in explained.get('provider_attempts',[]))else None)
            if explained.get('answer_kind')=='generated':
                result=deepcopy(explained);explanation_status='supported'
            else:explanation_status='unsupported'
        except Failure:
            explanation_status='unavailable'
            writer_execution={'writer_inference_performed':None,'inference_performed':None,
                              'inference_scope':'answer_writer','status':'unacknowledged_explanation'}
    messages={
        'ok':'',
        'consent_required':'Pred pošiljanjem preglejte izbrano besedilo in potrdite pošiljanje API-ju CJVT.',
        'target_required':'Napišite besedilo za preverjanje vejic, na primer za oznako »Preveri vejice:«.',
        'unsupported_language':'Ta preverjevalnik je namenjen slovenskim besedilom.',
        'too_long':'Besedilo presega trenutno omejitev tega preverjanja. Ni bilo skrajšano ali poslano.',
    }
    italian=bundle.get('locale')=='it'
    if italian:
        messages={'ok':'','consent_required':'Prima dell’invio controllate il testo selezionato e confermate l’invio all’API CJVT.',
            'target_required':'Inserite il testo di cui controllare le virgole, ad esempio dopo «Preveri vejice:».',
            'unsupported_language':'Questo strumento è destinato ai testi in sloveno.',
            'too_long':'Il testo supera il limite attuale del controllo. Non è stato troncato né inviato.'}
    text=messages.get(bundle['status'],
        'La proposta di questo controllo non può essere applicata. Il testo originale rimane conservato.'if italian else
        'Predloga tega preverjanja ni mogoče uporabiti. Izvirno besedilo ostaja ohranjeno.')
    if bundle['status']!='ok':text=''  # The saved proposal panel owns its local status notice.
    if task.get('explain') and bundle['status']=='ok'and explanation_status in {'unsupported','unavailable'}:
        text+=(' Per questa modifica non ho al momento una spiegazione collegata adeguatamente alle fonti conservate.'if italian else
               ' Za konkretni poseg trenutno nimam ustrezno vezane razlage iz ohranjenih virov.')
    if result is None:
        result={'status':'source_only','answer_kind':'tool_prediction','answer_support':'automated_prediction',
            'generation_status':'not_generated','answer_md':text,'short_answer':text,
            'display_blocks':[{'block_id':'comma-notice','text':text}], 'blocks':[{'block_id':'comma-notice','text':text}],
            'evidence':[],'source_badges':[],'claims':[],'citations':[],'limitations':[],
            'execution':writer_execution,'model_identity':{'model_id':'not_invoked'},
            'synthesis_mode':'automated_tool_prediction'}
    if explained is not None and result.get('answer_kind')=='tool_prediction':
        # An unsupported explanation can still have used the writer. Preserve
        # that attempt and its identity instead of inheriting not_invoked.
        for key in ('provider_attempts','usage','model_identity'):
            if key in explained:result[key]=deepcopy(explained[key])
    execution['writer_inference_performed']=writer_execution.get('inference_performed')
    connection=None
    if bundle.get('explanation') and result.get('answer_kind')=='generated':
        from .comma_explanations import connect,reader
        connection=connect(proposal,task,result,result.get('applied_unit_receipts',[]))
        visible=reader(connection)
        if visible is not None:
            explanation_status=visible['status']
            proposal['explanation_links']=visible['links']
            proposal['unexplained_edit_indexes']=visible['unexplained_edit_indexes']
    proposal['explanation_status']=explanation_status
    result.update(comma_proposal=reader_proposal(proposal),tool_execution=reader_execution(execution),
        deterministic_result={'comma_observation':deepcopy(observation),'task':deepcopy(task),
            'consent':deepcopy(bundle['consent']),'explanation_connection':connection,
            'applied_unit_receipts':deepcopy(result.get('applied_unit_receipts',[])),
            'source_explanation_result':deepcopy(explained) if result.get('answer_kind')=='tool_prediction' else None})
    result.setdefault('diagnostics',{})['comma_checker']=reader_execution(execution)
    result['diagnostics'].setdefault('writer',{'status':'not_invoked'if writer_execution.get('inference_performed')is False else'unknown','reason':'dedicated_comma_tool_with_separate_explanation'})
    return result


def source(gateway,bundle,evidence_id):
    checked(bundle)
    if not bundle.get('explanation'):raise Failure('not_found',404)
    from . import unified
    return unified.source(bundle['explanation'],evidence_id)
