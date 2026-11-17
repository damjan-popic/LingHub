"""Safe partial exports: synthetic frozen applications, no app/provider/live store."""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import io
import json
from zipfile import ZipFile

import pytest

from pilot_backend import document_files as files
from pilot_backend.document_uploads import Documents
from pilot_backend.security import Failure
from test_document_word import package

VERSION='document-source-applications.v1'
EID='synthetic:qualified-source'

def h(value):return hashlib.sha256(value.encode()if isinstance(value,str)else value).hexdigest()

def qualify(report,section,edit):
    source={'evidence_id':EID,'capture_sha256':h('synthetic-capture'),
        'focus':{'text':'Synthetic finite source application.'},'qualifications':['kept condition']}
    proof={'kind':'spelling_letters','adapter_version':VERSION,'input_sha256':h(section['text']),
        'source_capture_sha256':source['capture_sha256'],'source_focus_sha256':h(source['focus']['text']),
        'writer_inference_performed':False}
    proof['edit_binding_sha256']=files._partial_value_hash({'version':'document-application-edit-binding.v1',
        'input_sha256':h(section['text']),'edit':{key:edit[key]for key in ('start','end','original','replacement')}})
    proof['proof_sha256']=h(json.dumps(proof,sort_keys=True,ensure_ascii=False,separators=(',',':')))
    finding={'type':'executable_application','included_in_draft':True,'application_version':VERSION,
        'document_revision':report['source_sha256'],'document_location':section['location'],
        'remaining_conditions':[],'proposed_edit':deepcopy(edit),'application_proof':proof,
        'match':{'observed':{k:edit[k]for k in ('start','end')}|{'text':edit['original']}},
        'notice':'Synthetic source condition retained.','alternatives':[],
        'source':source,'related_source_captures':[{'evidence_id':EID,'capture_sha256':source['capture_sha256']}]}
    section.setdefault('sources',[{'evidence_id':EID,'title':'Synthetic source','captured_qualifications':['Synthetic source condition retained.']}])
    result=section.setdefault('document_result',{'mode':'review','status':'draft','source_sha256':h(section['text']),
        'proposed_text':'UNTRUSTED WHOLE PROPOSAL','edits':[],'review':{'overview':{'counts':{'qualified_advice':0,'recorded_contrast':0,'recorded_form_context':0,'executable_application':0}},'checks':[{'check_id':'comma_prediction','status':'failed'}],'findings':[]}})
    result['edits'].append(deepcopy(edit));result['review']['findings'].append(finding)
    result['review']['overview']['counts']['executable_application']=len(result['review']['findings'])
    return finding

def report_for(text='Nadaljni zapis.',locale='sl',word=None):
    parsed=files.extract(word,'source.docx')if word else None
    blocks=parsed['blocks']if parsed else [{'index':0,'location':f'text@0:{len(text)}','text':text}]
    report={'state':'complete','name':'source.docx'if word else'source.txt','source_sha256':h(word if word else text),
        'options':{'mode':'review','locale':locale,'review_strategy':'evidence_review_v1'},'mode':'review',
        'source_language':'sl','locale':locale,'sections':[],'warnings':parsed['warnings']if parsed else[]}
    for block in blocks:report['sections'].append({**deepcopy(block),'state':'complete','limitations':[{'code':'document_transform_failed'}]})
    first=report['sections'][0];start=first['text'].index('Nadaljni')
    edit={'start':start,'end':start+8,'original':'Nadaljni','replacement':'Nadaljnji',
        'evidence_ids':[EID],'explanation':'Synthetic located source application.'}
    qualify(report,first,edit)
    for section in report['sections'][1:]:section.update(state='failed',document_result=None)
    return report

class FakeDB:
    def __init__(self,original):self.original=original
    def execute(self,sql,args):
        assert sql=='SELECT package FROM document_originals WHERE document_id=?'
        assert args==('owned-doc',)
        return self
    def fetchone(self):return(self.original,)if self.original else None

class FakeStore:
    def __init__(self,original):self.original=original
    @contextmanager
    def connect(self):yield FakeDB(self.original)

def export(report,fmt,original=None,partial=True):
    service=object.__new__(Documents);service.store=FakeStore(original);calls=[]
    def owned(owner,identifier,*,internal=False):
        calls.append((owner,identifier,internal));assert(owner,identifier,internal)==('owned-user','owned-doc',True)
        return deepcopy(report)
    service.get=owned
    value=service.export('owned-user','owned-doc',fmt,draft=True,partial=partial)
    assert calls==[('owned-user','owned-doc',True)]
    return value

