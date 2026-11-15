"""Source-owned document bodies; capture identity and field ownership stay intact."""
import json,re

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
    unit=(record.get('capture')or{}).get('source_unit')
    if unit:return {'coordinate_base':'bound_retained_passage_body_unicode_codepoint_half_open','rendering':'unchanged_normal_source_passage_text','native_span':unit.get('span'),'writer_projection':unit.get('writer_projection')}
    return {'coordinate_base':'bound_source_body_unicode_codepoint_half_open',
        'capture_kind':record.get('capture_kind'),
        'rendering':'native_original_text'if isinstance((record.get('capture')or{}).get('original_text'),str)
            else'source_owned_fields_with_explicit_field_labels'}


def source_topics(record):
    """Unchanged quoted source expressions, not inferred document subjects."""
    raw=record.get('capture')or{};unit=raw.get('source_unit')or{}
    context=unit.get('context');question=context.get('question')if isinstance(context,dict)else None
    topic='\n'.join(v for v in (raw.get('title'),raw.get('question_original'),question)if isinstance(v,str))
    quoted=re.findall(r'»([^»«\n]+)«|«([^«»\n]+)»|“([^“”\n]+)”|"([^"\n]+)"',topic)
    return list(dict.fromkeys(next(v for v in row if v)for row in quoted))
