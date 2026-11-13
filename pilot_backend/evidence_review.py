"""Validation and deterministic presentation of writer-independent findings."""
from copy import deepcopy
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
                or finding['finding_id']in seen or finding.get('type')not in {'qualified_advice','recorded_contrast','recorded_form_context','executable_application'}
                or type(a)is not int or type(b)is not int or not 0<=a<b<=3000
                or text is not None and text[a:b]!=original.get('text')
                or finding.get('type')!='executable_application'and(finding.get('proposed_edit')is not None or finding.get('included_in_draft')is not False)):
            raise Failure('invalid_document_result',502)
        if (finding.get('document_revision')!=review.get('document_revision')or finding.get('document_location')!=review.get('document_location')):
            raise Failure('invalid_document_result',502)
        seen.add(finding['finding_id'])
        if finding['type']=='executable_application':
            proof=finding.get('application_proof')or{}
            if (proof.get('adapter_version')!='document-existing-applications.v1'
                    or proof.get('proof_sha256')!=digest({k:v for k,v in proof.items()if k!='proof_sha256'})
                    or proof.get('writer_inference_performed')is not False
                    or finding.get('included_in_draft')is not True
                    or finding.get('proposed_edit')not in review.get('applications',{}).get('edits',[])):
                raise Failure('invalid_document_result',502)
        source=finding.get('source')or{}
        if matches is not None:
            record=matches.get(source.get('evidence_id'));raw=(record or{}).get('capture')or{}
            body=raw.get('original_text');focus=source.get('focus')or{}
            left,right=focus.get('char_start'),focus.get('char_end')
            if (record is None or record['capture_sha256']!=source.get('capture_sha256')
                    or not isinstance(body,str)or type(left)is not int or type(right)is not int
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
    expected={kind:sum(f['type']==kind for f in review['findings'])for kind in ('qualified_advice','recorded_contrast','recorded_form_context','executable_application')}
    if review.get('overview',{}).get('counts')!=expected:raise Failure('invalid_document_result',502)
    statuses={'completed','completed_no_issue','applied_existing_contract','unresolved_proposal','failed','not_run','outside_coverage','partial'}
    if any(not isinstance(c,dict)or c.get('check_id')not in {'captured_source_forms','comma_prediction','general_linguistic_correctness','recorded_morphology','source_discovery'}or c.get('status')not in statuses for c in review['checks']):raise Failure('invalid_document_result',502)
    return deepcopy(review)


def overview(review,locale='sl'):
    counts=review['overview']['counts']
    if locale=='it':
        return (f"Revisione delle fonti: {counts['qualified_advice']} indicazioni qualificate, "
            f"{counts['recorded_contrast']} confronti registrati, {counts['recorded_form_context']} riferimenti contestuali, {counts['executable_application']} modifiche da applicazioni esistenti. "
            'Le indicazioni non sono correzioni automatiche. Controllare ambito e condizioni; non è una certificazione linguistica completa.')
    return (f"Pregled iz virov: {counts['qualified_advice']} kvalificiranih priporočil, "
        f"{counts['recorded_contrast']} zapisanih primerjav, {counts['recorded_form_context']} kontekstnih zapisov, {counts['executable_application']} sprememb iz obstoječih izvedljivih uporab. "
        'Nasveti niso samodejni popravki. Preverite obseg in pogoje; celovita jezikovna pravilnost ni potrjena.')


def report_blocks(review,locale='sl'):
    it=locale=='it'
    yield 'subheading','Revisione delle fonti senza autore generativo'if it else'Pregled iz virov brez generativnega pisca'
    yield 'body',overview(review,locale)
    for finding in review['findings']:
        span=finding['match']['observed'];source=finding['source'];advice=source.get('recorded_advice')or{}
        yield 'subheading',('Indicazione 'if it else'Ugotovitev ')+finding['finding_id']
        yield 'body',f"{span['start']}–{span['end']}: {span['text']}"
        yield 'body',finding['notice']
        if finding.get('document_revision'):yield 'body',('Revisione documento: 'if it else'Revizija dokumenta: ')+finding['document_revision']
        if source.get('native_table'):yield 'body',('Posizione nella fonte: 'if it else'Mesto v viru: ')+str(source['native_table']['locator'])
        yield 'body',(('Modifica proposta da un’applicazione esistente; da verificare.'if it else'Predlog iz obstoječe izvedljive uporabe; potreben je pregled.')if finding['included_in_draft']else('Originale conservato. Nessuna correzione eseguibile autorizzata.'if it else'Izvirnik je ohranjen. Izvedljivi popravek ni bil dovoljen.'))
        yield 'subheading','Materiale pertinente della fonte'if it else'Ustrezno gradivo iz vira'
        yield 'quote',source['focus']['text']
        for key,label in [('recommendation_force','Moč priporočila'),('scope_note','Obseg in izjeme'),
                          ('application_scope_note','Pogoji uporabe'),('mapping_status','Uredniško stanje')]:
            if advice.get(key):yield 'body',label+': '+str(advice[key])
        if source.get('untyped_context_limit'):yield 'body',source['untyped_context_limit']
    yield 'subheading','Controlli e limiti'if it else'Preverjanja in omejitve'
    names={'captured_source_forms':('Forme registrate nelle fonti','Zapisi iz virov'),
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
