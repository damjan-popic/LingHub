"""Bounded document text capture and source-linked report exports.

The source package is never unpacked, executed, fetched or rewritten. A report
is a new document and does not claim to preserve the source's Word layout.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import re
import stat
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from .security import Failure

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_TEXT = 200_000
MAX_BLOCKS = 100
MAX_SECTION = 3000
SECTION_TARGET = 1500
MAX_MEMBERS = 512
MAX_EXPANDED = 16 * 1024 * 1024
MAX_XML = 4 * 1024 * 1024
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
CONTENT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def _invalid(code='document_invalid', status=400):
    raise Failure(code, status)


def _xml(data):
    if len(data) > MAX_XML:
        _invalid('document_too_large', 413)
    try:
        decoded = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff'))
                              else 'utf-8-sig')
    except UnicodeError:
        _invalid()
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', decoded, re.I):
        _invalid()
    try:
        # Parse exactly the encoding whose declarations were checked above.
        # XML's automatic UTF-16 detection must not bypass the DTD guard.
        root = ET.fromstring(decoded)
    except (ET.ParseError, ValueError, RecursionError):
        _invalid()
    # Limit nesting before any subsequent tree walk uses recursive traversal.
    stack = [(root, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 64 or count > 200_000:
            _invalid('document_too_large', 413)
        stack.extend((child, depth + 1) for child in node)
    return root


def _valid_text(value):
    # Reject illegal XML/control values consistently for both input formats.
    if any(ord(c) < 32 and c not in '\n\r\t' or 0xD800 <= ord(c) <= 0xDFFF
           or ord(c) in {0xFFFE, 0xFFFF} for c in value):
        _invalid()
    return value


def _split(value,protected=()):
    """Keep whole sentences where possible within the document operation budget.

    This is text packaging, not a linguistic sentence analysis. Every slice is
    exact and adjacent; the full source and its original offsets are retained.
    """
    offset=0
    while len(value) > SECTION_TARGET:
        prefix=value[:SECTION_TARGET]
        boundaries=[m.end()for m in re.finditer(r'[.!?][»”"\')\]]*\s+|\r?\n[ \t]*\r?\n',prefix)
                    if m.end()>=SECTION_TARGET//2]
        cut=max(boundaries,default=0)
        if not cut:cut=max((m.end() for m in re.finditer(r'\s+',prefix)),default=0)
        if cut == 0:
            cut = SECTION_TARGET
        for mark in protected:
            if mark['start']<offset+cut<mark['end']:
                before,after=mark['start']-offset,mark['end']-offset
                if before>=SECTION_TARGET//2:cut=before
                elif after<=MAX_SECTION:cut=after
                else:_invalid('document_unsupported')
                break
        if value[cut-1:cut+1]=='\r\n':cut+=1
        yield value[:cut]
        value = value[cut:]
        offset+=cut
    if value:
        yield value


def _package(data):
    archive = None
    try:
        archive = ZipFile(io.BytesIO(data))
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_EXPANDED:
            _invalid('document_too_large', 413)
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            _invalid()
        for info in infos:
            path = PurePosixPath(info.filename)
            # Deflate supports bounded decompression in ZipExtFile; reject the
            # less common codecs rather than trusting their declared sizes.
            if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                _invalid('document_unsupported')
            if (info.flag_bits & 1 or path.is_absolute() or '..' in path.parts
                    or '\\' in info.filename or '\0' in info.filename
                    or ':' in info.filename or stat.S_ISLNK(info.external_attr >> 16)):
                _invalid()
            if info.file_size > MAX_EXPANDED or (info.file_size > 1_000_000
                    and info.file_size > 200 * max(1, info.compress_size)):
                _invalid('document_too_large', 413)
        if '[Content_Types].xml' not in names or 'word/document.xml' not in names:
            _invalid()
        # Macro/embedded packages are unsupported even when their text looks ordinary.
        if any(re.search(r'(?:vbaProject|activeX|embeddings|altChunk)', name, re.I)
               for name in names):
            _invalid('document_unsupported')
        for name in names:
            if name.endswith('.rels') or name == '[Content_Types].xml':
                root = _xml(archive.read(name))
                if name == '[Content_Types].xml':
                    if root.tag != CONTENT + 'Types':
                        _invalid()
                    types = [e.get('ContentType', '') for e in root]
                    if any('macroEnabled' in t or 'vbaProject' in t for t in types):
                        _invalid('document_unsupported')
                    # Body, headers, footers, notes and comments are captured below;
                    # no unrecognized Word text part may be silently discarded.
                    for entry in root:
                        content_type = entry.get('ContentType', '')
                        part = entry.get('PartName', '').lstrip('/')
                        if 'wordprocessingml' in content_type and part.endswith('.xml'):
                            allowed = (part == 'word/document.xml'
                                or re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml', part)
                                or part in {'word/styles.xml', 'word/settings.xml', 'word/numbering.xml',
                                            'word/fontTable.xml', 'word/webSettings.xml'})
                            if not allowed:
                                _invalid('document_unsupported')
        return archive
    except Failure:
        if archive is not None:
            archive.close()
        raise
    except (BadZipFile, KeyError, RuntimeError, OSError, ValueError, NotImplementedError):
        if archive is not None:
            archive.close()
        _invalid()


def _paragraph_text(paragraph):
    output = []
    for node in paragraph.iter():
        if node.tag == W + 't':
            output.append(node.text or '')
        elif node.tag == W + 'tab':
            output.append('\t')
        elif node.tag in {W + 'br', W + 'cr'}:
            output.append('\n')
        elif node.tag == W + 'noBreakHyphen':
            output.append('\u2011')
        elif node.tag == W + 'softHyphen':
            output.append('\u00ad')
        elif node.tag in {W + 'sym', W + 'ptab', W + 'delText', W + 'instrText', W + 'fldChar', W + 'fldSimple',
                          W + 'del', W + 'ins', W + 'moveFrom', W + 'moveTo',
                          W + 'altChunk', W + 'txbxContent', W + 'object'}:
            _invalid('document_unsupported')
    return _valid_text(''.join(output))


def _preserved_page_footer(root,part):
    """Recognize only inert cached PAGE fields; never evaluate a Word field."""
    field_tags={W+'fldSimple',W+'fldChar',W+'instrText'}
    fields=[node for node in root.iter()if node.tag in field_tags]
    if not fields or re.fullmatch(r'word/footer[0-9]+\.xml',part)is None:return False
    for field in fields:
        if any(node is not field and node.tag in field_tags for node in field.iter()):return False
        instruction=field.get(W+'instr','')
        if (field.tag!=W+'fldSimple'or not instruction.isascii()
                or re.fullmatch(r'[ \t]*PAGE(?:[ \t]+\\\*[ \t]+MERGEFORMAT)?[ \t]*',instruction,re.I)is None
                or not set(field.attrib)<={W+'instr',W+'dirty',W+'fldLock'}):return False
        if any(field.get(key)not in {'true','false','1','0'}for key in(W+'dirty',W+'fldLock')if key in field.attrib):return False
        if not list(field)or any(run.tag!=W+'r'for run in field):return False
        for run in field:
            if any(child.tag not in {W+'rPr',W+'t'}for child in run):return False
        cached=''.join(node.text or''for node in field.iter(W+'t'))
        if re.fullmatch('[0-9]{1,20}',cached)is None:return False
    return True


def _word_blocks(data):
    blocks, warnings = [], []
    with _package(data) as archive:
        names = archive.namelist()
        parts = ['word/document.xml'] + sorted(n for n in names if re.fullmatch(
            r'word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml', n))
        for name in parts:
            root = _xml(archive.read(name))
            expected = ('document' if name == 'word/document.xml' else
                        'hdr' if '/header' in name else 'ftr' if '/footer' in name else
                        PurePosixPath(name).stem)
            if root.tag != W + expected or (expected == 'document'
                    and len(root.findall(W + 'body')) != 1):
                _invalid()
            page_footer=_preserved_page_footer(root,name)
            # Reject constructs whose visible text cannot be captured faithfully.
            bad = {'altChunk', 'txbxContent', 'object', 'subDoc', 'del', 'ins',
                   'moveFrom', 'moveTo', 'fldSimple', 'fldChar', 'instrText', 'sym',
                   'ptab', 'delText'}
            if page_footer:bad.remove('fldSimple')
            if any(n.tag in {W + tag for tag in bad} for n in root.iter()):
                _invalid('document_unsupported')
            if any(n.tag.rsplit('}', 1)[-1] in {'oMath', 'oMathPara', 'AlternateContent'}
                   for n in root.iter()):
                _invalid('document_unsupported')
            # DrawingML/WordArt text needs a separate layout-aware capture path.
            if any(n.tag.rsplit('}', 1)[-1] == 't' and n.tag != W + 't'
                   for n in root.iter()):
                _invalid('document_unsupported')
            if any(n.tag in {W + 'drawing', W + 'pict'} for n in root.iter()):
                warnings.append('document_images_not_reviewed')

            if page_footer:
                # Preserve the whole footer unchanged, including its static text.
                warnings.append('document_page_footer_not_reviewed')
                continue

            def visit(node, path):
                if node.tag == W + 'p':
                    value = _paragraph_text(node)
                    if value:
                        blocks.append((path, value))
                    return
                counts = {}
                for child in node:
                    local = child.tag.rsplit('}', 1)[-1]
                    counts[local] = counts.get(local, 0) + 1
                    visit(child, f'{path}/{local}[{counts[local]}]')

            visit(root, name)
    return blocks, list(dict.fromkeys(warnings))


def extract(data: bytes, filename: str):
    """Capture TXT/DOCX into ordered, located sections without silent truncation."""
    if not isinstance(data, bytes) or not isinstance(filename, str):
        _invalid()
    if len(data) > MAX_UPLOAD_BYTES:
        _invalid('document_too_large', 413)
    if (not filename or len(filename) > 180 or any(c in filename for c in '/\\\0')
            or any(ord(c) < 32 for c in filename)):
        _invalid()
    suffix = PurePosixPath(filename).suffix.lower()
    anchors={}
    if suffix == '.txt':
        try:
            value = data.decode('utf-8-sig')
        except UnicodeError:
            _invalid()
        # Keep even blank lines and exact CRLFs in the captured source text.
        source, warnings = [('text', _valid_text(value))], []
    elif suffix == '.docx':
        try:
            source, warnings = _word_blocks(data)
            from .document_word import source_anchors
            try:anchors=source_anchors(data)
            except Failure as error:
                if error.code=='document_draft_unavailable':_invalid('document_unsupported')
                raise
        except Failure:
            raise
        except (BadZipFile, KeyError, RuntimeError, OSError, ValueError, NotImplementedError):
            _invalid()
    else:
        _invalid('document_unsupported', 415)
    if sum(len(value) for _, value in source) > MAX_TEXT:
        _invalid('document_too_large', 413)
    if not any(value.strip() for _, value in source):
        _invalid('document_empty')
    blocks = []
    for location, value in source:
        offset = 0
        marks=anchors.get(location,[])
        for part in _split(value,marks):
            spans=[{**mark,'start':mark['start']-offset,'end':mark['end']-offset}for mark in marks
                if offset<=mark['start']and mark['end']<=offset+len(part)]
            if len(spans)>16:_invalid('document_unsupported')
            blocks.append({'index': len(blocks), 'location': f'{location}@{offset}:{offset + len(part)}',
                           'text': part,**({'inline_spans':spans}if spans else{})})
            offset += len(part)
            if len(blocks) > MAX_BLOCKS:
                _invalid('document_too_large', 413)
    return {'name': filename, 'sha256': hashlib.sha256(data).hexdigest(),
            'blocks': blocks, 'warnings': warnings}


def report_location(location, locale='sl'):
    """Human locations, derived from the retained native locator without guessing."""
    it=locale=='it'
    location=str(location).rsplit('@',1)[0]
    if location=='text':return 'Testo'if it else'Besedilo'
    part=('Intestazione'if it else'Glava')if re.search(r'/header\d+\.xml',location)else(
        'Piè di pagina'if it else'Noga')if re.search(r'/footer\d+\.xml',location)else(
        'Nota'if it else'Opomba')if '/footnotes.xml'in location or'/endnotes.xml'in location else(
        'Commento originale'if it else'Izvirna pripomba')if '/comments.xml'in location else(
        'Testo'if it else'Besedilo')
    labels={'tbl':('tabella','tabela'),'tr':('riga','vrstica'),'tc':('cella','celica'),'p':('paragrafo','odstavek')}
    positions=[labels[k][0 if it else 1]+' '+v for k,v in re.findall(r'/(tbl|tr|tc|p)\[(\d+)\]',location)]
    return part+(' · '+' · '.join(positions)if positions else'')


def repeated_translation_sections(sections):
    """Flag exact draft replay across different slices of one paragraph.

    This is a conservative export check, not a semantic translation judge.
    Identical source slices and independent paragraphs remain valid repeats.
    """
    seen={};repeated=set()
    for position,section in enumerate(sections):
        result=section.get('document_result')or{}
        location=section.get('location');source=section.get('text');proposed=result.get('proposed_text')
        if (section.get('state')!='complete'or result.get('status')!='draft'or result.get('mode')!='translate'
                or not isinstance(location,str)or '@'not in location or not isinstance(source,str)
                or not isinstance(proposed,str)or not proposed.strip()):continue
        key=(location.rsplit('@',1)[0],result.get('source_language'),result.get('target_language'),proposed.strip())
        if key in seen and seen[key]!=source.strip():repeated.add(position)
        else:seen[key]=source.strip()
    return repeated


def failed_document_operation(section):
    """A recorded operation failure differs from bounded source coverage."""
    return any(isinstance(item,dict)and item.get('code')=='document_transform_failed'
               for item in section.get('limitations',[]))


def _report_blocks(report):
    it=report.get('locale')=='it';mode=report.get('mode')
    title=('Rapporto di traduzione'if it else'Poročilo o prevodu')if mode=='translate'else(
        'Rapporto di revisione'if it else'Poročilo jezikovnega pregleda')
    yield 'title',title
    yield 'body',('Documento 'if it else'Dokument ')+str(report.get('name',''))
    languages={'sl':'Slovenščina'if not it else'Sloveno','it':'Italijanščina'if not it else'Italiano'}
    if mode=='translate':
        yield 'body',('Direzione 'if it else'Smer ')+languages.get(report.get('source_language'),'')+' → '+languages.get(report.get('target_language'),'')
    else:yield 'body',('Lingua 'if it else'Jezik ')+languages.get(report.get('source_language'),'')
    yield 'body',('Il rapporto confronta originale e proposta, indica la copertura e distingue le fonti delle modifiche dal materiale di contesto. Le proposte richiedono una revisione umana.'if it else
        'Poročilo primerja izvirnik in predlog, opiše obseg obdelave ter loči vire sprememb od gradiva za kontekst. Predloge mora preveriti človek.')
    yield 'body',('Il rapporto usa una propria impaginazione. La bozza Word è un file separato.'if it else'Poročilo uporablja svojo postavitev. Wordov osnutek je ločena datoteka.')
    export_status=review_export_status(report)if mode=='review'else None
    if mode=='review':
        yield 'body',partial_review_labels(report.get('locale','sl'))[1]
        safe=export_status['partial_review']
        if safe['available']:
            yield 'body',(('Modifiche supportate incluse / proposte non incluse / sezioni incomplete: 'if it else
                'Vključene podprte spremembe / nevključeni predlogi / nepopolno pregledani odseki: ')
                +str(safe['applied_edits'])+' / '+str(safe['withheld_edits'])+' / '+str(safe['incomplete_sections']))
    if any((section.get('document_result')or{}).get('review')for section in report.get('sections',[])):
        from .evidence_review import common_report_notice
        yield 'body',common_report_notice(report.get('locale','sl'))
    if report.get('saved_result_url'):
        yield 'body',('Risultato salvato sul servizio KLIKai originale; richiede l’accesso con lo stesso account.'if it else'Shranjeni rezultat na izvornem primerku KLIKai; potrebna je prijava z istim računom.')
        yield 'link',('Apri documento salvato'if it else'Odpri shranjeni dokument',report['saved_result_url'])
    if report.get('review_manifest'):
        overview=report['review_manifest'];counts=overview['counts']
        yield 'subheading','Panoramica deterministica'if it else'Pregled ugotovitev'
        yield 'body',('Indicazioni / confronti / contesto / incluse / condizionate: 'if it else'Priporočila / primerjave / kontekst / vključeni / pogojni: ')+str(counts['qualified_advice'])+' / '+str(counts['recorded_contrast'])+' / '+str(counts['recorded_form_context'])+' / '+str(counts['executable_application'])+' / '+str(counts.get('application_proposal',0))
        yield 'body',('Nessun autore generativo usato per il riesame. Le indicazioni non stabiliscono automaticamente un errore; la copertura è limitata.'if it else'Pri pregledu generativni pisec ni bil uporabljen. Nasveti ne pomenijo samodejno ugotovljene napake; obseg je omejen.')
        summary=report.get('editorial_summary')
        if summary:
            yield 'subheading','Riepilogo editoriale IA facoltativo'if it else'Neobvezen uredniški povzetek z umetno inteligenco'
            if summary.get('historical'):yield 'body','Storico: riguarda una revisione precedente.'if it else'Zgodovinski: nanaša se na prejšnjo revizijo ugotovitev.'
            elif summary.get('status')=='available':
                for point in summary['output']['points']:yield 'body',point['text']+' ['+', '.join(point['finding_ids'])+']'
            else:yield 'body','Non disponibile. Risultati e download restano validi nel loro ambito.'if it else'Ni na voljo. Ugotovitve in prenosi ostajajo na voljo v navedenem obsegu.'
    sections=report.get('sections',[])
    repeated=repeated_translation_sections(sections)
    failed_operations=any(failed_document_operation(s)for s in sections)
    if mode in {'review','translate'} and (mode=='review'and not export_status['complete_available']or repeated or failed_operations or report.get('state')!='complete' or not sections or any(
            s.get('state')!='complete' or (s.get('document_result')or{}).get('status')!='draft'for s in sections)):
        yield 'body',('Il documento non ha ancora una bozza completa. Il rapporto conserva anche le sezioni non completate o non revisionate.'if it else
            'Dokument še nima popolnega osnutka. Poročilo ohrani tudi odseke, ki niso bili dokončani ali pregledani.')
    if repeated:yield 'body',('Una parte ripete la bozza di un’altra parte dello stesso paragrafo, anche se l’originale è diverso. La bozza Word non è disponibile; controlla il contenuto.'if it else
        'Del odstavka ponovi osnutek drugega dela istega odstavka, čeprav je izvirnik drugačen. Wordov osnutek ni na voljo; preverite vsebino.')
    for warning in report.get('warnings',[]):
        if warning=='document_page_footer_not_reviewed':yield 'body',('Il piè di pagina con il campo PAGE è conservato senza modifiche; testo e numerazione del piè di pagina non sono stati revisionati né aggiornati.'if it else'Noga s poljem PAGE je ohranjena brez sprememb; besedilo in številčenje noge nista pregledana ali posodobljena.')
        if warning=='document_translation_expired':yield 'body',('Il tempo della traduzione privata è scaduto; le parti non completate restano esplicite.'if it else'Čas zasebnega prevoda je potekel; nedokončani deli ostanejo izrecno označeni.')
        if warning=='document_translation_cancel_pending':yield 'body',('Si attende la conferma di arresto del traduttore. Un risultato tardivo non sarà utilizzato.'if it else'Čakamo na potrditev ustavitve prevajalnika. Pozni rezultat ne bo uporabljen.')
        if warning=='document_images_not_reviewed':yield 'body',('Le immagini sono conservate dove possibile, ma il loro contenuto non è stato revisionato né tradotto.'if it else'Slike se po možnosti ohranijo, njihova vsebina pa ni bila jezikovno pregledana ali prevedena.')
    if report.get('ai_suggestions_enabled'):
        from .document_ai_contract import EVIDENCE_LABEL
        yield 'subheading',EVIDENCE_LABEL if not it else'Suggerimenti basati sulle fonti KLIKai'
    parts={};seen={}
    for section in sections:
        key=str(section.get('location','')).rsplit('@',1)[0];parts[key]=parts.get(key,0)+1
    for position,section in enumerate(sections):
        number=int(section.get('index',0))+1
        yield 'heading',('Sezione 'if it else'Odsek ')+str(number)
        key=str(section.get('location','')).rsplit('@',1)[0];seen[key]=seen.get(key,0)+1
        label=report_location(section.get('location',''),report.get('locale','sl'))
        if parts[key]>1:label+=(' · parte 'if it else' · del ')+str(seen[key])+'/'+str(parts[key])
        yield 'body',label
        if section.get('saved_result_url'):
            yield 'body',('Risposta salvata e identità delle fonti'if it else'Shranjeni odgovor in identitete virov')
            yield 'link',('Apri risposta salvata'if it else'Odpri shranjeni odgovor',section['saved_result_url'])
        result=section.get('document_result')or{};state=section.get('state',section.get('status','unknown'))
        if failed_document_operation(section):
            status='Una sotto-operazione richiesta non è riuscita; la bozza completa non è disponibile.'if it else'Del zahtevane obdelave ni uspel; popoln osnutek ni na voljo.'
        elif position in repeated:
            status='Bozza ripetuta da un’altra parte; traduzione da verificare.'if it else'Ponovljen osnutek drugega dela; prevod je treba preveriti.'
        elif result.get('status')=='not_reviewed':
            status='Non revisionato; originale conservato senza modifiche.'if it else'Ni pregledano; izvirnik je ohranjen brez sprememb.'
        elif state=='complete'and result.get('status')=='draft':
            status=('Bozza di traduzione preparata.'if it else'Prevodni osnutek je pripravljen.')if mode=='translate'else(
                'Modifiche proposte nel testo della bozza.'if it else'Spremembe so vključene v predlagano besedilo.')if result.get('edits')else(
                'Nessuna modifica proposta nell’ambito supportato.'if it else'V podprtem obsegu ni predlaganih sprememb.')
        else:status=({'complete':('Elaborazione terminata.','Obdelava je zaključena.'),'failed':('Elaborazione non riuscita.','Obdelava ni uspela.'),
            'cancelled':('Elaborazione interrotta.','Obdelava je ustavljena.'),'queued':('In attesa.','Čaka na obdelavo.'),
            'running':('Elaborazione in corso.','Obdelava poteka.')} .get(state,('Non elaborato.','Ni obdelano.')))[0 if it else 1]
        yield 'body',status
        delivery=section.get('guidance_delivery')
        if isinstance(delivery,dict):
            states={'prepared_not_dispatched':('Materiale KLIKai preparato, non inviato al modello.','Gradivo KLIKai je pripravljeno, ni bilo poslano modelu.'),
                'possible_dispatch':('Input KLIKai conservato per una richiesta forse inviata; consegna non confermata.','Vhod z gradivom KLIKai je shranjen za morda poslano zahtevo; dostava ni potrjena.'),
                'response_received':('Ricevuta una risposta alla richiesta con il materiale KLIKai conservato.','Prejet je odgovor na zahtevo s shranjenim gradivom KLIKai.')}
            if delivery.get('delivery_state')in states:
                yield 'body',states[delivery['delivery_state']][0 if it else 1]
            yield 'body',('La consegna delle fonti non dimostra il loro rispetto. Le modifiche citano il supporto dichiarato; la verifica linguistica indipendente resta distinta.'if it else'Posredovanje virov ne dokazuje njihovega upoštevanja. Pri spremembah je navedena zatrjevana podpora; neodvisno jezikovno preverjanje ostaja ločeno.')
        if mode=='review':
            safe=next((row for row in export_status['partial_review']['sections']if row['index']==section.get('index')),None)
            if safe is not None:
                yield 'body',(('Nella bozza parziale: 'if it else'V delnem osnutku: ')
                    +str(safe['applied_edits'])+(' modifiche supportate incluse; 'if it else' vključenih podprtih sprememb; ')
                    +str(safe['withheld_edits'])+(' proposte non incluse.'if it else' nevključenih predlogov.'))
                if safe['original_preserved']:
                    yield 'body',('Originale conservato senza modifiche.'if it else'Izvirnik je ohranjen brez sprememb.')
                if safe['incomplete']:
                    yield 'body',('Copertura incompleta: le parti irrisolte o non controllate restano da verificare.'if it else
                        'Pregled je nepopoln: nerazrešene ali nepreverjene dele je treba preveriti.')
        yield 'subheading','Originale'if it else'Izvirnik'
        yield 'body',str(section.get('text',''))
        yield 'subheading','Proposta'if it else'Predlagano besedilo'
        yield 'body',str(result.get('proposed_text',section.get('complete_output',section.get('output',''))))
        for note in dict.fromkeys(result.get('notes',[])):
            if result.get('review')and note=='Pregled vsebuje navedene ugotovitve iz virov in opravljena preverjanja. V osnutek so vključeni samo predlogi z navedenim dokazom uporabe; celovita jezikovna pravilnost ni potrjena.':continue
            known={'evidence_excluded_from_writer':('Parte delle fonti trovate non è stata inclusa per limiti di spazio; la copertura è quindi parziale.','Del najdenih virov zaradi omejenega prostora ni bil uporabljen; obseg obdelave je zato delen.'),
                'source_support_unavailable':('Nessuna fonte ammessa disponibile per verificare questa sezione.','Za preverjanje tega odseka ni na voljo dovoljenih virov.'),
                'private_translation_candidate':('Traduzione privata sperimentale; controlla integralmente significato e stile.','Zasebni preizkus prevoda; preverite celoten pomen in slog.'),
                'terminology_sources_not_supplied':('Nessuna fonte terminologica KLIKai è stata inviata al traduttore.','Prevajalniku niso bili poslani terminološki viri KLIKai.'),
                'qualified_source_guidance_supplied':('Il traduttore ha ricevuto i brani KLIKai con le loro qualificazioni; l’applicazione e il significato richiedono verifica.','Prevajalnik je prejel odlomke KLIKai z njihovimi omejitvami; uporabo in pomen je treba preveriti.'),
                'semantic_inline_alignment_not_supported':('Questo percorso non associa espressioni tradotte a collegamenti o enfasi originali.','Ta pot ne povezuje prevedenih izrazov s povezavami ali poudarki v izvirniku.')}
            yield 'body',known.get(note,(note,note))[0 if it else 1]
        if result.get('review'):
            from .evidence_review import report_blocks
            yield from report_blocks(result['review'],report.get('locale','sl'),include_common=False)
            for check in result['review'].get('checks',[]):
                extent=check.get('checked_extent')if isinstance(check,dict)else None
                if (isinstance(check,dict)and check.get('check_id')=='comma_prediction'and check.get('status')=='partial'
                        and isinstance(extent,dict)and extent.get('start')==0 and extent.get('complete')is False
                        and extent.get('offset_convention')=='unicode_codepoint_half_open'
                        and type(extent.get('end'))is int and 0<=extent['end']<len(section['text'])):
                    yield 'body',(('Il controllo dedicato ha esaminato il prefisso di 'if it else'Namensko preverjanje je zajelo začetnih ')
                        +str(extent['end'])+'/'+str(len(section['text']))
                        +(' caratteri. La coda non controllata conserva l’originale; ogni modifica richiede una giustificazione propria.'if it else' znakov. Nepreverjeni konec ohrani izvirnik; vsaka sprememba potrebuje svojo utemeljitev.'))
        sources=section.get('sources')or[]
        if section.get('writer_input_selection')=='unknown':
            yield 'body',('La selezione delle fonti inviate al modello non è ricostruibile dai dati conservati. Non viene dedotta dall’elenco dei risultati trovati.'if it else'Iz shranjenih podatkov ni mogoče ugotoviti izbora virov, poslanih modelu. Izbora ne sklepamo iz seznama najdenih zadetkov.')
        if section.get('unresolved_writer_evidence_ids'):
            yield 'body',('Identità delle fonti nell’input senza una scheda recuperabile: 'if it else'Identitete virov v vhodu brez dosegljivega referenčnega zapisa: ')+', '.join(section['unresolved_writer_evidence_ids'])
        numbers={source.get('evidence_id'):source.get('local_number',i+1)for i,source in enumerate(sources)}
        used=set()
        for edit in result.get('edits',[]):
            used.update(edit['evidence_ids'])
            yield 'subheading','Modifica proposta'if it else'Predlagana sprememba'
            yield 'body',str(edit['original'])+' → '+str(edit['replacement'])
            yield 'body',str(edit['explanation'])
            yield 'body',('Fonti 'if it else'Viri ')+', '.join('['+str(numbers[eid])+']'for eid in edit['evidence_ids']if eid in numbers)
            if edit.get('support_quote'):
                yield 'subheading','Passaggio a sostegno'if it else'Podporni odlomek'
                yield 'quote',edit['support_quote']
        claimed=[s for s in sources if s.get('evidence_id')in used]
        contextual=[s for s in sources if s.get('evidence_id')not in used]
        for rows,heading in [(claimed,'Fonti delle modifiche'if it else'Viri sprememb'),(contextual,'Materiale di contesto'if it else'Gradivo za kontekst')]:
            if not rows:continue
            yield 'subheading',heading
            if rows is contextual:yield 'body',('Questo materiale non è una prova della correttezza dell’intero testo. Non è conservato un collegamento a specifiche scelte terminologiche.'if it else'To gradivo ni potrditev pravilnosti celotnega besedila. Povezava s posameznimi terminološkimi odločitvami ni shranjena.')
            for source in rows:
                title=' '.join(str(source.get('title')or source.get('source_label')or'').split())
                number=source.get('local_number')or next(i+1 for i,s in enumerate(sources)if s is source)
                yield 'body','['+str(number)+'] '+title
                for qualification in source.get('captured_qualifications',[]):yield 'body',qualification
                if rows is contextual:
                    roles={'finding_evidence':('Fonte della specifica indicazione; nessun autore generativo invocato.','Vir navedene ugotovitve; generativni pisec ni bil poklican.'),
                           'review_context':('Contesto trovato nel riesame delle fonti; nessuna conclusione specifica associata.','Kontekst, najden pri pregledu virov; brez povezave s posameznim sklepom.'),
                           'writer_input_context':('Fonte inclusa nell’input conservato del modello; uso in una specifica conclusione non attestato.','Vir je v shranjenem vhodu modela; uporaba pri posameznem sklepu ni potrjena.'),
                           'retrieval_context':('Materiale trovato, escluso dall’input conservato del modello.','Najdeno gradivo, ki ni bilo v shranjenem vhodu modela.'),
                           'selection_unknown':('Selezione per l’input del modello sconosciuta.','Izbor za vhod modela ni znan.')}
                    yield 'body',roles.get(source.get('report_role'),roles['selection_unknown'])[0 if it else 1]
                bundled=source.get('bundled_source_path')
                from .document_report_sources import bundled_reference_path
                if bundled_reference_path(bundled):
                    yield 'body',('File completo acquisito, nella cartella sources accanto al rapporto.'if it else'Celotno zajetje je v mapi sources ob tem poročilu.')
                    yield 'link',('Apri fonte allegata'if it else'Odpri priloženi vir',bundled)
                elif source.get('frozen_source_url'):
                    yield 'body',('Fonte congelata richiede accesso allo stesso account'if it else'Zamrznjeni vir zahteva prijavo z istim računom')
                    yield 'link',('Apri fonte congelata'if it else'Odpri zamrznjeni vir',source['frozen_source_url'])
                else:
                    yield 'body',('Identità e acquisizione completa della fonte sono conservate con il rapporto originale.'if it else'Identiteta in celotno zajetje vira sta shranjena ob izvirnem poročilu.')
                if source.get('url'):yield 'link',('Pubblicazione originale'if it else'Izvirna objava',str(source['url']))


    if report.get('ai_suggestions_enabled'):
        from .document_ai_suggestions import report_blocks as ai_blocks
        yield from ai_blocks(report.get('ai_suggestions'),report.get('locale','sl'),report.get('saved_result_url','').split('/documents/')[0]or'/api/pilot')


def _report_lines(report):
    return (text[0]+': '+text[1]if kind=='link'else text for kind,text in _report_blocks(report))


def report_text(report):
    return '\n'.join(_report_lines(report)).encode('utf-8')


def report_docx(report):
    """A new Word report, retaining exact original strings beside each result."""
    return _text_docx(_report_blocks(report),styled=True)


def _report_relationship_xml(relationships):
    """Use the interoperable OPC default namespace without global ET state."""
    namespace='http://schemas.openxmlformats.org/package/2006/relationships'
    plain=ET.Element('Relationships',{'xmlns':namespace})
    for relationship in relationships:
        ET.SubElement(plain,'Relationship',dict(relationship.attrib))
    return ET.tostring(plain,encoding='utf-8',xml_declaration=True)


def _text_docx(lines,styled=False):
    document = ET.Element(W + 'document')
    body = ET.SubElement(document, W + 'body')
    rel_namespace='http://schemas.openxmlformats.org/package/2006/relationships'
    reference_namespace='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    rels=ET.Element('{'+rel_namespace+'}Relationships')
    if styled:ET.SubElement(rels,'{'+rel_namespace+'}Relationship',{'Id':'styles','Type':reference_namespace+'/styles','Target':'styles.xml'})
    for line in lines:
        paragraph = ET.SubElement(body, W + 'p')
        if styled:
            kind,line=line
            properties=ET.SubElement(paragraph,W+'pPr')
            ET.SubElement(properties,W+'pStyle',{W+'val':{'title':'Title','heading':'Heading1','subheading':'Heading2','quote':'Quote'}.get(kind,'Normal')})
        parent=paragraph
        if styled and kind=='link':
            label,target=line
            # Report references are links; prose from an upload never enters
            # this branch. Unsupported schemes remain visible plain text.
            from .document_report_sources import bundled_reference_path
            if target.startswith(('https://','http://','/'))or bundled_reference_path(target):
                rid='link'+str(len(rels))
                ET.SubElement(rels,'{'+rel_namespace+'}Relationship',{'Id':rid,'Type':reference_namespace+'/hyperlink','Target':target,'TargetMode':'External'})
                parent=ET.SubElement(paragraph,W+'hyperlink',{'{'+reference_namespace+'}id':rid})
                line=label
            else:line=label+': '+target
        run = ET.SubElement(parent, W + 'r')
        if parent is not paragraph:
            properties=ET.SubElement(run,W+'rPr')
            ET.SubElement(properties,W+'color',{W+'val':'0563C1'});ET.SubElement(properties,W+'u',{W+'val':'single'})
        # Represent tabs/newlines as native Word tokens and preserve whitespace.
        for token in re.split(r'(\r\n|\r|\n|\t)', line):
            if token == '\t':
                ET.SubElement(run, W + 'tab')
            elif token in {'\r\n', '\n', '\r'}:
                ET.SubElement(run, W + 'br')
            elif token:
                ET.SubElement(run, W + 't', {XML_SPACE: 'preserve'}).text = token
    if styled:
        section=ET.SubElement(body,W+'sectPr')
        ET.SubElement(section,W+'pgSz',{W+'w':'11906',W+'h':'16838'})
        ET.SubElement(section,W+'pgMar',{W+'top':'1134',W+'bottom':'1134',W+'left':'1134',W+'right':'1134',W+'header':'567',W+'footer':'567'})
    content_types = (b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        +(b'<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'if styled else b'')+b'</Types>')
    relationships = (b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        b'</Relationships>')
    result = io.BytesIO()
    with ZipFile(result, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', relationships)
        archive.writestr('word/document.xml', ET.tostring(document, encoding='utf-8', xml_declaration=True))
        if styled:
            styles=ET.Element(W+'styles')
            for name,size,bold,spacing in [('Normal','22',False,'100'),('Title','32',True,'240'),('Heading1','26',True,'180'),('Heading2','22',True,'100'),('Quote','20',False,'100')]:
                style=ET.SubElement(styles,W+'style',{W+'type':'paragraph',W+'styleId':name})
                ET.SubElement(style,W+'name',{W+'val':name});p=ET.SubElement(style,W+'pPr')
                ET.SubElement(p,W+'spacing',{W+'after':spacing})
                if name in {'Title','Heading1','Heading2'}:ET.SubElement(p,W+'keepNext')
                r=ET.SubElement(style,W+'rPr');ET.SubElement(r,W+'rFonts',{W+'ascii':'Arial',W+'hAnsi':'Arial'})
                ET.SubElement(r,W+'sz',{W+'val':size});ET.SubElement(r,W+'color',{W+'val':'000000'})
                if bold:ET.SubElement(r,W+'b')
            archive.writestr('word/styles.xml',ET.tostring(styles,encoding='utf-8',xml_declaration=True))
            archive.writestr('word/_rels/document.xml.rels',_report_relationship_xml(rels))
    return result.getvalue()


def draft_section_text(section):
    """Keep source boundary whitespace when joining translated sections.

    The model's saved proposal stays immutable. The returned shift translates
    its inline target offsets into the layout-preserving exported section.
    """
    result=section.get('document_result')or{}
    proposed=result.get('proposed_text');source=section.get('text')
    if not isinstance(proposed,str):_invalid('document_draft_unavailable',409)
    if result.get('mode')!='translate':return proposed,0
    if not isinstance(source,str):_invalid('document_draft_unavailable',409)
    if not source.strip():
        if proposed.strip():_invalid('document_draft_unavailable',409)
        return source,0
    core=proposed.strip()
    if not core:_invalid('document_draft_unavailable',409)
    leading=re.match(r'\s*',source).group()
    trailing=re.search(r'\s*$',source).group()
    removed=len(proposed)-len(proposed.lstrip())
    return leading+core+trailing,len(leading)-removed


def draft_blocks(report):
    """Return a complete proposed text, only after every section has a draft."""
    if (report.get('partial_review_export')or 'document_page_footer_not_reviewed'in report.get('warnings',[])
            or report.get('state')!='complete' or not report.get('sections')):
        _invalid('document_draft_unavailable',409)
    if repeated_translation_sections(report['sections']):_invalid('document_draft_unavailable',409)
    blocks=[]
    for section in report['sections']:
        result=section.get('document_result')or{}
        if (section.get('state')!='complete'or result.get('status')!='draft'
                or failed_document_operation(section)or result.get('review')and _review_incomplete(section)
                or not isinstance(result.get('proposed_text'),str)):
            _invalid('document_draft_unavailable',409)
        proposed,_=draft_section_text(section)
        location=section['location'].rsplit('@',1)[0]
        if blocks and blocks[-1][0]==location:
            blocks[-1]=(location,blocks[-1][1]+proposed)
        else:blocks.append((location,proposed))
    return [text for _,text in blocks]


def draft_text(report):
    return '\n\n'.join(draft_blocks(report)).encode('utf-8')


def partial_review_document(report):
    """Assemble a separate partial export copy; never change saved results."""
    mode=report.get('mode',(report.get('options')or{}).get('mode'))
    sections=report.get('sections')or[]
    if (mode!='review'or report.get('state')not in {'complete','failed','cancelled'}or not sections
            or any(s.get('state')in {'queued','running','pending'}for s in sections)):
        _invalid('document_draft_unavailable',409)
    copies=[]
    for section in sections:
        result=section.get('document_result')or{}
        valid=(section.get('state')=='complete'and result.get('status')=='draft'
               and result.get('mode')=='review'and isinstance(result.get('proposed_text'),str))
        if valid:
            proposed={**result,'edits':[dict(edit)for edit in result.get('edits',[])]}
        else:
            source=_valid_text(section['text'])
            proposed={'mode':'review','status':'draft','proposed_text':source,'edits':[],
                      'source_sha256':hashlib.sha256(source.encode()).hexdigest()}
        copies.append({**section,'state':'complete','limitations':[],
                       'document_result':proposed,'partial_original_preserved':not valid})
    return {**report,'state':'complete','sections':copies}


def draft_docx(report):
    """New text-only Word draft; the original layout is never overwritten."""
    return _text_docx(draft_blocks(report))



PARTIAL_REVIEW_CONTRACT='klikai.safe-partial-review.v1'
_PARTIAL_COMPLETE_CHECKS={'completed','completed_no_issue','applied_existing_contract','outside_coverage'}


def _review_incomplete(section):
    result=section.get('document_result');result=result if isinstance(result,dict)else{}
    review=result.get('review');review=review if isinstance(review,dict)else{}
    return (section.get('state')!='complete'or result.get('status')!='draft'
        or failed_document_operation(section)
        or any(not isinstance(check,dict)or not isinstance(check.get('status'),str)or check['status']not in _PARTIAL_COMPLETE_CHECKS
               for check in review.get('checks',[]))
        or (review.get('resource_coverage')or{}).get('bounded_or_partial')is True)


def _partial_value_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _bound_punctuation_finding(edit,finding,section,proof,source):
    """Join the stored CORE application and parent observations, without new inference."""
    result=section['document_result'];review=result.get('review')or{};text=section['text']
    if (finding.get('applicability')!='existing_contract_applied'
            or finding.get('remaining_conditions',[])!=[]
            or proof.get('applicability')!='existing_source_bound_conditions_satisfied'
            or proof.get('source_capture_set_sha256')!=review.get('source_capture_set_sha256')
            or not isinstance(review.get('source_capture_set_sha256'),str)
            or re.fullmatch('[a-f0-9]{64}',review['source_capture_set_sha256'])is None):return False
    observations={}
    for name in ('application_analysis','comma_prediction'):
        checks=[c for c in review.get('checks',[])if isinstance(c,dict)and c.get('check_id')==name]
        if len(checks)!=1 or not isinstance(checks[0].get('observation'),dict):return False
        observations[name]=checks[0]['observation']
    syntax=observations['application_analysis'];checker=observations['comma_prediction']
    if (proof.get('syntax_capture_sha256')!=_partial_value_hash(syntax)
            or proof.get('checker_observation_sha256')!=_partial_value_hash(checker)
            or checker.get('input_sha256')!=result['source_sha256']or checker.get('input_text')!=text):return False
    unit=proof.get('application_unit');focus=source.get('focus')or{}
    if (not isinstance(unit,dict)or unit.get('unit_type')!='construction_application'
            or not isinstance(unit.get('payload'),dict)or not isinstance(unit.get('bindings'),list)
            or unit.get('signed_profile_sha256')!=proof.get('application_context_sha256')
            or not isinstance(focus.get('text'),str)
            or focus.get('text_sha256')!=hashlib.sha256(focus['text'].encode()).hexdigest()):return False
    record=unit['payload'].get('source_record')or{}
    if record.get('text')!=focus['text']or record.get('text_sha256')!=focus['text_sha256']:return False
    for app in(review.get('applications')or{}).get('applications',[]):
        if not isinstance(app,dict)or app.get('proof')!=proof:continue
        binding=app.get('primary_source_binding');operations=app.get('observed_edits')
        if (not isinstance(binding,dict)or binding not in unit['bindings']
                or binding.get('source_slug')=='linguistic-analysis'
                or binding.get('passage_id')!=source.get('evidence_id')
                or binding.get('evidence_id')!=source.get('evidence_id')
                or binding.get('source_slug')!=source.get('source_slug')
                or binding.get('text_sha256')!=focus['text_sha256']
                or not isinstance(operations,list)or not operations or len(operations)>64
                or finding.get('observed_checker_edits')!=operations
                or not isinstance(app.get('edit'),dict)):continue
        accepted=False;parts=[];offset=0;valid=True
        for operation in sorted(operations,key=lambda e:e.get('start',-1)if isinstance(e,dict)else-1):
            if not isinstance(operation,dict):valid=False;break
            a,b=operation.get('start'),operation.get('end');replacement=operation.get('replacement')
            if (type(a)is not int or type(b)is not int or not offset<=a<=b<=len(text)
                    or text[a:b]!=operation.get('original')or not isinstance(replacement,str)
                    or replacement==text[a:b]or replacement.replace(',','')!=text[a:b].replace(',','')
                    or operation not in checker.get('edits',[])):valid=False;break
            primitive={**app['edit'],**{key:operation[key]for key in ('start','end','original','replacement')}}
            accepted=accepted or primitive==edit
            parts.extend((text[offset:a],replacement));offset=b
        parts.append(text[offset:])
        if not valid or not accepted or hashlib.sha256(''.join(parts).encode()).hexdigest()!=proof.get('output_sha256'):continue
        projection=proof.get('native_sentence_projection');payload=unit['payload']
        # A missing receipt denotes only the actual original whole-parent unit.
        # A projected response cannot acquire legacy authority by dropping its receipt.
        start,end=(projection.get('start'),projection.get('end'))if isinstance(projection,dict)else(0,len(text))
        if (type(start)is not int or type(end)is not int or not 0<=start<=end<=len(text)
                or payload.get('construction')!='finite_subordinate_clause_boundary'
                or payload.get('analysis_basis')!='original' or payload.get('input_text')!=text[start:end]
                or not isinstance(payload.get('output_text'),str)
                or text[:start]+payload['output_text']+text[end:]!=''.join(parts)):continue
        sr=payload.get('syntax_record')or{}
        if not isinstance(sr.get('text'),str)or hashlib.sha256(sr['text'].encode()).hexdigest()!=sr.get('text_sha256'):continue
        try:response=json.loads(sr['text'])
        except(ValueError,TypeError):continue
        if projection is None:
            captures=syntax.get('captures')
            if (checker.get('status')!='ok' or not isinstance(captures,list)or not captures
                    or not isinstance(captures[0],dict)or response!=captures[0].get('response')):continue
        else:
            keys={'version','parent_syntax_sha256','parent_input_sha256','sentence_id','start','end','word_ids','projected_syntax_sha256'}
            if (not isinstance(projection,dict)or set(projection)!=keys
                    or projection['version']!='document-native-sentence-projection.v1'
                    or projection['parent_syntax_sha256']!=proof['syntax_capture_sha256']
                    or projection['parent_input_sha256']!=result['source_sha256']
                    or type(projection['sentence_id'])is not int
                    or type(projection['start'])is not int or type(projection['end'])is not int
                    or not 0<=projection['start']<=edit['start']<=edit['end']<=projection['end']<=len(text)
                    or not isinstance(projection['word_ids'],list)or not projection['word_ids']
                    or any(type(i)is not int for i in projection['word_ids'])
                    or len(set(projection['word_ids']))!=len(projection['word_ids'])):continue
            if (not isinstance(response,dict)
                    or response.get('document_sentence_projection')!={k:v for k,v in projection.items()if k!='projected_syntax_sha256'}
                    or response.get('execution')!={'projection_only':True,'inference_performed':False}
                    or response.get('input_text')!=text[projection['start']:projection['end']]):continue
            cap={'operation':'syntax','request':{'text':response['input_text']},'response':response,
                'response_sha256':_partial_value_hash(response),'duration_ms':0}
            child={'version':'klikai.syntax-collation.v1','plan':{'status':'ready',
                'routing_kind':'comma_explanation_syntax','focus':response['input_text']},
                'captures':[cap],'capture_set_sha256':_partial_value_hash([cap])}
            if _partial_value_hash(child)!=projection['projected_syntax_sha256']:continue
        return True
    return False


def _bound_partial_finding(edit,section,revision):
    """Reuse a stored executable application; never infer one from a citation."""
    if not isinstance(revision,str)or re.fullmatch('[a-f0-9]{64}',revision)is None:return False
    result=section.get('document_result')or{}
    known={row.get('evidence_id')for row in section.get('sources',[])if isinstance(row,dict)}
    ids=edit.get('evidence_ids')
    if (not isinstance(ids,list)or not ids or any(not isinstance(x,str)or not x for x in ids)
            or len(ids)!=len(set(ids))or not set(ids)<=known):return False
    for finding in(result.get('review')or{}).get('findings',[]):
        if (not isinstance(finding,dict)or finding.get('included_in_draft')is not True
                or finding.get('type')!='executable_application'
                or finding.get('document_revision')!=revision
                or finding.get('document_location')!=section.get('location')
                or finding.get('proposed_edit')!=edit):continue
        proof=finding.get('application_proof');source=finding.get('source')or{}
        captures=finding.get('related_source_captures')
        if not isinstance(proof,dict)or not isinstance(captures,list)or not isinstance(source,dict):continue
        bound={};conflict=False
        for row in captures:
            if (isinstance(row,dict)and isinstance(row.get('evidence_id'),str)
                    and isinstance(row.get('capture_sha256'),str)
                    and re.fullmatch('[a-f0-9]{64}',row['capture_sha256'])):
                if row['evidence_id']in bound and bound[row['evidence_id']]!=row['capture_sha256']:conflict=True
                bound[row['evidence_id']]=row['capture_sha256']
        if (conflict or not set(ids)<=set(bound)or source.get('evidence_id')not in ids
                or bound[source['evidence_id']]!=source.get('capture_sha256')
                or proof.get('input_sha256')!=result.get('source_sha256')
                or proof.get('writer_inference_performed')is not False):continue
        body={key:value for key,value in proof.items()if key!='proof_sha256'}
        try:
            if proof.get('proof_sha256')!=_partial_value_hash(body):continue
            if proof.get('adapter_version')=='document-existing-applications.v1':
                if _bound_punctuation_finding(edit,finding,section,proof,source):return True
                continue
        except(TypeError,ValueError,KeyError,AttributeError):continue
        focus=source.get('focus');focus=focus.get('text')if isinstance(focus,dict)else None
        if (finding.get('application_version')=='document-source-applications.v1'
                and proof.get('adapter_version')==finding['application_version']
                and finding.get('remaining_conditions')==[]
                and proof.get('edit_binding_sha256')==_partial_value_hash({
                    'version':'document-application-edit-binding.v1','input_sha256':result['source_sha256'],
                    'edit':{key:edit[key]for key in ('start','end','original','replacement')}})
                and proof.get('source_capture_sha256')==source.get('capture_sha256')
                and isinstance(focus,str)
                and proof.get('source_focus_sha256')==hashlib.sha256(focus.encode()).hexdigest()):return True
    return False


def _safe_partial_section(section,revision):
    source=_valid_text(section['text']);result=section.get('document_result')or{}
    reasons=[];edits=result.get('edits',[]);candidates=[]
    valid=(section.get('state')=='complete'and result.get('status')=='draft'
        and result.get('mode')=='review'and result.get('source_sha256')==hashlib.sha256(source.encode()).hexdigest())
    if not valid:reasons.append('saved_review_unbound')
    elif not isinstance(edits,list)or len(edits)>64:reasons.append('invalid_edit_list');valid=False
    if valid:
        for edit in edits:
            if not isinstance(edit,dict):reasons.append('invalid_edit');continue
            a,b,value=edit.get('start'),edit.get('end'),edit.get('replacement')
            if (type(a)is not int or type(b)is not int or not 0<=a<=b<=len(source)
                    or source[a:b]!=edit.get('original')or not isinstance(value,str)
                    or len(value)>MAX_SECTION*4 or value==source[a:b]):
                reasons.append('invalid_edit');continue
            try:_valid_text(value)
            except Failure:reasons.append('invalid_edit');continue
            if not _bound_partial_finding(edit,section,revision):reasons.append('unbound_edit_support');continue
            candidates.append(dict(edit))
    # A conflicting component is withheld in full; unrelated supported edits survive.
    conflicting=set()
    for i,left in enumerate(candidates):
        for j,right in enumerate(candidates[:i]):
            a,b=left['start'],left['end'];x,y=right['start'],right['end']
            if max(a,x)<min(b,y)or a==b and x<=a<=y or x==y and a<=x<=b:
                conflicting.update((i,j))
    if conflicting:reasons.append('overlapping_edits')
    accepted=sorted((e for i,e in enumerate(candidates)if i not in conflicting),key=lambda e:(e['start'],e['end']))
    pieces=[];offset=0
    for edit in accepted:pieces.extend([source[offset:edit['start']],edit['replacement']]);offset=edit['end']
    pieces.append(source[offset:]);proposed=''.join(pieces)
    withheld=(len(edits)if isinstance(edits,list)else 0)-len(accepted)
    status={'index':section.get('index'),'applied_edits':len(accepted),
        'applied_edit_indexes':[next(i for i,e in enumerate(edits)if e==edit)for edit in accepted],
        'withheld_edits':max(0,withheld),
        'original_preserved':proposed==source,'incomplete':_review_incomplete(section)or bool(reasons),
        'reasons':sorted(set(reasons)),'saved_state':section.get('state')if section.get('state')in {'complete','failed','cancelled','queued','running','pending','unknown'}else'unknown'}
    # This is a derived partial-export view. Retain failure/coverage history.
    copy=deepcopy(section);copy['state']='complete'
    copy['document_result']={**deepcopy(result),'mode':'review','status':'draft','proposed_text':proposed,
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'edits':accepted,'inline_mappings':[]}
    copy['partial_review_export']=status
    return copy,status


def safe_partial_review_document(report):
    """Derive only bound supported edit splices; original saved data stays exact."""
    mode=report.get('mode',(report.get('options')or{}).get('mode'));sections=report.get('sections')or[]
    if (mode!='review'or report.get('state')not in {'complete','failed','cancelled'}or not sections
            or len(sections)>MAX_BLOCKS or any(s.get('state')in {'queued','running','pending'}for s in sections)):
        _invalid('document_draft_unavailable',409)
    copies=[];statuses=[];size=0
    for section in sections:
        if (not isinstance(section,dict)or not isinstance(section.get('text'),str)
                or not isinstance(section.get('location'),str)):_invalid('document_draft_unavailable',409)
        copy,status=_safe_partial_section(section,report.get('source_sha256'))
        size+=len(copy['document_result']['proposed_text'])
        if size>MAX_TEXT*4:_invalid('document_draft_unavailable',409)
        copies.append(copy);statuses.append(status)
    summary={'contract':PARTIAL_REVIEW_CONTRACT,'available':True,'partial':True,
        'applied_edits':sum(s['applied_edits']for s in statuses),
        'withheld_edits':sum(s['withheld_edits']for s in statuses),
        'original_sections_preserved':sum(s['original_preserved']for s in statuses),
        'incomplete_sections':sum(s['incomplete']for s in statuses),
        'unreviewed_page_footer':'document_page_footer_not_reviewed'in report.get('warnings',[]),'sections':statuses}
    return {**report,'state':'complete','sections':copies,'partial_review_export':summary}


def partial_review_blocks(report):
    assembled=safe_partial_review_document(report);blocks=[]
    for section in assembled['sections']:
        base=section['location'].rsplit('@',1)[0];text=section['document_result']['proposed_text']
        if blocks and blocks[-1][0]==base:blocks[-1]=(base,blocks[-1][1]+text)
        else:blocks.append((base,text))
    return [text for _,text in blocks]


def partial_review_labels(locale='sl'):
    return (('Bozza parziale di revisione','Contiene solo modifiche supportate e precisamente localizzate. Le parti irrisolte o non controllate conservano il testo originale. Non è una revisione completa; controlla le modifiche e il rapporto con limiti e fonti.')if locale=='it'else
        ('Delni osnutek jezikovnega pregleda','Vsebuje samo podprte in natančno umeščene spremembe. Nerazrešeni ali nepreverjeni deli ohranijo izvirno besedilo. To ni popoln pregled; preverite spremembe in poročilo z omejitvami in viri.'))


def partial_review_text(report):
    title,note=partial_review_labels((report.get('options')or{}).get('locale',report.get('locale','sl')))
    return (title+'\n'+note+'\n\n'+'\n\n'.join(partial_review_blocks(report))).encode('utf-8')


def review_export_status(report):
    try:
        draft_blocks(report);complete=True
    except(Failure,KeyError,TypeError,ValueError):complete=False
    try:partial=safe_partial_review_document(report)['partial_review_export']
    except(Failure,KeyError,TypeError,ValueError):partial={'contract':PARTIAL_REVIEW_CONTRACT,'available':False,'partial':True,
        'applied_edits':0,'withheld_edits':0,'original_sections_preserved':0,'incomplete_sections':0,'sections':[]}
    return {'complete_available':complete,'partial_review':partial}
