"""Stream official XML through the unmodified XSD with local-only resolution.

Run with an existing interpreter providing lxml; never installs dependencies.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse, unquote

from lxml import etree
from tools.xml_safety import CheckedXMLReader


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class LocalSchemas(etree.Resolver):
    def __init__(self, root, w3c):
        self.root, self.w3c = root.resolve(), w3c.resolve()

    def resolve(self, url, public_id, context):
        if url in ('http://www.w3.org/2001/xml.xsd', 'https://www.w3.org/2001/xml.xsd'):
            return self.resolve_filename(str(self.w3c), context)
        parsed = urlparse(url)
        if parsed.scheme not in ('', 'file'):
            raise ValueError('External schema resolution rejected')
        path = Path(unquote(parsed.path))
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        if path != self.w3c and (not path.is_relative_to(self.root) or path.suffix != '.xsd'):
            raise ValueError('Schema resolution outside verified source directory')
        return self.resolve_filename(str(path), context)


def validate(source, w3c, output):
    source, w3c = source.resolve(), w3c.resolve()
    schema_path = source / 'monolingual_dictionaries.xsd'
    parser = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
    parser.resolvers.add(LocalSchemas(source, w3c))
    schema = etree.XMLSchema(etree.parse(str(schema_path), parser))
    files = sorted(source.rglob('*.xml'))
    if not files:
        raise ValueError('No source XML files')
    results = []
    for path in files:
        started = time.monotonic()
        entries = 0
        try:
            with path.open('rb') as stream:
                for _, element in etree.iterparse(CheckedXMLReader(stream), events=('end',), schema=schema,
                                                  no_network=True, load_dtd=False, resolve_entities=False):
                    if element.tag == 'entry':
                        entries += 1
                    parent = element.getparent()
                    element.clear()
                    if parent is not None:
                        while element.getprevious() is not None:
                            del parent[0]
            result = {'file': str(path.relative_to(source)), 'status': 'PASS', 'entries': entries}
        except etree.XMLSyntaxError as error:
            result = {'file': str(path.relative_to(source)), 'status': 'FAIL', 'entries_seen': entries,
                      'errors': [{'line': item.line, 'type': item.type_name, 'message': item.message}
                                 for item in list(error.error_log)[-8:]]}
        result['seconds'] = round(time.monotonic()-started, 3)
        results.append(result)
        print(json.dumps(result), flush=True)
    report = {'schema': 'lingstack.cjvt-xsd-validation.v1', 'status': 'PASS' if all(r['status']=='PASS' for r in results) else 'FAIL',
              'checked_at': datetime.now(timezone.utc).isoformat(), 'source': str(source),
              'lxml_version': etree.LXML_VERSION, 'libxml_version': etree.LIBXML_VERSION,
              'validator_sha256': digest(Path(__file__)),
              'network_resolution': False, 'dtd_and_entity_declarations': 'REJECTED',
              'schemas': {str(path): digest(path) for path in [*sorted(source.glob('*.xsd')), w3c]},
              'files': results, 'xml_files': len(results)}
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--w3c-schema', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        raise SystemExit('Refusing to overwrite schema evidence')
    report = validate(args.source, args.w3c_schema, args.report)
    raise SystemExit(0 if report['status']=='PASS' else 1)
