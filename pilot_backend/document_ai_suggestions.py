"""Optional document AI stage, saved separately from authoritative evidence."""
from copy import deepcopy
import hashlib,json,re
from .store import canonical,digest,stamp
from .document_source_body import source_body,body_binding
from .security import Failure
from .document_ai_contract import VERSION,PACKET,AI_LABEL,EVIDENCE_LABEL,WARNING,checked_packet,checked_output,selected_sections

CONTRACT='klikai.document-ai-job.v1'
def invalid():raise Failure('document_ai_unavailable',409)
def _options(documents,owner,identifier):
    with documents.store.connect()as db:
        row=documents.owned(db,owner,identifier);return json.loads(row['options']or'{}')
def label_coordinates(value):
    """Add representation labels to a new packet; never rewrite historical fields."""
    value['coordinate_base_scope']='new_ai_items_only'
    value['coordinate_spaces']={
        'original_text_unicode_codepoint_half_open':['sections[].evidence_edits',
            'sections[].findings[].match.observed','sections[].findings[].proposed_edit',
            'sections[].findings[].alternatives[] (where document offsets are present)'],
        'evidence_draft_unicode_codepoint_half_open':['sections[].coordinate_guide','new_ai_items'],
        'bound_source_original_text_unicode_codepoint_half_open':['sections[].findings[].source_focus',
            'sources[].recorded_advice[].source_span']}
    for section in value['sections']:
        for finding in section['findings']:
            finding['evidence_draft_relation']=('already_incorporated_historical_evidence'
                if finding.get('included_in_draft')else'historical_evidence_not_applied')
    return value

CONTEXT_POLICY='document-qualified-model-context-v2'


def _context_words(projection,bundle,text):
    """Consume the CORE-owned validated observation; never parse citation prose."""
    if not projection:return []
    expected=digest({k:v for k,v in projection.items()if k!='projection_sha256'})
    syntax=(bundle.get('review_observations')or{}).get('syntax')or{}
    if (projection.get('projection_sha256')!=expected
            or projection.get('source_sha256')!=hashlib.sha256(text.encode()).hexdigest()
            or projection.get('syntax_capture_sha256')!=digest(syntax)):invalid()
    observation=projection.get('native_observation')
    if projection.get('status')!='available'or observation is None:return []
    if observation.get('input_text')!=text or observation.get('input_sha256')!=projection['source_sha256']:invalid()
    return observation.get('analysis',{}).get('words',[])


def _whole_expression(form,text,occurrences=None):
    from . import document_surface as surface
    if not isinstance(form,str)or not form.strip():return []
    words=re.findall(r"[^\W_]+(?:[-’'][^\W_]+)*",form)
    if len(words)<2 and surface.normalized(form)!=surface.normalized(text):return []
    found=list(surface.occurrences(form,text))
    if occurrences is not None:
        found=[span for span in found if any(span['start']<=o['start']<o['end']<=span['end']for o in occurrences)]
    return found


def _source_examples(value):
    # Only explicitly named publisher example fields nominate an expression.
    # Relation targets and arbitrary metadata prose are not construction proof.
    values=[]
    for examples in [value.get('examples'),(value.get('sense')or{}).get('examples')]:
        if isinstance(examples,str):values.append(examples)
        elif isinstance(examples,list):
            values.extend(item if isinstance(item,str)else item.get('text')for item in examples if isinstance(item,(str,dict)))
    return [v for v in values if isinstance(v,str)and v]


