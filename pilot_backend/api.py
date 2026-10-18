"""Small authenticated same-origin pilot surface; internal APIs stay private."""
from __future__ import annotations
from contextlib import asynccontextmanager
import asyncio,csv,hashlib,hmac,io,ipaddress,json,os,re,secrets,threading
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,HTMLResponse,FileResponse,RedirectResponse,Response
from starlette.concurrency import run_in_threadpool
from .store import Store,canonical,digest,stamp
from .security import Auth,Failure
from .domain import Domain,text
from .jobs import Jobs
from .adapter import Gateway
from .adoption import historical_source
from .editorial import OPERATOR_POLICY_ID
from . import unified
from .presentation import tester_view,delivery_token,documentary_sources,packaged_documentary_sources
from .project_accounts import COHORT as PROJECT_COHORT

MESSAGES={'sl':{'login_required':'Prijavite se.','login_failed':'Prijava ni uspela.','login_throttled':'Preveč poskusov. Poskusite pozneje.','invalid_request':'Zahteva ni veljavna.','not_found':'Zapis ni na voljo.','forbidden':'Dostop ni dovoljen.','csrf_failed':'Osvežite stran in poskusite znova.','password_change_required':'Najprej spremenite začasno geslo.'},'it':{'login_required':'Accedi.','login_failed':'Accesso non riuscito.','login_throttled':'Troppi tentativi. Riprova più tardi.','invalid_request':'Richiesta non valida.','not_found':'La voce non è disponibile.','forbidden':'Accesso non consentito.','csrf_failed':'Aggiorna la pagina e riprova.','password_change_required':'Prima cambia la password temporanea.'}}

MESSAGES['sl']['generation_waiting_capacity']='Priprava odgovora trenutno ni na voljo. Najdene vire lahko še vedno pregledate.'
MESSAGES['it']['generation_waiting_capacity']='La preparazione della risposta non è al momento disponibile. Puoi comunque consultare le fonti trovate.'

def config_checked(config):
    c=dict(config);base=c.get('base_path','').rstrip('/')
    if base and(not base.startswith('/')or'//'in base or any(x in base for x in ('..','?','#','\\','"',"'",'<','>'))):raise ValueError('Invalid prefix')
    c['base_path']=base;c.setdefault('secure_cookies',True);c.setdefault('workers',2);c.setdefault('tasks',['reference','source_search']);c.setdefault('models',['qwen','gemma'])
    if type(c['workers'])is not int or not 1<=c['workers']<=2:raise ValueError('Configure at most two generator workers')
    origins=c.get('allowed_origins',[])
    if not origins:raise ValueError('Explicit exact allowed origins required')
    for origin in origins:
        p=urlsplit(origin)
        if p.scheme not in {'http','https'}or not p.hostname or p.path or p.query or p.fragment or p.username or origin.rstrip('/')!=origin:raise ValueError('Invalid origin')
        if p.scheme!='https'and(c['secure_cookies']or p.hostname not in {'127.0.0.1','localhost','::1'}):raise ValueError('HTTP is allowed only in explicit loopback development')
    c.setdefault('human_testing_mode',False);c.setdefault('comparisons_enabled',not c['human_testing_mode']);c.setdefault('editorial_full_records',False)
    c.setdefault('unified_answers',False)
    c.setdefault('project_beta',False)
    if type(c['project_beta'])is not bool:raise ValueError('Project beta capability must be boolean')
    c['documentary_sources']=documentary_sources(c['documentary_sources'])if'documentary_sources'in c else packaged_documentary_sources()
    for key in ('beta_updated','beta_release'):
        if key in c and(not isinstance(c[key],str)or not 1<=len(c[key])<=120):raise ValueError('Invalid beta release label')
    if c.get('release_id')is not None and(not isinstance(c['release_id'],str)or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}',c['release_id'])):raise ValueError('Invalid release label')
    if type(c['unified_answers'])is not bool:raise ValueError('Unified answers capability must be boolean')
    if c['unified_answers']and not c.get('klikai_url'):raise ValueError('Unified answers require a configured KLIKai API')
    if not isinstance(c['models'],(list,tuple))or not c['models']or any(not isinstance(model,str)or model not in {'auto','qwen','gemma'}for model in c['models'])or len(set(c['models']))!=len(c['models']):raise ValueError('Configure explicit supported model choices')
    if c['project_beta']and(not c['unified_answers']or'auto'not in c['models']):raise ValueError('Project beta requires unified automatic routing')
    if'auto'in c['models']and not c['unified_answers']:raise ValueError('Automatic model choice requires unified answers')
    if type(c['editorial_full_records'])is not bool:raise ValueError('Editorial full-record capability must be boolean')
    c.setdefault('editorial_operator_policy_id',None)
    if c['editorial_operator_policy_id']is not None and(c['editorial_operator_policy_id']!=OPERATOR_POLICY_ID or not c['editorial_full_records']or not c.get('klikai_url')):raise ValueError('Invalid local editorial operator policy')
    if any(type(c[k])is not bool for k in ('human_testing_mode','comparisons_enabled')):raise ValueError('Testing mode flags must be boolean')
    c['allowed_hosts']={urlsplit(o).netloc for o in origins}
    if not c['secure_cookies']and any(urlsplit(o).hostname not in {'127.0.0.1','localhost','::1'}for o in origins):raise ValueError('Secure cookies required outside loopback')
    if c.get('edge_token')and len(c['edge_token'])<32:raise ValueError('Strong boundary token required')
    return c

