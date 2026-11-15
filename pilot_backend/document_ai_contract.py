"""Pure mechanical AI-suggestion contract, identical in CORE and WEB.

Mechanical eligibility never certifies meaning, grammar or source authority.
"""
from copy import deepcopy
from collections import Counter
import hashlib,json,re
from .document_protection import protected
from . import document_evidence_explanations as explanations
VERSION='klikai.document-ai-suggestions.v1'
PACKET='klikai.document-ai-packet.v1'
SCOPES='klikai.document-advice-scopes.v1'
MAX_DOCUMENT_ITEMS=24
MAX_DOCUMENT_DISPATCHES=8
EVIDENCE_LABEL='Predlogi na podlagi virov KLIKai'
AI_LABEL='Predlogi umetne inteligence — preverite pred uporabo'
WARNING='Predlogi umetne inteligence lahko vsebujejo napake ali spremenijo pomen. Pred uporabo jih preverite. Predlogi niso samodejno vključeni v besedilo.'
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sha(text):return hashlib.sha256(text.encode()).hexdigest()
def checked_packet(packet):
    if (not isinstance(packet,dict)or packet.get('version')!=PACKET
            or packet.get('packet_sha256')!=digest({k:v for k,v in packet.items()if k!='packet_sha256'})
            or packet.get('locale')not in {'sl','it'}or not re.fullmatch('[0-9a-f]{64}',str(packet.get('evidence_manifest_sha256')))
            or not re.fullmatch('[0-9a-f]{64}',str(packet.get('document_revision')))
            or not isinstance(packet.get('sections'),list)or not 1<=len(packet['sections'])<=100
            or not isinstance(packet.get('sources'),dict)):
        raise ValueError('AI packet identity differs')
    if packet.get('writer_purpose')not in (None,explanations.PURPOSE):raise ValueError('Unknown document writer purpose')
    ids=set()
    for section in packet['sections']:
        if (type(section.get('index'))is not int or section['index']in ids
                or section.get('original_sha256')!=sha(section['original_text'])
                or section.get('draft_sha256')!=sha(section['evidence_draft'])):
            raise ValueError('AI section binding differs')
        ids.add(section['index']);rendered=[];last=0
        for edit in section['evidence_edits']:
            a,b=edit['start'],edit['end']
            if not 0<=last<=a<=b<=len(section['original_text'])or section['original_text'][a:b]!=edit['original']:
                raise ValueError('Evidence edit map differs')
            rendered.extend([section['original_text'][last:a],edit['replacement']]);last=b
        if ''.join(rendered)+section['original_text'][last:]!=section['evidence_draft']:raise ValueError('Evidence draft differs')
    for key,source in packet['sources'].items():
        if (not re.fullmatch('S[0-9a-f]{16}',key)or source.get('body_sha256')!=sha(source['original_text'])):
            raise ValueError('Qualified source differs')
    for section in packet['sections']:
        _checked_lexical_context(section,packet['sources'])
    if packet.get('coverage',{}).get('provided_sections')!=[s['index']for s in packet['sections']]:raise ValueError('Coverage differs')
    relationships=packet.get('source_relationships')
    if relationships is not None and(relationships.get('version')!='document-evidence-relationships-v1'
            or relationships.get('snapshot_sha256')!=digest({k:v for k,v in relationships.items()if k!='snapshot_sha256'})
            or set(relationships.get('by_source',{}))!=set(packet['sources'])):raise ValueError('Frozen source relationships differ')
    if 'review_scope'in packet:_checked_review_scope(packet)
    return packet