def context_nomination(record,task,words):
    """Select whole qualified units using existing native forms/observations.

    A candidate connection is useful context, never a resolved sense, grammar
    judgment, applicability proof or permission to substitute the source form.
    """
    from . import document_surface as surface
    from .document_source_body import source_topics
    from .document_transform import _native_term_surfaces,_native_review_cue,_documentary_review_occurrences
    raw=record['capture'];text=task['source_text'];ordinary=record.get('capture_kind')=='ordinary_context_unit'
    def decision(eligible,reason,occurrences=None,**extra):return {'eligible':bool(eligible),'reason':reason,
        'occurrences':deepcopy(occurrences or[]),'applicability_not_established':True,**extra}
    if record.get('rights_mode')in {'linked_only','metadata_only','unknown'}:return decision(False,'linked_or_unavailable_content')
    eligible=(record.get('model_context_eligible')or record.get('context_eligible'))if ordinary else record.get('context_eligible')
    if not eligible:return decision(False,(record.get('document_admission')or{}).get('reason')or'native_capture_not_admitted')
    if ordinary:
        p=record['native_passage'];unit=p.get('original_json')or{};fields=unit.get('fields')or{};kind=p.get('tool_kind')
        observed=record.get('admitted_occurrences',record.get('occurrences',[]))
        if kind=='collocations':
            literal=_whole_expression(fields.get('surface'),text,observed)
            if literal:return decision(True,'recorded_whole_collocation_in_original',literal)
            components=fields.get('components')or[]
            lemmas=[surface.normalized(c['lemma'])for c in components if isinstance(c,dict)and isinstance(c.get('lemma'),str)and c['lemma']]
            head=fields.get('headword')
            nominations=[]
            if len(lemmas)>=2 and len(lemmas)==len(components)and isinstance(head,str):
                for occurrence in observed:
                    anchors=[w for w in words if w.get('start')==occurrence['start']and w.get('end')==occurrence['end']and isinstance(w.get('lemma'),str)and surface.normalized(w['lemma'])==surface.normalized(head)]
                    for anchor in anchors:
                        sentence=anchor.get('sentence_id')
                        if sentence is None:continue
                        selected=[w for w in words if w.get('sentence_id')==sentence and isinstance(w.get('lemma'),str)]
                        if all(any(surface.normalized(w['lemma'])==lemma for w in selected)for lemma in lemmas):
                            nominations.append({'start':occurrence['start'],'end':occurrence['end'],'text':occurrence['text'],
                                'native_sentence_id':sentence,'source_component_lemmas':lemmas,
                                'observation_role':'analyzer_candidate_only_not_dependency_or_sense_certification'})
            return decision(bool(nominations),'native_component_lemmas_in_original_sentence'if nominations else'no_local_collocation_construction_nomination',nominations)
        if kind=='synonyms':
            forms=_source_examples(fields)
            spans=[span for form in forms for span in _whole_expression(form,text,observed)]
            if spans:return decision(True,'source_owned_example_in_original',spans)
            if observed and surface.normalized(text)==surface.normalized(observed[0]['text']):
                return decision(True,'whole_section_lexical_operand_senses_unresolved',observed)
            return decision(False,'unscoped_synonym_senses_retained_in_candidate_trace')
        if kind:
            if kind=='morphology'and observed and surface.normalized(text)==surface.normalized(observed[0]['text']):
                return decision(True,'whole_section_recorded_form_operand_ambiguity_retained',observed)
            return decision(False,'routine_'+kind+'_observation_without_document_application_nomination')
        if p.get('role')=='expert_answer':
            spans=[span for form in source_topics(record)for span in _whole_expression(form,text)]
            allowed=[span for span in spans if any(o['start']<=span['start']<span['end']<=o['end']for o in observed)]
            return decision(bool(allowed),'complete_authored_source_expression_in_original'if allowed else'no_authored_topic_expression_nomination',allowed)
        return decision(False,'native_reference_without_specific_document_nomination')
    if record.get('capture_kind')=='terminology_record':
        spans=[span for form in _native_term_surfaces(raw,task['source_language'])for span in _whole_expression(form,text)]
        return decision(bool(spans),'source_owned_terminology_expression_in_original'if spans else'isolated_terminology_alias_without_document_scope',spans)
    if record.get('capture_kind')=='editorial_record':
        spans=[span for form in source_topics(record)for span in _whole_expression(form,text)]
        return decision(bool(spans),'complete_editorial_expression_in_original'if spans else'no_editorial_topic_expression_nomination',spans)
    projection=raw.get('original_projection')or{}
    head=projection.get('headword')or raw.get('published_headword')
    if isinstance(head,str)and head:
        spans=list(surface.occurrences(head,text))
        if spans:return decision(True,'publisher_entry_head_in_original_sense_unresolved',spans)
    cue=_native_review_cue(record,text,task['source_language'])
    occurrences=_documentary_review_occurrences(record,task)
    native=[o for o in occurrences if o['kind']=='recorded_advice_phrase'or raw.get('native_table_projection')and o['kind']=='recorded_table_form']
    return decision(cue>=2 or bool(native),'recorded_source_advice_or_native_table_occurrence'if cue>=2 or native else'peripheral_documentary_overlap_retained_in_trace',native)


