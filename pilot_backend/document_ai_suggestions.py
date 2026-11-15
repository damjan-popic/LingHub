"""Optional document AI stage, saved separately from authoritative evidence."""
from copy import deepcopy
import hashlib,json,re
from .store import canonical,digest,stamp
from .document_source_body import source_body,body_binding
from .security import Failure
from .document_ai_contract import (VERSION,PACKET,AI_LABEL,EVIDENCE_LABEL,WARNING,checked_packet,checked_output,selected_sections,
    SCOPES,advice_plan,scoped_packet)

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
            'sections[].findings[].alternatives[] (where document offsets are present)',
            'sections[].retained_context[].occurrences','sections[].retained_context[].selection.occurrences',
            'sections[].lexical_context.options[].construction.observed','sections[].lexical_context.options[].construction.context',
            'sections[].lexical_context.options[].construction.witnesses'],
        'evidence_draft_unicode_codepoint_half_open':['sections[].coordinate_guide','new_ai_items'],
        'bound_source_original_text_unicode_codepoint_half_open':['sections[].findings[].source_focus',
            'sources[].recorded_advice[].source_span']}
    for section in value['sections']:
        for finding in section['findings']:
            finding['evidence_draft_relation']=('already_incorporated_historical_evidence'
                if finding.get('included_in_draft')else'historical_evidence_not_applied')
    return value

