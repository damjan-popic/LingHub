"""One source/target decision for writer evidence and deterministic reader cards.

Candidates are annotated, never deleted. Source editorial state is independent
of grammatical register, normative authority and contextual correctness.
"""
import copy,hashlib,json

VERSION='lexical-focus-v2-20261018'

def editorial(fields):
    key=next((k for k in ('source_status','head_status','status')if fields.get(k)),None)
    raw=fields.get(key)if key else None
    normalized={'manual':'reviewed','ssss-manual':'reviewed','automatic':'automatic',
                'ssss-automatic':'automatic'}.get(str(raw).lower(),'unknown')
    return {'raw':raw,'field':key,'normalized':normalized,'mapping_version':'editorial-status-v1',
            'scope':'source editorial process; not register or normative approval'}

def focus_profile(profile):
    """Return a new annotated revision; old saved captures remain untouched."""
    p=copy.deepcopy(profile);plan=p['interpretation'];targets={t['text']:t for t in plan['targets']}
    if plan.get('selection',{}).get('version')==VERSION:return p
    translation='translation'in plan['requested_facets'];focus=plan.get('slot_focus')or{}
    acronym_targets={t for t in targets if t.isupper()and len(t)>1 and any(u['kind']=='terminology'and any(m.get('term')==t for m in u['fields'].get('matched_forms',[]))for u in p['evidence'])}
    lexemes={}
    for part in p['facets']['morphology'].get('data')or[]:
        for le in (part.get('result')or{}).get('lexemes',[]):lexemes[le['source_entry_id']]=le
    alternatives=set(targets);shared=set()
    if len(alternatives)>1 and focus:
        for ident,le in lexemes.items():
            if any(all(s['normalized_features'].get(k)==v for k,v in focus.items()if k in {'case','number'}and v)
                   and alternatives<={v['form']for v in s['variants']}for s in le['slots']):shared.add(ident)
        # An explicit name spelling and PROPN context resolve folded common nouns.
        if any(t[:1].isupper()for t in targets):
            proper={i for i in shared if lexemes[i]['grammatical_features'].get('type')=='proper'}
            if proper:shared=proper
    chosen={}
    for part in p['facets']['morphology'].get('data')or[]:
        les=(part.get('result')or{}).get('lexemes',[])
        ids=shared or {le['source_entry_id']for le in les if le['matched_by']=='exact_headword'}
        if not ids:
            target=targets.get(part['target'],{});predicted={f.get('upos')for f in target.get('context_features',[])}
            pos={'NOUN':'noun','PROPN':'noun','ADJ':'adjective','ADV':'adverb','VERB':'verb'}
            ids={le['source_entry_id']for le in les if le['grammatical_type']in {pos.get(x)for x in predicted}}
        chosen[part['target']]=({le['source_entry_id']for le in les if le['lemma']==part['target']}
            if part['target']in acronym_targets else ids or {le['source_entry_id']for le in les})
    primary_ids=set().union(*chosen.values())if chosen else set()
    proper_only=bool(primary_ids)and all(lexemes[i]['grammatical_features'].get('type')=='proper'for i in primary_ids)
    groups={}
    for u in p['evidence']:
        f=u['fields'];kind=u['kind'];state='primary';reason='chosen_target';ident=u['entry_id'].split(':slot:')[0]
        status=editorial(f);u['editorial_status']=status
        if translation and kind not in {'terminology','loris'}:state,reason='excluded','translation_whole_target'
        elif kind=='morphology':
            if ident not in chosen.get(u['target'],set()):state,reason='excluded','different_lexical_identity'
            elif status['normalized']=='automatic' and any(editorial(lexemes[i])['normalized']=='reviewed'for i in chosen.get(u['target'],set())):state,reason='alternative','secondary_automatic_analysis'
        elif kind in {'synonyms','collocations'}:
            if u['target']in acronym_targets and f.get('headword')!=u['target']:state,reason='excluded','explicit_acronym_not_folded_word'
            elif proper_only and kind not in plan['requested_facets']:state,reason='excluded','personal_name_not_common_noun_profile'
            elif u['match_relation']in {'component_only','alternative_lexical_identity'}:state,reason='excluded','incidental_component_or_reading'
            elif primary_ids and plan['query_kind']=='word' and f.get('headword')not in {lexemes[i]['lemma']for i in primary_ids}:state,reason='excluded','different_headword'
            elif kind=='collocations'and u['target'].islower()and primary_ids and all(lexemes[i]['grammatical_features'].get('type')=='common'for i in primary_ids):
                heads=[c for c in f.get('components',[])if c.get('role')=='headword']
                if heads and all(str(c.get('text',''))[:1].isupper()for c in heads):state,reason='alternative','capitalized_headword_context_not_resolved'
        elif kind=='terminology':
            matches=f.get('matched_forms',[])
            if matches and not any(m.get('term')in targets for m in matches)and all(str(m.get('term','')).isupper()for m in matches)and any(t.islower()for t in targets):state,reason='excluded','case_distinct_acronym_not_requested'
            elif translation and not any(' '.join(str(m.get('term','')).lower().split())==' '.join(t.lower().split())for m in matches for t in targets):state,reason='excluded','not_whole_translation_target'
        if kind=='collocations'and f.get('lexical_unit_id')is not None:
            # The source lexical unit, structure and all measurement/content
            # fields must agree. Homonymous strings alone are never merged.
            key=json.dumps([u['provenance'].get('resource_id'),u['provenance'].get('version'),
                f['lexical_unit_id'],f.get('surface'),f.get('structure_id'),f.get('frequency'),
                f.get('logDice'),f.get('status'),u['match_relation'],
                (u['provenance'].get('source')or{}).get('archive_sha256'),
                f.get('frequency_scope','collocation'),f.get('corpus','Gigafida 2.0'),
                [{k:c.get(k)for k in ('position','text','lemma','msd')}for c in f.get('components',[])]],sort_keys=True)
            if key in groups:
                rep=groups[key];rep.setdefault('source_memberships',[]).append({'record_id':u['record_id'],'entry_id':u['entry_id']})
                state,reason='excluded','duplicate_observation_membership'
            else:
                groups[key]=u;u['source_memberships']=[{'record_id':u['record_id'],'entry_id':u['entry_id']}]
        u['selection']={'state':state,'reason':reason,'version':VERSION}
        # Editorial annotations and grouping participate in the immutable unit ID.
    # Group membership accumulation finishes before identity is calculated.
    for u in p['evidence']:
        u['captured_unit_id']=u['record_id']
        u['record_id']='lexical:'+hashlib.sha256(json.dumps({k:v for k,v in u.items()if k!='record_id'},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    p['interpretation']['selection']={'version':VERSION,'primary_lexeme_ids':sorted(primary_ids),
        'morphology_by_target':{k:sorted(v)for k,v in chosen.items()},'proper_name':proper_only,
        'policy':'Same annotation consumed by prose and cards; all candidates retained in this capture'}
    return p