def frozen_context_analyses(documents,owner,document):
    """One pure CORE validation per retained original syntax capture."""
    result={}
    with documents.store.connect()as db:
        for section in document['sections']:
            row=db.execute('SELECT bundle FROM families WHERE id=?AND owner=?',(section['family_id'],owner)).fetchone()
            bundle=json.loads(row[0]or'{}')if row else{};syntax=(bundle.get('review_observations')or{}).get('syntax')
            if not syntax:continue
            projected=documents.domain.request_gateway.request('/internal/pilot/document-resource-context/project',{'captures':[],
                'contextual_syntax':{'source_text':section['text'],'source_sha256':hashlib.sha256(section['text'].encode()).hexdigest(),'syntax':syntax}})
            if projected.get('generation')!={'attempted':False,'inference_performed':False}:invalid()
            result[section['index']]=projected['contextual_analysis']
    return result


def packet(documents,owner,document,contextual_analyses=None):
    if (document['state']!='complete'or document['options'].get('review_strategy')!='evidence_review_v1'
            or not document.get('review_manifest')):invalid()
    from .document_resource_context import records as ordinary_records
    from .document_transform import _documentary_review_occurrences,_native_review_cue
    sections=[];sources={};delivery=[]
    def retain(record,family,role):
        raw=record['capture'];body=source_body(record)
        if not body:invalid()
        native={key:deepcopy(raw[key])for key in ('title','source_revision','source_status','source_date','date','edition','release_id',
            'normative_status','coverage_note','rights_mode','url','language','geographical_scope','jurisdiction','domain','register')if key in raw}
        native.update(source_slug=record['source_slug'],external_id=record['external_id'],rights_mode=record.get('rights_mode',raw.get('rights_mode','unknown')),original_text=body,
            body_sha256=hashlib.sha256(body.encode()).hexdigest())
        if raw.get('source_unit'):
            unit=deepcopy(raw['source_unit']);bound=body_binding(record)
            projection=unit.get('writer_projection')or{}
            if projection.get('version')=='lexical-field-projection-v1':
                # The normal writer_unit text already retains every linguistic
                # field/variant/qualification. Original JSON and the mechanical
                # field map stay in the frozen reader; do not duplicate them in
                # the effective input or invent a new excerpt/sense selector.
                proof={k:deepcopy(v)for k,v in projection.items()if k!='field_map'}
                proof.update(field_map_sha256=digest(projection.get('field_map',[])),field_map_available_in_frozen_reader=True)
                unit.pop('original_json',None);unit.pop('text',None);unit['writer_projection']=proof
                bound={**bound,'writer_projection':proof}
                native['input_representation']='complete_existing_ordinary_writer_unit_with_qualified_native_metadata'
            native.update(native_source_unit=unit,body_binding=bound,
                context_use=record.get('context_use'),native_answer_eligibility=record.get('context_eligible'))
        elif not isinstance(raw.get('original_text'),str):native.update(body_binding=body_binding(record),source_owned_fields=deepcopy(raw))
        key='S'+digest(native)[:16]
        if key not in sources:sources[key]={**native,'recorded_advice':[],'related_captures':[],
            'admission_roles':[],'reader':{'family_id':family,'evidence_id':record['evidence_id']}}
        linked={'evidence_id':record['evidence_id'],'capture_sha256':record['capture_sha256']}
        if linked not in sources[key]['related_captures']:sources[key]['related_captures'].append(linked)
        if role not in sources[key]['admission_roles']:sources[key]['admission_roles'].append(role)
        return key
    with documents.store.connect()as db:
        for section in document['sections']:
            result=section.get('document_result')or{};review=result.get('review')
            if section['state']!='complete'or result.get('status')!='draft'or not review:invalid()
            row=db.execute('SELECT bundle FROM families WHERE id=?AND owner=?',(section['family_id'],owner)).fetchone()
            bundle=json.loads(row[0]or'{}')if row else{};context=(bundle.get('review_observations')or{}).get('ordinary_resources')
            by_id={r['evidence_id']:r for r in bundle.get('source_bundle',{}).get('matches',[])}
            by_id.update({r['evidence_id']:r for r in ordinary_records(context,section['text'],hashlib.sha256(section['text'].encode()).hexdigest())})
            task={'source_text':section['text'],'mode':'review','source_language':document['options']['source_language']}
            words=_context_words((contextual_analyses or{}).get(section['index']),bundle,section['text'])
            decisions={eid:context_nomination(record,task,words)for eid,record in by_id.items()}
            findings=[];finding_sources=set();retained=[];omitted=[];omitted_findings=[]
            for finding in review['findings']:
                references=finding.get('related_source_captures')or[{'evidence_id':finding['source']['evidence_id']}]
                authoritative=finding.get('included_in_draft')or finding.get('type')in {'executable_application','application_proposal'}
                if not authoritative and not all(decisions.get(linked['evidence_id'],{}).get('eligible')for linked in references):
                    omitted_findings.append({'finding_id':finding['finding_id'],'finding_sha256':digest(finding),
                        'reason':'historical_lookup_finding_not_fully_locally_nominated','source_decisions':{linked['evidence_id']:decisions.get(linked['evidence_id'])for linked in references}})
                    continue
                source=finding['source'];record=by_id.get(source['evidence_id'])
                if record is None or record['capture_sha256']!=source['capture_sha256']:invalid()
                key=retain(record,section['family_id'],'finding_evidence');finding_sources.add(source['evidence_id'])
                refs=[key]
                for linked in finding.get('related_source_captures',[]):
                    alternate=by_id.get(linked['evidence_id'])
                    if alternate is None or alternate['capture_sha256']!=linked['capture_sha256']:invalid()
                    ref=retain(alternate,section['family_id'],'finding_evidence');finding_sources.add(linked['evidence_id'])
                    if ref not in refs:refs.append(ref)
                advice=source.get('recorded_advice')
                if advice is not None and advice not in sources[key]['recorded_advice']:sources[key]['recorded_advice'].append(deepcopy(advice))
                kept={k:deepcopy(finding[k])for k in ('finding_id','type','applicability','match','notice','proposed_edit','included_in_draft','remaining_conditions','alternatives')if k in finding}
                kept.update(source_refs=refs,source_focus=deepcopy(source['focus']),source_qualifications=deepcopy(source['qualifications']))
                findings.append(kept)
            for eid,record in by_id.items():
                if eid in finding_sources:continue
                nomination=decisions[eid]
                if not nomination['eligible']:
                    omitted.append({'evidence_id':eid,'capture_sha256':record['capture_sha256'],'reason':nomination['reason']})
                    continue
                ordinary=record.get('capture_kind')=='ordinary_context_unit'
                key=retain(record,section['family_id'],'model_context_not_edit_authority')
                retained.append({'source_refs':[key],'role':'candidate_context_only'if ordinary else'qualified_source_context',
                    'applicability_not_established':True,'occurrences':deepcopy(nomination['occurrences']),
                    'nomination':deepcopy(nomination),
                    'native_occurrence_bindings_sha256':digest(record.get('native_bindings',[])),
                    'native_occurrence_bindings_location':'saved source reader bibliography.native_context_bindings',
                    'native_answer_eligibility':record.get('context_eligible')})
            draft=result['proposed_text']
            sections.append({'index':section['index'],'location':section['location'],'original_text':section['text'],
                'original_sha256':hashlib.sha256(section['text'].encode()).hexdigest(),'evidence_draft':draft,
                'draft_sha256':hashlib.sha256(draft.encode()).hexdigest(),
                'evidence_edits':[{k:deepcopy(e[k])for k in ('start','end','original','replacement')}for e in result['edits']],
                'findings':findings,'retained_context':retained,'evidence_manifest_sha256':review['manifest_sha256'],
                'checks':[{'check_id':c['check_id'],'status':c['status']}for c in review['checks']],
                'coordinate_guide':[{'start':m.start(),'end':m.end(),'text':m.group()}for m in re.finditer(r'\S+',draft)]})
            delivery.append({'section_index':section['index'],'finding_source_count':len(finding_sources),
                'additional_context_count':len(retained),'omitted':omitted,'omitted_findings':omitted_findings,
                'original_finding_count':len(review['findings']),'provided_finding_count':len(findings),
                'contextual_analysis_projection_sha256':((contextual_analyses or{}).get(section['index'])or{}).get('projection_sha256'),
                'ordinary_context_present':context is not None,'source_trace_location':'saved section bundle review_observations.ordinary_resources'})
    value={'version':PACKET,'document_id':document['document_id'],'document_revision':document['source_sha256'],
        'evidence_manifest_sha256':document['review_manifest']['manifest_sha256'],'locale':document['options']['locale'],
        'coordinate_base':'frozen_evidence_draft_unicode_codepoint_half_open','sections':sections,'sources':sources,
        'coverage':{'provided_sections':[s['index']for s in sections],'omitted_sections':[],
            'scope':'All extracted text sections supplied. Source context is bounded, qualified and not applicability or comprehensive review.',
            'resource_context_delivery':delivery,'document_warnings':deepcopy(document['warnings'])},
        'authority':'AI comments/suggestions only; original source findings and evidence draft remain unchanged'}
    value['context_selection_policy']=CONTEXT_POLICY
    label_coordinates(value);value['packet_sha256']=digest(value);return checked_packet(value)