@pytest.mark.parametrize('locale',['sl','it'])
@pytest.mark.parametrize('fmt',['txt','docx'])
def test_both_explicit_partial_formats_apply_only_bound_edits_and_label_incomplete(locale,fmt):
    report=report_for(locale=locale);before=deepcopy(report)
    value=export(report,fmt)
    paragraphs=files.extract(value,'partial.docx')['blocks']if fmt=='docx'else None
    text='\n'.join(p['text']for p in paragraphs)if paragraphs else value.decode()
    assert ('Bozza parziale'if locale=='it'else'Delni osnutek')in text
    assert 'Nadaljnji zapis.'in text and'UNTRUSTED WHOLE PROPOSAL'not in text
    assert report==before
    assert files.review_export_status(report)['partial_review']['applied_edits']==1
    assert files.review_export_status(report)['complete_available']is False
    with pytest.raises(Failure,match='document_draft_unavailable'):export(report,fmt,partial=False)

@pytest.mark.parametrize('defect',['source_hash','revision','location','missing_evidence','unknown_capture',
    'conditions','writer_inference','proof_hash','focus_hash','not_included','unknown_version'])
def test_unbound_or_conditional_change_preserves_exact_original(defect):
    report=report_for();section=report['sections'][0];finding=section['document_result']['review']['findings'][0]
    if defect=='source_hash':section['document_result']['source_sha256']=h('stale')
    elif defect=='revision':finding['document_revision']=h('stale document')
    elif defect=='location':finding['document_location']='other@0:8'
    elif defect=='missing_evidence':section['sources']=[]
    elif defect=='unknown_capture':finding['related_source_captures']=[]
    elif defect=='conditions':finding['remaining_conditions']=['condition not established']
    elif defect=='writer_inference':finding['application_proof']['writer_inference_performed']=True
    elif defect=='proof_hash':finding['application_proof']['proof_sha256']=h('wrong')
    elif defect=='focus_hash':finding['source']['focus']['text']='Different source focus.'
    elif defect=='not_included':finding['included_in_draft']=False
    elif defect=='unknown_version':finding['application_version']='unknown'
    before=deepcopy(report);assembled=files.safe_partial_review_document(report)
    assert assembled['sections'][0]['document_result']['proposed_text']==section['text']
    assert assembled['partial_review_export']['applied_edits']==0
    assert assembled['partial_review_export']['withheld_edits']==1
    assert section['text']in export(report,'txt').decode()
    assert report==before


def test_conflicting_changes_withheld_but_independent_supported_change_survives():
    report=report_for('Nadaljni star zapis.');section=report['sections'][0]
    edit=deepcopy(section['document_result']['edits'][0]);edit['replacement']='Drug predlog';qualify(report,section,edit)
    qualify(report,section,{'start':9,'end':14,'original':'star ','replacement':'nov ',
        'evidence_ids':[EID],'explanation':'Independent synthetic support.'})
    assembled=files.safe_partial_review_document(report)
    assert assembled['sections'][0]['document_result']['proposed_text']=='Nadaljni nov zapis.'
    assert assembled['partial_review_export']['applied_edits']==1
    assert assembled['partial_review_export']['withheld_edits']==2
    assert assembled['partial_review_export']['sections'][0]['applied_edit_indexes']==[2]


def test_boolean_or_wrong_offsets_are_not_changes():
    report=report_for();section=report['sections'][0]
    section['document_result']['edits'][0]['start']=True
    assert files.partial_review_blocks(report)==[section['text']]


def test_active_and_translation_operations_have_no_partial_export():
    for mode,state in [('review','running'),('translate','complete')]:
        report=report_for();report['options']['mode']=report['mode']=mode;report['state']=state
        assert files.review_export_status(report)['partial_review']['available']is False
        with pytest.raises(Failure,match='document_draft_unavailable'):export(report,'txt')


def test_failure_history_preserved_and_derived_view_cannot_be_complete_draft():
    report=report_for();before=deepcopy(report);derived=files.safe_partial_review_document(report)
    assert derived['sections'][0]['limitations']==report['sections'][0]['limitations']
    assert derived['sections'][0]['document_result']['review']==report['sections'][0]['document_result']['review']
    with pytest.raises(Failure,match='document_draft_unavailable'):files.draft_blocks(derived)
    assert report==before


