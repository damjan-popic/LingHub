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


def task_for(block, options):
    task={key:options[key]for key in ('mode','source_language','target_language','locale')}
    task.update(source_text=block['text'],source_sha256=hashlib.sha256(block['text'].encode('utf-8')).hexdigest())
    if options['mode']=='translate'and block.get('inline_spans'):
        task['inline_spans']=block['inline_spans']
    return task


class Documents:
    def __init__(self, store, domain):
        self.store, self.domain = store, domain
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread = None

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
        fields(payload, {'mode', 'source_language', 'target_language', 'locale', 'idempotency_key'})
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
        with self.store.tx() as db:
            def action():
                row = self.owned(db, owner, identifier)
                if row['state'] != 'draft':
                    raise Failure('document_already_started', 409)
                active = db.execute("SELECT count(*) FROM uploaded_documents WHERE owner=? "
                                    "AND state IN ('queued','running')", (owner,)).fetchone()[0]
                if active:
                    raise Failure('queue_full', 429)
                db.execute("UPDATE uploaded_documents SET state='queued',options=? WHERE id=?",
                           (canonical(options), identifier))
                return {'document_id': identifier}
            self.domain.idem(db, owner, 'document-start:' + identifier, payload, action)
        return self.get(owner, identifier)

    def get(self, owner, identifier):
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
            state = link.get('state') or ('cancelled' if row['state'] == 'cancelled' else 'unknown' if row['state']=='failed' else 'pending')
            if state=='complete'and result.get('status')in {'failed','rejected'}:state='failed'
            # A finished job may explicitly abstain. Completion is processing,
            # never a claim that every sentence is correct or translated.
            sections.append({**block, 'state': state, 'family_id': link.get('family_id'),
                'job_id': link.get('job_id'), 'error_code': link.get('error_code'),
                'output': result.get('answer_md') or result.get('short_answer') or '',
                'answer_support': result.get('answer_support'),
                'answer_method': result.get('answer_method'),
                'document_result': result.get('document_result'),
                'limitations': result.get('limitations', []),
                'sources': [{k: e[k] for k in ('local_number', 'evidence_id', 'title', 'source_label', 'url')
                             if k in e} for e in result.get('evidence', []) if isinstance(e, dict)]})
        return {'document_id': identifier, 'name': row['name'], 'state': row['state'],
                'has_word_original':original_word,
                'created': row['created'], 'options': json.loads(row['options'] or '{}'),
                'warnings': json.loads(row['warnings']), 'sections': sections,
                'processed': sum(s['state'] in TERMINAL for s in sections), 'total': len(sections)}

    def list(self, owner):
        with self.store.connect() as db:
            return {'documents': [dict(r) for r in db.execute(
                'SELECT id AS document_id,name,state,created FROM uploaded_documents '
                'WHERE owner=? ORDER BY created DESC', (owner,))]}

    @serialized
    def cancel(self, owner, identifier):
        with self.store.tx() as db:
            row = self.owned(db, owner, identifier)
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
                jobs = list(db.execute('SELECT j.id,j.state,j.run_id FROM document_sections d '
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

    def export(self, owner, identifier, fmt, draft=False):
        if fmt not in {'txt', 'docx'}:
            raise Failure('invalid_request')
        document = self.get(owner, identifier)
        if draft:
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
        report = {**document, **document['options'], 'sections': [
            {**s, 'status': s['state']} for s in document['sections']]}
        return document_files.report_docx(report) if fmt == 'docx' else document_files.report_text(report)

    @serialized
    def advance(self):
        """One bounded scheduling pass, also usable without threads in tests."""
        with self.store.connect() as db:
            active = [dict(r) for r in db.execute("SELECT * FROM uploaded_documents "
                                                "WHERE state IN ('queued','running') ORDER BY created")]
        for row in active:
            document = self.get(row['owner'], row['id'])
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
                accepted = self.domain.create_family(row['owner'], payload,document_task=task_for(pending,document['options']))
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