def prepare(documents,owner,identifier):
    if not documents.ai_suggestions:invalid()
    document=documents.get(owner,identifier,internal=True);old=document['options'].get('ai_review')
    if old and old['packet']['evidence_manifest_sha256']==document['review_manifest']['manifest_sha256']and old['packet'].get('context_selection_policy')==CONTEXT_POLICY:return documents.get(owner,identifier)
    analyses=frozen_context_analyses(documents,owner,document)
    value=packet(documents,owner,document,analyses)
    prepared=documents.domain.request_gateway.request('/internal/pilot/document-ai-suggestions/prepare',{'packet':value})
    if (prepared.get('version')!=VERSION or prepared.get('packet_sha256')!=value['packet_sha256']
            or prepared.get('preparation_sha256')!=digest({k:v for k,v in prepared.items()if k!='preparation_sha256'})
            or prepared.get('status')not in {'prepared','unavailable_input_limit'}):invalid()
    stage={'packet':value,'prepared':prepared,'state':prepared['status'],'created':stamp(),'family_id':None,'job_id':None,'run_id':None}
    with documents.store.tx()as db:
        row=documents.owned(db,owner,identifier);options=json.loads(row['options'])
        if old:options.setdefault('ai_history',[]).append({**old,'decisions':deepcopy(options.get('ai_decisions',{})),'decision_history':deepcopy(options.get('ai_decision_history',[]))})
        options.update(ai_review=stage,ai_decisions={},ai_decision_history=[])
        db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(canonical(options),identifier))
    return documents.get(owner,identifier)