def _checked_lexical_context(section,sources):
    """Bind optional phrase choices to original text and complete owned units.

    This is a mechanical reference check, never semantic approval.
    """
    context=section.get('lexical_context')
    if context is None:return
    if (context.get('version')!='recorded-contextual-lexical-choices-v1'
            or context.get('semantic_selection_performed')is not False
            or not isinstance(context.get('options'),list)or len(context['options'])>12):
        raise ValueError('Lexical context differs')
    text=section['original_text']
    for option in context['options']:
        if (option.get('error_asserted')is not False or option.get('replacement')is not None
                or option.get('frequency_comparison')is not None
                or not 1<=len(option.get('alternatives',[]))<=4):raise ValueError('Lexical choice authority differs')
        construction=option['construction']
        for span in [construction['observed'],construction['context']]+construction['witnesses']:
            a,b=span['start'],span['end']
            if type(a)is not int or type(b)is not int or not 0<=a<b<=len(text)or text[a:b]!=span['text']:
                raise ValueError('Lexical construction span differs')
        for alternative in option['alternatives']:
            refs=alternative.get('source_refs')
            if not isinstance(refs,list)or len(refs)!=1 or refs[0]not in sources:raise ValueError('Lexical source differs')
            source=sources[refs[0]]
            if (alternative.get('source_facts_ref')!='/sources/'+refs[0]+'/original_text'
                    or {'evidence_id':alternative['evidence_id'],'capture_sha256':alternative['capture_sha256']}not in source.get('related_captures',[])):
                raise ValueError('Lexical capture differs')
            unit=json.loads(source['original_text']);fields=unit.get('fields')or{}
            if option['kind']=='collocations':
                origin=fields.get('provenance')or{}
                identity=[unit['provenance'].get('version'),origin.get('build_id'),fields.get('occurrence_id'),fields.get('sense_id'),fields.get('structure_id')]
                bound=alternative.get('attested_text')==fields.get('surface')
            elif option['kind']=='synonyms':
                sense=fields.get('sense')or{}
                identity=[unit['provenance'].get('version'),sense.get('sense_id')]
                captured=[r['raw_text']for r in sense.get('relations',[])if r.get('relation_type')=='synonym'and isinstance(r.get('raw_text'),str)and r['raw_text'].strip()]
                bound=alternative.get('attested_texts')==captured[:3]
            else:raise ValueError('Lexical resource differs')
            if not bound or alternative.get('identity')!=identity:raise ValueError('Lexical attestation differs')

def overlap(a,b,c,d):return max(a,c)<min(b,d)or a==b and c<=a<=d or c==d and a<=c<=b

def original_span(section,start,end):
    """Map an unchanged evidence-draft range exactly through recorded edits.

    A proposal crossing an applied source change remains a comment until its
    conflict is resolved; it cannot independently reverse an evidence decision.
    """
    shift=0
    for edit in section['evidence_edits']:
        a=edit['start']+shift;b=a+len(edit['replacement'])
        if max(start,a)<min(end,b)or a==b and start<a<end:raise ValueError('overlaps_evidence_change')
        shift+=len(edit['replacement'])-(edit['end']-edit['start'])
    def point(value,side):
        shift=0
        for edit in section['evidence_edits']:
            a=edit['start']+shift;b=a+len(edit['replacement'])
            if value<a:return value-shift
            if value==a:
                if a==b and side=='start':
                    shift+=len(edit['replacement'])-(edit['end']-edit['start']);continue
                return edit['start']
            if value<b:raise ValueError('overlaps_evidence_change')
            if value==b and side=='end':return edit['end']
            shift+=len(edit['replacement'])-(edit['end']-edit['start'])
        return value-shift
    return point(start,'start'),point(end,'end')

