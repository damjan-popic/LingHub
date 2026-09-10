"""Explicit private recovery support, including the irreplaceable evidence signing key."""
from __future__ import annotations
import hashlib,hmac,json,os,re,shutil,stat,tarfile,tempfile
from pathlib import Path
from .store import canonical,digest,stamp
from .backup import backup,verify_backup,restore,sha,private_directory
from .cycles import private_write

MEMBERS={'manifest.json','feedback.tar.gz','current-deletions.jsonl','evidence-signing-key','config-backend.json','config-gateway.json','pins.json'}

def sanitized_config(value,*,pins=False):
    """Only explicit config/pin metadata; raw bodies and plaintext credentials are refused."""
    def visit(node):
        if isinstance(node,dict):
            for key,item in node.items():
                if not isinstance(key,str):raise ValueError('Invalid recovery metadata key')
                lowered=key.lower()
                if any(x in lowered for x in('password','credential','secret')):raise ValueError('Credential field forbidden in recovery metadata')
                if ('token'in lowered or lowered.endswith('key'))and not lowered.endswith(('_file','_sha256','_hash')):
                    if not(lowered.endswith('_file')or lowered.endswith('_sha256')or lowered in{'max_output_tokens','max_new_tokens','tokenizer_revision','tokenizer_repository','tokenizer_python','tokenizer','max_tokens','reasoning_token_budget'}):raise ValueError('Plaintext credential field forbidden in recovery metadata')
                if lowered in{'question','answer_md','short_answer','claims','source_text','raw_text','reasoning_content','reasoning','payload','content'}:raise ValueError('Raw body field forbidden in recovery metadata')
                visit(item)
        elif isinstance(node,list):
            for item in node:visit(item)
        elif isinstance(node,str):
            if len(node)>8192 or re.search(r'(?i)(?:postgres(?:ql)?|https?)://[^/\s:@]+:[^/\s@]+@',node)or '-----BEGIN PRIVATE KEY-----'in node or re.search(r'\bhf_[A-Za-z0-9]{20,}\b',node):raise ValueError('Credential or blob forbidden in recovery metadata')
        elif node is not None and type(node)not in{int,float,bool}:raise ValueError('Invalid recovery metadata type')
    if not isinstance(value,dict):raise ValueError('Recovery metadata must be an object')
    visit(value)
    if len(canonical(value).encode())>1048576:raise ValueError('Recovery metadata exceeds bound')
    if pins and not value.get('git_commits'):raise ValueError('Recovery code identities must be pinned')
    return value

def ledger_bytes(path):
    path=Path(path)
    if not path.is_file():return b''
    if path.is_symlink()or path.stat().st_size>64*1024**2:raise ValueError('Invalid current deletion ledger')
    data=path.read_bytes();previous='0'*64
    for index,line in enumerate(data.splitlines(),1):
        row=json.loads(line);h=row.pop('record_hash')
        if row.get('seq')!=index or row.get('previous_hash')!=previous or digest(row)!=h:raise ValueError('Deletion ledger differs')
        previous=h
    if data and not data.endswith(b'\n'):raise ValueError('Incomplete deletion ledger')
    return data

def archive_old_ledger(feedback):
    with tarfile.open(feedback,'r:gz')as tar:return tar.extractfile('pilot.deletions.jsonl').read()

