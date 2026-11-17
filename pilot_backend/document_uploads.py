"""Owned document reports composed from ordinary frozen KLIKai answer families.

The scheduler submits one section at a time to the existing queue. A document
never creates a separate provider, source corpus or unbounded generation path.
"""
import functools
import hashlib
import json
import threading
from .store import canonical, stamp, uid
from .domain import checked_key, fields
from .security import Failure
from . import document_files
from . import private_translation as translation

SCHEMA = '''
CREATE TABLE IF NOT EXISTS uploaded_documents(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 name TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_bytes INTEGER NOT NULL,
 blocks TEXT NOT NULL, warnings TEXT NOT NULL, state TEXT NOT NULL,
 options TEXT, created REAL NOT NULL, finished REAL);
CREATE TABLE IF NOT EXISTS document_sections(
 document_id TEXT NOT NULL REFERENCES uploaded_documents(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, family_id TEXT REFERENCES families(id) ON DELETE SET NULL,
 PRIMARY KEY(document_id,ordinal), UNIQUE(family_id));
CREATE TABLE IF NOT EXISTS document_originals(
 document_id TEXT PRIMARY KEY REFERENCES uploaded_documents(id) ON DELETE CASCADE,
 package BLOB NOT NULL);
'''
ACTIVE = {'queued', 'running'}
TERMINAL = {'complete', 'failed', 'cancelled'}


