"""One source/target decision for writer evidence and deterministic reader cards.

Candidates are annotated, never deleted. Source editorial state is independent
of grammatical register, normative authority and contextual correctness.
"""
import copy,hashlib,json
from .lexical_identity import morphology_key,source_occurrence_key
from .loris_roles import loris_advice_roles

VERSION='lexical-focus-v5-20261103'

def editorial(fields):
    key=next((k for k in ('source_status','head_status','status')if fields.get(k)),None)
    raw=fields.get(key)if key else None
    normalized={'manual':'reviewed','ssss-manual':'reviewed','automatic':'automatic',
                'ssss-automatic':'automatic'}.get(str(raw).lower(),'unknown')
    return {'raw':raw,'field':key,'normalized':normalized,'mapping_version':'editorial-status-v1',
            'scope':'source editorial process; not register or normative approval'}

def accepted_target_match(match,target):
    """A recorded whole-surface inflection remains a term match downstream."""
    fold=lambda value:' '.join(str(value or '').casefold().split())
    if fold(match.get('term'))==fold(target):return True
    return (fold(match.get('matched_query'))==fold(target)
        and match.get('match_tier') in {'exact','morphology_exact'}
        and match.get('ambiguity_cost',0)==0
        and (match.get('match_tier')=='exact' or bool(match.get('query_signature')and match.get('lexicon_release_id'))))