def checked_output(raw,packet):
    checked_packet(packet)
    explanation=packet.get('writer_purpose')==explanations.PURPOSE
    anchors=[r['anchor_id']for r in raw.get('items',[])]if explanation and isinstance(raw,dict)and isinstance(raw.get('items'),list)and all(isinstance(r,dict)and 'anchor_id'in r for r in raw['items'])else[]
    if explanation:raw={'items':explanations.comment_rows(raw,packet)}
    scope=packet.get('review_scope');limit=scope['unit']['item_limit']if scope else MAX_DOCUMENT_ITEMS
    if not isinstance(raw,dict)or set(raw)!={'items'}or not isinstance(raw['items'],list)or len(raw['items'])>limit:
        raise ValueError('AI output shape differs')
    sections={s['index']:s for s in packet['sections']};known_findings={f['finding_id']for s in packet['sections']for f in s['findings']}
    result=[]
    fields={'kind','section_index','start','end','observed_text','replacement','explanation','uncertainty','finding_ids','source_refs'}
    for position,row in enumerate(raw['items']):
        if (not isinstance(row,dict)or set(row)!=fields or row['kind']not in {'finding_comment','additional_suggestion'}
                or type(row['section_index'])is not int or type(row['start'])is not int or type(row['end'])is not int
                or not isinstance(row['observed_text'],str)or not explanation and len(row['observed_text'])>3000
                or row['replacement']is not None and(not isinstance(row['replacement'],str)or len(row['replacement'])>3000)
                or not isinstance(row['explanation'],str)or not 1<=len(row['explanation'].strip())<=2000
                or not isinstance(row['uncertainty'],str)or len(row['uncertainty'])>1000
                or not all(isinstance(row[k],list)and len(row[k])<=64 and all(isinstance(v,str)for v in row[k])for k in ('finding_ids','source_refs'))
                or any(marker in row['explanation']for marker in ('<think>','</think>','|channel|'))):
            raise ValueError('Invalid AI item')
        if scope:
            unit=scope['unit'];span=unit['draft_span']
            if (row['section_index']!=unit['section_index']or not span['start']<=row['start']<row['end']<=span['end']
                    or not set(row['source_refs'])<=set(unit['source_refs'])
                    or not set(row['finding_ids'])<=set(unit['finding_ids'])):
                raise ValueError('AI item escapes assigned responsibility or delivered sources')
        flags=[];section=sections.get(row['section_index']);mapped=None
        if section is None:flags.append('unknown_section')
        elif (not 0<=row['start']<row['end']<=len(section['evidence_draft'])
                or section['evidence_draft'][row['start']:row['end']]!=row['observed_text']):flags.append('span_not_bound')
        else:
            if row['replacement']is not None:
                if row['replacement']==row['observed_text']:flags.append('no_op')
                replacement=row['replacement']
                if any(ord(c)<32 and c not in '\n\t\r' or 0xD800<=ord(c)<=0xDFFF for c in replacement):flags.append('invalid_document_character')
                before=section['evidence_draft'];after=before[:row['start']]+replacement+before[row['end']:]
                values=r'https?://[^\s<>"«»]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\{[^{}\n]+\}|\b[A-Za-z]{1,12}[-.:]\d[\w.:-]*\b|\d+(?:[.,:/-]\d+)*'
                if Counter(re.findall(values,before))!=Counter(re.findall(values,after)):flags.append('protected_values_changed')
                reason=protected(section['evidence_draft'],row['start'],row['end'])
                if reason:flags.append(reason)
                # Full identifiers/numbers are protected too; the source-form
                # boundary helper also guards matches inside connected IDs.
                for match in re.finditer(r'\b[A-Za-z]{1,12}[-.:]\d[\w.:-]*\b|\b\d+(?:[.,:/-]\d+)*\b',section['evidence_draft']):
                    if max(row['start'],match.start())<min(row['end'],match.end()):flags.append('protected_identifier_or_number');break
                try:
                    mapped=original_span(section,row['start'],row['end'])
                    if section['original_text'][mapped[0]:mapped[1]]!=row['observed_text']:flags.append('original_mapping_not_bound')
                except ValueError as error:flags.append(str(error))
        if not set(row['finding_ids'])<=known_findings or not set(row['source_refs'])<=set(packet['sources']):flags.append('unknown_reference')
        if row['kind']=='finding_comment'and not row['finding_ids']:flags.append('finding_comment_without_reference')
        item={**deepcopy(row),'suggestion_id':'A'+digest({'packet':packet['packet_sha256'],'position':position,'item':row})[:24],
            'origin':'ai_generated','authority':'not_source_authorized','draft_sha256':section['draft_sha256']if section else None,
            'mechanical_issues':list(dict.fromkeys(flags)),'eligible_for_accept':row['replacement']is not None and not flags,
            'original_span':{'start':mapped[0],'end':mapped[1]}if mapped is not None else None}
        if explanation:item.update(writer_purpose=explanations.PURPOSE,anchor_id=anchors[position])
        result.append(item)
    return result