def serialized(fn):
    @functools.wraps(fn)
    def call(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return call


def request_for(block, options):
    """Document text is an explicit operand, never an instruction or a source."""
    if options['mode'] == 'translate':
        destination = 'italijanščino' if options['target_language'] == 'it' else 'slovenščino'
        question = 'Prevedi naslednje besedilo v ' + destination + ':\n' + block['text']
    else:
        question = 'Jezikovno preglej naslednje besedilo in predlagaj popravke z razlago:\n' + block['text']
    return {'question': question, 'context': '', 'task': 'reference',
            'input_language': options['source_language'], 'locale': options['locale'],
            'model': options['model']}


def task_for(block, options, *, previous_sections=(), document_id=None):
    task={key:options[key]for key in ('mode','source_language','target_language','locale')}
    task.update(source_text=block['text'],source_sha256=hashlib.sha256(block['text'].encode('utf-8')).hexdigest())
    if document_id is not None:
        task['document_binding']={'document_id':document_id,'section_index':block['index']}
    if options.get('review_strategy'):
        task['review_strategy']=options['review_strategy']
        task['document_revision']=options['document_revision']
        task['document_location']=block.get('location','')
    if translation.selected(options):
        task['translation_profile']=translation.profile_checked(options['translation_profile'])
        task['translation_request_id']='document-'+options['translation_document_id']+'-'+str(block['index'])
    if options['mode']=='translate'and block.get('inline_spans'):
        task['inline_spans']=block['inline_spans']
    if options['mode']=='translate'and not translation.selected(options):
        candidates=[]
        for section in sorted(previous_sections,key=lambda row:row['index']):
            result=section.get('document_result')or{}
            if (section['index']>=block['index']or section.get('state')!='complete'
                    or result.get('status')!='draft'or result.get('mode')!='translate'
                    or result.get('source_language')!=options['source_language']
                    or result.get('target_language')!=options['target_language']
                    or not isinstance(result.get('proposed_text'),str)or not result['proposed_text']):continue
            source=section['text'];translated=result['proposed_text']
            source_hash=hashlib.sha256(source.encode('utf-8')).hexdigest()
            if result.get('source_sha256')!=source_hash:continue
            candidates.append({'section_index':section['index'],'source_text':source,
                'source_sha256':source_hash,'translated_text':translated,
                'translated_sha256':hashlib.sha256(translated.encode('utf-8')).hexdigest()})
        context=[];total=0
        # Retain the opening draft's address/register alongside the immediate
        # prior successful draft. Context remains fallible, hash-bound data.
        for pair in candidates[:1]+candidates[-1:]:
            if context and pair['section_index']==context[-1]['section_index']:continue
            size=len(pair['source_text'])+len(pair['translated_text'])
            if total+size>6000:continue
            context.append(pair)
            total+=size
        if context:task['translation_context']=context
    return task


class Documents:
    def __init__(self, store, domain, *, private_translation=False, translation_limits=None, evidence_review=False, editorial_summary=False, ai_suggestions=False, evidence_review_only=False):
        self.store, self.domain = store, domain
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.ai_suggestions=ai_suggestions
        self.editorial_summary=editorial_summary
        self.evidence_review_only=evidence_review_only
        self.evidence_review=evidence_review
        self.private_translation=private_translation
        self.translation_limits={**translation.DEFAULT_LIMITS,**(translation_limits or {})}

    def owned(self, db, owner, identifier):
        row = db.execute('SELECT * FROM uploaded_documents WHERE id=? AND owner=?',
                         (identifier, owner)).fetchone()
        if row is None:
            raise Failure('not_found', 404)
        return dict(row)

    def upload(self, owner, data, filename, key):
        checked_key(key)
        parsed = document_files.extract(data, filename)
        payload = {'idempotency_key': key, 'name': parsed['name'], 'sha256': parsed['sha256']}
        with self.store.tx() as db:
            def action():
                count, size = db.execute('SELECT count(*),coalesce(sum(source_bytes),0) '
                    'FROM uploaded_documents WHERE owner=?', (owner,)).fetchone()
                if count >= 20 or size + len(data) > 20 * 1024 * 1024:
                    raise Failure('document_quota', 429)
                identifier = uid()
                db.execute('INSERT INTO uploaded_documents VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (identifier, owner, parsed['name'], parsed['sha256'], len(data),
                     canonical(parsed['blocks']), canonical(parsed['warnings']), 'draft', None, stamp(), None))
                if parsed['name'].lower().endswith('.docx'):
                    db.execute('INSERT INTO document_originals VALUES(?,?)',(identifier,data))
                return {'document_id': identifier}
            saved = self.domain.idem(db, owner, 'document-upload', payload, action)
        return self.get(owner, saved['document_id'])

    @serialized
    def start(self, owner, identifier, payload):
        fields(payload, {'mode', 'source_language', 'target_language', 'locale', 'idempotency_key','translation_provider','review_strategy'})
        checked_key(payload.get('idempotency_key'))
        mode = payload.get('mode')
        source, target = payload.get('source_language'), payload.get('target_language')
        locale = payload.get('locale', 'sl')
        if (not all(isinstance(value,str)for value in (mode,source,locale))
                or target is not None and not isinstance(target,str)
                or mode not in {'review', 'translate'} or source not in {'sl', 'it'}
                or locale not in {'sl', 'it'} or
                (mode == 'translate' and (target not in {'sl', 'it'} or target == source))
                or (mode == 'review' and target is not None)):
            raise Failure('invalid_request')
        options = {'mode': mode, 'source_language': source, 'target_language': target,
                   'locale': locale, 'model': self.domain.default_model}
        strategy=payload.get('review_strategy')
        if self.evidence_review_only and (mode!='review'or source!='sl'):
            raise Failure('document_review_ineligible',422)
        if mode=='review'and self.evidence_review:
            strategy=strategy or'evidence_review_v1'
            if strategy!='evidence_review_v1'or source!='sl':raise Failure('document_review_ineligible',422)
            options['review_strategy']=strategy
        elif strategy is not None:raise Failure('document_review_ineligible',422)
        provider=payload.get('translation_provider')
        if provider is not None:
            if (provider!=translation.PROVIDER or not self.private_translation
                    or mode!='translate' or source!='sl' or target!='it'):
                raise Failure('document_translation_ineligible',422)
            # Idempotent re-delivery reuses the original profile. New network
            # preflight happens before the write transaction, not under its lock.
            with self.store.connect() as db: current=self.owned(db,owner,identifier)
            if current['state']=='draft':
                blocks=json.loads(current['blocks'])
                if len(blocks)>self.translation_limits['max_sections']:
                    raise Failure('document_translation_section_limit',422)
                if any(block.get('inline_spans') for block in blocks):
                    raise Failure('document_translation_inline_unsupported',422)
                gateway=self.domain.request_gateway
                if not gateway or not hasattr(gateway,'translation_profile') or not hasattr(gateway,'translation_preflight'):
                    raise Failure('document_translation_unavailable',503)
                profile=translation.profile_checked(gateway.translation_profile())
                texts=[{'text':b['text'],'source_sha256':hashlib.sha256(b['text'].encode()).hexdigest()} for b in blocks]
                try:preflight=gateway.translation_preflight(profile,texts)
                except Failure as error:
                    if hasattr(error,'preflight'):
                        with self.store.tx()as db:
                            observation=self.owned(db,owner,identifier)
                            if observation['state']=='draft':
                                previous=json.loads(observation['options']or'{}')
                                previous['translation_failed_preflight']={**error.preflight,'error_code':error.code,'recorded':stamp()}
                                db.execute('UPDATE uploaded_documents SET options=? WHERE id=?',(canonical(previous),identifier))
                    raise
                options.update(translation_provider=provider,translation_profile=profile,
                    translation_preflight=preflight,translation_queued_at=stamp(),translation_limits=dict(self.translation_limits),
                    translation_document_id=identifier)
                previous=json.loads(current['options']or'{}')
                if previous.get('translation_failed_preflight'):
                    options['translation_failed_preflight']=previous['translation_failed_preflight']
        with self.store.tx() as db:
            def action():
                row = self.owned(db, owner, identifier)
                if row['state'] != 'draft':
                    raise Failure('document_already_started', 409)
                if options.get('review_strategy'):
                    options['document_revision']=row['source_sha256']
                active = db.execute("SELECT count(*) FROM uploaded_documents WHERE owner=? "
                                    "AND state IN ('queued','running')", (owner,)).fetchone()[0]
                if active:
                    raise Failure('queue_full', 429)
                if provider is not None:
                    candidate=[json.loads(r[0]or'{}') for r in db.execute(
                        "SELECT options FROM uploaded_documents WHERE state IN ('queued','running')")]
                    if sum(translation.selected(o) for o in candidate)>=1+self.translation_limits['waiting_documents']:
                        raise Failure('queue_full',429)
                db.execute("UPDATE uploaded_documents SET state='queued',options=? WHERE id=?",
                           (canonical(options), identifier))
                return {'document_id': identifier}
            self.domain.idem(db, owner, 'document-start:' + identifier, payload, action)
        return self.get(owner, identifier)

    def get(self, owner, identifier, *, internal=False):
        with self.store.connect() as db:
            row = self.owned(db, owner, identifier)
            original_word=bool(db.execute('SELECT 1 FROM document_originals WHERE document_id=?',(identifier,)).fetchone())
            links = {r['ordinal']: dict(r) for r in db.execute(
                'SELECT d.ordinal,d.family_id,r.state,r.result,j.id AS job_id,j.error_code '
                'FROM document_sections d LEFT JOIN runs r ON r.family_id=d.family_id '
                "AND r.tier='short' AND r.assignment_id IS NULL "
                'LEFT JOIN jobs j ON j.run_id=r.id WHERE d.document_id=?', (identifier,))}
        sections = []
        for block in json.loads(row['blocks']):
            link = links.get(block['index'], {})
            result = json.loads(link.get('result') or '{}')
            from .presentation import checked_answer_presentation,complete_answer_text
            complete_output=complete_answer_text(result)if checked_answer_presentation(result)is not None else None
            state = link.get('state') or ('cancelled' if row['state'] == 'cancelled' else 'unknown' if row['state']=='failed' else 'pending')
            if state=='complete'and result.get('status')in {'failed','rejected'}:state='failed'
            # A finished job may explicitly abstain. Completion is processing,
            # never a claim that every sentence is correct or translated.
            sections.append({**block, 'state': state, 'family_id': link.get('family_id'),
                'job_id': link.get('job_id'), 'error_code': link.get('error_code'),
                'output': result.get('answer_md') or result.get('short_answer') or '',
                **({'complete_output':complete_output}if complete_output is not None else{}),
                'answer_support': result.get('answer_support'),
                'answer_method': result.get('answer_method'),
                'document_result': result.get('document_result'),
                **({'guidance_delivery':result['execution']['guidance_delivery']}
                   if (result.get('execution')or{}).get('guidance_delivery')else{}),
                'limitations': result.get('limitations', []),
                'sources': [{k: e[k] for k in ('local_number', 'evidence_id', 'title', 'source_label', 'url','source_id','source_revision','locator')
                             if k in e} for e in result.get('evidence', []) if isinstance(e, dict)]})
        warnings=json.loads(row['warnings'])
        if document_files.repeated_translation_sections(sections):warnings.append('document_repeated_translation')
        options=json.loads(row['options']or'{}')
        if row['state']=='draft'and options.get('translation_failed_preflight'):
            warnings.append(options['translation_failed_preflight']['error_code'])
        public_options={k:v for k,v in options.items()if k not in {'editorial_summary','editorial_summary_history','ai_review','ai_history','ai_decisions','ai_decision_history'}and(not k.startswith('translation_')or k=='translation_provider')}
        if translation.selected(options):
            public_options['translation_profile_sha256']=translation.profile_hash(options['translation_profile'])
            public_options['translation_guidance']=translation.guided(options['translation_profile'])
        result={'document_id': identifier, 'name': row['name'], 'state': row['state'],
                'has_word_original':original_word,'source_sha256':row['source_sha256'],
                'created': row['created'], 'options': options if internal else public_options,
                'warnings': warnings, 'sections': sections,
                'processed': sum(s['state'] in TERMINAL for s in sections), 'total': len(sections)}
        from .document_summary import manifest,public_summary
        result['review_manifest']=manifest(result)
        result['editorial_summary']=public_summary(options.get('editorial_summary'),result['review_manifest'])
        result['draft_exports']=document_files.review_export_status(result)
        from .document_ai_suggestions import public_review
        if self.ai_suggestions or options.get('ai_review'):result['ai_suggestions']=public_review(self,owner,result,options)
        return result

    @serialized
    def prepare_ai(self,owner,identifier,payload=None):
        from .document_ai_suggestions import prepare
        return prepare(self,owner,identifier,payload)

    @serialized
    def start_ai(self,owner,identifier):
        from .document_ai_suggestions import start
        return start(self,owner,identifier)

    @serialized
    def decide_ai(self,owner,identifier,payload):
        from .document_ai_suggestions import decide
        return decide(self,owner,identifier,payload)

    @serialized
    def cancel_ai(self,owner,identifier):
        from .document_ai_suggestions import cancel
        return cancel(self,owner,identifier)

    def export_selected(self,owner,identifier,fmt,revision,partial=False):
        from .document_ai_suggestions import selected_export
        return selected_export(self,owner,identifier,fmt,revision,partial)

    def prepare_summary(self,owner,identifier):
        if not self.editorial_summary:raise Failure('document_summary_unavailable',409)
        document=self.get(owner,identifier,internal=True)
        from .document_summary import packet
        bundles=[]
        with self.store.connect()as db:
            for section in document['sections']:
                row=db.execute('SELECT bundle FROM families WHERE id=?AND owner=?',
                    (section.get('family_id'),owner)).fetchone()
                bundles.append(json.loads(row[0])if row else{})
        material=packet(document,bundles)
        previous=document['options'].get('editorial_summary')
        if previous and previous.get('manifest_sha256')==material['manifest_sha256']:
            return self.get(owner,identifier)
        gateway=self.domain.request_gateway
        if gateway is None:raise Failure('document_summary_unavailable',503)
        prepared=gateway.request('/internal/pilot/document-summary/prepare',{'packet':material})
        if (prepared.get('packet_sha256')!=material['packet_sha256']or prepared.get('status')not in {'prepared','unavailable_input_limit'}):
            raise Failure('document_summary_unavailable',502)
        saved={key:prepared[key]for key in ('version','status','manifest_sha256','document_revision')}
        saved.update(packet=material,prepared=prepared,output=None,attempted=False,authority='optional_editorial_summary_only')
        with self.store.tx()as db:
            row=self.owned(db,owner,identifier);options=json.loads(row['options'])
            if previous:options.setdefault('editorial_summary_history',[]).append(previous)
            options['editorial_summary']=saved
            db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(canonical(options),identifier))
        return self.get(owner,identifier)

    def generate_summary(self,owner,identifier):
        if not self.editorial_summary:raise Failure('document_summary_unavailable',409)
        with self.store.tx()as db:
            row=self.owned(db,owner,identifier);options=json.loads(row['options']or'{}')
            saved=options.get('editorial_summary')
            if not saved or saved['status']!='prepared':raise Failure('document_summary_unavailable',409)
            saved['status']='dispatch_pending'
            db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(canonical(options),identifier))
        current=self.get(owner,identifier,internal=True)
        if saved['manifest_sha256']!=current['review_manifest']['manifest_sha256']:
            response={**saved,'status':'historical','output':None}
        else:
            try:
                response=self.domain.request_gateway.request('/internal/pilot/document-summary',
                    {'packet':saved['packet'],'prepared':saved['prepared']})
                if (response.get('version')!=saved['version']or response.get('manifest_sha256')!=saved['manifest_sha256']
                        or response.get('status')not in {'available','unavailable'}):raise Failure('document_summary_unavailable',502)
                if response['status']=='available':
                    known={f['finding_id']for f in saved['packet']['findings']}
                    if not isinstance(response.get('output'),dict)or set(response['output'])!={'points'}:raise Failure('document_summary_unavailable',502)
                    for point in response['output']['points']:
                        if not isinstance(point.get('text'),str)or not point.get('finding_ids')or not set(point['finding_ids'])<=known:
                            raise Failure('document_summary_unavailable',502)
            except Failure as error:
                response={'version':saved['version'],'manifest_sha256':saved['manifest_sha256'],
                    'document_revision':saved['document_revision'],'status':'unavailable','output':None,
                    'attempted':None,'failure_type':error.code,'authority':'optional_editorial_summary_only'}
        with self.store.tx()as db:
            row=self.owned(db,owner,identifier);options=json.loads(row['options'])
            options['editorial_summary']={**response,'packet':saved['packet'],'prepared':saved['prepared']}
            db.execute('UPDATE uploaded_documents SET options=?WHERE id=?',(canonical(options),identifier))
        return self.get(owner,identifier)

    def list(self, owner):
        with self.store.connect() as db:
            return {'documents': [dict(r) for r in db.execute(
                'SELECT id AS document_id,name,state,created FROM uploaded_documents '
                'WHERE owner=? ORDER BY created DESC', (owner,))]}

    @serialized
    def cancel(self, owner, identifier):
        candidate_cancellations=[]
        with self.store.tx() as db:
            row = self.owned(db, owner, identifier)
            options=json.loads(row['options']or'{}')
            # Recover any accepted section whose linking transaction was
            # interrupted. Cancellation also covers that already queued work.
            for block in json.loads(row['blocks']):
                key = 'document-' + identifier + '-' + str(block['index'])
                saved = db.execute("SELECT response FROM request_keys WHERE owner=? "
                                   "AND scope='family' AND key=?", (owner, key)).fetchone()
                if saved:
                    db.execute('INSERT OR IGNORE INTO document_sections VALUES(?,?,?)',
                               (identifier, block['index'], json.loads(saved[0])['family_id']))
            if row['state'] in ACTIVE or row['state'] == 'draft':
                db.execute("UPDATE uploaded_documents SET state='cancelled',finished=? WHERE id=?",
                           (stamp(), identifier))
                jobs = list(db.execute('SELECT j.id,j.state,j.run_id,d.ordinal FROM document_sections d '
                    'JOIN jobs j ON j.run_id IN (SELECT id FROM runs WHERE family_id=d.family_id) '
                    "WHERE d.document_id=? AND j.state IN ('queued','running')", (identifier,)))
                for job in jobs:
                    # Use the same cancellation behavior as ordinary questions.
                    if job['state'] == 'queued':
                        from .human_feedback import terminal_display
                        result, display = terminal_display(db, job['run_id'], 'cancelled')
                        db.execute("UPDATE jobs SET state='cancelled',finished=? WHERE id=?", (stamp(), job['id']))
                        db.execute("UPDATE runs SET state='cancelled',result=?,display_hash=?,finished=? WHERE id=?",
                                   (result, display, stamp(), job['run_id']))
                    else:
                        db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job['id'],))
                    if translation.selected(options):
                        block=next(b for b in json.loads(row['blocks'])if b['index']==job['ordinal'])
                        candidate_cancellations.append({'translation_profile':options['translation_profile'],
                            'translation_request_id':'document-'+identifier+'-'+str(block['index']),
                            'source_sha256':hashlib.sha256(block['text'].encode()).hexdigest()})
        for request in candidate_cancellations:
            try:self.domain.request_gateway.translation_cancel(**request)
            except Exception:
                # The durable cancellation already prevents late completion.
                # CORE's uncertain marker/deadline owns provider settlement.
                with self.store.tx()as db:
                    current=self.owned(db,owner,identifier);warnings=json.loads(current['warnings'])
                    if 'document_translation_cancel_pending'not in warnings:warnings.append('document_translation_cancel_pending')
                    db.execute('UPDATE uploaded_documents SET warnings=? WHERE id=?',(canonical(warnings),identifier))
        return self.get(owner, identifier)

    @serialized
    def delete(self, owner, identifier):
        with self.store.tx() as db:
            row = self.owned(db, owner, identifier)
            running = db.execute('SELECT 1 FROM document_sections d JOIN jobs j '
                'ON j.run_id IN (SELECT id FROM runs WHERE family_id=d.family_id) '
                "WHERE d.document_id=? AND j.state='running'", (identifier,)).fetchone()
            if row['state'] in ACTIVE or running:
                raise Failure('document_running', 409)
            families = [r[0] for r in db.execute('SELECT family_id FROM document_sections '
                        'WHERE document_id=?', (identifier,)) if r[0]]
            db.execute('DELETE FROM uploaded_documents WHERE id=?', (identifier,))
            ai=json.loads(row['options']or'{}').get('ai_review')or{}
            if ai.get('family_id'):
                active_ai=db.execute("SELECT 1 FROM jobs WHERE id=?AND state IN('queued','running')",(ai['job_id'],)).fetchone()
                if active_ai:raise Failure('document_running',409)
                families.append(ai['family_id'])
            # An explicitly linked follow-up retains its parent run. Delete the
            # document's own descendant answers together, in one FK-checked
            # statement, rather than break immutable historical references.
            owned=set(families)
            while owned:
                marks=','.join('?'for _ in owned)
                children={r[0]for r in db.execute('SELECT DISTINCT child.family_id FROM runs child '
                    'JOIN runs parent ON parent.id=child.parent_run_id JOIN families f ON f.id=child.family_id '
                    'WHERE parent.family_id IN ('+marks+') AND f.owner=?',(*owned,owner))}
                if children<=owned:break
                owned.update(children)
            if owned:
                active_child=db.execute('SELECT 1 FROM jobs j JOIN runs r ON r.id=j.run_id '
                    'WHERE r.family_id IN ('+','.join('?'for _ in owned)+') '
                    "AND j.state IN ('queued','running') LIMIT 1",tuple(owned)).fetchone()
                if active_child:raise Failure('document_running',409)
                db.execute('DELETE FROM families WHERE owner=? AND id IN ('+','.join('?'for _ in owned)+')',
                           (owner,*owned))
        return {'acknowledged': True}

    def export(self, owner, identifier, fmt, draft=False, origin=None, api_path='/api/pilot', partial=False):
        if fmt not in {'txt', 'docx'}:
            raise Failure('invalid_request')
        document = self.get(owner, identifier,internal=True)
        if draft:
            if partial:
                # Both formats derive the same source-supported edit subset.
                document_files.safe_partial_review_document(document)
                if fmt=='txt':return document_files.partial_review_text(document)
                with self.store.connect()as db:
                    original=db.execute('SELECT package FROM document_originals WHERE document_id=?',(identifier,)).fetchone()
                from .document_word import rewrite_safe_partial_review,label_partial_review
                data=(rewrite_safe_partial_review(original[0],document)if original else
                      document_files._text_docx(document_files.partial_review_blocks(document)))
                return label_partial_review(data,document['options'].get('locale','sl'),safe_source=True)
            # Validate completeness before consulting any source package.
            document_files.draft_blocks(document)
            if fmt=='docx':
                with self.store.connect()as db:
                    original=db.execute('SELECT package FROM document_originals WHERE document_id=?',(identifier,)).fetchone()
                if original:
                    from .document_word import rewrite
                    return rewrite(original[0],document['sections'])
                return document_files.draft_docx(document)
            return document_files.draft_text(document)
        if partial:raise Failure('invalid_request')
        from .document_report_sources import report_section
        from urllib.parse import quote
        api_root=(origin or'')+api_path
        sections=[]
        with self.store.connect()as db:
            for section in document['sections']:
                saved=db.execute("SELECT f.bundle,o.snapshot FROM runs r JOIN families f ON f.id=r.family_id "
                    "LEFT JOIN run_observations o ON o.run_id=r.id WHERE r.family_id=?AND r.tier='short' "
                    "AND r.assignment_id IS NULL ORDER BY r.revision DESC LIMIT 1",(section.get('family_id'),)).fetchone()
                bundle=json.loads(saved[0]or'{}')if saved else{}
                snapshot=json.loads(saved[1]or'{}')if saved else{}
                sections.append(report_section({**section,'status':section['state']},bundle,snapshot,api_root))
        report = {**document, **document['options'], 'sections':sections,'ai_suggestions_enabled':self.ai_suggestions or bool(document.get('ai_suggestions')),
                  'saved_result_url':api_root+'/documents/'+quote(identifier,safe='')}
        return document_files.report_docx(report) if fmt == 'docx' else document_files.report_text(report)

    @serialized
    def advance(self):
        """One bounded scheduling pass, also usable without threads in tests."""
        with self.store.connect() as db:
            active = [dict(r) for r in db.execute("SELECT * FROM uploaded_documents "
                                                "WHERE state IN ('queued','running') ORDER BY created")]
        candidates=[r for r in active if translation.selected(json.loads(r['options']or'{}'))]
        current_candidate=next((r['id'] for r in candidates if r['state']=='running'),None)
        if current_candidate is None and candidates:
            current_candidate=min(candidates,key=lambda r:json.loads(r['options'])['translation_queued_at'])['id']
        for row in active:
            document = self.get(row['owner'], row['id'],internal=True)
            options=document['options']
            if translation.selected(options):
                limits=options['translation_limits'];now=stamp()
                queued=options['translation_queued_at'];started=options.get('translation_started_at')
                expired=(started is None and now-queued>limits['queue_seconds'] or
                    started is not None and now-started>limits['document_seconds'])
                if expired:
                    self.cancel(row['owner'],row['id'])
                    with self.store.tx()as db:
                        warnings=json.loads(row['warnings']);warnings.append('document_translation_expired')
                        db.execute("UPDATE uploaded_documents SET state='failed',warnings=?,finished=? WHERE id=?",
                            (canonical(warnings),stamp(),row['id']))
                    continue
                if row['id']!=current_candidate:continue
                if started is None:
                    options['translation_started_at']=now
                    with self.store.tx()as db:
                        db.execute("UPDATE uploaded_documents SET options=?,state='running' WHERE id=? AND state='queued'",
                            (canonical(options),row['id']))
            if any(s['state'] in ACTIVE for s in document['sections']):
                continue
            pending = next((s for s in document['sections'] if s['state'] == 'pending'), None)
            if pending is None:
                state = 'complete' if all(s['state'] == 'complete' for s in document['sections']) else 'failed'
                with self.store.tx() as db:
                    db.execute('UPDATE uploaded_documents SET state=?,finished=? WHERE id=? '
                               "AND state IN ('queued','running')", (state, stamp(), row['id']))
                continue
            # The deterministic key recovers a family accepted just before a
            # scheduler interruption, avoiding duplicate model dispatches.
            key = 'document-' + row['id'] + '-' + str(pending['index'])
            try:
                payload = {**request_for(pending, document['options']), 'idempotency_key': key}
                task=task_for(pending,document['options'],previous_sections=document['sections'],document_id=row['id'])
                # A family accepted before linking may predate document_binding.
                # Recover its original contract; Domain still checks every
                # task/input field through the existing idempotency key.
                with self.store.connect()as db:
                    prior=db.execute("SELECT f.options FROM request_keys k JOIN family_request_options f "
                        "ON f.family_id=json_extract(k.response,'$.family_id') WHERE k.owner=? "
                        "AND k.scope='family' AND k.key=?",(row['owner'],key)).fetchone()
                if prior and 'document_binding'not in (json.loads(prior[0]or'{}').get('document_task')or{}):
                    task.pop('document_binding',None)
                accepted = self.domain.create_family(row['owner'], payload,document_task=task)
                fid = accepted['family']['answer_family_id']
            except Failure as error:
                if error.code in {'queue_full', 'generation_waiting_capacity'}:
                    continue
                with self.store.tx() as db:
                    db.execute("UPDATE uploaded_documents SET state='failed',finished=? WHERE id=? "
                               "AND state IN ('queued','running')", (stamp(), row['id']))
                continue
            with self.store.tx() as db:
                current = self.owned(db, row['owner'], row['id'])
                db.execute('INSERT OR IGNORE INTO document_sections VALUES(?,?,?)',
                           (row['id'], pending['index'], fid))
                if current['state'] in ACTIVE:
                    db.execute("UPDATE uploaded_documents SET state='running' WHERE id=?", (row['id'],))
            if current['state'] not in ACTIVE:
                self.cancel(row['owner'], row['id'])

        if self.ai_suggestions:
            from .document_ai_suggestions import advance_units
            advance_units(self)

    def start_worker(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.loop, name='pilot-documents', daemon=True)
            self.thread.start()

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.advance()
            except Exception as error:
                # Do not log source documents, account details or provider errors.
                import logging
                logging.getLogger(__name__).warning('Document scheduling failed (%s)',type(error).__name__)
            self.stop_event.wait(1)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
