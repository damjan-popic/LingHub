"""Application credentials for fixed read-only LingHub operations; no browser session reuse."""
import asyncio,hashlib,hmac,json,re,time,uuid
from collections import deque
from pathlib import Path
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from .adapter import private_url

PREFIX='/api/linghub/v1'
ROUTES={
 ('GET','capabilities'):('/v1/capabilities','read'),('GET','resources'):('/v1/resources','read'),
 ('POST','analyze'):('/v1/analyze','analysis'),('POST','profile'):('/v1/lexical/profile','lexical'),
 ('GET','paradigm'):('/v1/lexical/paradigm','lexical'),
 ('POST','morphology/lemmatize'):('/v1/morphology/lemmatize','lexical'),
 ('POST','morphology/inflect'):('/v1/morphology/inflect','lexical'),
 ('GET','synonyms'):('/v1/resources/thesaurus','lexical'),('GET','collocations'):('/v1/resources/collocations','lexical')}
PROFILES={'stable':'linghub_lexical_stable_v1','experimental':'klikai_experimental_v1'}

def install(app,config):
 clients_path=config.get('linghub_clients_file');base=config.get('linghub_url')
 if not clients_path or not base:return
 base=private_url(base);stable_base=private_url(config.get('linghub_stable_url')or base);windows={};active={};global_active=0
 def authenticate(request):
  header=request.headers.get('authorization','')
  match=re.fullmatch(r'Bearer ([a-zA-Z0-9_-]{1,64})\.([a-zA-Z0-9_-]{32,128})',header)
  if not match:return None
  try:
   path=Path(clients_path);st=path.stat()
   if st.st_mode&0o077:return None
   config_clients=json.loads(path.read_text())['clients'];item=config_clients.get(match[1])
   if not item or item.get('revoked')or not hmac.compare_digest(item['secret_sha256'],hashlib.sha256(match[2].encode()).hexdigest()):return None
   return match[1],item
  except (OSError,ValueError,KeyError):return None
 @app.api_route(PREFIX+'/{operation:path}',methods=['GET','POST'])
 async def dispatch(operation:str,request:Request):
  nonlocal global_active
  rid=str(uuid.uuid4());started=time.monotonic();auth=authenticate(request);status=503
  def response(data,code):
   nonlocal status
   status=code;return JSONResponse(data,status_code=code,headers={'X-Request-ID':rid,'Cache-Control':'no-store'})
  if not auth:return response({'error':'invalid_client','request_id':rid},401)
  cid,client=auth
  if operation=='openapi.json'and request.method=='GET':
   return response(json.loads(Path(__file__).with_name('linghub-openapi.json').read_text()),200)
  route=ROUTES.get((request.method,operation))
  if not route:return response({'error':'unknown_operation'},404)
  if route[1]not in client.get('scopes',[]):return response({'error':'scope_denied'},403)
  now=time.monotonic();window=windows.setdefault(cid,deque())
  while window and window[0]<now-60:window.popleft()
  if len(window)>=min(60,client.get('requests_per_minute',30))or active.get(cid,0)>=min(2,client.get('concurrency',1))or global_active>=4:return response({'error':'capacity_limit','retry_after_seconds':2},429)
  window.append(now);active[cid]=active.get(cid,0)+1;global_active+=1;status=503;selected='stable'
  try:
   params=dict(request.query_params);selected=params.pop('profile','stable')
   if operation in {'synonyms','collocations'}:
    params.setdefault('limit','8')
    if not params['limit'].isdigit()or not 1<=int(params['limit'])<=20:return response({'error':'record_bound'},422)
   payload=await request.json()if request.method=='POST'else None
   if payload is not None:
    if not isinstance(payload,dict):return response({'error':'object_required'},422)
    selected=payload.pop('profile',selected)
   if selected not in PROFILES or selected not in client.get('profiles',['stable']):return response({'error':'profile_denied'},403)
   if operation in {'profile','analyze'}:
    if not isinstance(payload.get('text'),str)or not 1<=len(payload['text'])<=3000:return response({'error':'text_bound'},422)
    if operation=='profile':payload['profile']=PROFILES[selected]
    else:payload['engine']=PROFILES[selected]
   async with httpx.AsyncClient(timeout=35,trust_env=False,follow_redirects=False)as upstream:
    async with upstream.stream(request.method,(stable_base if selected=='stable'else base)+route[0],params=params,headers={'X-Linghub-Cache-Scope':'api:'+cid},**({'json':payload}if payload is not None else{}))as r:
     status=r.status_code;body=bytearray()
     async for chunk in r.aiter_bytes():
      body.extend(chunk)
      if len(body)>2*1024*1024:return response({'error':'response_bound'},413)
     data=json.loads(body)
   return response(data,status)
  except (httpx.HTTPError,ValueError,TypeError):return response({'error':'linghub_unavailable','request_id':rid},503)
  finally:
   active[cid]-=1;global_active-=1
   print(json.dumps({'event':'linghub_api','request_id':rid,'client':cid,'profile':selected,'operation':operation,'status':status,'duration_ms':round((time.monotonic()-started)*1000,2)}),flush=True)
