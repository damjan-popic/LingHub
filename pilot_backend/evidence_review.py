"""Validation and deterministic presentation of writer-independent findings."""
from copy import deepcopy
from .document_source_body import source_body,body_binding
import hashlib
import re
from .store import digest
from .security import Failure

VERSION='klikai.document-evidence-review.v1'
STRATEGY='evidence_review_v1'


def checked(review,text=None,task=None,matches=None):
    if (not isinstance(review,dict)or review.get('version')!=VERSION or review.get('strategy')!=STRATEGY
            or review.get('manifest_sha256')!=digest({k:v for k,v in review.items()if k!='manifest_sha256'})
            or review.get('generation')!={'writer_invoked':False,'generative_attempts':0}
            or not isinstance(review.get('findings'),list)or len(review['findings'])>64
            or not isinstance(review.get('checks'),list)or len(review['checks'])>32):
        raise Failure('invalid_document_result',502)
    if text is not None and review['source_sha256']!=hashlib.sha256(text.encode()).hexdigest():
        raise Failure('invalid_document_result',502)
    if task is not None and (task.get('review_strategy')!=STRATEGY
            or review.get('document_binding')!=task.get('document_binding')
            or review.get('document_revision')!=task.get('document_revision')
            or review.get('document_location')!=task.get('document_location')):
        raise Failure('invalid_document_result',502)
    seen=set()
    for finding in review['findings']:
        original=(finding.get('match')or{}).get('observed')or{}
        a,b=original.get('start'),original.get('end')
        if (not re.fullmatch(r'F[0-9a-f]{24}',str(finding.get('finding_id')))
                or finding['finding_id']in seen or finding.get('type')not in {'qualified_advice','recorded_contrast','recorded_form_context','executable_application','application_proposal'}
                or type(a)is not int or type(b)is not int or not 0<=a<=b<=3000
                or text is not None and text[a:b]!=original.get('text')
                or finding.get('type')!='executable_application'and(finding.get('type')!='application_proposal'and finding.get('proposed_edit')is not None or finding.get('included_in_draft')is not False)):
            raise Failure('invalid_document_result',502)
        if (finding.get('document_revision')!=review.get('document_revision')or finding.get('document_location')!=review.get('document_location')):
            raise Failure('invalid_document_result',502)
        seen.add(finding['finding_id'])
        if finding['type']=='executable_application':
            proof=finding.get('application_proof')or{}
            if (proof.get('adapter_version')not in {'document-existing-applications.v1','document-source-applications.v1'}
                    or proof.get('proof_sha256')!=digest({k:v for k,v in proof.items()if k!='proof_sha256'})
                    or proof.get('writer_inference_performed')is not False
                    or finding.get('included_in_draft')is not True
                    or finding.get('proposed_edit')not in review.get('applications',{}).get('edits',[])):
                raise Failure('invalid_document_result',502)
            edit=finding['proposed_edit']
            if (edit.get('start')!=a or edit.get('end')!=b or edit.get('original')!=original.get('text')
                    or proof.get('input_sha256')!=review['source_sha256']):
                raise Failure('invalid_document_result',502)
            if proof['adapter_version']=='document-existing-applications.v1'and text is not None and review.get('applications',{}).get('composition'):
                operations=finding.get('observed_checker_edits')or[]
                if not any(all(edit.get(k)==op.get(k)for k in ('start','end','original','replacement'))for op in operations):
                    raise Failure('invalid_document_result',502)
                rendered=[];last=0
                for operation in operations:
                    left,right=operation.get('start'),operation.get('end')
                    if (type(left)is not int or type(right)is not int or not last<=left<=right<=len(text)
                            or text[left:right]!=operation.get('original')
                            or operation.get('original')==operation.get('replacement')):
                        raise Failure('invalid_document_result',502)
                    rendered.extend([text[last:left],operation['replacement']]);last=right
                proposed=''.join(rendered)+text[last:]
                if hashlib.sha256(proposed.encode()).hexdigest()!=proof.get('output_sha256'):
                    raise Failure('invalid_document_result',502)
        source=finding.get('source')or{}
        if finding.get('application_proof',{}).get('adapter_version')=='document-source-applications.v1'and finding.get('included_in_draft'):
            # Consumer checks exact contract/registry/source/document bindings.
            # Normative applicability is established and rechecked in CORE.
            from .document_application_observations import application_registry
            data,identity=application_registry();proof=finding['application_proof']
            index=proof.get('registry_entry');edit=finding['proposed_edit']
            if (proof.get('registry_sha256')!=identity or type(index)is not int or not 0<=index<len(data['entries'])
                    or proof.get('input_sha256')!=review['source_sha256']
                    or proof.get('source_capture_sha256')!=source.get('capture_sha256')):
                raise Failure('invalid_document_result',502)
            entry=data['entries'][index]
            if (entry['kind']!='spelling_letters'or source.get('focus',{}).get('text_sha256')!=entry['row_sha256']
                    or edit['original'].casefold()!=entry['original'].casefold()
                    or edit['replacement'].casefold()!=entry['replacement'].casefold()
                    or text is not None and text[edit['start']:edit['end']]!=edit['original']):
                raise Failure('invalid_document_result',502)
        if matches is not None:
            record=matches.get(source.get('evidence_id'));raw=(record or{}).get('capture')or{}
            body=source_body(record or{});focus=source.get('focus')or{}
            left,right=focus.get('char_start'),focus.get('char_end')
            if (record is None or record['capture_sha256']!=source.get('capture_sha256')
                    or not isinstance(body,str)or source.get('body_binding')is not None and source['body_binding']!=body_binding(record)or type(left)is not int or type(right)is not int
                    or not 0<=left<right<=len(body)or body[left:right]!=focus.get('text')
                    or hashlib.sha256(body[left:right].encode()).hexdigest()!=focus.get('text_sha256')
                    or source.get('recorded_advice')is not None and source['recorded_advice']not in(raw.get('advice_units')or[])):
                raise Failure('invalid_document_result',502)
            for related in finding.get('related_source_captures',[]):
                if (related.get('evidence_id')not in matches
                        or matches[related['evidence_id']]['capture_sha256']!=related.get('capture_sha256')):
                    raise Failure('invalid_document_result',502)
    included=[f['proposed_edit']for f in review['findings']if f['included_in_draft']]
    if included!=review.get('applications',{}).get('edits',[]):raise Failure('invalid_document_result',502)
    if review.get('overview',{}).get('included_edits')!=len(included):raise Failure('invalid_document_result',502)
    expected={kind:sum(f['type']==kind for f in review['findings'])for kind in ('qualified_advice','recorded_contrast','recorded_form_context','executable_application','application_proposal')}
    if {k:review.get('overview',{}).get('counts',{}).get(k,0)for k in expected}!=expected or set(review.get('overview',{}).get('counts',{}))-set(expected):raise Failure('invalid_document_result',502)
    statuses={'completed','completed_no_issue','applied_existing_contract','unresolved_proposal','failed','not_run','outside_coverage','partial'}
    if any(not isinstance(c,dict)or c.get('check_id')not in {'captured_source_forms','comma_prediction','general_linguistic_correctness','recorded_morphology','source_discovery','application_analysis','application_paradigms'}or c.get('status')not in statuses for c in review['checks']):raise Failure('invalid_document_result',502)
    return deepcopy(review)


