"""Operator CLI. Passwords enter only from protected files, never argv or output."""
import argparse,json,os
from pathlib import Path
from .store import Store,canonical
from .security import Auth
from .cycles import analyse,adapt,monitor_canary,revert_policy
from .backup import backup,restore,exercise,verify_backup
from .recovery import build_support,restore_support

def protected_json(path):
    p=Path(path)
    if p.is_symlink()or p.stat().st_mode&0o077 or p.stat().st_uid!=os.geteuid():raise ValueError('Input must be a protected0600 file')
    return json.loads(p.read_text())

def replay_gateway(config_path):
    from .replay import read_private
    from .adapter import Gateway
    config=read_private(config_path);secret=Path(config['gateway_token_file'])
    if secret.is_symlink()or secret.stat().st_mode&0o077:raise ValueError('Private gateway token file required')
    return Gateway(config['gateway_url'],secret.read_text().strip())

def main():
    p=argparse.ArgumentParser();p.add_argument('--store',type=Path);p.add_argument('--config-file',type=Path);sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    export=sub.add_parser('export-attempts');export.add_argument('--output-dir',type=Path,required=True)
    export.add_argument('--owner');export.add_argument('--group',action='append',dest='groups');export.add_argument('--origin',choices=['project_use','technical_manual','automated_test','unknown','all'])
    for field in ('release','model','provider','outcome','rating','review-status','run-id'):export.add_argument('--'+field)
    export.add_argument('--since',type=float);export.add_argument('--until',type=float);export.add_argument('--degraded',choices=['true','false'])
    invite=sub.add_parser('invite-admin');invite.add_argument('--username',required=True);invite.add_argument('--password-file',type=Path,required=True);invite.add_argument('--bootstrap-id')
    project=sub.add_parser('project-accounts');project.add_argument('--credentials-file',type=Path,required=True)
    status=sub.add_parser('bootstrap-status');status.add_argument('--bootstrap-id',required=True)
    a=sub.add_parser('analysis');a.add_argument('--output-dir',type=Path)
    a=sub.add_parser('adaptation');a.add_argument('--candidates',type=Path);a.add_argument('--validation',type=Path);a.add_argument('--config',type=Path);a.add_argument('--cases',type=Path);a.add_argument('--cases-sha256')
    replay=sub.add_parser('replay');replay.add_argument('--config',type=Path,required=True);replay.add_argument('--cases',type=Path,required=True);replay.add_argument('--cases-sha256',required=True);replay.add_argument('--max-cases',type=int,default=4);replay.add_argument('--force-test',action='store_true')
    sub.add_parser('retention');sub.add_parser('monitor-canary').add_argument('--config',type=Path);sub.add_parser('revert')
    b=sub.add_parser('backup');b.add_argument('--archive',type=Path,required=True)
    r=sub.add_parser('restore');r.add_argument('--archive',type=Path,required=True);r.add_argument('--destination',type=Path,required=True);r.add_argument('--current-deletions',type=Path,required=True);r.add_argument('--sha256',required=True)
    verify=sub.add_parser('verify-backup');verify.add_argument('--archive',type=Path,required=True)
    support=sub.add_parser('recovery-support')
    for field in('feedback-archive','current-ledger','signing-key','backend-config','gateway-config','pins-file','archive'):support.add_argument('--'+field,type=Path,required=True)
    support=sub.add_parser('restore-support');support.add_argument('--archive',type=Path,required=True);support.add_argument('--destination',type=Path,required=True);support.add_argument('--sha256',required=True)
    e=sub.add_parser('recovery-exercise');e.add_argument('--directory',type=Path,required=True)
    args=p.parse_args()
    if args.config_file:
        if args.store:p.error('Use --store or --config-file, not both')
        args.store=Path(json.loads(args.config_file.read_text())['store'])
    if args.command=='verify-backup':result=verify_backup(args.archive)
    elif args.command=='recovery-support':result=build_support(args.feedback_archive,args.current_ledger,args.signing_key,args.backend_config,args.gateway_config,args.pins_file,args.archive)
    elif args.command=='restore-support':result=restore_support(args.archive,args.destination,args.sha256)
    elif args.command=='restore':result=restore(args.archive,args.destination,args.current_deletions,args.sha256)
    elif args.command=='recovery-exercise':result=exercise(args.directory)
    elif args.command=='bootstrap-status':
        import sqlite3
        if args.store is None:p.error('--store is required')
        if not args.store.is_file():result={'status':'not_initialized'}
        else:
            db=sqlite3.connect(args.store.absolute().as_uri()+'?mode=ro',uri=True)
            try:
                row=db.execute("SELECT value FROM meta WHERE key='admin_bootstrap'").fetchone()
                prior=json.loads(row[0])if row else None
                if prior and prior['bootstrap_id']!=args.bootstrap_id:raise ValueError('Bootstrap identity differs')
                account=db.execute('SELECT id,must_change FROM accounts WHERE id=?',(prior['account_id'],)).fetchone()if prior else None
                result={'status':'ready'if account else'not_initialized','account_id':account[0]if account else None,'must_change_password':bool(account[1])if account else None}
            finally:db.close()
    else:
        if args.store is None:p.error('--store is required')
        store=Store(args.store)
        if args.command=='init':result={'status':'initialized','schema':store.meta('schema')}
        elif args.command=='export-attempts':
            from .human_feedback import export_attempts
            options={key:getattr(args,key) for key in ('groups','origin','since','until','release','model','provider','outcome','rating','review_status','run_id')}
            options['degraded']=None if args.degraded is None else args.degraded=='true'
            result=export_attempts(store,args.output_dir,**options)
            if args.owner:
                import pwd
                identity=pwd.getpwnam(args.owner)
                for path in [*args.output_dir.iterdir(),args.output_dir]:os.chown(path,identity.pw_uid,identity.pw_gid)
        elif args.command=='project-accounts':
            from .project_accounts import provision
            result=provision(store,args.credentials_file)
        elif args.command=='invite-admin':
            path=args.password_file
            if path.is_symlink()or path.stat().st_mode&0o077 or path.stat().st_uid!=os.geteuid():raise ValueError('Password input must be an owned protected0600 file')
            password=path.read_text().rstrip('\n')
            with store.connect()as db:
                if not args.bootstrap_id and db.execute("SELECT 1 FROM accounts WHERE role='admin'AND enabled=1").fetchone():raise ValueError('Bootstrap already completed; use authenticated account administration')
            account=Auth(store).create(args.username,password,'admin',must_change=True,bootstrap_id=args.bootstrap_id);result={'account_id':account,'status':'invited','must_change_password':True}
        elif args.command=='analysis':result=analyse(store,args.output_dir)
        elif args.command=='adaptation':
            if (args.cases is None)!=(args.cases_sha256 is None):p.error('--cases and --cases-sha256 must be supplied together')
            result=adapt(store,protected_json(args.candidates)if args.candidates else None,protected_json(args.validation)if args.validation else None,replay_gateway=replay_gateway(args.config)if args.config else None,replay_cases=(args.cases,args.cases_sha256)if args.cases else None)
        elif args.command=='replay':
            from .replay import run
            os.nice(10)
            result=run(store,replay_gateway(args.config),args.cases,args.cases_sha256,max_cases=args.max_cases,force_test=args.force_test)
        elif args.command=='retention':result=store.retention()
        elif args.command=='monitor-canary':result=monitor_canary(store,gateway=replay_gateway(args.config)if args.config else None)
        elif args.command=='revert':result=revert_policy(store)
        elif args.command=='backup':result=backup(store,args.archive)
    # Report paths/counts/hashes; no passwords, feedback bodies or signed answer packets.
    if args.command=='analysis':result={k:result[k]for k in ('cycle_id','cutoff','status','counts')}
    print(canonical(result))
if __name__=='__main__':main()
