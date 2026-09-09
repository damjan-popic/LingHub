from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tools.xml_safety import CheckedXMLReader, UnsafeXML, safe_iterparse


class XMLSafetyTests(unittest.TestCase):
    def consume(self, source, chunk_size):
        reader = CheckedXMLReader(io.BytesIO(source))
        parts = []
        while chunk := reader.read(chunk_size):
            parts.append(chunk)
        return b''.join(parts)

    def test_utf8_bom_multibyte_characters_and_declarations_across_chunks(self):
        for source in (b'<?xml version="1.0" encoding="UTF-8"?><entry>\xc4\x8das</entry>',
                       b'\xef\xbb\xbf<entry>\xc5\xbe</entry>', b'<entry/>'):
            for chunk_size in range(1, 20):
                with self.subTest(chunk_size=chunk_size, source=source):
                    self.assertEqual(self.consume(source, chunk_size), source)

    def test_all_dtd_and_entity_split_positions_are_rejected(self):
        declarations = [b'<!DOCTYPE entry [<!ENTITY payload "expanded">]><entry>&payload;</entry>',
                        b'<!DOCTYPE entry SYSTEM "https://example.invalid/external.dtd"><entry/>',
                        b'<!ENTITY payload SYSTEM "file:///etc/passwd">',
                        b'<!doctype entry><entry/>']
        for source in declarations:
            for chunk_size in range(1, len(source) + 1):
                with self.subTest(chunk_size=chunk_size), self.assertRaises(UnsafeXML):
                    self.consume(source, chunk_size)

    def test_wrong_encoding_invalid_utf8_nul_and_truncated_multibyte_are_rejected(self):
        sources = [b'<?xml version="1.0" encoding="ISO-8859-1"?><entry/>',
                   '<entry/>'.encode('utf-16'), b'<entry>\xff</entry>',
                   b'<entry>\xc4', b'<entry>\x00</entry>']
        for source in sources:
            for chunk_size in (1, 2, 7, 16384):
                with self.subTest(chunk_size=chunk_size), self.assertRaises(UnsafeXML):
                    self.consume(source, chunk_size)

    def test_safe_parser_retains_events_namespaces_and_source_text(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'source.xml'
            path.write_text('<dictionary xmlns="urn:synthetic"><entry id="x">čas</entry></dictionary>')
            events = list(safe_iterparse(path, events=('start', 'end')))
            self.assertEqual([(kind, element.tag) for kind, element in events], [
                ('start', '{urn:synthetic}dictionary'), ('start', '{urn:synthetic}entry'),
                ('end', '{urn:synthetic}entry'), ('end', '{urn:synthetic}dictionary')])
            self.assertEqual(events[2][1].text, 'čas')
            path.write_text('<dictionary><entry></dictionary>')
            with self.assertRaises(ET.ParseError):
                list(safe_iterparse(path))

    def test_zero_length_read_does_not_finalize_and_header_storage_is_bounded(self):
        reader = CheckedXMLReader(io.BytesIO(b'<entry/>'))
        self.assertEqual(reader.read(0), b'')
        self.assertFalse(reader.finished)
        self.assertEqual(reader.read(), b'<entry/>')
        self.assertEqual(reader.read(), b'')
        with self.assertRaises(UnsafeXML):
            self.consume(b'<?xml ' + b' ' * 5000, 500)


if __name__ == '__main__':
    unittest.main()
