"""Actual local HTTP assertions for the versioned reusable platform."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import statistics
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--missing-analyzer',action='store_true');a=p.parse_args()
    report={'status':'RUNNING','at':datetime.now(timezone.utc).isoformat(),'checks':[],'examples':{},'port':a.port}
    def call(path,payload=None,status=200,query=None):
        url=f'http://127.0.0.1:{a.port}'+path+('?' + urlencode(query) if query else '')
        t=time.perf_counter()
        try:
            with urlopen(Request(url,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'}),timeout=40) as r:code,raw=r.status,r.read()
        except HTTPError as e:code,raw=e.code,e.read()
        report['checks'].append({'path':path,'status':code,'expected':status,'milliseconds':round((time.perf_counter()-t)*1000,3),'response_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
        assert code==status,(path,code,raw[:200]);return json.loads(raw)
    try:
        caps=call('/v1/capabilities');catalog=call('/v1/resources')
        assert '/data/' not in json.dumps(caps) and '/home/' not in json.dumps(caps)
        assert {r['id'] for r in catalog['resources']}=={'cjvt-thesaurus','cjvt-ksss','sloleks','morph-it'}
        assert all(r['artifact_validated'] and r['capability_usable'] for r in catalog['resources'])
        assert caps['identity']==catalog['identity'];report['capabilities']=caps
        sl=call('/v1/morphology/lemmatize',{'lang':'sl','tokens':['občnega','zbora','uciteljskemu','BES','občnega'],'limit':4})
        assert sl['unique_queries']==4 and sl['items'][0]['candidates'][0]['lemma_normalized']=='občen'
        assert sl['items'][1]['candidates'][0]['lemma_normalized']=='zbor'
        assert sl['items'][2]['candidates'][0]['matched_by']=='folded' and sl['items'][3]['ambiguous']
        assert sl['items'][0]['candidates']==sl['items'][4]['candidates'];report['examples']['sl_lemmas']=sl
        limited=call('/v1/morphology/lemmatize',{'lang':'sl','tokens':['BES'],'limit':1})
        assert limited['items'][0]['truncated'] and len(limited['items'][0]['candidates'])==1
        it=call('/v1/morphology/lemmatize',{'lang':'it','tokens':['bambini','delibere']})
        assert any(c['lemma_normalized']=='bambino' for c in it['items'][0]['candidates'])
        assert call('/v1/morphology/lemmatize',{'lang':'sl','tokens':['zqxv987654']})['status']=='no_hit'
        for lang,lemma in [('sl','zbor'),('it','bambino')]:
            forms=call('/v1/morphology/inflect',{'lang':lang,'lemma':lemma,'limit':4})
            assert forms['forms'] and all(f['msds'] for f in forms['forms'])
            exact=call('/v1/morphology/inflect',{'lang':lang,'lemma':lemma,'msd':forms['forms'][0]['msds'][0]})
            assert all(f['msds']==[forms['forms'][0]['msds'][0]] for f in exact['forms'])
            report['examples'][lang+'_forms']=forms
        assert call('/v1/morphology/inflect',{'lang':'sl','lemma':'zbor','msd':"%' OR 1=1 --"})['status']=='no_hit'
        signatures=call('/v1/morphology/signatures',{'lang':'sl','text':'občnega zbora'})
        assert any(x['signature_normalized']=='občen zbor' for x in signatures['signatures'])
        assert signatures['provenance']['fingerprint']=='fa2fe5c58891375b9953ed868812d3489192602aab97fd2c2a288f9fc0c8db0b'
        syn=call('/v1/resources/thesaurus',query={'lemma':'otrok','limit':5})
        assert syn['count_senses']==5
        assert {r['synonym_type'] for e in syn['entries'] for s in e['senses'] for r in s['relations']} >= {'core','near',None}
        grammar=call('/v1/resources/collocations',query={'lemma':'absoluten','kind':'grammaticalCombination','limit':20})
        assert grammar['collocations'] and all(c['kind']=='grammaticalCombination' for c in grammar['collocations'])
        report['examples']['grammatical_combination']=grammar['collocations'][0]
        text='🙂  šolskim otrokom';selection={'start':3,'end':len(text)}
        explore=call('/v1/explore',{'lang':'sl','text':text,'selection':selection})
        assert explore['selection']['text']=='šolskim otrokom' and explore['provenance']['analysis_calls']==1
        assert explore['results']['morphology']['status']=='ok' and explore['results']['synonyms']['status']=='ok'
        assert explore['results']['collocations']['status']=='ok'
        if a.missing_analyzer:
            assert explore['results']['analysis']['status']=='unavailable'
            assert not explore['results']['synonyms']['data']['contextual_candidates']
            call('/v1/analyze',{'lang':'sl','text':'otrok'},status=503)
        else:
            assert explore['results']['analysis']['status']=='ok'
            assert any(m.get('license') in ('CC BY-NC-SA 3.0','CC-BY-NC-SA-3.0') for m in catalog['models'])
            for engine in ('spacy','classla'):
                analysis=call('/v1/analyze',{'lang':'sl','text':'🙂  Ana bere knjige.\nV Trst je prišla.','engine':engine})
                assert analysis['engine_used']==engine
                assert all(analysis['text'][t['start']:t['end']]==t['text'] for t in analysis['analysis']['tokens'])
                report['examples'][engine]=analysis
            call('/v1/analyze',{'lang':'it','text':'I bambini leggono.','engine':'classla'},status=422)
            it_analysis=call('/v1/analyze',{'lang':'it','text':'I bambini leggono.','engine':'default'})
            assert it_analysis['engine_used']=='spacy'
            nohit=call('/v1/explore',{'lang':'sl','text':'zqxv987654','include':['loris','synonyms','collocations']})
            assert nohit['results']['loris']['status']=='no_hit'
        call('/v1/explore',{'lang':'sl','text':'🙂a','selection':{'start':0,'end':3}},status=422)
        for payload in ({'lang':'sl','tokens':[]},{'lang':'sl','tokens':['two tokens']},{'lang':'de','tokens':['a']},{'lang':'sl','tokens':['a'],'limit':5}):
            call('/v1/morphology/lemmatize',payload,status=422)
        call('/v1/morphology/inflect',{'lang':'sl','lemma':'zbor','limit':101},status=422)
        call('/v1/morphology/signatures',{'lang':'sl','text':'a '*33},status=422)
        report['status']='PASS'
    except Exception as e:
        report.update(status='FAIL',error_type=type(e).__name__,error=str(e));raise
    finally:
        a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');a.report.chmod(0o600)
        print(json.dumps({'status':report['status'],'checks':len(report['checks']),'report':str(a.report)}))


if __name__=='__main__':main()