def test_already_correct_text_is_unchanged_and_never_certified_complete():
    report=report_for();section=report['sections'][0];section['document_result']['edits']=[]
    section['document_result']['review']['findings']=[]
    assert files.partial_review_blocks(report)==[section['text']]
    status=files.review_export_status(report)
    assert status['partial_review']['applied_edits']==0 and status['partial_review']['original_sections_preserved']==1
    assert status['complete_available']is False


def test_unresolved_operation_without_failure_flag_still_blocks_full_default():
    report=report_for();section=report['sections'][0];section['limitations']=[]
    section['document_result']['review']['checks'][0]['status']='partial'
    assert files.review_export_status(report)['complete_available']is False
    with pytest.raises(Failure,match='document_draft_unavailable'):export(report,'txt',partial=False)
    assert 'Nadaljnji'in export(report,'txt').decode()


def test_partial_word_preserves_original_runs_table_headers_parts_and_zip_metadata():
    table='<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Nepregledano ostane.</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    body='<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Nadaljni</w:t></w:r><w:r><w:t xml:space="preserve"> zapis.</w:t></w:r></w:p>'+table+'<w:sectPr/>'
    original=package(body,{'word/styles.xml':b'<styles/>','word/media/preserved.bin':b'unchanged image'})
    report=report_for(word=original);before=deepcopy(report);changed=export(report,'docx',original)
    with ZipFile(io.BytesIO(original))as old,ZipFile(io.BytesIO(changed))as new:
        assert old.namelist()==new.namelist()and old.comment==new.comment
        for name in old.namelist():
            x,y=old.getinfo(name),new.getinfo(name)
            assert(x.date_time,x.compress_type,x.external_attr)==(y.date_time,y.compress_type,y.external_attr)
            if name!='word/document.xml':assert old.read(name)==new.read(name)
        xml=new.read('word/document.xml')
        assert table.encode()in xml
        from xml.etree import ElementTree as ET
        assert any(run.find(files.W+'rPr/'+files.W+'b')is not None and ''.join(n.text or''for n in run.iter(files.W+'t'))=='Nadaljnji'for run in ET.fromstring(xml).iter(files.W+'r'))
        assert b'<w:pPr><w:pStyle w:val="Title"/></w:pPr>'in xml and b'<w:sectPr/>'in xml
    assert report==before
    assert files.partial_review_blocks(report)==['Nadaljnji zapis.','Nepregledano ostane.']


def test_report_describes_safe_subset_and_retains_original_sources_and_failed_checks():
    report=report_for();rendered=files.report_text(report).decode()
    assert 'Vključene podprte spremembe'in rendered and'nepopolno pregledani'in rendered
    assert 'Nadaljni zapis.'in rendered and EID in json.dumps(report)
    assert 'V delnem osnutku: 1'in rendered and'Dokument še nima popolnega osnutka.'in rendered
    assert 'Synthetic source condition retained.'in rendered


@pytest.mark.parametrize('instruction',[' PAGE ','PAGE \\* MERGEFORMAT','page'])
def test_only_cached_simple_page_footer_is_preserved_opaque_unchecked(instruction):
    footer='<w:ftr xmlns:w="'+files.W[1:-1]+'"><w:p><w:r><w:t>Static footer stays.</w:t></w:r><w:fldSimple w:instr="'+instruction+'"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p></w:ftr>'
    original=package('<w:p><w:r><w:t>Nadaljni zapis.</w:t></w:r></w:p>',{'word/footer1.xml':footer})
    captured=files.extract(original,'page.docx')
    assert len(captured['blocks'])==1
    assert captured['warnings']==['document_page_footer_not_reviewed']
    report=report_for(word=original);changed=export(report,'docx',original)
    with ZipFile(io.BytesIO(changed))as result:
        assert result.read('word/footer1.xml')==footer.encode()
    assert files.review_export_status(report)['partial_review']['unreviewed_page_footer']is True
    assert 'številčenje noge nista pregledana'in files.report_text(report).decode()
    with pytest.raises(Failure,match='document_draft_unavailable'):export(report,'docx',original,partial=False)

@pytest.mark.parametrize('instruction',['DATE','NUMPAGES','DDE external','INCLUDETEXT remote','PAGE \\* ROMAN','PAGE\u00a0'])
def test_other_footer_field_instructions_remain_unsupported(instruction):
    footer='<w:ftr xmlns:w="'+files.W[1:-1]+'"><w:p><w:fldSimple w:instr="'+instruction+'"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p></w:ftr>'
    data=package('<w:p><w:r><w:t>Original.</w:t></w:r></w:p>',{'word/footer1.xml':footer})
    with pytest.raises(Failure,match='document_unsupported'):files.extract(data,'field.docx')

