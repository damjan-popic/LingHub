"""Deliver recorded relationships from admitted captures and existing policy.

No inferred edges, source ranking, new source selection or network access.
Origin paths refer to the saved capture/policy, not model-authored metadata.
"""
from copy import deepcopy
from .store import digest
from .document_application_observations import application_registry,bound_entries

VERSION='document-evidence-relationships-v1'


def capture_relationships(record):
    raw=record['capture'];unit=raw.get('source_unit')or{};body=unit.get('original_json')or{}
    origin={'kind':'retained_native_capture','evidence_id':record['evidence_id'],'capture_sha256':record['capture_sha256'],
        'source_slug':record['source_slug'],'external_id':record['external_id'],
        'source_revision':raw.get('source_revision')}
    facts=[]
    def keep(role,pointer,value):
        if value is not None:facts.append({'role':role,'origin':{**origin,'pointer':pointer},'value':deepcopy(value)})
    keep('source_derivation_not_independent_support','/native_table_projection',raw.get('native_table_projection'))
    # These complete source-owned values are omitted as machine audit by the
    # ordinary reading. Here their exact provenance reaches the writer too.
    keep('source_unit_provenance','/source_unit/provenance',unit.get('provenance'))
    keep('source_record_provenance','/source_unit/original_json/provenance',body.get('provenance'))
    keep('source_archive_entry_lineage','/source_unit/original_json/fields/provenance',(body.get('fields')or{}).get('provenance'))
    sense=(body.get('fields')or{}).get('sense')or{}
    if 'relations'in sense:
        keep('within_source_sense_relationships_not_cross_dictionary_identity','/source_unit/original_json/fields/sense',sense)
    entries=[{'registry_entry':index,'value':deepcopy(entry)}for index,entry in bound_entries(raw)]
    return {'capture_origin':origin,'recorded_facts':facts,'candidate_policy_entries':entries}


def snapshot(sources,sections,captures):
    policy,policy_sha=application_registry();owned={}
    for ref in sources:
        data=deepcopy(captures[ref]);candidates=data.pop('candidate_policy_entries')
        row_hashes={f.get('source_focus',{}).get('text_sha256')for s in sections for f in s['findings']if ref in f['source_refs']}
        for candidate in candidates:
            entry=candidate['value']
            if entry['row_sha256']not in row_hashes:continue
            data['recorded_facts'].append({'role':'bounded_project_application_policy_not_linguistic_certification',
                'origin':{'kind':'project_application_policy','artifact':'project:document_source_applications.v1.json','sha256':policy_sha,
                    'version':policy['version'],'pointer':'/entries/'+str(candidate['registry_entry'])},'value':entry})
        data['unknowns']={
            'registry_resource_manifest_relationships':'not_delivered_by_existing_saved_registry_consumer_path',
            'cross_source_independence_conflict_equivalence_supersession_authority_order':'unknown_unless_explicitly_recorded_in_the_delivered_facts'}
        owned[ref]=data
    value={'version':VERSION,'authority':'server_frozen_existing_project_policy_and_source_records',
        'by_source':owned,'scope':'Recorded facts retain their own scope. Resource/annotation lineage is not linguistic supersession; classification is not source hierarchy. Unknown edges are not negative evidence.'}
    value['snapshot_sha256']=digest(value);return value