def overview(review,locale='sl'):
    counts=review['overview']['counts']
    if locale=='it':
        return (f"Revisione delle fonti: {counts['qualified_advice']} indicazioni qualificate, "
            f"{counts['recorded_contrast']} confronti registrati, {counts['recorded_form_context']} riferimenti contestuali, {counts['executable_application']} proposte incluse, {counts.get('application_proposal',0)} applicazioni condizionate. "
            'Le indicazioni non sono correzioni automatiche. Controllare ambito e condizioni; non è una certificazione linguistica completa.')
    return (f"Pregled iz virov: {counts['qualified_advice']} kvalificiranih priporočil, "
        f"{counts['recorded_contrast']} zapisanih primerjav, {counts['recorded_form_context']} kontekstnih zapisov, {counts['executable_application']} vključenih predlogov, {counts.get('application_proposal',0)} pogojnih uporab. "
        'Nasveti niso samodejni popravki. Preverite obseg in pogoje; celovita jezikovna pravilnost ni potrjena.')


def report_blocks(review,locale='sl'):
    it=locale=='it'
    yield 'subheading','Revisione delle fonti senza autore generativo'if it else'Pregled iz virov brez generativnega pisca'
    yield 'body',overview(review,locale)
    rendered_advice={}
    for number,finding in enumerate(review['findings'],1):
        span=finding['match']['observed'];source=finding['source'];advice=source.get('recorded_advice')or{}
        identity=digest({k:finding.get(k)for k in ('source','notice','type','applicability','alternatives','remaining_conditions','proposed_edit','included_in_draft')})
        previous=rendered_advice.get(identity)
        rendered_advice.setdefault(identity,number)
        yield 'subheading',('Indicazione 'if it else'Ugotovitev ')+str(number)
        yield 'body',f"{span['start']}–{span['end']}: {span['text']}"
        if previous is None:yield 'body',finding['notice']
        else:yield 'body',('Lo stesso testo integrale e le condizioni della fonte sono riportati nell’indicazione '+str(previous)+'.'if it else'Enako celotno pojasnilo in pogoji istega vira so navedeni pri ugotovitvi '+str(previous)+'.')
        yield 'body',(('Proposta con prova di applicabilità inclusa nella bozza; da verificare.'if it else'Predlog z dokazom uporabe je vključen v osnutek; potreben je pregled.')if finding['included_in_draft']else('Originale conservato. Nessuna correzione eseguibile autorizzata.'if it else'Izvirnik je ohranjen. Izvedljivi popravek ni bil dovoljen.'))
        if finding.get('proposed_edit'):
            yield 'body',finding['proposed_edit']['original']+' → '+finding['proposed_edit']['replacement']
        for alternative in finding.get('alternatives',[]):
            yield 'body',('Alternativa condizionata: 'if it else'Pogojna možnost: ')+alternative['form']+' ('+({'singular':('singolare','ednina'),'dual':('duale','dvojina'),'plural':('plurale','množina')}.get(alternative.get('number'),('',''))[0 if it else 1])+')'
        if (source.get('native_source_unit')or{}).get('tool_kind')or (source.get('body_binding')or{}).get('capture_kind')=='terminology_record':
            yield 'body','Letture, sensi, alternative e misure originali sono conservati nella fonte acquisita; non sono sostituzioni automatiche.'if it else'Izvirne razlage, pomeni, možnosti in mere so ohranjeni v zajetem viru; to niso samodejne zamenjave.'
        elif previous is None and source['focus']['text']not in finding['notice']:
            yield 'subheading','Materiale pertinente della fonte'if it else'Ustrezno gradivo iz vira'
            yield 'quote',source['focus']['text']
        for key,label in [('recommendation_force','Moč priporočila'),('scope_note','Obseg in izjeme'),
                          ('application_scope_note','Pogoji uporabe'),('mapping_status','Uredniško stanje')]:
            if advice.get(key):
                labels={'recommendation_with_exception':('Raccomandazione con eccezione','Priporočilo z izjemo'),
                    'context_dependent':('Dipende dal contesto','Odvisno od sobesedila'),
                    'qualified_explanation':('Spiegazione con ambito limitato','Razlaga z omejenim obsegom'),
                    'proposed_needs_review':('Associazione editoriale proposta; ancora da revisionare','Predlagana uredniška povezava; še ni pregledana')}
                value=labels.get(str(advice[key]))
                yield 'body',label+': '+(value[0 if it else 1]if value else str(advice[key]))
        if source.get('untyped_context_limit'):yield 'body',('Il confronto registrato non è una regola eseguibile. Controlla il contesto completo nella fonte.'if it else'Zapisana primerjava ni izvedljivo pravilo. Preverite celotno sobesedilo v viru.')
    coverage=review.get('resource_coverage')
    if coverage:
        labels={'jezikovna_svetovalnica':'Jezikovna svetovalnica','epravopis':'ePravopis','cjvt-thesaurus':'Sopomenke','cjvt-ksss':'Kolokacije','sloleks':'Sloleks','loris':'LORIS','morph-it':'Morph-it!'}
        names=[labels.get(name,name)for name in coverage.get('captured_families',[])]
        if names:yield 'body',('Materiale acquisito: 'if it else'Zajeto gradivo: ')+', '.join(names)+'.'
        if coverage.get('bounded_or_partial'):yield 'body','La ricerca ha un ambito limitato; i risultati non certificano la correttezza del testo.'if it else'Iskanje ima omejen obseg; rezultat ne potrjuje pravilnosti besedila.'
    yield 'subheading','Controlli e limiti'if it else'Preverjanja in omejitve'
    names={'application_analysis':('Analisi contestuale dedicata','Namenska skladenjska analiza'),
        'application_paradigms':('Paradigmi registrati per le proposte','Zapisane paradigme za predloge'),
        'captured_source_forms':('Forme registrate nelle fonti','Zapisi iz virov'),
        'comma_prediction':('Controllo dedicato delle virgole','Namensko preverjanje vejic'),
        'recorded_morphology':('Candidati morfologici registrati','Zapisani oblikoslovni kandidati'),
        'source_discovery':('Ricerca delle fonti','Iskanje virov'),
        'general_linguistic_correctness':('Correttezza linguistica complessiva','Celovita jezikovna pravilnost')}
    states={'completed':('Eseguito nel solo ambito dichiarato','Opravljeno v navedenem obsegu'),
        'completed_no_issue':('Nessun problema segnalato dal controllo','Preverjanje ni javilo težave'),
        'applied_existing_contract':('Proposta giustificata da un’applicazione esistente','Predlog iz obstoječe izvedljive uporabe'),
        'unresolved_proposal':('Proposta irrisolta; originale conservato','Nerazrešen predlog; izvirnik ohranjen'),
        'failed':('Operazione non riuscita','Obdelava ni uspela'),'not_run':('Non eseguito','Ni opravljeno'),
        'outside_coverage':('Fuori copertura','Zunaj obsega'),'partial':('Copertura parziale','Delni obseg')}
    for check in review['checks']:
        yield 'body',names[check['check_id']][0 if it else 1]+': '+states[check['status']][0 if it else 1]
    yield 'body',('La ricerca è limitata alle fonti acquisite. Candidati lessicali e confronti non risolvono il senso o l’applicabilità. Le condizioni e i dettagli completi sono conservati con le fonti.'if it else
        'Iskanje je omejeno na zajete vire. Leksikalni kandidati in primerjave ne razrešijo pomena ali uporabnosti. Pogoji in podrobnosti so ohranjeni ob virih.')