def start(documents,owner,identifier):
    if not documents.ai_suggestions:invalid()
    document=documents.get(owner,identifier,internal=True);stage=document['options'].get('ai_review')
    if not stage or stage['packet']['evidence_manifest_sha256']!=document['review_manifest']['manifest_sha256']:invalid()
    if stage.get('family_id'):return documents.get(owner,identifier)
    if stage['state']!='prepared':invalid()
    payload={'question':'Predlogi umetne inteligence za dokončani pregled dokumenta '+document['name'],
        'context':'','locale':document['options']['locale'],'input_language':'sl','task':'reference','model':documents.domain.default_model,
        'idempotency_key':'document-ai-'+identifier+'-'+stage['packet']['packet_sha256'][:32]}
    accepted=documents.domain.create_family(owner,payload,document_ai={'packet':stage['packet'],'prepared':stage['prepared']})
    with documents.store.tx()as db:
        row=documents.owned(db,owner,identifier);options=json.loads(row['options']);current=options['ai_review']
        if current['packet']['packet_sha256']!=stage['packet']['packet_sha256']:invalid()
        current.update(family_id=accepted['family']['answer_family_id'],job_id=accepted['job']['job_id'],
                       run_id=accepted['family']['short']['run_id'],state='queued')
        db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(canonical(options),identifier))
    return documents.get(owner,identifier)