CONTEXT_POLICY='document-qualified-model-context-v8'


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
    """Nominate eligible whole units using existing native forms/observations.

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
        if spans and surface.normalized(head)==surface.normalized(text):
            return decision(True,'whole_section_publisher_head_operand_senses_unresolved',spans)
        # A bare head is insufficient. Independent source-owned examples or
        # existing recorded advice/construction cues below can still nominate
        # this whole dictionary unit without a deterministic finding.
        spans=[span for form in _source_examples(raw)+_source_examples(projection)for span in _whole_expression(form,text)]
        if spans:return decision(True,'publisher_source_owned_example_in_original',spans)
    cue=_native_review_cue(record,text,task['source_language'])
    occurrences=_documentary_review_occurrences(record,task)
    native=[o for o in occurrences if o['kind']=='recorded_table_form'and _whole_expression(o['text'],text)]
    return decision(cue>=2 or bool(native),'recorded_source_advice_or_native_table_occurrence'if cue>=2 or native else'peripheral_documentary_overlap_retained_in_trace',native)


def _optional_constructions(record,nomination,text,words):
    """Locate candidate constructions, not just their separated component lemmas.

    This is a stricter delivery choice than nomination, never a dependency,
    sense, grammatical agreement or correction certificate.
    """
    from .document_surface import normalized
    if nomination['reason']!='native_component_lemmas_in_original_sentence':
        return deepcopy(nomination['occurrences'])
    fields=(record['native_passage'].get('original_json')or{}).get('fields')or{}
    lemmas=[normalized(c['lemma'])for c in fields.get('components',[])if isinstance(c.get('lemma'),str)]
    if len(lemmas)<2 or len(lemmas)!=len(fields.get('components',[])):return []
    ordered=sorted(words,key=lambda w:w['start']);spans=[]
    for i in range(len(ordered)-len(lemmas)+1):
        window=ordered[i:i+len(lemmas)]
        if [normalized(w.get('lemma')or'')for w in window]!=lemmas:continue
        if window[0].get('sentence_id')is None or len({w.get('sentence_id')for w in window})!=1:continue
        if any(text[a['end']:b['start']].strip()for a,b in zip(window,window[1:])):continue
        a,b=window[0]['start'],window[-1]['end']
        if not any(a<=o['start']<o['end']<=b for o in nomination['occurrences']):continue
        spans.append({'start':a,'end':b,'text':text[a:b],
            'basis':'contiguous_ordered_native_component_lemmas_candidate_only'})
    return spans


def optional_context_selection(by_id,decisions,finding_sources,findings,text,words,lexical=None):
    """Select additional whole useful records; keep every other identity in trace.

    Required finding evidence is handled independently. Optional attestations
    inside an already-covered finding add no independent construction guidance.
    For one located construction, an additional record is still retained when
    it has different source facts, sense IDs, status or qualifications. This
    comparison is about delivered information, never native-record equivalence.
    """
    choices={};ranked=[];covered=[];lexical_members={}
    options=(lexical or{}).get('options',[])
    def construction_key(option):
        c=option['construction']
        return (c['kind'],tuple(sorted((w['start'],w['end'])for w in c['witnesses'])))
    phrase_choices={construction_key(o)for o in options if o['kind']=='collocations'}
    attested=[]
    for eid,nomination in decisions.items():
        if nomination.get('eligible')and nomination.get('reason')in {
                'recorded_whole_collocation_in_original','native_component_lemmas_in_original_sentence'}:
            attested.extend(_optional_constructions(by_id[eid],nomination,text,words))
    for option in options:
        c=option['construction'];witnesses=c['witnesses'];observed=c['observed']
        object_rename=(option['kind']=='synonyms'and c['kind']=='verb_object'
            and witnesses[0].get('upos')in {'NOUN','PROPN'}and witnesses[0].get('deprel')=='obj')
        independent=not(object_rename and(construction_key(option)in phrase_choices
            or any(s['start']<=observed['start']<observed['end']<=s['end']for s in attested)))
        reason=('source_specific_construction_choice'if independent else
            'object_head_renaming_adds_no_independent_guidance_to_recorded_phrase')
        for alternative in option['alternatives']:
            lexical_members.setdefault(alternative['evidence_id'],[]).append({
                'option_id':option['option_id'],'selected':independent,'reason':reason,
                'occurrence':deepcopy(observed)})
    for finding in findings:
        observed=(finding.get('match')or{}).get('observed')or{}
        if type(observed.get('start'))is int and type(observed.get('end'))is int:covered.append(observed)
    for eid,record in by_id.items():
        if eid in finding_sources:
            if eid in lexical_members:choices[eid]={'eligible':True,'selected':True,
                'reason':'required_finding_source','lexical_options':deepcopy(lexical_members[eid])}
            continue
        nomination=decisions[eid]
        members=lexical_members.get(eid,[]);active=[m for m in members if m['selected']]
        if active:
            nomination={'eligible':True,'reason':'original_bound_source_construction_choice',
                'occurrences':[deepcopy(m['occurrence'])for m in active],
                'applicability_not_established':True}
        choice={'eligible':nomination['eligible'],'selected':False,'reason':nomination['reason']}
        if members:
            choice['lexical_options']=deepcopy(members)
            if not active and not nomination['eligible']:choice['reason']=members[0]['reason']
        choices[eid]=choice
        if not nomination['eligible']:continue
        spans=_optional_constructions(record,nomination,text,words)
        collocation=record.get('capture_kind')=='ordinary_context_unit'and record['native_passage'].get('tool_kind')=='collocations'
        if collocation and not spans:
            choice['reason']='eligible_sentence_lemma_candidate_without_located_ordered_construction';continue
        if collocation and not active:
            spans=[s for s in spans if not any(o['start']<=s['start']<s['end']<=o['end']for o in covered)]
            if not spans:
                choice['reason']='optional_attestation_inside_required_finding_without_independent_construction';continue
        choice.update(occurrences=spans,nomination=deepcopy(nomination),
            applicability_not_established=True)
        if active:
            # A whole qualified choice can supply alternatives absent from the
            # original. Eligibility does not make every lookup sense mandatory.
            # Keep competing records in a selected group; do not merge owners.
            choice.update(selected=True,reason='additional_source_construction_choice')
            continue
        # Prefer a literal original expression, then located lemma candidates.
        # Retrieval order and frequency do not decide linguistic correctness.
        strength=1 if nomination['reason']=='native_component_lemmas_in_original_sentence'else 2
        ranked.append((-strength,-sum(s['end']-s['start']for s in spans),eid,record,collocation))
    delivered={}
    for _,__,eid,record,collocation in sorted(ranked):
        choice=choices[eid];spans=choice['occurrences'];facts=None
        if collocation:
            native=record['native_passage'];unit=native.get('original_json')or{}
            # Only binding/lookup mechanics are omitted from this information
            # comparison. All unknown fields and source-owned qualifiers stay.
            fields=deepcopy(unit.get('fields')or{})
            for key in ('headword','headword_key','headword_lexical_unit_id','match_relation','occurrence_id','provenance'):
                fields.pop(key,None)
            for component in fields.get('components',[]):
                component.pop('role',None);component.pop('role_origin',None)
            information=deepcopy(unit)
            for key in ('record_id','entry_id','target','match_relation'):information.pop(key,None)
            information['fields']=fields
            facts=digest({'source_slug':record['source_slug'],'source_revision':native.get('source_revision'),
                'information':information,
                'qualifications':{k:v for k,v in native.items()if k in {'source_status','edition','date','warnings','tool_scope','rights_mode'}}})
        keys=[(s['start'],s['end'],facts)for s in spans]if facts else[]
        if keys and all(k in delivered for k in keys):
            choice.update(reason='optional_construction_information_already_selected',
                selected_evidence_ids=sorted({delivered[k]for k in keys}),
                distinct_native_record_retained_in_frozen_trace=True,record_equivalence_asserted=False)
            continue
        choice.update(selected=True,reason='additional_located_construction_context'if collocation else'additional_qualified_source_guidance')
        for key in keys:delivered.setdefault(key,eid)
    return choices


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
            lexical=deepcopy(review.get('lexical_context'));lexical_source_ids=set()
            if lexical:
                if lexical.get('version')!='recorded-contextual-lexical-choices-v1':invalid()
            task={'source_text':section['text'],'mode':'review','source_language':document['options']['source_language']}
            words=_context_words((contextual_analyses or{}).get(section['index']),bundle,section['text'])
            decisions={eid:context_nomination(record,task,words)for eid,record in by_id.items()}
            findings=[];finding_sources=set();retained=[];omitted=[];omitted_findings=[]
            for finding in review['findings']:
                references=finding.get('related_source_captures')or[{'evidence_id':finding['source']['evidence_id']}]
                preserve_application=finding.get('included_in_draft')or finding.get('type')in {'executable_application','application_proposal'}
                # A signed, located complete native first-cell comparison is
                # already a useful qualified source relation. Preserve it
                # independently of broad raw-candidate nomination.
                match=finding.get('match')or{}
                located_relation=(finding.get('type')=='recorded_contrast'and match.get('basis')=='complete_native_cell'
                    and match.get('recorded_cell_order')=='first'and bool(finding.get('source',{}).get('native_table')))
                if not(preserve_application or located_relation)and not all(decisions.get(linked['evidence_id'],{}).get('eligible')for linked in references):
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
            selections=optional_context_selection(by_id,decisions,finding_sources,findings,section['text'],words,lexical)
            if lexical:
                selected_options=[];omitted_options=[]
                for option in lexical['options']:
                    members=[m for a in option['alternatives']for m in selections.get(a['evidence_id'],{}).get('lexical_options',[])
                        if m['option_id']==option['option_id']]
                    selected=bool(members)and all(m['selected']for m in members)
                    if not selected:
                        omitted_options.append({'option_id':option['option_id'],'kind':option['kind'],
                            'observed':deepcopy(option['construction']['observed']),
                            'reason':members[0]['reason']if members else'no_additional_source_construction_delivery',
                            'sources':[{'evidence_id':a['evidence_id'],'capture_sha256':a['capture_sha256'],'identity':deepcopy(a['identity'])}for a in option['alternatives']],
                            'meaning_ruled_out':False,'retained_in':'original_frozen_review.lexical_context'})
                        continue
                    for alternative in option['alternatives']:
                        record=by_id.get(alternative['evidence_id'])
                        if (not record or record['capture_sha256']!=alternative['capture_sha256']
                                or not record.get('model_context_eligible')
                                or record.get('rights_mode')in {'linked_only','metadata_only','unknown'}):invalid()
                        alternative['source_refs']=[retain(record,section['family_id'],'contextual_lexical_choice_not_edit_authority')]
                        lexical_source_ids.add(record['evidence_id'])
                        alternative.pop('source_facts',None)
                        alternative['source_facts_ref']='/sources/'+alternative['source_refs'][0]+'/original_text'
                    selected_options.append(option)
                lexical['nominated_option_count']=len(lexical['options'])
                lexical['options']=selected_options
                lexical['displayed_option_count']=len(selected_options)
                lexical['delivery']={'selected_options':len(selected_options),'omitted_options':omitted_options,
                    'scope':'whole optional choices selected for additional construction information; omissions do not rule out meanings'}
            for eid,record in by_id.items():
                if eid in finding_sources or eid in lexical_source_ids:continue
                nomination=decisions[eid];selection=selections[eid]
                if not selection['selected']:
                    omitted.append({'evidence_id':eid,'capture_sha256':record['capture_sha256'],'reason':selection['reason'],
                        **({'nomination':deepcopy(nomination),'selection':deepcopy(selection)}if nomination['eligible']else{})})
                    continue
                ordinary=record.get('capture_kind')=='ordinary_context_unit'
                key=retain(record,section['family_id'],'model_context_not_edit_authority')
                retained.append({'source_refs':[key],'role':'candidate_context_only'if ordinary else'qualified_source_context',
                    'applicability_not_established':True,'occurrences':deepcopy(nomination['occurrences']),
                    'nomination':deepcopy(nomination),
                    'selection':{k:deepcopy(v)for k,v in selection.items()if k!='nomination'},
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
            if lexical:sections[-1]['lexical_context']=lexical
            delivery.append({'section_index':section['index'],'finding_source_count':len(finding_sources),
                'lexical_choice_source_count':len(lexical_source_ids),
                'lexical_choice_option_count':len(lexical['options'])if lexical else 0,
                'eligible_additional_context_count':sum(s['eligible']for eid,s in selections.items()if eid not in finding_sources),
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
    if (old and old['packet']['evidence_manifest_sha256']==document['review_manifest']['manifest_sha256']
            and old['packet'].get('context_selection_policy')==CONTEXT_POLICY and old.get('plan',{}).get('version')==SCOPES):
        return _prepare_units(documents,owner,identifier,old)if old['state']=='preparing'else documents.get(owner,identifier)
    analyses=frozen_context_analyses(documents,owner,document)
    value=packet(documents,owner,document,analyses)
    plan=advice_plan(value)
    stage={'packet':value,'plan':plan,'prepared':{'preparation_sha256':None},'state':'preparing','created':stamp(),
        'family_id':None,'job_id':None,'run_id':None,'cancel_requested':False,
        'units':[{'unit':u,'packet':scoped_packet(value,plan,u)if u['dispatch_eligible']else None,'prepared':None,
            'state':'pending'if u['dispatch_eligible']else'unavailable_dispatch_limit',
            'family_id':None,'job_id':None,'run_id':None}for u in plan['units']]}
    with documents.store.tx()as db:
        row=documents.owned(db,owner,identifier);options=json.loads(row['options'])
        if old:options.setdefault('ai_history',[]).append({**old,'decisions':deepcopy(options.get('ai_decisions',{})),'decision_history':deepcopy(options.get('ai_decision_history',[]))})
        options.update(ai_review=stage,ai_decisions={},ai_decision_history=[])
        db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(canonical(options),identifier))
    return _prepare_units(documents,owner,identifier,stage)


def _save_stage(documents,owner,identifier,stage):
    with documents.store.tx()as db:
        row=documents.owned(db,owner,identifier);options=json.loads(row['options']);current=options['ai_review']
        if (current['packet']['packet_sha256']!=stage['packet']['packet_sha256']
                or current.get('plan',{}).get('plan_sha256')!=stage['plan']['plan_sha256']):invalid()
        if current.get('cancel_requested')and not stage.get('cancel_requested'):invalid()
        options['ai_review']=deepcopy(stage)
        db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(canonical(options),identifier))


def _prepare_units(documents,owner,identifier,stage):
    # Persist intent before each possible count. A process interruption never
    # rearms a possibly attempted preparation; pending units alone can resume.
    if stage.get('cancel_requested'):return documents.get(owner,identifier)
    for unit in stage['units']:
        if unit['state']=='preparing':
            unit.update(state='failed',failure_type='interrupted_preparation_no_retry');_save_stage(documents,owner,identifier,stage)
        if unit['state']!='pending':continue
        unit['state']='preparing';_save_stage(documents,owner,identifier,stage)
        try:
            value=unit['packet'];prepared=documents.domain.request_gateway.request('/internal/pilot/document-ai-suggestions/prepare',{'packet':value})
            if (prepared.get('version')!=VERSION or prepared.get('packet_sha256')!=value['packet_sha256']
                    or prepared.get('preparation_sha256')!=digest({k:v for k,v in prepared.items()if k!='preparation_sha256'})
                    or prepared.get('status')not in {'prepared','unavailable_input_limit'}):invalid()
            unit.update(prepared=prepared,state=prepared['status'])
        except (Failure,OSError,ValueError,KeyError,TypeError,TimeoutError)as error:
            unit.update(state='failed',failure_type=type(error).__name__)
        _save_stage(documents,owner,identifier,stage)
    stage['state']='prepared'if any(u['state']=='prepared'for u in stage['units'])else'unavailable'
    stage['prepared']={'preparation_sha256':digest({'plan':stage['plan']['plan_sha256'],
        'units':[{'unit_id':u['unit']['unit_id'],'state':u['state'],'preparation':u['prepared']}for u in stage['units']]})}
    _save_stage(documents,owner,identifier,stage);return documents.get(owner,identifier)

def start(documents,owner,identifier):
    if not documents.ai_suggestions:invalid()
    document=documents.get(owner,identifier,internal=True);stage=document['options'].get('ai_review')
    if not stage or stage['packet']['evidence_manifest_sha256']!=document['review_manifest']['manifest_sha256']:invalid()
    if 'plan'in stage:return _start_units(documents,owner,identifier,document,stage)
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


def _start_units(documents,owner,identifier,document,stage):
    if stage['plan']!=advice_plan(stage['packet']):invalid()
    if stage.get('cancel_requested'):return documents.get(owner,identifier)
    stage.update(started=True,scheduling=True);_save_stage(documents,owner,identifier,stage)
    for unit in stage['units']:
        if unit.get('family_id')or unit['state']!='prepared':continue
        if unit['packet']!=scoped_packet(stage['packet'],stage['plan'],unit['unit']):invalid()
        payload={'question':'Predlogi umetne inteligence za dokončani pregled dokumenta '+document['name'],
            'context':'','locale':document['options']['locale'],'input_language':'sl','task':'reference','model':documents.domain.default_model,
            'idempotency_key':_unit_key(identifier,unit)}
        try:accepted=documents.domain.create_family(owner,payload,document_ai={'packet':unit['packet'],'prepared':unit['prepared']})
        except Failure as error:
            if error.code not in {'queue_full','generation_waiting_capacity'}:raise
            stage['queue_issue']=error.code;break
        unit.update(family_id=accepted['family']['answer_family_id'],job_id=accepted['job']['job_id'],
            run_id=accepted['family']['short']['run_id'],state='queued')
        # The existing domain idempotency record survives a crash before this
        # save. Repeating start reattaches the same job; it never retries it.
        stage['state']='queued'
        if len(stage['units'])==1:
            for key in ('family_id','job_id','run_id'):stage[key]=unit[key]
        try:_save_stage(documents,owner,identifier,stage)
        except Failure:
            # Cancellation or a newer stage may have won after durable family
            # acceptance. Retire this accepted job through normal cancellation.
            documents.domain.cancel(owner,unit['job_id']);raise
    stage['scheduling']=any(u['state']=='prepared'and not u.get('family_id')for u in stage['units'])
    _save_stage(documents,owner,identifier,stage);return documents.get(owner,identifier)


def _unit_key(identifier,unit):return 'document-ai-'+identifier+'-'+unit['packet']['packet_sha256'][:32]


def advance_units(documents):
    """Continue already-started advice through the existing document scheduler.

    Only not-yet-linked prepared units advance. Completed, failed, ambiguous
    and cancelled jobs never return to the dispatch queue.
    """
    with documents.store.connect()as db:
        rows=[dict(r)for r in db.execute("SELECT id,owner FROM uploaded_documents WHERE state='complete' "
            "AND json_extract(options,'$.ai_review.scheduling')=1 ORDER BY created")]
    for row in rows:
        document=documents.get(row['owner'],row['id'],internal=True);stage=document['options']['ai_review']
        if stage.get('cancel_requested')or stage['packet']['evidence_manifest_sha256']!=document['review_manifest']['manifest_sha256']:
            stage['scheduling']=False;_save_stage(documents,row['owner'],row['id'],stage);continue
        _start_units(documents,row['owner'],row['id'],document,stage)

def validate_result(result,packet,prepared=None):
    if (not isinstance(result,dict)or result.get('version')!=VERSION or result.get('packet_sha256')!=packet['packet_sha256']
            or result.get('evidence_manifest_sha256')!=packet['evidence_manifest_sha256']
            or result.get('result_sha256')!=digest({k:v for k,v in result.items()if k!='result_sha256'})
            or result.get('origin')!='ai_generated'or result.get('authority')!='not_source_authorized'
            or result.get('status')not in {'available','unavailable'}):raise Failure('invalid_answer',502)
    if prepared is not None and result.get('preparation_sha256')!=prepared['preparation_sha256']:raise Failure('invalid_answer',502)
    if result['status']=='available':
        try:items=checked_output(json.loads(result['raw_content']),packet)
        except(ValueError,TypeError,KeyError):raise Failure('invalid_answer',502)from None
        if items!=result.get('items'):raise Failure('invalid_answer',502)
    elif result.get('items')!=[]:raise Failure('invalid_answer',502)
    return result

def public_review(documents,owner,document,options):
    stage=options.get('ai_review')
    if not stage:return None
    if 'plan'in stage:return _public_units(documents,owner,document,options,stage)
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


def _public_units(documents,owner,document,options,stage):
    packet=stage['packet'];plan=stage['plan'];manifest=(document.get('review_manifest')or{}).get('manifest_sha256')
    if plan!=advice_plan(packet)or [u['unit']for u in stage['units']]!=plan['units']:invalid()
    stale=packet['evidence_manifest_sha256']!=manifest
    summaries=[];raw=[];owners=[];terminal={'completed','empty','unavailable','unavailable_input_limit','unavailable_dispatch_limit','failed','cancelled'}
    for saved in stage['units']:
        unit=saved['unit'];summary={'unit_id':unit['unit_id'],'section_index':unit['section_index'],
            'original_span':unit['original_span'],'draft_span':unit['draft_span'],'source_refs':unit['source_refs'],
            'item_limit':unit['item_limit'],'state':saved['state'],'packet_sha256':(saved.get('packet')or{}).get('packet_sha256'),
            'preparation_sha256':(saved.get('prepared')or{}).get('preparation_sha256'),
            **{key:saved.get(key)for key in ('family_id','job_id','run_id','failure_type')}}
        if saved.get('family_id'):
            if not unit['dispatch_eligible']or saved['packet']!=scoped_packet(packet,plan,unit):invalid()
            with documents.store.connect()as db:
                row=db.execute('SELECT state,result FROM runs WHERE id=?AND family_id=?',(saved['run_id'],saved['family_id'])).fetchone()
            if row is None:invalid()
            summary['state']=row['state'];result=json.loads(row['result']or'{}').get('document_ai_suggestions')
            if row['state']=='complete':
                try:
                    validate_result(result,saved['packet'],saved['prepared'])
                    summary.update(state=('completed'if result['items']else'empty')if result['status']=='available'else'unavailable',
                        result_sha256=result['result_sha256'],attempted=result.get('attempted'),inference_performed=result.get('inference_performed'))
                    if result.get('failure_type'):summary['failure_type']=result['failure_type']
                    if result['status']=='available':
                        rows=json.loads(result['raw_content'])['items'];raw.extend(rows);owners.extend([unit['unit_id']]*len(rows))
                except (Failure,ValueError,TypeError,KeyError):summary.update(state='failed',failure_type='invalid_unit_result')
        summaries.append(summary)
    states=[s['state']for s in summaries];success=sum(s in {'completed','empty'}for s in states)
    complete=success==len(states);finished=all(s in terminal for s in states)
    if 'running'in states:state='running'
    elif 'queued'in states:state='queued'
    elif any(s in {'prepared','pending','preparing'}for s in states):state=('queued'if stage.get('started')else'prepared')if 'prepared'in states else'preparing'
    elif complete:state='available'
    elif success:state='partial'
    elif all(s=='cancelled'for s in states):state='cancelled'
    elif all(s=='unavailable_input_limit'for s in states):state='unavailable_input_limit'
    else:state='unavailable'
    items=checked_output({'items':raw},packet)
    for item,unit_id in zip(items,owners):item['unit_id']=unit_id
    result_hash=digest({'plan':plan['plan_sha256'],'units':summaries,'items':items})
    public={'version':VERSION,'state':state,'stale':stale,'coverage':packet['coverage'],'review_complete':complete,
        'partial':not complete,'units':summaries,'completed_units':success,'total_units':len(states),'finished':finished,
        'can_start':not stage.get('started')and not stage.get('cancel_requested')and any(u['state']=='prepared'and not u.get('family_id')for u in stage['units']),
        'can_prepare':stage['state']=='preparing'and not stage.get('cancel_requested'),
        'can_cancel':not finished and not stage.get('cancel_requested'),'cancel_requested':stage.get('cancel_requested',False),
        'global_suggestion_limit':plan['max_document_items'],'max_potential_dispatches':plan['max_potential_dispatches'],
        'plan_sha256':plan['plan_sha256'],'packet_sha256':packet['packet_sha256'],'evidence_manifest_sha256':packet['evidence_manifest_sha256'],
        'preparation_sha256':stage['prepared']['preparation_sha256'],'result_sha256':result_hash,
        'origin':'ai_generated','authority':'not_source_authorized','items':items,
        **{key:stage.get(key)for key in ('family_id','job_id','run_id')},
        'decisions':deepcopy(options.get('ai_decisions',{})),'selected_revision':None,
        'source_references':{key:{'title':s.get('title'),**s['reader']}for key,s in packet['sources'].items()}}
    accepted=[i['suggestion_id']for i in items if public['decisions'].get(i['suggestion_id'],{}).get('action')=='accept']
    if accepted and not stale and finished:
        try:sections=selected_sections(packet,items,public['decisions'])
        except ValueError:public['selection_issue']='overlapping_or_stale_selection';return public
        identity={'packet_sha256':packet['packet_sha256'],'ai_result_sha256':result_hash,
            'evidence_manifest_sha256':manifest,'decisions':public['decisions'],'sections':sections}
        public['selected_revision']={'revision_id':'U'+digest(identity),'accepted_ids':accepted,'sections':sections,
            'evidence_manifest_sha256':manifest,'ai_result_sha256':result_hash,'origin':'user_selected_unverified_ai',
            'acceptance_is_certification':False,'partial_advice':not complete}
    return public

def decide(documents,owner,identifier,payload):
    if not documents.ai_suggestions:invalid()
    if not isinstance(payload,dict)or set(payload)!={'suggestion_id','decision','result_sha256','evidence_manifest_sha256'}:invalid()
    if payload['decision']not in {'accept','dismiss','undecided'}:invalid()
    document=documents.get(owner,identifier,internal=True);public=document['ai_suggestions'];options=document['options']
    if (not public or public['state']not in {'available','partial'}or public['stale']or public.get('result_sha256')!=payload['result_sha256']
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
    if stage and 'plan'in stage:
        stage.update(cancel_requested=True,scheduling=False)
        # A family may have been durably accepted just before linking was
        # interrupted. The same existing idempotency record owns cancellation.
        with documents.store.connect()as db:
            for unit in stage['units']:
                if unit.get('family_id')or not unit.get('packet'):continue
                saved=db.execute("SELECT response FROM request_keys WHERE owner=?AND scope='family'AND key=?",
                    (owner,_unit_key(identifier,unit))).fetchone()
                if saved:
                    link=json.loads(saved[0]);unit.update(family_id=link['family_id'],job_id=link['job_id'],run_id=link['run_id'],state='queued')
        for unit in stage['units']:
            if not unit.get('job_id')and unit['state']in {'pending','preparing','prepared'}:unit['state']='cancelled'
        _save_stage(documents,owner,identifier,stage)
        for unit in stage['units']:
            if unit.get('job_id'):documents.domain.cancel(owner,unit['job_id'])
        return documents.get(owner,identifier)
    if not stage or not stage.get('job_id'):invalid()
    documents.domain.cancel(owner,stage['job_id']);return documents.get(owner,identifier)

def selected_export(documents,owner,identifier,fmt,revision,partial=False):
    from . import document_files as files
    document=documents.get(owner,identifier,internal=True);ai=document.get('ai_suggestions')or{};selected=ai.get('selected_revision')
    if fmt not in {'txt','docx'}or not selected or selected['revision_id']!=revision:raise Failure('document_ai_stale',409)
    if selected.get('partial_advice')and not partial:raise Failure('document_ai_partial',409)
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
        return label_partial_review(data,document['options']['locale'],ai=selected.get('partial_advice',False))
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
    response=gateway.request('/internal/pilot/document-ai-suggestions',bundle['document_ai']);validate_result(response,packet,bundle['document_ai']['prepared'])
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
    states.update(partial=('Delni AI pregled je pripravljen','Revisione IA parziale disponibile'),preparing=('Priprava AI pregleda','Preparazione della revisione IA'))
    yield 'body',('Stato: 'if it else'Stanje: ')+states.get(ai['state'],('Shranjeno stanje','Stato salvato'))[1 if it else 0]
    if ai.get('units'):
        yield 'body',('Revisione IA parziale: le parti senza risultato restano non riesaminate.'if it else'Delni AI pregled: deli brez rezultata ostajajo nepregledani.')if not ai['review_complete']else(
            'Tutte le parti assegnate hanno restituito un risultato; non è una certificazione linguistica.'if it else'Vsi dodeljeni deli so vrnili rezultat; to ni jezikovno potrdilo.')
        labels={'completed':('Predlogi pripravljeni','Suggerimenti pronti'),'empty':('Brez dodatnih predlogov','Nessun suggerimento aggiuntivo'),
            'unavailable_dispatch_limit':('Zunaj omejitve te obdelave','Oltre il limite di questa elaborazione')}
        for unit in ai['units']:
            yield 'body',('Sezione 'if it else'Odsek ')+str(unit['section_index']+1)+' · '+unit['original_span']['text']+' — '+labels.get(unit['state'],states.get(unit['state'],('Shranjeno stanje','Stato salvato')))[1 if it else 0]
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
