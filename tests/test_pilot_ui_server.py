"""Browser-only fixture: real auth/jobs/SQLite, explicitly synthetic generation.

Never mounted in the public application. Requires a fresh owned private directory.
No operational/model endpoint is contacted; credentials are written only to 0600.
"""
from __future__ import annotations
import argparse,json,os,secrets,stat,time
from pathlib import Path

class SyntheticFixtureAdapter:
    capacity_path=None
    def capabilities(self):
        ready=not self.capacity_path or self.capacity_path.read_text().strip()=='available'
        return {'generation':{'status':'AVAILABLE'if ready else'GENERATION_WAITING_CAPACITY','models':{m:{'available':ready}for m in ('qwen','gemma')}},'dense':{'available':False,'status':'DENSE_ENCODING_WAITING_CAPACITY'},'lexical':{'available':True},'tools':{'available':True}}
    def require_generation(self,models):
        from pilot_backend.security import Failure
        if not all(self.capabilities()['generation']['models'][m]['available']for m in models):raise Failure('generation_waiting_capacity',503)
    def freeze(self,family,policy):
        return {'bundle':{'question':family['question'],'locale':family['locale'],'fixture':True},'identity':{'fixture':'synthetic-ui-v1'}}
    def generate(self,bundle,tier,model,parent):
        time.sleep(.2 if tier=='short'else .7)
        if 'slow-cancel' in bundle['question']:time.sleep(2)
        text=('SYNTHETIC TEST — 🙂 e\u0301 otrok otrok — Huga. [1]\n'
              'To je označena ponazoritev za preizkus vmesnika.' if bundle['locale']=='sl'else
              'SYNTHETIC TEST — 🙂 e\u0301 otrok otrok — Huga. [1]\n'
              'Questo è un esempio contrassegnato per il test dell’interfaccia.')
        if tier=='long':text+='\n\n'+('Podrobnejša označena ponazoritev ohrani izvirne oblike in isti preskusni vir.'if bundle['locale']=='sl'else'La spiegazione sintetica conserva le forme originali e la stessa fonte di prova.')
        if '<img' in bundle['question']:text+='\n<img src=x onerror="window.fixtureXSS=1">'
        return {'status':'validated','short_answer':text if tier=='short'else parent['short_answer'],'answer_md':text,'blocks':[{'block_id':'body','text':text}],'source_badges':[{'source_key':'jezikovna_svetovalnica','label':'Svetovalnica'}],'evidence':[{'evidence_id':'fixture-e1','source_key':'jezikovna_svetovalnica','local_number':1,'excerpt':'SYNTHETIC SOURCE — Huga.'}]}
    def source(self,bundle,evidence_id,tier):
        if evidence_id!='fixture-e1':raise ValueError('Not synthetic evidence')
        return {'evidence_id':evidence_id,'source_label':'Svetovalnica','excerpt':'SYNTHETIC SOURCE — Huga.','locator':'SYNTHETIC TEST','status':'advisory','url':'https://svetovalnica.zrc-sazu.si/','bibliography':{'author':'Synthetic bibliography only'}}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--directory',type=Path,required=True);ap.add_argument('--port',type=int,required=True);ap.add_argument('--prefix',default='');ap.add_argument('--reuse',action='store_true');ap.add_argument('--capacity-control',action='store_true');ap.add_argument('--human-testing',action='store_true');args=ap.parse_args()
    if args.directory.exists()and not args.reuse:raise SystemExit('Use a NEW private directory')
    if args.reuse:
        if args.directory.is_symlink()or args.directory.stat().st_uid!=os.getuid()or stat.S_IMODE(args.directory.stat().st_mode)!=0o700:raise SystemExit('Fixture directory differs')
        if not(args.directory/'accounts.json').is_file():raise SystemExit('Missing owned fixture receipt')
    else:args.directory.mkdir(mode=0o700)
    import subprocess
    subprocess.run(['setfacl','-b','-k',str(args.directory)],check=True);args.directory.chmod(0o700)
    from pilot_backend.api import create_app
    adapter=SyntheticFixtureAdapter()
    if args.capacity_control:
        adapter.capacity_path=args.directory/'synthetic-capacity.txt'
        if not args.reuse:adapter.capacity_path.write_text('available');adapter.capacity_path.chmod(0o600)
    app=create_app({'store':str(args.directory/'pilot.sqlite3'),'allowed_origins':[f'http://127.0.0.1:{args.port}'],'secure_cookies':False,'base_path':args.prefix,'tasks':['reference','source_search','text_check','terminology'],'models':['qwen','gemma'],'human_testing_mode':args.human_testing},adapter=adapter)
    if not args.reuse:
        accounts={}
        for username,role,must_change in [('ui-tester','tester',False),('ui-other','tester',False),('ui-admin','admin',False),('ui-initial','tester',True),('ui-reviewer','reviewer',False)]:
            password=secrets.token_urlsafe(28);identifier=app.state.auth.create(username,password,role=role,origin='synthetic',must_change=must_change);accounts[username]={'id':identifier,'password':password}
        fd=os.open(args.directory/'accounts.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w')as stream:json.dump(accounts,stream)
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