def validate_result(result,packet):
    if (not isinstance(result,dict)or result.get('version')!=VERSION or result.get('packet_sha256')!=packet['packet_sha256']
            or result.get('evidence_manifest_sha256')!=packet['evidence_manifest_sha256']
            or result.get('result_sha256')!=digest({k:v for k,v in result.items()if k!='result_sha256'})
            or result.get('origin')!='ai_generated'or result.get('authority')!='not_source_authorized'
            or result.get('status')not in {'available','unavailable'}):raise Failure('invalid_answer',502)
    if result['status']=='available':
        try:items=checked_output(json.loads(result['raw_content']),packet)
        except(ValueError,TypeError,KeyError):raise Failure('invalid_answer',502)from None
        if items!=result.get('items'):raise Failure('invalid_answer',502)
    elif result.get('items')!=[]:raise Failure('invalid_answer',502)
    return result

def public_review(documents,owner,document,options):
    stage=options.get('ai_review')
    if not stage:return None
    packet=stage['packet'];manifest=(document.get('review_manifest')or{}).get('manifest_sha256')
    stale=packet['evidence_manifest_sha256']!=manifest
    public={'version':VERSION,'state':stage['state'],'stale':stale,'coverage':packet['coverage'],
        'packet_sha256':packet['packet_sha256'],'evidence_manifest_sha256':packet['evidence_manifest_sha256'],
        'preparation_sha256':stage['prepared']['preparation_sha256'],'origin':'ai_generated','authority':'not_source_authorized',
        'family_id':stage.get('family_id'),'job_id':stage.get('job_id'),'run_id':stage.get('run_id'),'items':[],
        'decisions':deepcopy(options.get('ai_decisions',{})),'selected_revision':None,
        'source_references':{key:{'title':s.get('title'),**s['reader']}for key,s in packet['sources'].items()}}
    if not stage.get('family_id'):return public
    with documents.store.connect()as db:
        row=db.execute("SELECT state,result FROM runs WHERE id=?AND family_id=?",(stage['run_id'],stage['family_id'])).fetchone()
    if row is None:invalid()
    public['state']=row['state'];saved=json.loads(row['result']or'{}');result=saved.get('document_ai_suggestions')
    if row['state']!='complete'or result is None:return public
    validate_result(result,packet);public.update(state=result['status'],result_sha256=result['result_sha256'],items=deepcopy(result['items']))
    if result.get('failure_type'):public['failure_type']=result['failure_type']
    accepted=[i['suggestion_id']for i in result['items']if public['decisions'].get(i['suggestion_id'],{}).get('action')=='accept']
    if accepted and not stale:
        try:sections=selected_sections(packet,result['items'],public['decisions'])
        except ValueError:public['selection_issue']='overlapping_or_stale_selection';return public
        identity={'packet_sha256':packet['packet_sha256'],'ai_result_sha256':result['result_sha256'],
                  'evidence_manifest_sha256':manifest,'decisions':public['decisions'],'sections':sections}
        public['selected_revision']={'revision_id':'U'+digest(identity),'accepted_ids':accepted,'sections':sections,
            'evidence_manifest_sha256':manifest,'ai_result_sha256':result['result_sha256'],'origin':'user_selected_unverified_ai',
            'acceptance_is_certification':False}
    return public