def _draft_boundary(section,value,end=False):
    shift=0
    for edit in section['evidence_edits']:
        if value<edit['start']:break
        if value<edit['end']:return edit['start']+shift+(len(edit['replacement'])if end else 0)
        shift+=len(edit['replacement'])-(edit['end']-edit['start'])
    return value+shift


def advice_plan(parent):
    """Partition review responsibility from existing original-coordinate ties.

    Native sentence contexts stay whole. Overlapping constructions/findings
    and evidence edits connect them; uncovered text still receives an owner.
    This never resolves meanings, ranks sources, clips units or estimates fit.
    """
    checked_packet(parent)
    if 'review_scope'in parent:raise ValueError('Cannot scope another unit')
    units=[]
    for section in parent['sections']:
        text=section['original_text'];options=section.get('lexical_context',{}).get('options',[])
        findings=section['findings'];contexts=section.get('retained_context',[])
        spans=[(o['construction']['context']['start'],o['construction']['context']['end'])for o in options]
        if not spans:spans=[(0,len(text))]
        def observed(row):
            candidates=row.get('occurrences')or[row.get('match',{}).get('observed',{})]
            return [(s['start'],s['end'])for s in candidates if type(s.get('start'))is int
                and type(s.get('end'))is int and 0<=s['start']<=s['end']<=len(text)]
        # Unknown/unlocated source responsibility must remain whole-section;
        # never silently orphan a selected record through a guessed sentence.
        links=[observed(row)or[(0,len(text))]for row in findings+contexts]
        spans.extend(span for row in links for span in row)
        spans.extend((e['start'],e['end'])for e in section['evidence_edits'])
        if not spans:spans=[(0,len(text))]
        def merged(values):
            result=[]
            for a,b in sorted(values,key=lambda span:(span[0],span[1])):
                if result and(a<result[-1][1]or a==b==result[-1][1]):result[-1][1]=max(result[-1][1],b)
                else:result.append([a,b])
            return result
        groups=merged(spans);gaps=[];last=0
        for a,b in groups+[[len(text),len(text)]]:
            fragment=text[last:a];left=len(fragment)-len(fragment.lstrip());right=len(fragment.rstrip())
            if right>left:gaps.append((last+left,last+right))
            last=max(last,b)
        groups=merged(groups+gaps)
        for a,b in groups:
            intersects=lambda spans:any(max(a,c)<min(b,d)or a==b==c==d for c,d in spans)
            owned_options=[o for o in options if intersects([(o['construction']['context']['start'],o['construction']['context']['end'])])]
            owned_findings=[f for f in findings if intersects(observed(f)or[(0,len(text))])]
            owned_contexts=[i for i,c in enumerate(contexts)if intersects(observed(c)or[(0,len(text))])]
            refs={r for o in owned_options for alt in o['alternatives']for r in alt['source_refs']}
            refs.update(r for f in owned_findings for r in f['source_refs'])
            refs.update(r for i in owned_contexts for r in contexts[i]['source_refs'])
            da,db=_draft_boundary(section,a),_draft_boundary(section,b,True)
            units.append({'section_index':section['index'],'original_span':{'start':a,'end':b,'text':text[a:b]},
                'draft_span':{'start':da,'end':db,'text':section['evidence_draft'][da:db]},
                'source_refs':sorted(refs),'finding_ids':[f['finding_id']for f in owned_findings],
                'lexical_option_ids':[o['option_id']for o in owned_options],'retained_context_indices':owned_contexts})
    if set(parent['sources'])!={r for u in units for r in u['source_refs']}:raise ValueError('Selected source has no review owner')
    runnable=min(len(units),MAX_DOCUMENT_DISPATCHES)
    for index,unit in enumerate(units):
        unit['dispatch_eligible']=index<runnable
        unit['item_limit']=(MAX_DOCUMENT_ITEMS//runnable+(index<MAX_DOCUMENT_ITEMS%runnable))if index<runnable else 0
        unit['unit_id']='U'+digest({'parent':parent['packet_sha256'],'unit':unit})[:24]
    plan={'version':SCOPES,'parent_packet_sha256':parent['packet_sha256'],'document_id':parent['document_id'],
        'document_revision':parent['document_revision'],'evidence_manifest_sha256':parent['evidence_manifest_sha256'],
        'max_document_items':MAX_DOCUMENT_ITEMS,'max_potential_dispatches':MAX_DOCUMENT_DISPATCHES,'units':units}
    plan['plan_sha256']=digest(plan);return plan


def scoped_packet(parent,plan,unit):
    if plan!=advice_plan(parent)or unit not in plan['units']:raise ValueError('Frozen review plan differs')
    value=deepcopy(parent);value.pop('packet_sha256')
    value['sources']={r:deepcopy(parent['sources'][r])for r in unit['source_refs']}
    if 'source_relationships'in value:
        relations=value['source_relationships'];relations['by_source']={r:relations['by_source'][r]for r in unit['source_refs']}
    for section in value['sections']:
        owned=section['index']==unit['section_index'];section['context_only']=not owned
        section['findings']=[f for f in section['findings']if owned and f['finding_id']in unit['finding_ids']]
        section['retained_context']=[c for i,c in enumerate(section.get('retained_context',[]))if owned and i in unit['retained_context_indices']]
        lexical=section.get('lexical_context')
        if lexical:
            lexical['options']=[o for o in lexical['options']if owned and o['option_id']in unit['lexical_option_ids']]
            if not lexical['options']:section.pop('lexical_context')
            else:
                lexical['displayed_option_count']=len(lexical['options'])
                lexical['parent_lexical_context_ref']='/sections/'+str(section['index'])+'/lexical_context'
                lexical['delivery']={'selected_options':len(lexical['options']),'omitted_options':[],
                    'scope':'Complete choices owned by this responsibility. Other selected choices and original omission reasons stay in the bound parent packet; no meaning is ruled out.'}
    if 'source_relationships'in value:
        relations=value['source_relationships']
        for ref,data in relations['by_source'].items():
            hashes={f.get('source_focus',{}).get('text_sha256')for s in value['sections']for f in s['findings']if ref in f['source_refs']}
            data['recorded_facts']=[f for f in data['recorded_facts']if f['role']!='bounded_project_application_policy_not_linguistic_certification'
                or f['value']['row_sha256']in hashes]
        relations['snapshot_sha256']=digest({k:v for k,v in relations.items()if k!='snapshot_sha256'})
    value['coverage'].pop('resource_context_delivery',None)
    value['coverage'].update(scope='All section texts are read-only context except the explicit assigned draft span. Only owned complete selected sources are delivered.',
        parent_coverage_ref='full_parent_packet.coverage',review_complete=False)
    value['review_scope']={'version':SCOPES,'parent_packet_sha256':parent['packet_sha256'],
        'parent_document_revision':parent['document_revision'],'parent_evidence_manifest_sha256':parent['evidence_manifest_sha256'],
        'plan_sha256':plan['plan_sha256'],'unit':deepcopy(unit),
        'context_rule':'All original/evidence-draft texts and edit maps remain visible. Review only assigned span, in draft coordinates. Other text is context, never another editable section.',
        'source_rule':'Cite only delivered owned source_refs; generated advice from another unit is never evidence.'}
    value['packet_sha256']=digest(value);return checked_packet(value)


def _checked_review_scope(packet):
    scope=packet['review_scope'];unit=scope['unit'];section=next((s for s in packet['sections']if s['index']==unit['section_index']),None)
    if section is None:raise ValueError('Review section differs')
    if (scope.get('version')!=SCOPES or scope.get('parent_document_revision')!=packet['document_revision']
            or scope.get('parent_evidence_manifest_sha256')!=packet['evidence_manifest_sha256']
            or any(not re.fullmatch('[0-9a-f]{64}',str(scope.get(k)))for k in ('parent_packet_sha256','plan_sha256'))
            or unit['unit_id']!='U'+digest({'parent':scope['parent_packet_sha256'],'unit':{k:v for k,v in unit.items()if k!='unit_id'}})[:24]
            or unit['source_refs']!=sorted(packet['sources'])or type(unit['item_limit'])is not int
            or not 0<=unit['item_limit']<=MAX_DOCUMENT_ITEMS or type(unit['dispatch_eligible'])is not bool
            or unit['dispatch_eligible']!=(unit['item_limit']>0)):raise ValueError('Review responsibility differs')
    for field,text in [('original_span',section['original_text']),('draft_span',section['evidence_draft'])]:
        span=unit[field];a,b=span['start'],span['end']
        if type(a)is not int or type(b)is not int or not 0<=a<=b<=len(text)or span['text']!=text[a:b]:raise ValueError('Review span differs')
    if (unit['draft_span']['start']!=_draft_boundary(section,unit['original_span']['start'])
            or unit['draft_span']['end']!=_draft_boundary(section,unit['original_span']['end'],True)):
        raise ValueError('Review edit mapping differs')

def selected_sections(packet,items,decisions):
    checked_packet(packet);by_section={s['index']:[]for s in packet['sections']}
    for item in items:
        if decisions.get(item['suggestion_id'],{}).get('action')!='accept':continue
        if not item['eligible_for_accept']:raise ValueError('Suggestion is not mechanically eligible')
        by_section[item['section_index']].append(item)
    selected=[]
    for section in packet['sections']:
        chosen=sorted(by_section[section['index']],key=lambda x:(x['start'],x['end']))
        for i,item in enumerate(chosen):
            if any(overlap(item['start'],item['end'],other['start'],other['end'])for other in chosen[:i]):raise ValueError('overlapping_suggestions')
        edits=deepcopy(section['evidence_edits'])
        for item in chosen:
            a,b=item['original_span']['start'],item['original_span']['end']
            if section['original_text'][a:b]!=item['observed_text']:raise ValueError('Original mapping differs')
            edits.append({'start':a,'end':b,'original':item['observed_text'],'replacement':item['replacement'],
                          'explanation':item['explanation'],'ai_suggestion_id':item['suggestion_id']})
        if len(edits)>64:raise ValueError('selected_edit_limit')
        edits.sort(key=lambda x:(x['start'],x['end']));parts=[];last=0
        for edit in edits:
            if edit['start']<last:raise ValueError('overlapping_original_edits')
            parts.extend([section['original_text'][last:edit['start']],edit['replacement']]);last=edit['end']
        final=''.join(parts)+section['original_text'][last:]
        # Independently reconstruct through the frozen draft coordinate base.
        expected=section['evidence_draft']
        for item in reversed(chosen):expected=expected[:item['start']]+item['replacement']+expected[item['end']:]
        if final!=expected:raise ValueError('Selected reconstruction differs')
        selected.append({'index':section['index'],'location':section['location'],'text':section['original_text'],'state':'complete',
            'document_result':{'status':'draft','mode':'review','source_sha256':section['original_sha256'],'proposed_text':final,'edits':edits}})
    return selected