def create_app(config,adapter=None,hasher=None,start_workers=True):
    config=config_checked(config);base=config['base_path'];api=base+'/api/pilot';store=Store(config['store']);auth=Auth(store,hasher);adapter=adapter or Gateway(config['gateway_url'],config['gateway_token'],config.get('linghub_url'),config.get('klikai_url'),config['editorial_full_records'],config['editorial_operator_policy_id'],config['unified_answers'],config.get('lexical_profiles',False));domain=Domain(store,config['tasks'],config['models'],release=config.get('beta_release') or config.get('release_id'),generation_admission=getattr(adapter,'require_generation',None),comparisons_enabled=config['comparisons_enabled'],unified_answers=config['unified_answers']);jobs=Jobs(store,adapter,config['workers'],release=config.get('beta_release')or config.get('release_id'));password_gate=threading.BoundedSemaphore(2)
    adapter.unified_answers=config['unified_answers']
    if config.get('human_testing_mode'):
        with store.tx()as db:db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('human_testing_mode','1'))
    cookie='__Host-klikai_session'if config['secure_cookies']and not base else'klikai_session'
    @asynccontextmanager
    async def lifespan(app):
        if start_workers:jobs.start()
        yield
        if start_workers:jobs.close()
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    from .linghub_api import install as install_linghub_api
    install_linghub_api(app,config)
    app.state.store=store;app.state.auth=auth;app.state.domain=domain;app.state.jobs=jobs;app.state.config=config
    def failure(error,locale='sl'):
        return JSONResponse({'error':{'code':error.code,'message':MESSAGES[locale].get(error.code,'Zahteva ni uspela.'if locale=='sl'else'Operazione non riuscita.'),'locale':locale}},status_code=error.status)
    @app.exception_handler(Failure)
    async def handle(request,error):return failure(error,'it'if request.headers.get('X-Pilot-Locale')=='it'else'sl')
    @app.middleware('http')
    async def boundary(request,call_next):
        def early(error):return secure_headers(failure(error,'it'if request.headers.get('X-Pilot-Locale')=='it'else'sl'))
        if request.headers.get('host')not in config['allowed_hosts']:return early(Failure('forbidden',403))
        token=config.get('edge_token')
        if token and not hmac.compare_digest(request.headers.get('X-Pilot-Edge-Token',''),token):return early(Failure('forbidden',403))
        # The authenticated TLS edge reaches this app over loopback HTTP. Its
        # secure deployment permits only HTTPS origins; arbitrary forwarded
        # headers remain untrusted, including when no edge token is configured.
        if token and config['secure_cookies']:request.scope['scheme']='https'
        if request.method not in {'GET','HEAD','POST'}:return early(Failure('invalid_request',405))
        if request.method=='POST':
            if not request.url.path.startswith('/api/linghub/v1/') and (request.headers.get('origin')not in config['allowed_origins']or urlsplit(request.headers.get('origin','')).netloc!=request.headers.get('host')):return early(Failure('forbidden',403))
            if request.headers.get('content-type','').split(';')[0]!='application/json':return early(Failure('invalid_request',415))
            try:
                if int(request.headers.get('content-length','0'))>65536:return early(Failure('request_too_large',413))
            except ValueError:return early(Failure('invalid_request'))
            # Bound chunked bodies too, before JSON parsing or expensive password work.
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>65536:return early(Failure('request_too_large',413))
            request._body=bytes(body)
        response=await call_next(request)
        return secure_headers(response)
    def secure_headers(response):
        response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
        if config['secure_cookies']:response.headers['Strict-Transport-Security']='max-age=31536000'
        return response
    async def payload(request):
        try:data=await request.json()
        except Exception:raise Failure('invalid_request')from None
        if not isinstance(data,dict):raise Failure('invalid_request')
        return data
    def user(request,changed=True,roles=None):
        s=auth.session(request.cookies.get(cookie))
        if request.method=='POST'and not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),s['csrf']):raise Failure('csrf_failed',403)
        if changed and s['must_change']:raise Failure('password_change_required',403)
        if roles and s['role']not in roles:raise Failure('forbidden',403)
        return s
    async def password_call(fn,*args):
        if not password_gate.acquire(blocking=False):raise Failure('login_busy',429)
        try:return await run_in_threadpool(fn,*args)
        finally:password_gate.release()
    def beta(s):return config['project_beta']and s['role']=='tester'
    def shared_group(s):return s.get('cohort')==PROJECT_COHORT
    def present(s,value):return tester_view(value,s['id'],store.meta('audit_salt'))if beta(s)else value
    def operator_presentation(request):
        s=user(request)
        if beta(s):raise Failure('forbidden',403)
        return s
    def capability_result(s):
        if beta(s):return {'project_beta':True,'tasks':['reference'],'unified_answers':True,'comparisons':False,'documentary_sources':list(config['documentary_sources']),**({'beta_updated':config['beta_updated']}if config.get('beta_updated')else{})}
        return {'tasks':list(domain.tasks),'models':list(domain.models),'default_model':domain.default_model,'unified_answers':config['unified_answers'],'comparisons':domain.comparisons_enabled and store.meta('human_testing_mode','0')!='1'and set(domain.comparison_models)=={'qwen','gemma'}}
    def session_result(s):return {'authenticated':True,'csrf_token':s['csrf'],'user':{'id':s['id'],'username':s['username'],'role':s['role'],'must_change_password':bool(s['must_change']),'shared_group':shared_group(s),'can_change_password':not shared_group(s)},'capabilities':capability_result(s)}
    def set_session(token,s):
        result=JSONResponse(session_result(s));result.set_cookie(cookie,token,httponly=True,secure=config['secure_cookies'],samesite='strict',path=base+'/'if base else'/',max_age=auth.absolute);return result
    @app.get(base+'/health')
    async def health():return {'status':'ok',**({'release_id':config['release_id']}if config.get('release_id')else{})}
    @app.get(api+'/session')
    async def session(request:Request):
        try:s=auth.session(request.cookies.get(cookie));return session_result(s)
        except Failure:return {'authenticated':False,'csrf_token':None,'user':None,'capabilities':{}}
    @app.post(api+'/login')
    async def login(request:Request):
        p=await payload(request);username=text(p.get('username'),80);password=text(p.get('password'),1024)
        # Only the actual peer is a rate-limit source. Spoofed forwarded headers are ignored.
        source=request.client.host if request.client else'unknown'
        if config.get('edge_token'):
            try:source=str(ipaddress.ip_address(request.headers['X-Pilot-Client-IP']))
            except(KeyError,ValueError):raise Failure('forbidden',403)from None
        token,s=await password_call(auth.login,username,password,source)
        old=request.cookies.get(cookie)
        if old:auth.logout(old)
        return set_session(token,s)
    @app.post(api+'/logout')
    async def logout(request:Request):
        user(request,False);auth.logout(request.cookies.get(cookie));r=JSONResponse({'acknowledged':True});r.delete_cookie(cookie,path=base+'/'if base else'/');return r
    @app.post(api+'/password')
    async def password(request:Request):
        u=user(request,False)
        if shared_group(u):raise Failure('forbidden',403)
        p=await payload(request);token,s=await password_call(auth.change_password,u['id'],text(p.get('current_password'),1024),text(p.get('new_password'),1024));return set_session(token,s)
    @app.get(api+'/families')
    async def families(request:Request):
        u=user(request);return present(u,domain.families(u['id']))
    @app.get(api+'/capabilities')
    async def capabilities(request:Request):
        u=user(request);result=capability_result(u)
        if beta(u):return result
        if hasattr(adapter,'capabilities'):result.update(await run_in_threadpool(adapter.capabilities))
        return result
    @app.post(api+'/families')
    async def new_family(request:Request):
        u=user(request);p=await payload(request)
        if beta(u):
            if p.get('task','reference')!='reference':raise Failure('forbidden',403)
            p['model']='auto'
        return present(u,await run_in_threadpool(domain.create_family,u['id'],p))
    @app.get(api+'/families/{fid}')
    async def family(fid:str,request:Request):
        u=user(request);return present(u,domain.family(u['id'],fid))
    @app.get(api+'/families/{fid}/sources')
    async def family_sources(fid:str,request:Request):
        u=user(request);return present(u,domain.matches(u['id'],fid,summary=True))
    @app.get(api+'/families/{fid}/matches')
    async def family_matches(fid:str,request:Request,offset:int=0,limit:int=20,source:str|None=None):
        u=user(request);return present(u,domain.matches(u['id'],fid,offset,limit,source=source))
    @app.post(api+'/families/{fid}/expand')
    async def expand(fid:str,request:Request):
        u=user(request)
        if beta(u)and domain.family(u['id'],fid)['model']not in {'auto','qwen'}:raise Failure('expansion_unavailable',409)
        return present(u,await run_in_threadpool(domain.expand,u['id'],fid,await payload(request)))
    @app.get(api+'/jobs/{jid}')
    async def job(jid:str,request:Request):
        u=user(request);return present(u,domain.job(u['id'],jid))
    @app.post(api+'/jobs/{jid}/cancel')
    async def cancel(jid:str,request:Request):
        u=user(request);return present(u,domain.cancel(u['id'],jid))
    @app.post(api+'/runs/{rid}/delivered')
    async def delivered(rid:str,request:Request):
        u=user(request);p=await payload(request)
        if beta(u):
            with store.connect()as db:r=domain.run(db,rid,u['id'])
            with store.connect() as db:
                locale=db.execute('SELECT locale FROM families WHERE id=?',(r['family_id'],)).fetchone()[0]
            from .human_feedback import visible_result
            from .store import digest
            display=r['display_hash'] or digest(visible_result(r,locale).get('blocks',[]))
            expected=delivery_token(store.meta('audit_salt'),u['id'],rid,display)
            if not isinstance(p.get('delivery_token'),str)or not hmac.compare_digest(p['delivery_token'],expected):raise Failure('display_changed',409)
            p={'display_sha256':display,'expanded':p.get('expanded',False)}
        return domain.delivered(u['id'],rid,p)
    async def source_for(owner,fid,eid,tier,aid=None,side=None,current_original=False):
        if tier not in {'short','long'}:raise Failure('invalid_request')
        with store.connect()as db:
            f=domain.owned(db,'families',fid,owner)
            if aid:
                domain.owned(db,'assignments',aid,owner)
                r=db.execute('SELECT r.* FROM assignment_runs ar JOIN runs r ON r.id=ar.run_id WHERE ar.assignment_id=?AND ar.side=?AND ar.tier=?',(aid,side,tier)).fetchone()
            else:r=db.execute('SELECT *FROM runs WHERE family_id=?AND tier=?AND assignment_id IS NULL ORDER BY revision DESC LIMIT 1',(fid,tier)).fetchone()
            bundle=json.loads(f['bundle'])if f['bundle']else{}
            is_unified=bundle.get('contract')==unified.CONTRACT and aid is None
            if not r or(r['state']!='complete'and not(is_unified and r['state']in {'running','cancelled','failed'})):raise Failure('not_found',404)
            evidence=bundle['matches']if is_unified else json.loads(r['result']).get('evidence',[])
            if not any(unified.evidence_id(e)==eid for e in evidence):raise Failure('not_found',404)
            if current_original and not is_unified:raise Failure('not_found',404)
            retained=historical_source(db,fid,r['id'],owner,eid)
        if current_original:
            unified.current_original_discovery(bundle,eid)
            result=await run_in_threadpool(adapter.current_original,bundle,eid)
        else:result=retained if retained is not None else await run_in_threadpool(adapter.source,bundle,eid,tier)
        if isinstance(result.get('evidence'),list):
            evidence=next((e for e in result['evidence']if e.get('citation_id',e.get('evidence_id'))==eid),None)
            if not evidence:raise Failure('not_found',404)
            labels={'jezikovna_svetovalnica':'Jezikovna svetovalnica','epravopis':'ePravopis 2025'}
            result={'evidence_id':eid,'source_label':labels.get(evidence.get('source_slug'),evidence.get('title','Vir')),'excerpt':evidence.get('snippet',''),'locator':evidence.get('locator'),'url':evidence.get('url'),'status':evidence.get('source_status'),'edition':evidence.get('edition'),'bibliography':evidence.get('bibliographic'),'title':evidence.get('title'),'publication_scope':evidence.get('publication_scope'),'annual_scope':evidence.get('annual_scope')}
        return {k:result[k]for k in ('evidence_id','source_label','excerpt','locator','url','status','edition','bibliography','title','publication_scope','annual_scope','current_original_lookup','original_view')if k in result}
    @app.get(api+'/families/{fid}/sources/{eid:path}/current-original')
    async def family_current_original(fid:str,eid:str,request:Request,tier:str='short'):
        u=user(request);return present(u,await source_for(u['id'],fid,eid,tier,current_original=True))
    @app.get(api+'/families/{fid}/sources/{eid:path}')
    async def family_source(fid:str,eid:str,request:Request,tier:str='short'):
        u=user(request);return present(u,await source_for(u['id'],fid,eid,tier))
    @app.post(api+'/feedback')
    async def feedback(request:Request):return domain.feedback(user(request)['id'],await payload(request))
    @app.get(api+'/feedback')
    async def feedback_history(request:Request):
        u=user(request);return present(u,domain.feedback_history(owner=u['id']))
    @app.post(api+'/comparisons')
    async def comparison_new(request:Request):return await run_in_threadpool(domain.create_comparison,operator_presentation(request)['id'],await payload(request))
    @app.get(api+'/comparisons/{aid}')
    async def comparison(aid:str,request:Request):return domain.comparison(operator_presentation(request)['id'],aid)
    @app.post(api+'/comparisons/{aid}/expand')
    async def comparison_expand(aid:str,request:Request):return await run_in_threadpool(domain.expand_comparison,operator_presentation(request)['id'],aid,await payload(request))
    @app.post(api+'/comparisons/{aid}/delivered')
    async def comparison_delivered(aid:str,request:Request):return domain.comparison_delivery(operator_presentation(request)['id'],aid,await payload(request))
    @app.post(api+'/comparisons/{aid}/vote')
    async def comparison_vote(aid:str,request:Request):return domain.vote(operator_presentation(request)['id'],aid,await payload(request))
    @app.post(api+'/comparisons/{aid}/reveal')
    async def comparison_reveal(aid:str,request:Request):return domain.reveal(operator_presentation(request)['id'],aid)
    @app.get(api+'/comparisons/{aid}/sources/{eid:path}')
    async def comparison_source(aid:str,eid:str,request:Request,side:str='A',tier:str='short'):
        owner=operator_presentation(request)['id']
        if side not in {'A','B'}:raise Failure('invalid_request')
        with store.connect()as db:a=domain.owned(db,'assignments',aid,owner)
        return await source_for(owner,a['family_id'],eid,tier,aid,side)
    def accounts():
        with store.connect()as db:return {'accounts':[dict(r)for r in db.execute('SELECT id,username,role,cohort,origin,enabled,must_change,created FROM accounts ORDER BY created')]}
    def protect_last_admin(db,target,actor=None):
        row=db.execute('SELECT *FROM accounts WHERE id=?',(target,)).fetchone()
        if not row:raise Failure('not_found',404)
        if row['role']=='admin'and row['enabled']and db.execute("SELECT count(*)FROM accounts WHERE role='admin'AND enabled=1").fetchone()[0]<=1:raise Failure('last_admin',409)
    @app.get(api+'/admin/accounts')
    async def admin_accounts(request:Request):user(request,roles={'admin'});return accounts()
    @app.post(api+'/admin/accounts')
    async def admin_create(request:Request):
        u=user(request,roles={'admin'});p=await payload(request);await password_call(auth.reauthenticate,u['id'],text(p.get('reauth_password'),1024));aid=await password_call(auth.create,text(p.get('username'),80),text(p.get('temporary_password'),1024),p.get('role','tester'),text(p.get('cohort','pilot'),80),'human',True);store.audit('account_created',u['id'],{'account_id':aid});return {'account_id':aid,'acknowledged':True}
    @app.post(api+'/admin/accounts/{aid}/reset')
    async def admin_reset(aid:str,request:Request):
        u=user(request,roles={'admin'});p=await payload(request);await password_call(auth.reauthenticate,u['id'],text(p.get('reauth_password'),1024))
        with store.connect()as db:
            if not db.execute('SELECT 1 FROM accounts WHERE id=?',(aid,)).fetchone():raise Failure('not_found',404)
        await password_call(auth.reset_password,aid,text(p.get('temporary_password'),1024),True);store.audit('password_reset',u['id'],{'account_id':aid});return {'acknowledged':True}
    @app.post(api+'/admin/accounts/{aid}/disable')
    async def admin_disable(aid:str,request:Request):
        u=user(request,roles={'admin'});p=await payload(request);disabled=p.get('disabled',True)
        if type(disabled)is not bool:raise Failure('invalid_request')
        await password_call(auth.reauthenticate,u['id'],text(p.get('reauth_password'),1024))
        with store.account_lock(),store.tx()as db:
            if disabled:protect_last_admin(db,aid)
            db.execute('UPDATE accounts SET enabled=?WHERE id=?',(int(not disabled),aid));db.execute('DELETE FROM sessions WHERE account_id=?',(aid,));store.audit('account_disabled'if disabled else'account_enabled',u['id'],{'account_id':aid},db)
        return {'acknowledged':True}
    @app.post(api+'/admin/accounts/{aid}/role')
    async def admin_role(aid:str,request:Request):
        u=user(request,roles={'admin'});p=await payload(request);role=p.get('role')
        if not isinstance(role,str)or role not in {'tester','reviewer','admin'}:raise Failure('invalid_request')
        await password_call(auth.reauthenticate,u['id'],text(p.get('reauth_password'),1024))
        with store.account_lock(),store.tx()as db:
            if role!='admin':protect_last_admin(db,aid)
            if not db.execute('SELECT 1 FROM accounts WHERE id=?',(aid,)).fetchone():raise Failure('not_found',404)
            db.execute('UPDATE accounts SET role=?WHERE id=?',(role,aid));db.execute('DELETE FROM sessions WHERE account_id=?',(aid,));store.audit('account_role_changed',u['id'],{'account_id':aid,'role':role},db)
        return {'acknowledged':True}
    @app.post(api+'/admin/accounts/{aid}/delete')
    async def admin_delete(aid:str,request:Request):
        u=user(request,roles={'admin'});p=await payload(request);await password_call(auth.reauthenticate,u['id'],text(p.get('reauth_password'),1024))
        with store.account_lock():
            with store.connect()as db:protect_last_admin(db,aid)
            store.delete_account(aid)
        return {'acknowledged':True}
    @app.get(api+'/admin/feedback')
    async def admin_feedback(request:Request):
        u=user(request,roles={'admin','reviewer'});return domain.feedback_history(reviewer=u['id']if u['role']=='reviewer'else None)
    @app.post(api+'/admin/feedback/{fid}/assign')
    async def assign_feedback(fid:str,request:Request):
        u=user(request,roles={'admin'});p=await payload(request)
        with store.tx()as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=?AND role='reviewer'AND enabled=1",(p.get('reviewer_id'),)).fetchone()or not db.execute('SELECT 1 FROM feedback WHERE id=?',(fid,)).fetchone():raise Failure('not_found',404)
            db.execute('INSERT OR IGNORE INTO review_assignments VALUES(?,?)',(p['reviewer_id'],fid))
        return {'acknowledged':True}
    @app.post(api+'/admin/feedback/{fid}/triage')
    async def triage(fid:str,request:Request):
        from .store import uid
        u=user(request,roles={'admin','reviewer'});p=await payload(request)
        if p.get('status')not in {'open','reviewing','resolved','dismissed'}:raise Failure('invalid_request')
        with store.tx()as db:
            if not db.execute('SELECT 1 FROM feedback WHERE id=?',(fid,)).fetchone()or(u['role']=='reviewer'and not db.execute('SELECT 1 FROM review_assignments WHERE feedback_id=?AND reviewer=?',(fid,u['id'])).fetchone()):raise Failure('not_found',404)
            db.execute('INSERT INTO feedback_triage VALUES(?,?,?,?,?,?)',(uid(),fid,u['id'],p['status'],text(p.get('note',''),8000),stamp()))
        return {'acknowledged':True}
    @app.get(api+'/admin/overview')
    async def overview(request:Request):
        user(request,roles={'admin'})
        with store.connect()as db:
            counts={table:db.execute(f'SELECT count(*)FROM {table}').fetchone()[0]for table in ('accounts','families','feedback','jobs')};cycles=[dict(r)for r in db.execute('SELECT id,kind,status,created,finished FROM cycles ORDER BY created DESC LIMIT 30')];canaries=[dict(r)for r in db.execute('SELECT id,state,created,expires,completed_runs FROM canaries ORDER BY created DESC LIMIT 5')]
        with store.connect()as db:
            latest=db.execute("SELECT id,report FROM cycles WHERE kind='analysis'ORDER BY created DESC LIMIT 1").fetchone()
            activity=[dict(r)for r in db.execute("SELECT event,detail,created FROM audit WHERE event IN ('policy_reverted','canary_observation')ORDER BY seq DESC LIMIT 30")]
        summary=json.loads(latest['report']).get('reports',{})if latest else None;changes=[]
        for event in activity:
            detail=json.loads(event['detail'])
            if event['event']=='policy_reverted':changes.append({'sl':'Nastavitve so bile vrnjene na prejšnjo potrjeno različico.','it':'Le impostazioni sono state ripristinate alla precedente versione confermata.','created':event['created']})
            elif detail.get('state')=='promoted':changes.append({'sl':'Preverjena nastavitev razlage je zaključila omejeni preizkus in postala privzeta za nove odgovore.','it':'Una configurazione verificata ha completato la prova limitata ed è diventata predefinita per le nuove risposte.','created':event['created']})
            elif detail.get('state')=='rolled_back':changes.append({'sl':'Omejeni preizkus je bil ustavljen; prejšnja nastavitev ostaja veljavna.','it':'La prova limitata è stata interrotta; rimane valida la configurazione precedente.','created':event['created']})
        result={'counts':counts,'cycles':cycles,'canaries':canaries,'summary':summary,'analysis_cycle_id':latest['id']if latest else None,'changes':changes,'status':cycles[0]['status']if cycles else 'NO_ANALYSIS_YET','paused':store.meta('adaptation_paused','0')=='1','active_policy':store.meta('active_policy')}
        # Only report a real, recent timer observation supplied by the deployment owner.
        if config.get('scheduler_status_file'):
            from datetime import datetime
            schedule=Path(config['scheduler_status_file'])
            try:
                if schedule.is_file()and not schedule.is_symlink():
                    observed=json.loads(schedule.read_text());age=stamp()-observed.get('observed_at',0)
                    candidate=observed.get('next_run');instant=datetime.fromisoformat(candidate)if isinstance(candidate,str)else None
                    if observed.get('installed')is True and type(observed.get('observed_at'))in {int,float}and 0<=age<86400 and instant and instant.tzinfo and instant.timestamp()>stamp():result['next_run']=candidate
            except(OSError,ValueError,TypeError):pass
        result['human_testing_mode']=store.meta('human_testing_mode','0')=='1'
        result['human_feedback_summary']=json.loads(latest['report']).get('counts',{}).get('human_quality')if latest else None
        return result
    @app.post(api+'/admin/pause')
    async def pause(request:Request):
        u=user(request,roles={'admin'});p=await payload(request)
        if type(p.get('paused'))is not bool:raise Failure('invalid_request')
        if p['paused']is False and store.meta('human_testing_mode','0')=='1':raise Failure('adaptation_deferred',409)
        with store.tx()as db:db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('adaptation_paused','1'if p['paused']else'0'));store.audit('adaptation_pause',u['id'],{'paused':p['paused']},db)
        return {'paused':p['paused']}
    @app.post(api+'/admin/revert')
    async def revert(request:Request):
        from .cycles import revert_policy
        u=user(request,roles={'admin'});p=await payload(request);await password_call(auth.reauthenticate,u['id'],text(p.get('reauth_password'),1024));return revert_policy(store,u['id'])
    def export(owner=None):
        # Export all owned records, not the paginated UI history; never passwords/sessions/signed packets.
        with store.connect()as db:
            owner_ids=[owner]if owner else[r[0]for r in db.execute('SELECT id FROM accounts')]
            exported=[]
            for who in owner_ids:
                account=dict(db.execute('SELECT id,username,role,cohort,origin,created FROM accounts WHERE id=?',(who,)).fetchone())
                ids=[r[0]for r in db.execute('SELECT id FROM families WHERE owner=?ORDER BY created',(who,))]
                assignments=[r[0]for r in db.execute('SELECT id FROM assignments WHERE owner=?ORDER BY created',(who,))]
                events=[dict(r)for r in db.execute('SELECT e.* FROM feedback_events e JOIN feedback f ON f.id=e.feedback_id WHERE f.owner=?ORDER BY e.seq',(who,))]
                votes=[dict(r)for r in db.execute('SELECT id,assignment_id,revision,tier,choice,expanded,created FROM votes WHERE owner=?ORDER BY seq',(who,))]
                for event in events:
                    from .human_feedback import attach
                    attach(db,event)
                    for key in ('tags','anchor','target'):event[key]=json.loads(event[key])if event[key]else None
                exported.append({'account':account,'families':[domain.family(who,fid)for fid in ids],'comparisons':[domain.comparison(who,aid)for aid in assignments],'feedback_revisions':events,'votes':votes})
        with store.connect()as db:
            latest=db.execute("SELECT report FROM cycles WHERE kind='analysis'ORDER BY created DESC LIMIT 1").fetchone()
        return {'schema':'klikai.pilot.export.v1','created':stamp(),'accounts':exported,'latest_analysis':json.loads(latest[0])if latest and owner is None else None,'human_testing_mode':store.meta('human_testing_mode','0')=='1'}
    @app.post(api+'/admin/export')
    async def admin_export(request:Request):
        u=user(request,roles={'admin'});p=await payload(request);await password_call(auth.reauthenticate,u['id'],text(p.get('reauth_password'),1024))
        if p.get('format','json')not in {'json','csv'}:raise Failure('invalid_request')
        data=export()
        if p.get('format')=='csv':
            from .human_feedback import csv_export
            return Response(csv_export(data),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="klikai-pilot-feedback.csv"'})
        return JSONResponse(data,headers={'Content-Disposition':'attachment; filename="klikai-pilot-export.json"'})
    @app.post(api+'/account/export')
    async def account_export(request:Request):
        u=operator_presentation(request);p=await payload(request);await password_call(auth.reauthenticate,u['id'],text(p.get('current_password'),1024));return JSONResponse(export(u['id']),headers={'Content-Disposition':'attachment; filename="klikai-my-records.json"'})
    @app.post(api+'/account/delete')
    async def account_delete(request:Request):
        u=user(request)
        if shared_group(u):raise Failure('forbidden',403)
        p=await payload(request);await password_call(auth.reauthenticate,u['id'],text(p.get('current_password'),1024))
        with store.account_lock():
            with store.connect()as db:protect_last_admin(db,u['id'])
            store.delete_account(u['id'])
        result=JSONResponse({'acknowledged':True});result.delete_cookie(cookie,path=base+'/'if base else'/');return result
    ui=Path(config.get('ui_root',Path(__file__).resolve().parent.parent/'pilot_ui')).resolve()
    @app.get(base+'/assets/{asset:path}')
    async def asset(asset:str):
        if asset not in {'pilot.css','pilot.mjs','core.mjs','source-explorer.mjs','interface-details.mjs','public-release.mjs','i18n/sl.json','i18n/it.json','source-presentation.json','brand/regione-fvg.jpg','brand/slori.png','brand/social-icons.svg'}:raise Failure('not_found',404)
        target=ui/asset
        if not target.is_file()or target.resolve().parent not in {ui,ui/'i18n',ui/'brand'}:raise Failure('not_found',404)
        return FileResponse(target)
    async def shell(locale):
        source=(ui/'index.html').read_text();return HTMLResponse(source.replace('__PILOT_BASE__',base).replace('__PILOT_LOCALE__',locale))
    @app.get(base+'/')
    async def sl_shell():return await shell('sl')
    @app.get(base+'/it/')
    async def it_shell():return await shell('it')
    @app.get(base+'/sl/')
    async def sl_redirect():return RedirectResponse(base+'/',status_code=308)
    return app
