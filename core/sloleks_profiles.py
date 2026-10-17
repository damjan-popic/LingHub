"""Source-faithful Sloleks lexemes, slots and measurements; no inferred totals."""
from contextlib import closing
import hashlib,json,sqlite3,zlib,copy
from functools import lru_cache
import xml.etree.ElementTree as ET
from pathlib import Path
MAPPING='sloleks-source-features-v1'
def features(node,path):return {n.get('name'):n.text for n in node.findall(path)}
def node_record(n):return {'tag':n.tag,'attributes':dict(n.attrib),'text':n.text,'children':[node_record(c)for c in n]}
def measures(node,scope,locator):
 out=[]
 for i,n in enumerate(node.findall('measureList/measure')):
  raw=n.text;value=None
  if raw is not None:
   try:value=int(raw)
   except ValueError:pass
  out.append({'value':value,'raw_value':raw,'measure':n.get('type'),'corpus':n.get('source'),'scope':scope,'unit':'occurrences'if n.get('type')=='frequency'else'unknown','origin':'source_supplied','source_attributes':dict(n.attrib),'source_locator':locator+'/measureList/measure['+str(i+1)+']'})
 return out

def entry(row,manifest):
 el=ET.fromstring(zlib.decompress(row['xml']));head=el.find('head');grammar=features(head,'grammar/grammarFeature')
 provenance={'resource_id':'sloleks','version':'3.1','source_entry_id':row['source_id'],'source_key':row['source_key'],'source_shard':row['shard'],'entry_ordinal':row['ordinal'],'source_archive_sha256':manifest['source_archive_sha256'],'source_url':manifest['source_url'],'license':manifest['license']}
 slots=[];patterns={}
 for wi,w in enumerate(el.findall('body/wordFormList/wordForm')):
  msd=w.find('msd');feat={**grammar,**features(w,'grammarFeatureList/grammarFeature')};variants=[]
  for ri,rep in enumerate(w.findall('formRepresentations')):
   for oi,o in enumerate(rep.findall('orthographyList/orthography')):
    form=o.findtext('form');loc=f'body/wordFormList/wordForm[{wi+1}]/formRepresentations[{ri+1}]/orthographyList/orthography[{oi+1}]'
    # Original document ordinals are locators, never fabricated source IDs.
    pats=(o.get('morphologyPatterns')or'').split();labels=[node_record(n)for n in o if n.tag not in {'form','measureList'}]
    variant={'form':form,'original_form_id':o.get('id'),'source_locator':loc,'source_pattern_ids':pats,'labels':labels,'source_attributes':dict(o.attrib),'frequency_observations':measures(o,'surface_form_in_recorded_reading',loc),'accentuation': [node_record(n)for n in rep.findall('accentuationList')], 'pronunciation':[node_record(n)for n in rep.findall('pronunciationList')],'evidence':provenance}
    variants.append(variant)
    for pat in pats:patterns.setdefault(pat,[]).append({'slot':wi,'variant':len(variants)-1})
  slots.append({'slot_locator':wi,'original_msd':msd.text if msd is not None else None,'msd_attributes':dict(msd.attrib)if msd is not None else{},'normalized_features':feat,'mapping_version':MAPPING,'mapping_basis':'source English feature names retained; no UD/JOS equivalence inferred','variants':variants,'pattern_relationship':'source_membership'if any(v['source_pattern_ids']for v in variants)else'unknown'})
 particularities=[{'kind':'parallel_slot_forms','affected_slots':[s['slot_locator']],'forms':list(dict.fromkeys(v['form']for v in s['variants'])),'explanation_basis':'multiple source orthographies in the same source grammatical slot; not a frequency preference','evidence':provenance}for s in slots if len({v['form']for v in s['variants']})>1]
 return {'source_entry_id':row['source_id'],'entry_identity':str(row['id']),'lemma':row['lemma'],'language':'sl','grammatical_type':row['category'],'grammatical_features':grammar,'source_status':head.findtext('status'),'frequency_observations':measures(head,'lexeme','head'),'patterns':[{'source_pattern_id':p,'members':members,'description':None,'evidence':provenance}for p,members in patterns.items()],'slots':slots,'particularities':particularities,'evidence':provenance,'head_source':node_record(head),'frequency_aggregation':'none; repeated readings and homographs are not summed'}

class SloleksProfiles:
 def __init__(self,root):
  self.root=Path(root).resolve(strict=True);self.database=self.root/'sloleks.sqlite';self.manifest=json.loads((self.root/'manifest.json').read_text())
  if self.manifest.get('status')!='READY'or self.manifest.get('schema_version')!=1:raise ValueError('Unready Sloleks profile edition')
  with self.database.open('rb')as f:
   if hashlib.file_digest(f,'sha256').hexdigest()!=self.manifest['database_sha256']:raise ValueError('Sloleks index digest differs')
  self.identity=self.file_identity()
  with closing(self.connect())as db:
   if 'profile'not in {row[1]for row in db.execute('PRAGMA table_info(entries)')}:raise ValueError('Compiled JSON profiles required; XML is build-time only')
 def file_identity(self):
  st=self.database.stat();return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
 def connect(self):
  if self.file_identity()!=self.identity:raise ValueError('Sloleks index changed')
  db=sqlite3.connect(self.database.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');return db
 @lru_cache(maxsize=256)
 def cached_entries(self,surface,limit,offset):
  return self._lookup(surface,limit,offset)
 def lookup(self,surface,limit=4,offset=0):
  if self.file_identity()!=self.identity:raise ValueError("Sloleks index changed")
  return copy.deepcopy(self.cached_entries(surface,limit,offset))
 def _lookup(self,surface,limit=4,offset=0):
  key=surface.strip().casefold();limit=min(8,max(1,limit))
  with closing(self.connect())as db:
   rows=db.execute('SELECT * FROM entries WHERE id IN (SELECT id FROM entries WHERE lemma_key=? UNION SELECT entry_id FROM forms WHERE form_key=?) ORDER BY CASE WHEN lemma=? THEN 0 WHEN lemma_key=? THEN 1 WHEN substr(lemma,1,1)=substr(?,1,1) THEN 2 ELSE 3 END,id LIMIT ? OFFSET ?',(key,key,surface,key,surface,limit+1,offset)).fetchall()
  lexemes=[json.loads(zlib.decompress(r['profile']))for r in rows[:limit]]
  for le in lexemes:
   le['submitted_form']=surface;le['matched_by']='exact_headword'if le['lemma']==surface else'folded_headword'if le['lemma'].casefold()==key else'recorded_form'
   le['submitted_analyses']=[{'slot_locator':s['slot_locator'],'features':s['normalized_features'],'original_msd':s['original_msd'],'variant_locator':v['source_locator'],'exact_surface':v['form']==surface}for s in le['slots']for v in s['variants']if v['form'].casefold()==key]
  return {'status':'matched'if lexemes else'no_match','submitted_form':surface,'lexemes':lexemes,'truncated':len(rows)>limit,'next_offset':offset+limit if len(rows)>limit else None,'provenance':self.manifest,'contextual_selection':'not_performed','frequency_semantics':'source-supplied lexeme total separate from surface/readings; no derived totals'}
