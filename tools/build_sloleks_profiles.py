#!/usr/bin/env python3
"""Extend the retained Sloleks edition with lossless entries and reverse lookup.

A new immutable index, never a rewrite of the legacy morphology projection.
"""
import argparse,hashlib,json,os,sqlite3,sys,time,zipfile,zlib
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.xml_safety import CheckedXMLReader
from core.sloleks_profiles import entry
VERSION='sloleks-profile-v1'
def key(s):return s.strip().casefold()
def build(archive,output):
 if output.exists():raise ValueError('Choose a new index directory')
 output.mkdir(parents=True);start=time.monotonic()
 digest=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()
 if digest!='e53727a06b7b3a19b53637475bb111476a6746fe31b6bc8378a497d933c2398c':raise ValueError('Expected retained official Sloleks 3.1 archive')
 db=sqlite3.connect(output/'sloleks.sqlite');db.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
 CREATE TABLE entries(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL,source_key TEXT,lemma TEXT NOT NULL,lemma_key TEXT NOT NULL,category TEXT,shard TEXT,ordinal INTEGER,xml BLOB NOT NULL,profile BLOB NOT NULL);
 CREATE TABLE forms(form_key TEXT NOT NULL,entry_id INTEGER NOT NULL,PRIMARY KEY(form_key,entry_id)) WITHOUT ROWID;
 CREATE INDEX lemma_lookup ON entries(lemma_key);
 CREATE INDEX source_lookup ON entries(source_id);
 ''')
 count=0;forms=0
 try:
  with zipfile.ZipFile(archive)as z:
   readme=z.read('Sloleks.3.1/00README.txt');(output/'SOURCE-README.txt').write_bytes(readme)
   for name in sorted(n for n in z.namelist()if n.endswith('.xml')):
    ordinal=0
    with z.open(name)as stream:
     parser=ET.iterparse(CheckedXMLReader(stream),events=('start','end'));_,root=next(parser)
     for event,el in parser:
      if event!='end'or el.tag!='entry':continue
      ordinal+=1;count+=1
      lu=el.find('head/lexicalUnit');lemma=el.findtext('head/headword/lemma');assert lu is not None and lemma
      raw=ET.tostring(el,encoding='utf-8')
      values=(count,lu.get('sloleksId'),lu.get('sloleksKey'),lemma,key(lemma),el.findtext('head/grammar/category'),name,ordinal,zlib.compress(raw,3))
      row=dict(zip(('id','source_id','source_key','lemma','lemma_key','category','shard','ordinal','xml'),values))
      profile=entry(row,{'source_archive_sha256':digest,'source_url':'https://www.clarin.si/repository/xmlui/handle/11356/2080','license':'CC-BY-SA-4.0'})
      db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)',(*values,zlib.compress(json.dumps(profile,ensure_ascii=False,separators=(',',':')).encode(),3)))
      surfaces={key(n.text or'')for n in el.findall('body/wordFormList/wordForm/formRepresentations/orthographyList/orthography/form')};surfaces.discard('')
      db.executemany('INSERT INTO forms VALUES(?,?)',[(f,count)for f in surfaces]);forms+=len(surfaces)
      root.remove(el)
    db.commit();print(json.dumps({'shard':name,'entries':count,'seconds':round(time.monotonic()-start,1)}),flush=True)
  assert count>300000 and forms>2000000
  assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';db.close()
  m={'schema_version':1,'builder':VERSION,'status':'READY','resource_id':'sloleks','version':'3.1','source_url':'https://www.clarin.si/repository/xmlui/handle/11356/2080','source_archive_sha256':digest,'license':'CC-BY-SA-4.0','entries':count,'reverse_entry_surfaces':forms,'database_sha256':hashlib.file_digest((output/'sloleks.sqlite').open('rb'),'sha256').hexdigest(),'frequency_policy':'Source-supplied lexeme and form observations; never summed across readings. Zero and missing distinct.','source_encoding':'Retained parsed XML, with original field values and entry/shard locators; byte-original archive retained separately.','elapsed_seconds':round(time.monotonic()-start,3)}
  (output/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');print(json.dumps(m),flush=True)
 except BaseException:
  db.close();raise
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();build(a.archive,a.output)
