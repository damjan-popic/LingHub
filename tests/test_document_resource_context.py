"""Synthetic producer-body receiving contract, no source/model calls."""
from copy import deepcopy
import hashlib
import pytest
from pilot_backend.document_source_body import source_body,body_binding
from pilot_backend.evidence_review import checked
from pilot_backend.store import digest
from pilot_backend.security import Failure
from test_document_evidence_review import sample

def test_editorial_full_body_binding_survives_consumer_validation():
    task,doc,rows=sample();review=doc['review'];finding=review['findings'][0]
    raw={'title':'Synthetic Q/A','question_original':'Vprašanje?',
       'answer_text':'Odgovor velja le pod pogojem A.','additional_info':'Druga možnost B.'}
    record=rows['E1'];record.update(capture_kind='editorial_record',capture=raw,capture_sha256=digest(raw))
    body=source_body(record);source=finding['source'];source.update(capture_sha256=digest(raw),body_binding=body_binding(record),
       focus={'char_start':0,'char_end':len(body),'text':body,'text_sha256':hashlib.sha256(body.encode()).hexdigest()})
    finding['related_source_captures'][0]['capture_sha256']=digest(raw)
    review['manifest_sha256']=digest({k:v for k,v in review.items()if k!='manifest_sha256'})
    assert checked(review,task['source_text'],task,rows)==review
    bad=deepcopy(review);bad['findings'][0]['source']['body_binding']['rendering']='invented'
    bad['manifest_sha256']=digest({k:v for k,v in bad.items()if k!='manifest_sha256'})
    with pytest.raises(Failure):checked(bad,task['source_text'],task,rows)
