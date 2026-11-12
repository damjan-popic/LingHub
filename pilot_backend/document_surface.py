"""Offset-preserving NFKC/case/whitespace matching of recorded literal forms.

The paired CORE/WEB copies are identical. No diacritic folding, stemming,
alias splitting, sentence interpretation or source-text rewrite occurs here.
"""
from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def _word(char):
    return char.isalnum() or char == '_' or unicodedata.category(char).startswith('M')


@lru_cache(maxsize=16)
def _mapped(text):
    expanded, mapping = [], []
    for index, char in enumerate(text):
        value = unicodedata.normalize('NFKC', char).casefold()
        expanded.extend(value)
        mapping.extend([(index, index+1)] * len(value))
    expanded = ''.join(expanded)
    whole = unicodedata.normalize('NFKC', text).casefold()
    if expanded != whole:
        aligned = []
        for kind, a, b, c, d in SequenceMatcher(None, expanded, whole, autojunk=False).get_opcodes():
            if kind == 'equal':
                aligned.extend(mapping[a:b])
            elif d > c:
                affected = mapping[a:b] or mapping[max(0,a-1):min(len(mapping),a+1)]
                span = (min(item[0] for item in affected),max(item[1] for item in affected))
                aligned.extend([span] * (d-c))
        mapping = aligned
    output, spans = [], []
    for char, span in zip(whole, mapping):
        if char.isspace():
            if output and output[-1] == ' ':
                spans[-1] = (min(spans[-1][0],span[0]),max(spans[-1][1],span[1]))
            elif output:
                output.append(' ');spans.append(span)
        else:
            output.append(char);spans.append(span)
    if output and output[-1] == ' ':
        output.pop();spans.pop()
    assert ''.join(output) == normalized(text)
    return ''.join(output),tuple(spans)


def occurrences(literal, text):
    if not isinstance(literal,str) or not literal.strip():
        return
    target = normalized(literal)
    view, spans = _mapped(text)
    for match in re.finditer(re.escape(target),view):
        covered = spans[match.start():match.end()]
        a,b = min(item[0] for item in covered),max(item[1] for item in covered)
        while a < b and text[a].isspace():a += 1
        while b > a and text[b-1].isspace():b -= 1
        # Reject partial compatibility expansions and uncertain alignment. This
        # proves equivalence of the exact original span, not a linguistic sense.
        if normalized(text[a:b]) != target:
            continue
        if (a and _word(text[a-1])) or (b < len(text) and _word(text[b])):
            continue
        yield {'start':a,'end':b,'text':text[a:b]}


def entry_forms(entry):
    """Mirror the existing source ingestion's opaque scalar alias contract."""
    if not isinstance(entry,dict) or entry.get('lang') not in {'sl','it'} or not isinstance(entry.get('term'),str):
        return
    yield {'text':entry['term'],'language':entry['lang'],'field':'term'}
    aliases = entry.get('variantForms')
    if not isinstance(aliases,list):aliases = [aliases]
    for alias in aliases:
        if isinstance(alias,(str,int,float,bool)):
            value = str(alias).strip()
            if value:
                yield {'text':value,'language':entry['lang'],'field':'variantForms','parent_term':entry['term']}
