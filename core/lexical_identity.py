"""Internal occurrence identity; publisher identifiers remain unmodified."""
import hashlib
import json


def source_occurrence_key(provenance):
    fields = ('resource_id', 'version', 'source_archive_sha256',
              'source_shard', 'entry_ordinal')
    values = [provenance.get(key) for key in fields]
    if (not all(isinstance(value, str) and value for value in values[:4])
            or type(values[4]) is not int or values[4] < 0):
        return None
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode()
    return 'sloleks-occurrence:' + hashlib.sha256(raw).hexdigest()


def morphology_key(record):
    """Legacy native IDs remain usable, never substitute for located occurrences."""
    provenance = record.get('provenance', record.get('evidence', {}))
    return (source_occurrence_key(provenance)
            or record.get('source_entry_id')
            or record.get('entry_id', '').split(':slot:')[0])