def decide(documents,owner,identifier,payload):
    if not documents.ai_suggestions:invalid()
    if not isinstance(payload,dict)or set(payload)!={'suggestion_id','decision','result_sha256','evidence_manifest_sha256'}:invalid()
    if payload['decision']not in {'accept','dismiss','undecided'}:invalid()
    document=documents.get(owner,identifier,internal=True);public=document['ai_suggestions'];options=document['options']
    if (not public or public['state']!='available'or public['stale']or public.get('result_sha256')!=payload['result_sha256']
            or public['evidence_manifest_sha256']!=payload['evidence_manifest_sha256']):raise Failure('document_ai_stale',409)
    item=next((i for i in public['items']if i['suggestion_id']==payload['suggestion_id']),None)
    if item is None:invalid()
    if payload['decision']=='accept'and not item['eligible_for_accept']:raise Failure('document_ai_ineligible',422)
    decisions=deepcopy(options.get('ai_decisions',{}));prior=decisions.get(item['suggestion_id'])
    choice={'action':payload['decision'],'changed_at':stamp(),'actor':owner,'result_sha256':public['result_sha256'],
            'evidence_manifest_sha256':public['evidence_manifest_sha256'],'origin':'human_decision_on_unverified_ai'}
    decisions[item['suggestion_id']]=choice
    try:selected_sections(options['ai_review']['packet'],public['items'],decisions)
    except ValueError:raise Failure('document_ai_overlap',409)from None
    with documents.store.tx()as db:
        row=documents.owned(db,owner,identifier);latest=json.loads(row['options'])
        if latest.get('ai_decisions',{})!=options.get('ai_decisions',{}):raise Failure('document_ai_stale',409)
        history=latest.setdefault('ai_decision_history',[])
        if len(history)>=1000:invalid()
        history.append({'suggestion_id':item['suggestion_id'],'previous':prior,'decision':choice})
        latest['ai_decisions']=decisions
        db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(canonical(latest),identifier))
    return documents.get(owner,identifier)

def cancel(documents,owner,identifier):
    options=_options(documents,owner,identifier);stage=options.get('ai_review')
    if not stage or not stage.get('job_id'):invalid()
    documents.domain.cancel(owner,stage['job_id']);return documents.get(owner,identifier)

def selected_export(documents,owner,identifier,fmt,revision,partial=False):
    from . import document_files as files
    document=documents.get(owner,identifier,internal=True);ai=document.get('ai_suggestions')or{};selected=ai.get('selected_revision')
    if fmt not in {'txt','docx'}or not selected or selected['revision_id']!=revision:raise Failure('document_ai_stale',409)
    assembled={**document,'sections':[{**old,**new}for old,new in zip(document['sections'],selected['sections'])]}
    if partial:
        if fmt!='docx':invalid()
        assembled=files.partial_review_document(assembled)
    else:files.draft_blocks(assembled)
    if fmt=='txt':return files.draft_text(assembled)
    with documents.store.connect()as db:
        original=db.execute('SELECT package FROM document_originals WHERE document_id=?',(identifier,)).fetchone()
    if original:
        from .document_word import rewrite
        data=rewrite(original[0],assembled['sections'])
    else:data=files.draft_docx(assembled)
    if partial:
        from .document_word import label_partial_review
        return label_partial_review(data,document['options']['locale'])
    return data

def freeze(gateway,family,policy):
    value=deepcopy(family.get('request_options',{}).get('document_ai'));checked_packet(value['packet'])
    body={'contract':CONTRACT,'document_ai':value,'retrieval':{'status':'retained_evidence','coverage':value['packet']['coverage']}}
    body['snapshot_sha256']=digest(body)
    return {'bundle':body,'identity':{'kind':CONTRACT,'packet_sha256':value['packet']['packet_sha256'],
            'captured_response_sha256':body['snapshot_sha256']}}

def generate(gateway,bundle,tier,model,parent=None):
    if tier not in {'short','answer'}or parent is not None:raise Failure('expansion_unavailable',409)
    if bundle.get('contract')!=CONTRACT or bundle.get('snapshot_sha256')!=digest({k:v for k,v in bundle.items()if k!='snapshot_sha256'}):invalid()
    packet=bundle['document_ai']['packet'];checked_packet(packet)
    response=gateway.request('/internal/pilot/document-ai-suggestions',bundle['document_ai']);validate_result(response,packet)
    text=AI_LABEL+'\n'+WARNING+'\n'+('Predlogi so pripravljeni za posamezni pregled.'if response['status']=='available'else'Predlogi umetne inteligence niso na voljo. Pregled iz virov ostaja uporaben.')
    return {'status':'ok','short_answer':text,'answer_md':text,'answer_kind':'ai_suggestions','generation_status':'generated'if response.get('inference_performed')is True else'uncertain_dispatch'if response.get('attempted')else'not_generated',
        'document_ai_suggestions':response,'evidence':[],'source_badges':[],'blocks':[{'block_id':'ai-suggestions-label','text':text}],
        'execution':{'scope':'optional_ai_suggestions','writer_inference_performed':response.get('inference_performed'),'completion_attempted':response.get('attempted'),
            'evidence_review_unchanged':True},'diagnostics':{'writer':{'ai_suggestions':response}},'limitations':[],'retrieval':bundle['retrieval']}

