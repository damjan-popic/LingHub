"""Bounded private HTTP adapter. No browser-supplied URL, bundle or secret."""
import json
from urllib.parse import urlsplit,urlencode,quote
import httpx
from .security import Failure
from . import terminology, collation, morphology, editorial, unified


def private_url(url):
    p=urlsplit(url)
    if p.scheme!='http'or p.hostname not in {'127.0.0.1','::1','localhost'}or p.username or p.password or p.query or p.fragment:raise ValueError('Backend must be an explicit loopback HTTP URL')
    return url.rstrip('/')

class Gateway:
    def __init__(self,url,token,linghub_url=None,klikai_url=None,editorial_full_records=False,editorial_operator_policy_id=None,unified_answers=False,lexical_profiles=False):
        self.url=private_url(url);self.token=token;self.linghub_url=private_url(linghub_url)if linghub_url else None;self.klikai_url=private_url(klikai_url)if klikai_url else None
        if type(editorial_full_records)is not bool:raise ValueError('Editorial full-record capability must be boolean')
        if type(unified_answers)is not bool:raise ValueError('Unified answers capability must be boolean')
        self.editorial_full_records=editorial_full_records
        self.unified_answers=unified_answers
        if type(lexical_profiles)is not bool:raise ValueError("Lexical opt-in must be boolean")
        self.lexical_profiles=lexical_profiles
        if editorial_operator_policy_id is not None and (editorial_operator_policy_id!=editorial.OPERATOR_POLICY_ID or not editorial_full_records or not self.klikai_url):raise ValueError('Invalid local editorial operator policy')
        self.editorial_operator_policy_id=editorial_operator_policy_id
        if not token or len(token)<32:raise ValueError('Configure a strong private gateway token')
    def request(self,path,payload,base=None):
        try:
            with httpx.Client(base_url=base or self.url,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(10 if base else 3600 if path=='/internal/pilot/generate'else 240,connect=5))as client:
                editorial_auth=bool(self.editorial_operator_policy_id and base==self.klikai_url and path.startswith('/api/editorial/records/'))
                with client.stream('GET'if payload is None else'POST',path,**({}if payload is None else{'json':payload}),headers={'X-Pilot-Internal-Token':self.token}if base is None or editorial_auth else{})as response:
                    if response.status_code!=200:
                        failure=Failure('answer_unavailable',503)
                        if base is None and path=='/internal/pilot/generate':
                            # Preserve useful classifications without retaining an
                            # upstream response body, URL, credential or traceback.
                            from .human_feedback import PROVIDER_FAILURE_CODES
                            raw=bytearray()
                            for chunk in response.iter_bytes():
                                raw.extend(chunk[:32769-len(raw)])
                                if len(raw)>32768:break
                            try:
                                data=json.loads(raw)if len(raw)<=32768 else{}
                                detail=(data.get('error')or data.get('detail')or data.get('provider_failure')or{})if isinstance(data,dict)else{}
                                code=detail.get('code')if isinstance(detail,dict)else None
                                if isinstance(code,str)and code in PROVIDER_FAILURE_CODES:failure=Failure(code,503)
                            except (ValueError,TypeError):pass
                            failure.http_status=response.status_code
                        raise failure
                    content=bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content)>8*1024*1024:raise Failure('answer_unavailable',503)
                    data=json.loads(content)
                    if not isinstance(data,dict):raise Failure('answer_unavailable',503)
                    return data
        except httpx.TimeoutException:raise Failure('source_timeout'if base else'answer_unavailable',503)from None
        except (httpx.HTTPError,ValueError,TypeError):raise Failure('answer_unavailable',503)from None
    def capabilities(self):
        """Short, read-only readiness observation; never probes by generating text."""
        unavailable={'generation':{'status':'GENERATION_WAITING_CAPACITY','models':{name:{'available':False,'status':'GENERATION_WAITING_CAPACITY'}for name in ('auto','qwen','gemma')}},'dense':{'available':False,'status':'DENSE_ENCODING_WAITING_CAPACITY'},'lexical':{'available':False},'tools':self.tool_capabilities()}
        try:
            with httpx.Client(base_url=self.url,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(2,connect=.5))as client:
                with client.stream('GET','/internal/pilot/capabilities',headers={'X-Pilot-Internal-Token':self.token})as response:
                    if response.status_code!=200:return unavailable
                    raw=bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>32768:return unavailable
                    value=json.loads(raw)
            for name in ('auto','qwen','gemma'):
                item=value.get('generation',{}).get('models',{}).get(name,{})
                ready=item.get('available')is True
                safe={'available':ready,'status':item.get('status')if item.get('status')in {'AVAILABLE','SHARED_READY','CPU_LOADING','CPU_BUSY','CPU_READY','GENERATION_WAITING_CAPACITY'}else'AVAILABLE'if ready else'GENERATION_WAITING_CAPACITY','device':item.get('device')if item.get('device')in {'cpu','gpu'}else None,'gpu_available':item.get('gpu_available')is True}
                from .human_feedback import public_routing
                safe.update(public_routing(item))
                cpu=item.get('cpu',{})
                if isinstance(cpu,dict):safe['cpu']={k:v for k,v in cpu.items()if(k in {'available','loaded'}and type(v)is bool)or(k=='availability'and v=='lazy')or(k=='phase'and isinstance(v,str)and v in {'ready','loading','running','generating','idle','busy','unavailable'})or(k in {'queued_or_running','queue_capacity'}and type(v)is int and 0<=v<=100)}
                unavailable['generation']['models'][name]=safe
            if any(m['available']for m in unavailable['generation']['models'].values()):unavailable['generation']['status']='AVAILABLE'
            for name in ('dense','lexical'):
                ready=value.get(name,{}).get('available')is True
                unavailable[name]={'available':ready,'status':'AVAILABLE'if ready else'DENSE_ENCODING_WAITING_CAPACITY'if name=='dense'else'UNAVAILABLE'}
            return unavailable
        except (httpx.HTTPError,ValueError,TypeError,AttributeError):return unavailable
    def tool_capabilities(self):
        def health(base,path):
            if not base:return {}
            try:
                with httpx.Client(base_url=base,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(.5,connect=.25))as client:
                    with client.stream('GET',path)as response:
                        if response.status_code!=200:return {}
                        raw=bytearray()
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw)>32768:return {}
                        value=json.loads(raw)
                return value if isinstance(value,dict)and value.get('status')=='ok'else{}
            except (httpx.HTTPError,ValueError,TypeError):return {}
        klikai=health(self.klikai_url,'/api/health');ling=health(self.linghub_url,'/health')
        def obj(value,key):
            item=value.get(key);return item if isinstance(item,dict)else{}
        tasks={'terminology':{'configured':bool(self.klikai_url),'available':bool(klikai)and obj(klikai,'morphology').get('usable')is True},'morphology':{'configured':bool(self.linghub_url),'available':obj(obj(ling,'resources'),'morphology').get('available')is True},'text_check':{'configured':bool(self.linghub_url),'available':ling.get('analyzer_service_reachable')is True}}
        for source in editorial.SOURCES:
            tasks[source]={'configured':bool(self.klikai_url),'available':bool(klikai)}
        configured=[v for v in tasks.values()if v['configured']]
        return {'configured':bool(configured),'available':bool(configured)and all(v['available']for v in configured),'tasks':tasks,'observation':'read_only_cpu_health'}
    def require_generation(self,models):
        observed=self.capabilities()
        if any(not observed['generation']['models'].get(model,{}).get('available')for model in models):raise Failure('generation_waiting_capacity',503)
    def freeze(self,family,policy):
        if family['task']=='reference'and self.unified_answers:
            return unified.freeze(self,family,policy)
        if family['task'] in editorial.SOURCES:
            if not self.klikai_url:raise Failure('task_unavailable',503)
            query=family['question']
            if not 2<=len(query)<=500:raise Failure('invalid_request')
            path='/api/search?'+urlencode({'q':query,'sources':family['task'],'limit':editorial.LIMIT})
            frozen=editorial.freeze(self.request(path,None,self.klikai_url),query,family['task'],family['locale'])
            if self.editorial_full_records and frozen['bundle']['hits']:
                # Discovery is validated before a bounded detail call per record.
                # A denial/error never falls back to a stale discovery excerpt.
                records=[self.request('/api/editorial/records/'+quote(family['task'],safe='')+'/'+quote(hit['resource_id'],safe=''),None,self.klikai_url)
                         for hit in frozen['bundle']['hits']]
                return editorial.freeze_records(frozen['bundle'],records,self.editorial_operator_policy_id)
            return frozen
        if family['task']=='morphology':
            if not self.linghub_url:raise Failure('task_unavailable',503)
            lang=family.get('input_language');tokens=family['question'].split()
            if lang not in {'sl','it'}or not 1<=len(tokens)<=32:raise Failure('invalid_request')
            data=self.request('/v1/morphology/lemmatize',{'lang':lang,'tokens':tokens,'limit':4},self.linghub_url)
            return morphology.freeze(data,tokens,lang,family['locale'])
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
        frozen=self.request('/internal/pilot/freeze',request)
        if family['task']=='reference'and tools is not None:
            # CPU collection precedes gateway validation/projection. Keep its
            # duration separate from that gateway's tool_evidence_ms observation.
            frozen.setdefault('timings',{})['cpu_collation_ms']=tools.get('timings',{}).get('tool_evidence_ms')
        return frozen
    def generate(self,bundle,tier,model,parent=None):
        if bundle.get('contract')==unified.CONTRACT:return unified.generate(self,bundle,tier,model,parent)
        if bundle.get('task')=='editorial_search':return editorial.answer(bundle,tier)
        if bundle.get('task')=='morphology':return morphology.answer(bundle,tier)
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
        if bundle.get('contract')==unified.CONTRACT:return unified.source(bundle,evidence_id)
        if bundle.get('task')=='editorial_search':return editorial.source(bundle,evidence_id,tier)
        if bundle.get('task')=='morphology':return morphology.source(bundle,evidence_id,tier)
        if bundle.get('task')=='terminology':return terminology.source(bundle,evidence_id,tier)
        return self.request('/internal/pilot/source',{'bundle':bundle,'evidence_id':evidence_id,'tier':tier,'bibliographic':True})
    def current_original(self,bundle,evidence_id):
        return unified.current_original(self,bundle,evidence_id)


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
