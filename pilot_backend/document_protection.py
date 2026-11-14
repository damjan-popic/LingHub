"""Shared literal quote/identifier boundaries, not a semantic check."""
import re

def protected(text,a,b):
    # An advisory token boundary is weaker than a safe edit boundary.
    if a and(text[a-1].isalnum()or text[a-1]in '_-/@'):return 'connected_identifier_or_compound'
    if b<len(text)and(text[b].isalnum()or text[b]in '_-/@'):return 'connected_identifier_or_compound'
    # A terminal period or prose colon is ordinary punctuation. A digit
    # joined through either delimiter makes a connected identifier.
    if (a>=2 and text[a-1]in '.:'and text[a-2].isalnum()
            or b+1<len(text)and text[b]in '.:'and text[b+1].isalnum()):
        return 'connected_identifier_or_compound'
    # Single quotes open only at a word boundary; apostrophes inside
    # words are not quotation openers. Inner Slovene quotes are paired too.
    quotes=r"»[^«]*«|«[^»]*»|›[^‹]*‹|‹[^›]*›|“[^”]*”|„[^“]*“|‘[^’]*’|‚[^‘]*‘|\"[^\"\n]*\"|`[^`]*`|(?<!\w)'(?=\S)[^\n]*?(?<=\S)'(?!\w)|https?://\S+|\S+@\S+"
    for match in re.finditer(quotes,text):
        if a<match.end()and match.start()<b:return 'quotation_or_address'
    if (a>=2 and text[a-1]in "'’"and text[a-2].isalnum()
            or b+1<len(text)and text[b]in "'’"and text[b+1].isalnum()):
        return 'connected_identifier_or_compound'
    return None

