"""Display-only interpretation of existing checked outcomes, never validation."""
import json

VERSION='document-ai-outcomes-v1'
TITLES={
 'no_explanatory_evidence':('Ni dokazov za razlago; AI ni bila uporabljena.','Nessuna prova da spiegare; IA non utilizzata.'),
 'eligible_change':('Sprememba za človeško presojo','Modifica da valutare'),
 'valid_comment':('Komentar AI; ni sprememba besedila','Commento IA; non è una modifica del testo'),
 'rejected_item':('Zavrnjen izhod AI','Voce IA rifiutata'),
 'completed':('Obdelava AI je končana; glejte izide','Elaborazione IA conclusa; vedi gli esiti'),
 'only_rejected':('AI je vrnila samo zavrnjene vnose','L’IA ha restituito solo voci rifiutate'),
 'valid_empty':('AI ni vrnila dodatnih nasvetov; to ni potrdilo pravilnosti','Nessun consiglio aggiuntivo restituito; non certifica la correttezza'),
 'invalid_response':('AI je odgovorila, vendar odgovor ni veljaven','L’IA ha risposto, ma la risposta non è valida'),
 'unavailable':('Rezultat AI ni na voljo','Risultato IA non disponibile'),
 'unavailable_input_limit':('Ni rezultata: vnos presega omejitev AI koraka','Nessun risultato: testo oltre il limite IA'),
 'unavailable_dispatch_limit':('Ta del ni bil obdelan zaradi omejitve obdelave','Parte non elaborata per il limite di elaborazione'),
 'cancelled':('Obdelava je ustavljena; rezultat za ta del ni na voljo','Elaborazione interrotta; risultato non disponibile per questa parte'),
 'failed':('Obdelava ni uspela; rezultat ni na voljo','Elaborazione non riuscita; risultato non disponibile'),
 'pending':('Priprava še ni opravljena','Preparazione non ancora eseguita'),
 'preparing':('Priprava poteka','Preparazione in corso'),
 'prepared':('Pripravljeno; AI odgovor še ni zahtevan','Preparato; risposta IA non ancora richiesta'),
 'queued':('Čaka na obdelavo AI','In attesa di elaborazione IA'),
 'running':('Obdelava AI poteka','Elaborazione IA in corso')}
REASONS={
 'no_op':('Predlagano besedilo je enako obravnavanemu besedilu; spremembe ni.','Il testo proposto è identico a quello esaminato; non c’è alcuna modifica.'),
 'span_not_bound':('Vrnjeno besedilo se ne ujema z označenim mestom.','Il testo restituito non coincide con l’intervallo indicato.'),
 'unknown_section':('Vrnjeni odsek ne obstaja v tem pregledu.','La sezione restituita non esiste in questa revisione.'),
 'unknown_reference':('Sklic na vir ali ugotovitev ni veljaven za ta pregled.','Riferimento a fonte o indicazione non valido per questa revisione.'),
 'finding_comment_without_reference':('Komentar ugotovitve nima sklica na ugotovitev.','Il commento a un’indicazione non ne riporta il riferimento.'),
 'protected_values_changed':('Predlog spreminja zaščitene vrednosti ali oznake.','La proposta cambia valori o identificativi protetti.'),
 'protected_identifier_or_number':('Predlog posega v zaščiteno oznako ali število.','La proposta interviene su un identificativo o numero protetto.'),
 'invalid_document_character':('Predlog vsebuje znak, ki ni dovoljen v dokumentu.','La proposta contiene un carattere non consentito nel documento.'),
 'overlaps_evidence_change':('Predlog se prekriva s spremembo, že vključeno iz virov.','La proposta si sovrappone a una modifica già inclusa dalle fonti.'),
 'original_mapping_not_bound':('Mesta predloga ni mogoče natančno povezati z izvirnikom.','L’intervallo proposto non corrisponde esattamente all’originale.'),
 'quotation_or_address':('Predlog posega v zaščiten navedek ali naslov.','La proposta interviene su una citazione o un indirizzo protetto.'),
 'connected_identifier_or_compound':('Predlog razdeli povezano oznako ali sestavljeno besedo.','La proposta divide un identificativo o una parola composta.')}


def localized(pair,locale):return pair[1 if locale=='it'else 0]


def item_kind(item):
    if item.get('mechanical_issues'):return 'rejected_item'
    if item.get('replacement')is None:return 'valid_comment'
    return 'eligible_change'if item.get('eligible_for_accept')else'rejected_item'