def report_blocks(ai,locale='sl',api_root='/api/pilot'):
    it=locale=='it'
    yield 'subheading','Suggerimenti dell’intelligenza artificiale — verificare prima dell’uso'if it else AI_LABEL
    yield 'body','Opzione sperimentale: suggerimenti IA non verificati.'if it else'Poskusna možnost: nepreverjeni AI predlogi.'
    yield 'body','I suggerimenti IA possono contenere errori o cambiare il significato. Verificali prima di usarli. Non sono inseriti automaticamente nel testo.'if it else WARNING
    if not ai:
        yield 'body','Non richiesti.'if it else'Niso bili zahtevani.';return
    states={'prepared':('Pripravljeno za zahtevo','Pronto per la richiesta'),'queued':('Čaka na AI obdelavo','In attesa di elaborazione IA'),
        'running':('AI obdelava poteka','Elaborazione IA in corso'),'available':('Predlogi so pripravljeni za pregled','Suggerimenti pronti per la revisione'),
        'unavailable':('AI predlogi niso na voljo; pregled iz virov ostaja uporaben','Suggerimenti IA non disponibili; la revisione delle fonti resta utilizzabile'),
        'unavailable_input_limit':('Besedilo presega omejitev AI koraka; pregled iz virov ostaja uporaben','Testo oltre il limite IA; la revisione delle fonti resta utilizzabile'),
        'failed':('AI obdelava ni uspela; pregled iz virov ostaja uporaben','Elaborazione IA non riuscita; la revisione delle fonti resta utilizzabile'),
        'cancelled':('AI obdelava je ustavljena','Elaborazione IA interrotta')}
    yield 'body',('Stato: 'if it else'Stanje: ')+states.get(ai['state'],('Shranjeno stanje','Stato salvato'))[1 if it else 0]
    if ai.get('stale'):yield 'body','Risultato precedente; non applicabile alla revisione attuale.'if it else'Zgodovinski rezultat; ni uporaben za sedanjo revizijo.'
    for item in ai['items']:
        action=ai['decisions'].get(item['suggestion_id'],{}).get('action','undecided')
        labels={'accept':('Accettato dall’utente; IA non verificata','Sprejel uporabnik; nepreverjen AI predlog'),
            'dismiss':('Scartato','Zavrnjen'),'undecided':('Da decidere','Neodločen')}
        yield 'subheading',('Suggerimento IA'if it else'Predlog umetne inteligence')+' · '+labels[action][0 if it else 1]
        yield 'body',item['observed_text']
        if item['replacement']is not None:yield 'body',item['observed_text']+' → '+item['replacement']
        yield 'body',item['explanation']
        if item['uncertainty']:yield 'body',('Valutazione dell’incertezza del modello: 'if it else'Modelova ocena negotovosti: ')+item['uncertainty']
        if item['mechanical_issues']:yield 'body','Non applicabile automaticamente al testo; controllare posizione o contenuto protetto.'if it else'Ni mogoče vključiti v besedilo; preverite mesto ali zaščiteno vsebino.'
        for ref in item['source_refs']:
            source=ai.get('source_references',{}).get(ref)
            if source:
                from urllib.parse import quote
                yield 'link',('Fonte citata dall’IA; non prova l’applicabilità'if it else'Vir, ki ga navaja AI; ne dokazuje uporabnosti',
                    api_root+'/families/'+quote(source['family_id'],safe='')+'/sources/'+quote(source['evidence_id'],safe='')+'?tier=answer')
    selected=ai.get('selected_revision')
    if selected:
        yield 'subheading','Bozza con suggerimenti IA selezionati dall’utente'if it else'Osnutek s predlogi umetne inteligence po izboru uporabnika'
        yield 'body',('Revisione selezionata:\n'if it else'Izbrana revizija:\n')+selected['revision_id']
        yield 'body','L’accettazione non è una certificazione linguistica.'if it else'Sprejem ni jezikovno potrdilo.'
        for section in selected['sections']:yield 'body',section['document_result']['proposed_text']
