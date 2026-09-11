"""Bounded private HTTP adapter. No browser-supplied URL, bundle or secret."""
import json
from urllib.parse import urlsplit,urlencode
import httpx
from .security import Failure
from .store import digest
from . import terminology, collation


def private_url(url):
    p=urlsplit(url)
    if p.scheme!='http'or p.hostname not in {'127.0.0.1','::1','localhost'}or p.username or p.password or p.query or p.fragment:raise ValueError('Backend must be an explicit loopback HTTP URL')
    return url.rstrip('/')

class Gateway:
    def __init__(self,url,token,linghub_url=None,klikai_url=None):
        self.url=private_url(url);self.token=token;self.linghub_url=private_url(linghub_url)if linghub_url else None;self.klikai_url=private_url(klikai_url)if klikai_url else None
        if not token or len(token)<32:raise ValueError('Configure a strong private gateway token')
    def request(self,path,payload,base=None):
        try:
            with httpx.Client(base_url=base or self.url,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(10 if base else 240,connect=5))as client:
                with client.stream('GET'if payload is None else'POST',path,**({}if payload is None else{'json':payload}),headers={'X-Pilot-Internal-Token':self.token}if base is None else{})as response:
                    if response.status_code!=200:raise Failure('answer_unavailable',503)
                    content=bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content)>8*1024*1024:raise Failure('answer_unavailable',503)
                    data=json.loads(content)
                    if not isinstance(data,dict):raise Failure('answer_unavailable',503)
                    return data
        except (httpx.HTTPError,ValueError,TypeError):raise Failure('answer_unavailable',503)from None
    def freeze(self,family,policy):
        if family['task']=='terminology':
            if not self.klikai_url:raise Failure('task_unavailable',503)
            query=family['question']
            if not 2<=len(query)<=500:raise Failure('invalid_request')
            path='/api/terminology/lookup?'+urlencode({'q':query,'allow_partial':'false','include_prose':'false','use_morphology':'true','limit':20})
            return terminology.freeze_lookup(self.request(path,None,self.klikai_url),query,family['locale'])
        if family['task']=='text_check':
            if not self.linghub_url:raise Failure('task_unavailable',503)
            if family.get('input_language')not in {'sl','it'}:raise Failure('input_language_required')
            capabilities=self.request('/v1/capabilities',None,self.linghub_url)
            if not capabilities.get('identity')or not capabilities.get('analyzer_service_reachable'):raise Failure('task_unavailable',503)
            identity={k:capabilities[k]for k in ('identity','resources','models','defaults')if k in capabilities}
            return {'bundle':{'text':family['question'],'lang':family['input_language'],'output_locale':family['locale'],'task':'text_check','capabilities_identity':capabilities['identity']},'identity':identity}
        request={'question':family['question'],'context':family['context'],'locale':family['locale'],'task':family['task'],'policy':policy}
        if family['task']=='reference':
            tools=collation.collect(self,family)
            if tools is not None:request['tool_evidence']=tools
        return self.request('/internal/pilot/freeze',request)
    def generate(self,bundle,tier,model,parent=None):
        if bundle.get('task')=='terminology':return terminology.answer(bundle,tier)
        if bundle.get('task')=='text_check':
            if not self.linghub_url:raise Failure('task_unavailable',503)
            before=self.request('/v1/capabilities',None,self.linghub_url)
            if before.get('identity')!=bundle['capabilities_identity']:raise Failure('resource_changed',503)
            data=self.request('/v1/analyze',{'text':bundle['text'],'lang':bundle['lang'],'engine':'default','include_loris':True},self.linghub_url)
            after=self.request('/v1/capabilities',None,self.linghub_url)
            expected=before['defaults'].get(bundle['lang'])
            if after.get('identity')!=bundle['capabilities_identity']or data.get('text')!=bundle['text']or data.get('lang')!=bundle['lang']or data.get('engine_used')!=expected or data.get('offset_convention')!='unicode_codepoint_half_open':raise Failure('resource_changed',503)
            return deterministic_answer(data,bundle.get('output_locale',bundle['lang']))
        return self.request('/internal/pilot/generate',{'bundle':bundle,'tier':tier,'model':model,'parent_answer':parent})
    def source(self,bundle,evidence_id,tier):
        if bundle.get('task')=='terminology':return terminology.source(bundle,evidence_id,tier)
        return self.request('/internal/pilot/source',{'bundle':bundle,'evidence_id':evidence_id,'tier':tier,'bibliographic':True})


def deterministic_answer(data,locale):
    """Human-readable labels around exact authoritative payloads; never model synthesis."""
    it=locale=='it';seen=set();findings=[]
    for issue in data.get('issues',[])+data.get('spans',[]):
        key=(issue.get('rule_id'),issue.get('start'),issue.get('end'))
        if key in seen:continue
        seen.add(key);findings.append(issue)
    intro=(f"Voci da consultare: {len(findings)}."if it else f"Iztočnice za preverjanje: {len(findings)}.")if findings else("Le regole disponibili non hanno segnalato casi specifici in questo testo. Questo controllo non copre ogni possibile problema linguistico."if it else"Razpoložljiva pravila v tem besedilu niso označila posebnih primerov. Pregled ne zajema vseh možnih jezikovnih težav.")
    if len(findings)>50:intro+=(' Sono mostrate le prime50 voci.'if it else' Prikazanih je prvih50 iztočnic.')
    blocks=[{'block_id':'summary','text':intro}]
    labels={'Izhodisce':'Voce'if it else'Iztočnica','Iscete':'Espressione cercata'if it else'Iskani izraz','SteMislili':'Possibilità da verificare'if it else'Možnosti za preverjanje','Npr':'Esempio originale'if it else'Izvirni primer','Vslo':'Spiegazione originale'if it else'Izvirno pojasnilo'}
    for index,issue in enumerate(findings[:50]):
        start,end=issue.get('start'),issue.get('end');original=data.get('text','')
        if type(start)is not int or type(end)is not int or not 0<=start<end<=len(original):raise Failure('invalid_answer',502)
        lines=[original[start:end]];payload=issue.get('payload',{})
        for key,label in labels.items():
            if isinstance(payload.get(key),str)and payload[key].strip()and payload[key]!='/':lines.append(label+': '+payload[key])
        blocks.append({'block_id':'finding-'+str(index),'text':'\n'.join(lines)})
    text='\n\n'.join(b['text']for b in blocks)
    return {'status':'validated','short_answer':intro,'answer_md':text,'display_blocks':blocks,'source_badges':[{'label':'LORIS','source_slug':'loris'}],'evidence':[],'answer_kind':'deterministic','answer_support':'supported','generation_status':'not_generated','deterministic_result':data,'limitations':[]}
