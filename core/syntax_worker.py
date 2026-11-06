"""One offline CPU dependency parse with the existing fixed CLASSLA models."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

MODEL_PACKAGE = 'classla'
MODEL_VERSION = '2.2.3'
MAX_CHARACTERS = 3000
OFFSET_CONVENTION = 'unicode_codepoint_half_open'
RESOURCES_SHA256 = '03eb6811384b1a90f786cc5e77fbdd2a01cf060c97dae58c10073511b6ec47e7'
MODEL_HASHES = {
    'sl/pos/standard.pt': '9865fbf5673de7ddeabf2f42cd75b3f522cc2ffe6f905d0a694939e3a68a94dd',
    'sl/lemma/standard.pt': '9a397d4ccaa9e8fbd7ea1619d859b9646dfb2f9c17e3f4df53c45525b5f5b38d',
    'sl/pretrain/standard.pt': 'f187803fc86a953194b0ed2f9230e2f80828775349a7a6dcd551c19bfe9067aa',
    'sl/depparse/standard.pt': 'fae51ebaf8e2ef99c2679b91fbd83b0af3d2e241c223b31b9644e4885d2d1c8a',
}


def records(doc, text):
    """Use the existing exact monotonic alignment; never search for a substring."""
    words = []; cursor = 0
    for sid, sentence in enumerate(doc.sentences, 1):
        identifiers = {word.id: len(words) + index for index, word in enumerate(sentence.words, 1)}
        for token in sentence.tokens:
            start, end = token.start_char, token.end_char
            native = (type(start) is int and type(end) is int and cursor <= start < end <= len(text)
                      and text[start:end] == token.text and (start == cursor or text[cursor:start].isspace()))
            if not native:
                start = cursor
                while start < len(text) and text[start].isspace():
                    start += 1
                end = start + len(token.text)
            if (not token.text or text[start:end] != token.text or len(token.words) != 1
                    or token.words[0].text != token.text):
                raise ValueError('word_alignment_unavailable')
            cursor = end; word = token.words[0]
            feats = dict(item.split('=', 1) for item in (word.feats or '').split('|') if '=' in item)
            words.append({'id': identifiers[word.id], 'text': word.text, 'start': start, 'end': end,
                          'lemma': word.lemma or '', 'upos': word.upos or '', 'feats': feats,
                          'head_id': 0 if word.head == 0 else identifiers[word.head],
                          'deprel': word.deprel or '', 'sentence_id': sid,
                          'offset_mapping': 'native' if native else 'monotonic_exact_after_whitespace'})
    if text[cursor:] and not text[cursor:].isspace():
        raise ValueError('unmapped_text')
    return words


def analysis_result(doc, text, model):
    words = records(doc, text)
    return {'schema': 'linghub.syntax-analysis.v1', 'text': text,
            'offset_convention': OFFSET_CONVENTION, 'head_convention': 'one_based_0_root',
            'tokens': words, 'words': words, 'model': model}


def model_metadata(torch_version, processors):
    return {'package': MODEL_PACKAGE, 'package_version': MODEL_VERSION,
            'torch_version': torch_version, 'pipeline': list(processors),
            'resource_sha256': dict(MODEL_HASHES), 'resources_manifest_sha256': RESOURCES_SHA256}


def analyze(text, resources):
    if importlib.metadata.version(MODEL_PACKAGE) != MODEL_VERSION:
        raise RuntimeError('model_version_mismatch')
    root = Path(resources).resolve(strict=True); allowed = {}
    for name, digest in {**MODEL_HASHES, 'resources.json': RESOURCES_SHA256}.items():
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root):
            raise RuntimeError('model_path')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise RuntimeError('model_identity')
        stat = path.stat()
        allowed[path] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    import torch
    torch.set_num_threads(2); torch.set_num_interop_threads(2)
    torch.cuda.is_available = lambda: False
    original_load = torch.load

    def checked_load(filename, *args, **kwargs):
        path = Path(os.fsdecode(filename)).resolve(strict=True); stat = path.stat()
        if path not in allowed or (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != allowed[path]:
            raise RuntimeError('unverified_model_path')
        return original_load(filename, *args, **kwargs)

    torch.load = checked_load
    import classla
    import classla.resources.common
    import requests.sessions

    def offline(*args, **kwargs):
        raise RuntimeError('network_download_forbidden')

    requests.sessions.Session.request = offline
    classla.download = offline
    classla.resources.common.download = offline
    classla.resources.common.request_file = offline
    pipeline = classla.Pipeline(lang='sl', dir=str(root), type='standard',
        processors='tokenize,pos,lemma,depparse', use_gpu=False, logging_level='ERROR')
    return analysis_result(pipeline(text), text, model_metadata(torch.__version__, pipeline.processors))


def main():
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--resources', required=True)
        args = parser.parse_args()
        raw = sys.stdin.buffer.read(MAX_CHARACTERS * 12 + 1025)
        if len(raw) > MAX_CHARACTERS * 12 + 1024:
            raise ValueError('input_bound')
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {'text'}:
            raise ValueError('invalid_input')
        text = request['text']
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARACTERS or '\0' in text:
            raise ValueError('invalid_input')
        text.encode('utf-8')
        print(json.dumps(analyze(text, args.resources), ensure_ascii=False, separators=(',', ':')))
        return 0
    except Exception as error:
        # No input, exception message, filesystem path or traceback leaves the helper.
        print(json.dumps({'error_type': type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