def build_support(feedback_archive,current_ledger,signing_key,backend_config,gateway_config,pins_file,archive):
    receipt=verify_backup(feedback_archive);key=Path(signing_key)
    if key.is_symlink()or not key.is_file()or stat.S_IMODE(key.stat().st_mode)!=0o600 or not 32<=key.stat().st_size<=4096:raise ValueError('Evidence signing key must be a protected0600 file')
    backend=sanitized_config(json.loads(Path(backend_config).read_text()));gateway=sanitized_config(json.loads(Path(gateway_config).read_text()));pins=sanitized_config(json.loads(Path(pins_file).read_text()),pins=True)
    ledger=ledger_bytes(current_ledger)
    if not ledger.startswith(archive_old_ledger(feedback_archive)):raise ValueError('Current deletion ledger must extend feedback archive')
    archive=Path(archive).absolute();archive.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if archive.exists()or archive.is_symlink():raise ValueError('Recovery support archive already exists')
    with tempfile.TemporaryDirectory(prefix='.pilot-support-',dir=archive.parent)as temporary:
        root=Path(temporary)
        fd=os.open(root/'feedback.tar.gz',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb')as out,Path(feedback_archive).open('rb')as source:shutil.copyfileobj(source,out)
        for name,value in(('current-deletions.jsonl',ledger),('evidence-signing-key',key.read_bytes())):
            fd=os.open(root/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb')as out:out.write(value)
        if sha(root/'feedback.tar.gz')!=receipt['sha256']:raise ValueError('Feedback archive changed while packaging')
        for name,value in(('config-backend.json',backend),('config-gateway.json',gateway),('pins.json',pins)):private_write(root/name,canonical(value)+'\n')
        manifest={'schema':'klikai.pilot.private-recovery-support.v1','created':stamp(),'members':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size}for p in root.iterdir()},'feedback_archive_sha256':receipt['sha256'],'offhost_verified':False,'excluded':['bootstrap_password','session_tokens','edge_token','gateway_token','model_weights','raw_source_blobs'],'runtime_reopening_requires':['matching_fixed_code_and_policy','source_vector_and_model_releases','rotated_matching_internal_auth_tokens','preserved_evidence_signing_key']}
        private_write(root/'manifest.json',canonical(manifest)+'\n')
        fd=os.open(archive,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb')as output,tarfile.open(fileobj=output,mode='w:gz')as tar:
            for name in sorted(MEMBERS):
                info=tar.gettarinfo(str(root/name),arcname=name);info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o600
                with(root/name).open('rb')as source:tar.addfile(info,source)
    return {'status':'PACKAGED','archive':str(archive),'sha256':sha(archive),'bytes':archive.stat().st_size,'offhost_verified':False,'manifest':manifest}

def restore_support(archive,destination,expected_sha256):
    archive=Path(archive)
    attrs=archive.lstat()
    if not stat.S_ISREG(attrs.st_mode)or stat.S_IMODE(attrs.st_mode)!=0o600 or sha(archive)!=expected_sha256:raise ValueError('Recovery support archive identity or protection differs')
    root=private_directory(destination)
    with tarfile.open(archive,'r:gz')as tar:
        members=tar.getmembers()
        if len(members)!=len(MEMBERS)or{m.name for m in members}!=MEMBERS or any(not m.isfile()or m.size>2*1024**3 for m in members):raise ValueError('Recovery support inventory differs')
        for item in members:
            if item.name in{'evidence-signing-key','manifest.json','config-backend.json','config-gateway.json','pins.json'}and item.size>1048576:raise ValueError('Recovery metadata exceeds bound')
            fd=os.open(root/item.name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb')as out:shutil.copyfileobj(tar.extractfile(item),out);out.flush();os.fsync(out.fileno())
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest.get('schema')!='klikai.pilot.private-recovery-support.v1'or set(manifest.get('members',{}))!=MEMBERS-{'manifest.json'}:raise ValueError('Recovery support manifest differs')
    for name,pin in manifest['members'].items():
        if not isinstance(pin,dict)or set(pin)!={'bytes','sha256'}or type(pin['bytes'])is not int or pin['bytes']<0 or not isinstance(pin['sha256'],str)or not re.fullmatch('[a-f0-9]{64}',pin['sha256']):raise ValueError('Recovery support member pin differs')
        if(root/name).stat().st_size!=pin['bytes']or sha(root/name)!=pin['sha256']:raise ValueError('Recovery support member differs: '+name)
    after=archive.lstat()
    if (attrs.st_dev,attrs.st_ino,attrs.st_size,attrs.st_mtime_ns,attrs.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('Recovery support archive changed during verification')
    if not 32<=(root/'evidence-signing-key').stat().st_size<=4096:raise ValueError('Recovery signing key size differs')
    verify_backup(root/'feedback.tar.gz');ledger_bytes(root/'current-deletions.jsonl')
    for name in('config-backend.json','config-gateway.json','pins.json'):sanitized_config(json.loads((root/name).read_text()),pins=name=='pins.json')
    proof=restore(root/'feedback.tar.gz',root/'account-store',root/'current-deletions.jsonl',manifest['feedback_archive_sha256'])
    return {'status':'RESTORED_PRIVATE_SUPPORT','destination':str(root),'sha256':expected_sha256,'feedback_restore':proof,'signing_key_sha256':sha(root/'evidence-signing-key'),'fixed_runtime_started':False,'active_pointers_changed':False,'offhost_verified':False}