@pytest.mark.parametrize('field',[
    '<w:r><w:fldChar w:fldCharType="begin"/><w:instrText>PAGE</w:instrText><w:fldChar w:fldCharType="end"/></w:r>',
    '<w:fldSimple w:instr="PAGE"><w:r><w:rPr><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:rPr><w:t>1</w:t></w:r></w:fldSimple>',
    '<w:fldSimple w:instr="PAGE" w:dirty="unknown"><w:r><w:t>1</w:t></w:r></w:fldSimple>',
    '<w:fldSimple w:instr="PAGE"><w:r><w:t>arbitrary text</w:t></w:r></w:fldSimple>',
])
def test_complex_nested_or_non_numeric_page_fields_remain_unsupported(field):
    footer='<w:ftr xmlns:w="'+files.W[1:-1]+'"><w:p>'+field+'</w:p></w:ftr>'
    data=package('<w:p><w:r><w:t>Original.</w:t></w:r></w:p>',{'word/footer1.xml':footer})
    with pytest.raises(Failure,match='document_unsupported'):files.extract(data,'field.docx')


def test_body_page_fields_and_partial_word_bold_remain_explicitly_unsupported():
    for body in ['<w:p><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p>',
                 '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Na</w:t></w:r><w:r><w:t>daljni zapis.</w:t></w:r></w:p>']:
        with pytest.raises(Failure,match='document_unsupported'):files.extract(package(body),'unsupported.docx')


