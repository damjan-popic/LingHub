"""Category-specific roles of the original LORIS fields, shared with CORE."""
VERSION = 'loris-source-roles-v1'


def loris_advice_roles(record):
    payload = record.get('payload')
    if not isinstance(payload, dict):
        return None
    category = record.get('category')
    def text(key):
        value = payload.get(key)
        return value if isinstance(value, str) and value.strip() else None
    if category == 'nepravilni' and text('Iscete') and text('Razlaga'):
        return {'version': VERSION, 'source_category': category,
                'relation': 'normative_explanation',
                'input_form': record.get('matched_text') or record.get('surface'),
                'recommended_replacements': text('Iscete'),
                'foreign_equivalent_cue': None, 'explanation': text('Razlaga'),
                'reference_url': text('Povezava')}
    if category == 'paronym' and text('Izhodisce') and text('SteMislili'):
        return {'version': VERSION, 'source_category': category,
                'relation': 'curated_replacement',
                'input_form': text('Izhodisce'),
                'recommended_replacements': text('SteMislili'),
                'foreign_equivalent_cue': text('Iscete'),
                'explanation': text('Vslo'), 'reference_url': text('Link')}
    return None
