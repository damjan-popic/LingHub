"""Bounded UTF-8 XML reader for public dictionary sources.

The compiler accepts UTF-8 only and rejects every DTD/entity declaration before
the XML parser receives a complete declaration. This is not an XSD validator.
"""
from __future__ import annotations

import codecs
import re
from typing import BinaryIO, Iterator
import xml.etree.ElementTree as ET


class UnsafeXML(ValueError):
    """The source is outside the compiler's explicitly supported XML profile."""


class CheckedXMLReader:
    """File wrapper checking each chunk and declaration fragments between reads."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.decoder = codecs.getincrementaldecoder('utf-8-sig')(errors='strict')
        self.tail = b''
        self.header = b''
        self.header_done = False
        self.finished = False

    def _check_header(self, chunk: bytes, final: bool) -> None:
        if self.header_done:
            return
        self.header += chunk
        prefix = self.header
        if prefix.startswith(codecs.BOM_UTF8):
            prefix = prefix[len(codecs.BOM_UTF8):]
        if len(prefix) < 6 and not final:
            return
        if not prefix.startswith(b'<?xml') or prefix[5:6] not in b' \t\r\n':
            self.header_done = True
            self.header = b''
            return
        end = prefix.find(b'?>')
        if end < 0:
            if len(prefix) > 4096:
                raise UnsafeXML('XML declaration exceeds the supported header bound')
            return
        declaration = prefix[:end]
        match = re.search(br'\bencoding\s*=\s*([\'"])([^\'"]+)\1', declaration)
        if match and match.group(2).lower() not in (b'utf-8', b'utf8'):
            raise UnsafeXML('XML must declare UTF-8 encoding')
        self.header_done = True
        self.header = b''

    def read(self, size: int = -1) -> bytes:
        if size == 0 or self.finished:
            return b''
        chunk = self.stream.read(size)
        final = not chunk
        if b'\x00' in chunk:
            raise UnsafeXML('NUL bytes and UTF-16/UTF-32 XML are not accepted')
        combined = (self.tail + chunk).upper()
        if b'<!DOCTYPE' in combined or b'<!ENTITY' in combined:
            raise UnsafeXML('DTD and entity declarations are not accepted')
        self.tail = combined[-8:]
        try:
            self.decoder.decode(chunk, final=final)
        except UnicodeDecodeError:
            raise UnsafeXML('XML source is not valid UTF-8') from None
        self._check_header(chunk, final)
        self.finished = final
        return chunk


def safe_iterparse(path, events=('end',)) -> Iterator[tuple[str, ET.Element]]:
    """Yield ElementTree events without loading the entire source into memory.

    Callers must clear/remove processed elements to bound their own tree memory.
    Parse errors and unsafe source declarations propagate; nothing is skipped.
    """
    with open(path, 'rb') as stream:
        yield from ET.iterparse(CheckedXMLReader(stream), events=events)
