"""Source-owned document bodies; capture identity and field ownership stay intact."""
import json

def source_body(record):
    raw=record.get('capture')or{}
    if isinstance(raw.get('original_text'),str):return raw['original_text']
    parts=[]
    fields=(('question_original','answer_text','article_text','additional_info')
        if record.get('capture_kind')=='editorial_record'else
        ('original_terms','standardized_terms','proposals','rationale','term_entries','examples'))
    for field in fields:
        value=raw.get(field)
        if value is None or value==[]or value=='':continue
        if not isinstance(value,str):value=json.dumps(value,ensure_ascii=False,sort_keys=True)
        parts.append(field+':\n'+value)
    return '\n\n'.join(parts)

def body_binding(record):
    return {'coordinate_base':'bound_source_body_unicode_codepoint_half_open',
        'capture_kind':record.get('capture_kind'),
        'rendering':'native_original_text'if isinstance((record.get('capture')or{}).get('original_text'),str)
            else'source_owned_fields_with_explicit_field_labels'}