def item_display(item):
    kind=item_kind(item);codes=list(item.get('mechanical_issues')or[])
    labels={}
    for locale in ('sl','it'):
        reasons=[{'code':code,'text':localized(REASONS.get(code,(
            'Drug zabeležen razlog mehanske zavrnitve.','Altro motivo registrato di rifiuto meccanico.')),locale)}for code in codes]
        labels[locale]={'title':('Spiegazione IA'if locale=='it'else'Razlaga AI')if item.get('writer_purpose')=='evidence_explanation'else localized(TITLES[kind],locale),'reasons':reasons}
    return {'kind':kind,'labels':labels}


def _counts(items,raw_count=None):
    kinds=[item_kind(i)for i in items]
    return {'returned_items':len(items)if raw_count is None else raw_count,'checked_items':len(items),
        'eligible_edits':kinds.count('eligible_change'),'valid_comments':kinds.count('valid_comment'),
        'rejected_items':kinds.count('rejected_item')}


def _raw_count(result,fallback):
    raw=result.get('raw_content')
    if isinstance(raw,str):
        try:value=json.loads(raw)
        except (ValueError,TypeError):return None
        if isinstance(value,dict)and isinstance(value.get('items'),list):return len(value['items'])
        return None
    return fallback


def count_text(counts,locale):
    n=lambda key:str(counts[key])if counts[key]is not None else'—'
    if locale=='it':return ('Voci restituite: '+n('returned_items')+' · modifiche valutabili: '+n('eligible_edits')+
        ' · commenti validi: '+n('valid_comments')+' · voci rifiutate: '+n('rejected_items'))
    return ('Vrnjeni vnosi: '+n('returned_items')+' · spremembe za presojo: '+n('eligible_edits')+
        ' · veljavni komentarji: '+n('valid_comments')+' · zavrnjeni vnosi: '+n('rejected_items'))


def presentation(ai,results=None):
    """Add separately derived labels/counts; never mutate items or result IDs.

    Returned counts include retained raw entries from invalid responses where
    their array size is known. Those are not invented checked/rejected items.
    Invalid responses remain a separate count and retain only recorded detail.
    """
    results=results or{};items=ai.get('items',[]);units={};invalid=0;unknown_raw=False;returned=0;legacy=None
    for unit in ai.get('units',[]):
        uid=unit['unit_id'];result=results.get(uid)or{}
        checked=result.get('items',[])if result else[i for i in items if i.get('unit_id')==uid]
        raw_count=_raw_count(result,len(checked));counts=_counts(checked,raw_count)
        if raw_count is None:counts['returned_items']=None;unknown_raw=True
        else:returned+=raw_count
        state=unit['state'];has_response=(result.get('inference_performed')is True or isinstance(result.get('raw_content'),str)
            or unit.get('inference_performed')is True)
        if state in {'completed','empty'}:
            if not checked:kind='valid_empty'
            elif counts['rejected_items']==len(checked):kind='only_rejected'
            else:kind='completed'
        elif state in {'unavailable','failed'}and has_response:kind='invalid_response';invalid+=1
        else:kind=state if state in TITLES else'unavailable'
        labels={locale:{'title':localized(TITLES[kind],locale),'counts':count_text(counts,locale)}for locale in ('sl','it')}
        units[uid]={'kind':kind,'counts':counts,'labels':labels}
        if isinstance(result.get('raw_content'),str):units[uid]['raw_content']=result['raw_content']
        failure=result.get('failure_type')or unit.get('failure_type')
        if failure:units[uid]['recorded_failure_type']=failure
    if not ai.get('units'):
        result=results.get('legacy')or{};returned=_raw_count(result,len(items));unknown_raw=returned is None
        if ai.get('state')=='unavailable'and(result.get('inference_performed')is True or isinstance(result.get('raw_content'),str)):
            invalid=1;legacy={'kind':'invalid_response','labels':{locale:{'title':localized(TITLES['invalid_response'],locale)}for locale in ('sl','it')}}
            if isinstance(result.get('raw_content'),str):legacy['raw_content']=result['raw_content']
            if result.get('failure_type'):legacy['recorded_failure_type']=result['failure_type']
    counts=_counts(items);counts.update(returned_items=None if unknown_raw else returned,invalid_responses=invalid)
    value={'version':VERSION,'counts':counts,'items':{i['suggestion_id']:item_display(i)for i in items},'units':units,
        'labels':{locale:{'counts':count_text(counts,locale),'invalid_responses':(
            ('Neveljavni odgovori AI: 'if locale=='sl'else'Risposte IA non valide: ')+str(invalid))}for locale in ('sl','it')}}
    if legacy is not None:value['legacy_response']=legacy
    return value
