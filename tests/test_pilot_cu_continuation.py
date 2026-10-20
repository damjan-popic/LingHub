import copy
import json
import pytest
from fastapi.testclient import TestClient
from pilot_backend.source_reader import prose, source_view
from pilot_backend.request_tasks import interpret
from core.lexical_planner import plan
from test_pilot_backend import rig, login, family, completed
from test_request_guidance import app_for, NoNetwork, ORIGIN, login as unified_login


def test_zrcola_display_projection_preserves_original_and_other_private_glyphs():
    source={'source_label':'ePravopis','excerpt':'p\ue466rvi ak\ue72f \uebd5 \ue951'}
    before=copy.deepcopy(source)
    assert source_view(source)['reader_blocks'][0]['text']=='pə̀rvi aku̯ ž̄ d̄'
    assert source==before
    other=dict(source,source_label='Druga pisava')
    assert source_view(other)['reader_blocks'][0]['text']==source['excerpt']


def test_submission_recovery_is_owned_read_only_and_does_not_repeat_generation(rig):
    app,c,adapter,ids=rig;h=login(c)
    first=family(c,h,'submission-recovery-001')
    recovered=c.get('/api/pilot/submissions/submission-recovery-001')
    assert recovered.status_code==200
    assert recovered.json()['family']==first['family']
    assert family(c,h,'submission-recovery-001')['job']['job_id']==first['job']['job_id']
    assert not adapter.calls
    assert c.post('/api/pilot/families',headers=h,json={
        'question':'different','idempotency_key':'submission-recovery-001'}).status_code==409
    assert family(c,h,'deliberate-repeat-001')['family']['answer_family_id']!=first['family']['answer_family_id']
    login(c,'reviewer')
    assert c.get('/api/pilot/submissions/submission-recovery-001').status_code==404


def test_feedback_additions_preserve_each_colleagues_text_and_are_owner_bound(rig):
    app,c,adapter,ids=rig;h=login(c);f=completed(app,c,h);run=f['short']
    c.post('/api/pilot/runs/'+run['run_id']+'/delivered',headers=h,json={'display_sha256':run['display_sha256']})
    for i,(rating,comment)in enumerate([('partly_useful','First colleague'),('cannot_judge','Second colleague')]):
        r=c.post('/api/pilot/feedback',headers=h,json={'run_id':run['run_id'],
            'rating':rating,'comment':comment,'idempotency_key':'append-feedback-'+str(i)})
        assert r.status_code==200
    events=c.get('/api/pilot/runs/'+run['run_id']+'/feedback').json()['events']
    assert [e['comment']for e in events]==['First colleague','Second colleague']
    assert [e['revision']for e in events]==[1,2]
    assert all(e['group']=='tester-user' for e in events)
    assert 'observation'not in str(events) and 'password'not in str(events)
    login(c,'reviewer')
    assert c.get('/api/pilot/runs/'+run['run_id']+'/feedback').status_code==404


@pytest.mark.parametrize('subject',['SCIA','BES','ovira'])
def test_followup_chain_keeps_explicit_subject_with_fresh_family_not_previous_answer(tmp_path,subject):
    app=app_for(tmp_path,NoNetwork())
    with TestClient(app,base_url=ORIGIN)as c:
        h=unified_login(c)
        parent=None;families=[]
        for i,q in enumerate([f'Kaj pomeni {subject}?','Kako se prevede?','Ali obstaja v Sloveniji?']):
            body={'question':q,'idempotency_key':'bound-chain-'+str(i)}
            if parent:body['parent_run_id']=parent
            r=c.post('/api/pilot/families',headers=h,json=body);assert r.status_code==200,r.text
            f=r.json()['family'];families.append(f['answer_family_id']);parent=f['short']['run_id']
            if i:
                assert f['context']==f'Kaj pomeni {subject}?'
                targets=plan(q,f['context'],'sl',{'raw_engines':{}})['targets']
                assert [t['text']for t in targets]==[subject]
                assert interpret(q,f['context'])is None
        assert len(set(families))==3
        # No implicit carry-over to a new question/tab or another account.
        assert interpret('Ali obstaja v Sloveniji?')['kind']=='missing_referent'
        other=unified_login(c,'unified-other')
        assert c.post('/api/pilot/families',headers=other,json={
            'question':'Kako se prevede?','parent_run_id':parent,'idempotency_key':'denied-parent'}).status_code==404


def test_source_markup_preserves_text_examples_and_untrusted_content_cannot_execute():
    raw='<h2>Pomen</h2><p>sportello <b>okence</b> &amp; vstopna točka.</p><p>&lt;glas&gt; in »<i>« sta zgleda.</p><script>alert(1)</script>'
    shown=prose(raw)
    assert 'sportello okence & vstopna točka.'in shown
    assert '<glas>'in shown and '»<i>«'in shown
    assert '<h2>'not in shown and 'alert'not in shown
    assert prose('Zapis <a> in <b> v fonetični oznaki.')=='Zapis <a> in <b> v fonetični oznaki.'
    assert prose('<code><b>beseda</b></code>')=='<b>beseda</b>'


def test_loris_and_term_source_views_remove_schema_without_rewriting_frozen_record():
    loris={'matched_text':'dekrete','rule_id':'private-id','payload':{
        'Izhodisce':'dekret','SteMislili':'uredba, odločba, odlok','Vslo':'dekret = (zastarelo) uradna listina',
        'Npr':'uredba predsednika vlade','source_file':'private/file'}}
    source={'excerpt':json.dumps(loris,ensure_ascii=False)}
    before=copy.deepcopy(source);view=source_view(source)
    text=str(view['reader_blocks'])
    assert source==before and view['excerpt']==before['excerpt']
    assert 'uredba predsednika vlade'in text and 'private-id'not in text and 'source_file'not in text
    old={'excerpt':'geographicalUsage — oblike 3:\n[\"Slovenija\"]\n\nmotivationSource — oblike 4:\n[{\"label\":\"Šolska 2022–2026\",\"url\":null}]'}
    shown=source_view(old)['reader_blocks'][0]['text']
    assert 'Območje rabe — oblike 3:'in shown and 'Slovenija'in shown
    assert 'Šolska 2022–2026'in shown and 'motivationSource'not in shown and '\"label\"'not in shown


def test_explicit_retry_links_failed_attempt_without_rewriting_it(rig):
    app,c,adapter,ids=rig;h=login(c);first=family(c,h,'retry-original-001')
    old=first['family']['short'];jid=first['job']['job_id']
    c.post('/api/pilot/jobs/'+jid+'/cancel',headers=h,json={})
    body={'question':'Kako?','locale':'sl','model':'qwen','task':'reference',
          'parent_run_id':old['run_id'],'parent_relation':'retry','idempotency_key':'retry-new-001'}
    r=c.post('/api/pilot/families',headers=h,json=body);assert r.status_code==200,r.text
    new=r.json()['family']['short'];assert new['run_id']!=old['run_id']
    assert new['parent_relation']=='retry'and new['parent_run_id']==old['run_id']
    assert c.get('/api/pilot/jobs/'+jid).json()['state']=='cancelled'
    body.update(question='Changed meaning',idempotency_key='retry-forged-001')
    assert c.post('/api/pilot/families',headers=h,json=body).status_code==400