def punctuation_report(projected=True):
    """Synthetic metadata receipts; CORE owns linguistic unit construction/validation."""
    report=report_for('Nadaljni pride ko želi.');section=report['sections'][0]
    result=section['document_result'];review=result['review'];text=section['text']
    source=deepcopy(review['findings'][0]['source'])
    source['source_slug']='synthetic-source';source['focus']['text_sha256']=h(source['focus']['text'])
    binding={'source_slug':source['source_slug'],'evidence_id':EID,'passage_id':EID,
        'text_sha256':source['focus']['text_sha256']}
    a=text.index(' ko');op={'start':a,'end':a,'original':'','replacement':',','kind':'insert'}
    output=text[:a]+','+text[a:]
    checker={'status':'partial'if projected else'ok','input_text':text,'input_sha256':h(text),'edits':[op]}
    parent_response={'input_text':text,'status':'ok','schema':'synthetic-native-analysis'}
    syntax={'version':'klikai.syntax-collation.v1','captures':[{'response':parent_response}]}
    unit={'unit_type':'construction_application','signed_profile_sha256':h('synthetic-qualified-application-context'),
        'payload':{'construction':'finite_subordinate_clause_boundary','analysis_basis':'original',
        'input_text':text,'output_text':output,'source_record':{
        'text':source['focus']['text'],'text_sha256':source['focus']['text_sha256']}},'bindings':[binding]}
    if not projected:
        original_sr=json.dumps(parent_response,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        unit['payload']['syntax_record']={'text':original_sr,'text_sha256':h(original_sr)}
    proof={'adapter_version':'document-existing-applications.v1','application_unit':unit,
        'application_context_sha256':h('synthetic-qualified-application-context'),
        'syntax_capture_sha256':files._partial_value_hash(syntax),
        'checker_observation_sha256':files._partial_value_hash(checker),
        'source_capture_set_sha256':h('synthetic-capture-set'),'input_sha256':h(text),'output_sha256':h(output),
        'applicability':'existing_source_bound_conditions_satisfied','writer_inference_performed':False}
    if projected:
        receipt={'version':'document-native-sentence-projection.v1',
            'parent_syntax_sha256':proof['syntax_capture_sha256'],'parent_input_sha256':h(text),
            'sentence_id':1,'start':0,'end':len(text),'word_ids':[1,2,3,4,5]}
        response={'input_text':text,'document_sentence_projection':deepcopy(receipt),
            'execution':{'projection_only':True,'inference_performed':False}}
        sr=json.dumps(response,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        unit['payload']['syntax_record']={'text':sr,'text_sha256':h(sr)}
        cap={'operation':'syntax','request':{'text':text},'response':response,
            'response_sha256':files._partial_value_hash(response),'duration_ms':0}
        child={'version':'klikai.syntax-collation.v1','plan':{'status':'ready',
            'routing_kind':'comma_explanation_syntax','focus':text},'captures':[cap],
            'capture_set_sha256':files._partial_value_hash([cap])}
        proof['native_sentence_projection']={**receipt,'projected_syntax_sha256':files._partial_value_hash(child)}
    proof['proof_sha256']=files._partial_value_hash(proof)
    whole={'start':0,'end':len(text),'original':text,'replacement':output,
        'evidence_ids':[EID],'explanation':'Synthetic source-bound comma application.',
        'support_quote':source['focus']['text']}
    edit={**whole,**{k:op[k]for k in ('start','end','original','replacement')}}
    finding={'type':'executable_application','applicability':'existing_contract_applied',
        'included_in_draft':True,'document_revision':report['source_sha256'],'document_location':section['location'],
        'proposed_edit':edit,'application_proof':proof,'observed_checker_edits':[op],
        'source':source,'related_source_captures':[{'evidence_id':EID,'capture_sha256':source['capture_sha256']}]}
    result['edits'].append(edit);review['findings'].append(finding)
    review['source_capture_set_sha256']=proof['source_capture_set_sha256']
    review['checks']=[{'check_id':'comma_prediction','status':'partial','observation':checker},
        {'check_id':'application_analysis','status':'completed','observation':syntax}]
    review['applications']={'applications':[{'proof':proof,'edit':whole,
        'primary_source_binding':binding,'observed_edits':[op]}]}
    return report


@pytest.mark.parametrize('projected',[False,True])
def test_punctuation_and_spelling_keep_independent_parent_bound_operations(projected):
    report=punctuation_report(projected);before=deepcopy(report)
    copy=files.safe_partial_review_document(report)
    assert copy['sections'][0]['document_result']['proposed_text']=='Nadaljnji pride, ko želi.'
    assert copy['partial_review_export']['applied_edits']==2
    assert copy['partial_review_export']['incomplete_sections']==1
    assert report==before


@pytest.mark.parametrize('defect',['application_join','primary_binding','unit_binding','focus',
    'capture_set','syntax_observation','checker_observation','output_hash','unsupported_version',
    'primitive_membership','projection_parent','projection_range','projection_word_ids','projection_hash'])
def test_punctuation_binding_mismatch_withholds_only_comma(defect):
    report=punctuation_report();section=report['sections'][0];r=section['document_result']['review']
    finding=r['findings'][1];proof=finding['application_proof'];app=r['applications']['applications'][0]
    if defect=='application_join':app['proof']={}
    elif defect=='primary_binding':app['primary_source_binding']={}
    elif defect=='unit_binding':proof['application_unit']['bindings']=[]
    elif defect=='focus':finding['source']['focus']['text']='Changed source condition.'
    elif defect=='capture_set':r['source_capture_set_sha256']=h('other-set')
    elif defect=='syntax_observation':r['checks'][1]['observation']['changed']=True
    elif defect=='checker_observation':r['checks'][0]['observation']['changed']=True
    elif defect=='output_hash':proof['output_sha256']=h('unbound output')
    elif defect=='unsupported_version':proof['adapter_version']='unknown'
    elif defect=='primitive_membership':app['observed_edits']=[]
    elif defect=='projection_parent':proof['native_sentence_projection']['parent_input_sha256']=h('stale')
    elif defect=='projection_range':proof['native_sentence_projection']['end']=0
    elif defect=='projection_word_ids':proof['native_sentence_projection']['word_ids']=[True]
    elif defect=='projection_hash':proof['native_sentence_projection']['projected_syntax_sha256']=h('unbound-projection')
    if defect not in {'application_join','unsupported_version'}:
        proof['proof_sha256']=files._partial_value_hash({k:v for k,v in proof.items()if k!='proof_sha256'})
    derived=files.safe_partial_review_document(report)
    assert derived['sections'][0]['document_result']['proposed_text']=='Nadaljnji pride ko želi.'
    assert derived['partial_review_export']['applied_edits']==1
    assert derived['partial_review_export']['withheld_edits']==1


@pytest.mark.parametrize('locale',['sl','it'])
def test_partial_checker_prefix_is_explicit_and_unchecked_tail_preserved(locale):
    report=report_for(locale=locale);r=report['sections'][0]['document_result']['review']
    r['checks']=[{'check_id':'comma_prediction','status':'partial','checked_extent':{
        'start':0,'end':8,'complete':False,'offset_convention':'unicode_codepoint_half_open'}}]
    text=files.report_text(report).decode('utf-8')
    assert ('8/'+str(len(report['sections'][0]['text'])))in text
    assert ('La coda non controllata conserva l’originale'if locale=='it'else'Nepreverjeni konec ohrani izvirnik')in text
    assert files.partial_review_blocks(report)==['Nadaljnji zapis.']


@pytest.mark.parametrize('defect',['replacement_and_finding','retarget_same_form','missing_legacy_binding','stale_input_binding'])
def test_generic_partial_requires_precise_registry_issued_edit_binding(defect):
    report=report_for('Nadaljni zapis. Nadaljni zapis.');s=report['sections'][0];r=s['document_result']
    finding=r['review']['findings'][0];proof=finding['application_proof']
    if defect=='replacement_and_finding':
        r['edits'][0]['replacement']='NEPODPRTO';finding['proposed_edit']['replacement']='NEPODPRTO'
    elif defect=='retarget_same_form':
        a=s['text'].rindex('Nadaljni')
        for e in (r['edits'][0],finding['proposed_edit']):e.update(start=a,end=a+8)
    elif defect=='missing_legacy_binding':
        proof.pop('edit_binding_sha256')
        proof['proof_sha256']=files._partial_value_hash({k:v for k,v in proof.items()if k!='proof_sha256'})
    elif defect=='stale_input_binding':
        proof['edit_binding_sha256']=files._partial_value_hash({'version':'document-application-edit-binding.v1',
            'input_sha256':h('stale input'),'edit':{key:r['edits'][0][key]for key in ('start','end','original','replacement')}})
        proof['proof_sha256']=files._partial_value_hash({k:v for k,v in proof.items()if k!='proof_sha256'})
    derived=files.safe_partial_review_document(report)
    assert derived['sections'][0]['document_result']['proposed_text']==s['text']
    assert derived['partial_review_export']['applied_edits']==0
    assert derived['partial_review_export']['withheld_edits']==1


def test_legacy_generic_partial_withheld_without_changing_old_full_or_saved_history():
    report=report_for();s=report['sections'][0];r=s['document_result'];proof=r['review']['findings'][0]['application_proof']
    proof.pop('edit_binding_sha256');proof['proof_sha256']=files._partial_value_hash({k:v for k,v in proof.items()if k!='proof_sha256'})
    s['limitations']=[];r['review']['checks']=[{'check_id':'comma_prediction','status':'completed_no_issue'}]
    r['proposed_text']='Nadaljnji zapis.';before=deepcopy(report)
    assert files.draft_blocks(report)==['Nadaljnji zapis.']
    assert files.partial_review_blocks(report)==['Nadaljni zapis.']
    assert report==before


@pytest.mark.parametrize('defect',['remove_projection_receipt','wrong_parent_unit','changed_parent_response','wrong_profile_context','unit_wrong_output','legacy_partial_checker'])
def test_punctuation_requires_true_parent_legacy_or_exact_projection(defect):
    report=punctuation_report(projected=defect!='legacy_partial_checker');s=report['sections'][0]
    r=s['document_result']['review'];proof=r['findings'][1]['application_proof'];payload=proof['application_unit']['payload']
    if defect=='remove_projection_receipt':proof.pop('native_sentence_projection')
    elif defect=='wrong_parent_unit':payload['input_text']='Different parent'
    elif defect=='changed_parent_response':
        response=json.loads(payload['syntax_record']['text']);response['unbound_change']=True
        payload['syntax_record']['text']=json.dumps(response,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        payload['syntax_record']['text_sha256']=h(payload['syntax_record']['text'])
    elif defect=='wrong_profile_context':proof['application_unit']['signed_profile_sha256']=h('other-context')
    elif defect=='unit_wrong_output':payload['output_text']='UNAPPROVED OUTPUT'
    elif defect=='legacy_partial_checker':
        r['checks'][0]['observation']['status']='partial'
        proof['checker_observation_sha256']=files._partial_value_hash(r['checks'][0]['observation'])
    proof['proof_sha256']=files._partial_value_hash({k:v for k,v in proof.items()if k!='proof_sha256'})
    assert files.partial_review_blocks(report)==['Nadaljnji pride ko želi.']
    assert files.safe_partial_review_document(report)['partial_review_export']['withheld_edits']==1