def focus_profile(profile):
    """Return a new annotated revision; old saved captures remain untouched."""
    p=copy.deepcopy(profile);plan=p['interpretation'];targets={t['text']:t for t in plan['targets']}
    if plan.get('selection',{}).get('version')==VERSION:return p
    translation='translation'in plan['requested_facets'];focus={k:v for k,v in (plan.get('slot_focus')or{}).items()if k!='basis'and v is not None}
    acronym_targets={t for t in targets if t.isupper()and len(t)>1 and any(u['kind']=='terminology'and any(m.get('term')==t for m in u['fields'].get('matched_forms',[]))for u in p['evidence'])}
    lexemes={}
    for part in p['facets']['morphology'].get('data')or[]:
        for le in (part.get('result')or{}).get('lexemes',[]):lexemes[morphology_key(le)]=le
    alternatives=set(targets);shared=set()
    if len(alternatives)>1 and focus:
        for ident,le in lexemes.items():
            if any(all(s['normalized_features'].get(k)==v for k,v in focus.items())
                   and alternatives<={v['form']for v in s['variants']}for s in le['slots']):shared.add(ident)
        # An explicit name spelling and PROPN context resolve folded common nouns.
        if any(t[:1].isupper()for t in targets):
            proper={i for i in shared if lexemes[i]['grammatical_features'].get('type')=='proper'}
            if proper:shared=proper
    choice=plan.get('form_choice')or(plan.get('request_plan')or{}).get('form_choice')or{}
    explicit=(choice.get('explicit_lexemes')or plan.get('explicit_lexemes')
              or(plan.get('request_plan')or{}).get('explicit_lexemes')or[])
    def explicit_match(le):
        return any(le['lemma']==item.get('text')
            and (not item.get('gender')or le['grammatical_features'].get('gender')==item['gender'])
            and (not item.get('grammatical_type')or le['grammatical_type']==item['grammatical_type'])
            and (item.get('lexical_kind')!='common_noun'or le['grammatical_features'].get('type')=='common')
            for item in explicit)
    intended={key for key,le in lexemes.items()if explicit_match(le)}
    if (not explicit and len(targets)>1 and choice.get('intent')!='dictionary_information'
            and not choice.get('distinct_referents')):
        # A contextual lemma nominates an identity only after the original
        # surface has been verified in that source's complete paradigm.
        nominated={lemma for t in targets.values()for lemma in t.get('lemma_candidates',[])}
        related={key for key,le in lexemes.items()if le['lemma']in nominated
            and le['lemma']not in targets
            and any(a.get('exact_surface')for a in le.get('submitted_analyses',[]))}
        if related:intended=related
    if intended:
        plan['form_choice_resolution']={'basis':'source-recorded identity with explicit scope'if explicit else'source-recorded form with contextual lemma nomination',
            'conditional':not bool(explicit),'lemmas':sorted({lexemes[key]['lemma']for key in intended}),
            'source_occurrence_keys':sorted(intended)}
    chosen={}
    for part in p['facets']['morphology'].get('data')or[]:
        les=(part.get('result')or{}).get('lexemes',[])
        ids=({morphology_key(le)for le in les}&intended)if intended else shared or {morphology_key(le)for le in les if le['matched_by']=='exact_headword'}
        if not ids and not intended:
            target=targets.get(part['target'],{});predicted={f.get('upos')for f in target.get('context_features',[])}
            pos={'NOUN':'noun','PROPN':'noun','ADJ':'adjective','ADV':'adverb','VERB':'verb'}
            ids={morphology_key(le)for le in les if le['grammatical_type']in {pos.get(x)for x in predicted}}
        matching={morphology_key(le)for le in les if morphology_key(le)in ids
            and focus and any(all(s['normalized_features'].get(k)==v for k,v in focus.items())for s in le['slots'])}
        if matching:ids=matching
        chosen[part['target']]=({morphology_key(le)for le in les if le['lemma']==part['target']}
            if part['target']in acronym_targets else ids if intended else ids or {morphology_key(le)for le in les})
    primary_ids=set().union(*chosen.values())if chosen else set()
    proper_only=bool(primary_ids)and all(lexemes[i]['grammatical_features'].get('type')=='proper'for i in primary_ids)
    groups={}
    for u in p['evidence']:
        f=u['fields'];kind=u['kind'];state='primary';reason='chosen_target';ident=morphology_key(u)
        status=editorial(f);u['editorial_status']=status
        if translation and kind not in {'terminology','loris'}:state,reason='excluded','translation_whole_target'
        elif kind=='morphology':
            if ident not in chosen.get(u['target'],set()):state,reason='alternative','different_lexical_identity'
        elif kind in {'synonyms','collocations'}:
            if u['target']in acronym_targets and f.get('headword')!=u['target']:state,reason='excluded','explicit_acronym_not_folded_word'
            elif proper_only and kind not in plan['requested_facets']:state,reason='excluded','personal_name_not_common_noun_profile'
            elif u['match_relation']in {'component_only','alternative_lexical_identity'}:state,reason='excluded','incidental_component_or_reading'
            elif not primary_ids and len(targets)==1 and str(f.get('headword','')).casefold()!=u['target'].casefold() and u['match_relation'] not in {'whole_expression','inflected_equivalent'}:state,reason='alternative','unverified_analyzer_lemma_not_target_identity'
            elif (primary_ids and (plan['query_kind']=='word'or len(targets)==1
                    and {'synonyms','collocations'}&set(plan['requested_facets']))
                    and f.get('headword')not in {lexemes[i]['lemma']for i in primary_ids}):
                state,reason='excluded','different_headword'
            elif kind=='collocations'and u['target'].islower()and primary_ids and all(lexemes[i]['grammatical_features'].get('type')=='common'for i in primary_ids):
                heads=[c for c in f.get('components',[])if c.get('role')=='headword']
                if heads and all(str(c.get('text',''))[:1].isupper()for c in heads):state,reason='alternative','capitalized_headword_context_not_resolved'
        elif kind=='loris':
            advice=loris_advice_roles(f)
            if u['match_relation']=='component_only':state,reason='alternative','rule_matches_component_not_whole_target'
            elif advice and advice['relation']=='normative_explanation':
                if choice.get('intent')=='dictionary_information'or choice.get('distinct_referents'):
                    state,reason='alternative','advice_not_the_requested_dictionary_identity'
                elif explicit and (any(x.get('text')==advice['input_form']for x in explicit)
                    or not any(v['form']==advice['recommended_replacements']for key in intended for s in lexemes[key]['slots']for v in s['variants'])):
                    state,reason='alternative','advice_for_different_explicit_lexeme'
        elif kind=='terminology':
            matches=f.get('matched_forms',[])
            if matches and not any(m.get('term')in targets for m in matches)and all(str(m.get('term','')).isupper()for m in matches)and any(t.islower()for t in targets):state,reason='excluded','case_distinct_acronym_not_requested'
            elif translation and not any(accepted_target_match(m,t) for m in matches for t in targets):state,reason='excluded','not_whole_translation_target'
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
                rep=groups[key];member={'record_id':u['record_id'],'entry_id':u['entry_id']}
                if state=='primary'and rep['selection']['state']!='primary':
                    # An ineligible source membership must not suppress an
                    # eligible equivalent observation encountered later.
                    u['source_memberships']=rep.pop('source_memberships')+[member]
                    groups[key]=u
                else:
                    rep.setdefault('source_memberships',[]).append(member)
                    state,reason='excluded','duplicate_observation_membership'
            else:
                groups[key]=u;u['source_memberships']=[{'record_id':u['record_id'],'entry_id':u['entry_id']}]
        u['selection']={'state':state,'reason':reason,'version':VERSION}
        # Editorial annotations and grouping participate in the immutable unit ID.
    # Group membership accumulation finishes before identity is calculated.
    for u in p['evidence']:
        u['captured_unit_id']=u['record_id']
        u['record_id']='lexical:'+hashlib.sha256(json.dumps({k:v for k,v in u.items()if k!='record_id'},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    p['interpretation']['selection']={'version':VERSION,
        'primary_lexeme_ids':sorted({lexemes[i]['source_entry_id']for i in primary_ids}),
        'primary_occurrence_keys':sorted(primary_ids),
        'morphology_by_target':{k:sorted({lexemes[i]['source_entry_id']for i in v})for k,v in chosen.items()},
        'morphology_occurrences_by_target':{k:sorted(v)for k,v in chosen.items()},'proper_name':proper_only,
        'policy':'Same annotation consumed by prose and cards; all candidates retained in this capture'}
    return p
