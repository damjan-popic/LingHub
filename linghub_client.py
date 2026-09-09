"""Small standard-library LingHub client and private resumable JSONL CLI."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


class ClientError(Exception):
    def __init__(self,message,status=None): self.status=status;super().__init__(message)


class LingHubClient:
    def __init__(self,base_url='http://127.0.0.1:8010',timeout=35,retries=2):
        u=urlsplit(base_url)
        if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost') or not u.port or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:
            raise ValueError('Use an explicit local HTTP origin and port.')
        if not 0<timeout<=120 or not 0<=retries<=3: raise ValueError('Timeout/retry bounds exceeded.')
        self.base_url=base_url.rstrip('/');self.timeout=timeout;self.retries=retries
        self.opener=build_opener(ProxyHandler({}),NoRedirect())

    def request(self,path,payload=None,query=None):
        if not path.startswith('/v1/') or '?' in path: raise ValueError('Only fixed versioned API routes are allowed.')
        request=Request(self.base_url+path+('?' + urlencode(query) if query else ''),
            data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,
            headers={'Content-Type':'application/json'})
        for attempt in range(self.retries+1):
            try:
                with self.opener.open(request,timeout=self.timeout) as response:
                    raw=response.read(2097153)
                if len(raw)>2097152: raise ClientError('Response exceeds2MiB limit.')
                return json.loads(raw)
            except HTTPError as error:
                if error.code not in (429,502,503,504) or attempt==self.retries:
                    raise ClientError('LingHub returned HTTP'+str(error.code),error.code) from None
            except (URLError,TimeoutError):
                if attempt==self.retries: raise ClientError('LingHub is unavailable.') from None
            except (ValueError,UnicodeError): raise ClientError('LingHub response is not valid JSON.') from None
            time.sleep(.2*(2**attempt))

    def analyze(self,lang,text,engine='default'):
        return self.request('/v1/analyze',{'lang':lang,'text':text,'engine':engine})
    def explore(self,lang,text,selection=None,engine='default',include=None):
        payload={'lang':lang,'text':text,'engine':engine}
        if selection is not None:payload['selection']=selection
        if include is not None:payload['include']=include
        return self.request('/v1/explore',payload)
    def lemmatize(self,lang,tokens,limit=4):
        return self.request('/v1/morphology/lemmatize',{'lang':lang,'tokens':tokens,'limit':limit})
    def inflect(self,lang,lemma,limit=40,msd=None):
        return self.request('/v1/morphology/inflect',{'lang':lang,'lemma':lemma,'limit':limit,'msd':msd})
    def signatures(self,lang,text,limit=32):
        return self.request('/v1/morphology/signatures',{'lang':lang,'text':text,'limit':limit})
    def dictionary(self,kind,lemma,limit=20):
        if kind not in ('synonyms','collocations'):raise ValueError('Unknown dictionary task.')
        return self.request('/v1/resources/thesaurus' if kind=='synonyms' else '/v1/resources/collocations',query={'lemma':lemma,'limit':limit})


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def private_directory(path):
    if path.is_symlink(): raise ValueError('Output directory must not be a symlink.')
    if not path.exists(): path.mkdir(mode=0o700,parents=False)
    s=path.stat()
    if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)&0o077:
        raise ValueError('Choose a private output directory owned by this user (mode0700).')


def atomic(path,content):
    if path.is_symlink(): raise ValueError('Output files must not be symlinks.')
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as out:out.write(content);out.flush();os.fsync(out.fileno())
        os.replace(name,path)
        directory_fd=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
    finally:
        if os.path.exists(name):os.unlink(name)


def execute_record(client,record):
    task=record['task'];lang=record['lang'];text=record['text'];engine=record['engine']
    if len(text)>10000 or not text.strip(): raise ValueError('Text must contain1..10000characters; automatic chunking is not performed.')
    if lang not in ('sl','it') or engine not in ('default','spacy','classla'): raise ValueError('Unsupported language or engine.')
    if task=='analyze':return client.analyze(lang,text,engine)
    if task=='explore':return client.explore(lang,text,record.get('selection'),engine,record.get('include'))
    if task=='lemmatize':return client.lemmatize(lang,record.get('tokens',text.split()),record.get('limit',4))
    if task=='inflect':return client.inflect(lang,text,record.get('limit',40),record.get('msd'))
    if task=='signatures':return client.signatures(lang,text,record.get('limit',32))
    if task in ('synonyms','collocations'):
        if lang!='sl':raise ValueError('These dictionaries are Slovene-only.')
        return client.dictionary(task,text,record.get('limit',20))
    raise ValueError('Unknown task.')


def process_jsonl(client,input_path,output_dir,resume=False):
    """Persist each completed item before atomically publishing ordered output.

    Raw input appears only in operator-selected private output. Progress manifests
    contain IDs/hashes/status, never raw text. A changed item or capability identity
    is recomputed; unchanged successful items are reused without duplicates.
    """
    output_dir=Path(output_dir);private_directory(output_dir)
    lockfd=os.open(output_dir/'.batch.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with Path(input_path).open('rb') as source: raw=source.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('Input JSONL exceeds8MiB.')
        records=[];ids=set()
        for line_no,line in enumerate(raw.splitlines(),1):
            if not line.strip():continue
            if len(line)>65536:raise ValueError('JSONL line exceeds64KiB at line'+str(line_no))
            value=json.loads(line)
            if not isinstance(value,dict) or not all(isinstance(value.get(k),str) for k in ('id','lang','text','task','engine')):
                raise ValueError('Each record requires string id,lang,text,task,engine.')
            if not value['id'] or len(value['id'])>256 or value['id'] in ids:raise ValueError('IDs must be unique nonempty strings of at most256characters.')
            ids.add(value['id']);records.append(value)
            if len(records)>500:raise ValueError('At most500documents per bounded batch.')
        capabilities=client.request('/v1/capabilities');identity=capabilities['identity']
        manifest_path=output_dir/'progress.json';slots=output_dir/'items';private_directory(slots)
        previous={}
        if manifest_path.exists():
            if not resume:raise ValueError('Output already contains progress; use resume explicitly.')
            if manifest_path.is_symlink():raise ValueError('Progress must not be a symlink.')
            previous=json.loads(manifest_path.read_text())
        manifest={'schema':'linghub.batch.v1','input_sha256':hashlib.sha256(raw).hexdigest(),
            'capability_identity':identity,'document_count':len(records),'completed':{},'failed_ids':[],
            'status':'running','reused':0,'processed':0,
            'previous_input_changed':bool(previous and previous.get('input_sha256')!=hashlib.sha256(raw).hexdigest()),
            'previous_identity_changed':bool(previous and previous.get('capability_identity')!=identity)}
        output=[];total_bytes=0
        def publish():
            atomic(output_dir/'results.jsonl',b''.join(canonical(x)+b'\n' for x in output))
            atomic(manifest_path,canonical(manifest)+b'\n')
        publish()
        try:
            for record in records:
                key=digest({'input':record,'task':record['task'],'engine':record['engine'],'capability_identity':identity})
                old=previous.get('completed',{}).get(record['id']);slot=slots/(key+'.json');result=None
                if old and old.get('key')==key and old.get('status') in ('ok','no_hit') and slot.is_file() and not slot.is_symlink():
                    content=slot.read_bytes()
                    if hashlib.sha256(content).hexdigest()!=old.get('sha256'):raise ValueError('Retained result checksum mismatch for '+record['id'])
                    result=json.loads(content);manifest['reused']+=1
                if result is None:
                    try:
                        data=execute_record(client,record)
                        if client.request('/v1/capabilities')['identity']!=identity:
                            raise ClientError('Resource or analyzer identity changed during batch; resume with the current identity.')
                        status=data.get('status','ok')
                        result={'id':record['id'],'status':status,'task':record['task'],'engine':record['engine'],
                            'input_sha256':digest(record),'capability_identity':identity,'result':data,
                            'provenance':data.get('provenance',data.get('resource',{}))}
                    except (ClientError,ValueError,TypeError,KeyError) as error:
                        result={'id':record['id'],'status':'error','task':record['task'],'engine':record['engine'],
                            'input_sha256':digest(record),'capability_identity':identity,
                            'error':{'type':type(error).__name__,'message':str(error),'http_status':getattr(error,'status',None)}}
                    content=canonical(result)+b'\n'
                    if len(content)>2097152:raise ValueError('Per-document result exceeds2MiB.')
                    atomic(slot,content);manifest['processed']+=1
                else:content=slot.read_bytes()
                total_bytes+=len(content)
                if total_bytes>64*1024*1024:raise ValueError('Batch output exceeds64MiB.')
                output.append(result)
                manifest['completed'][record['id']]={'key':key,'sha256':hashlib.sha256(content).hexdigest(),'status':result['status']}
                if result['status'] not in ('ok','no_hit'):manifest['failed_ids'].append(record['id'])
                publish()
            manifest['status']='complete_with_errors' if manifest['failed_ids'] else 'complete'
        except BaseException:
            manifest['status']='interrupted';publish();raise
        finally:publish()
        return manifest
    finally:os.close(lockfd)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8010')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('analyze','explore','lemmatize','inflect','signatures','synonyms','collocations'):
        p=sub.add_parser(name);p.add_argument('text');p.add_argument('--lang',choices=['sl','it'],default='sl')
        p.add_argument('--engine',choices=['default','spacy','classla'],default='default')
        p.add_argument('--start',type=int);p.add_argument('--end',type=int)
        p.add_argument('--limit',type=int);p.add_argument('--msd')
    p=sub.add_parser('batch');p.add_argument('input',type=Path);p.add_argument('output_dir',type=Path);p.add_argument('--resume',action='store_true')
    args=parser.parse_args(argv);client=LingHubClient(args.base_url)
    try:
        if args.command=='batch':
            result=process_jsonl(client,args.input,args.output_dir,args.resume)
            print(json.dumps({'status':result['status'],'completed':len(result['completed']),'reused':result['reused'],'failed_ids':result['failed_ids']},ensure_ascii=False))
            return 1 if result['failed_ids'] else 0
        record={'id':'single','lang':args.lang,'text':args.text,'task':args.command,'engine':args.engine}
        if args.limit is not None:record['limit']=args.limit
        if args.msd is not None:record['msd']=args.msd
        if args.start is not None or args.end is not None:
            if args.start is None or args.end is None:raise ValueError('Both start and end are required.')
            record['selection']={'start':args.start,'end':args.end}
        result=execute_record(client,record)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result.get('status','ok') in ('ok','no_hit') else 1
    except (ClientError,ValueError,OSError) as error:
        print(json.dumps({'status':'error','error':str(error)},ensure_ascii=False),file=sys.stderr);return 1
    except KeyboardInterrupt:
        print('Interrupted; completed items are private and resumable.',file=sys.stderr);return 130


if __name__=='__main__':raise SystemExit(main())
