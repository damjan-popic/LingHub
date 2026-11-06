"""Optional syntax on the existing CLASSLA worker, sharing its bounded runner.

The host wrapper explicitly installs this extension before the ordinary worker
lifespan. The legacy /analyze pipeline, metadata and admission stay unchanged.
"""
import asyncio
import importlib.metadata
import os
from pathlib import Path
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .syntax_worker import (MODEL_PACKAGE, MODEL_VERSION, MODEL_HASHES,
                            MAX_CHARACTERS, RESOURCES_SHA256,
                            analysis_result, model_metadata)


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _dependency_pipeline(worker, original_torch_load, torch):
    """Load only the missing processor; never duplicate POS or lemma models."""
    if importlib.metadata.version(MODEL_PACKAGE) != MODEL_VERSION:
        raise RuntimeError('syntax_model_version')
    base_pins = {key: value for key, value in MODEL_HASHES.items() if '/depparse/' not in key}
    if worker.MODEL_HASHES != base_pins or worker.RESOURCES_SHA != RESOURCES_SHA256:
        raise RuntimeError('syntax_base_identity')
    root = Path(worker.MODEL_ROOT).resolve(strict=True)
    path = (root / 'sl/depparse/standard.pt').resolve(strict=True)
    if not path.is_relative_to(root) or worker.digest(path) != MODEL_HASHES['sl/depparse/standard.pt']:
        raise RuntimeError('syntax_dependency_identity')
    identity = _signature(path)
    base_checked_load = torch.load

    def checked_load(filename, *args, **kwargs):
        if isinstance(filename, (str, bytes, os.PathLike)):
            selected = Path(os.fsdecode(filename)).resolve(strict=True)
            if selected == path:
                if _signature(selected) != identity:
                    raise RuntimeError('syntax_model_changed')
                return original_torch_load(filename, *args, **kwargs)
        return base_checked_load(filename, *args, **kwargs)

    torch.load = checked_load
    import classla
    # The existing loader has already disabled all model/network downloads.
    dependency = classla.Pipeline(lang='sl', dir=str(root), type='standard',
        processors='depparse', depparse_pretagged=True, use_gpu=False, logging_level='ERROR')
    processor = dependency.processors['depparse']
    if any(parameter.device.type != 'cpu' for parameter in processor._trainer.model.parameters()):
        raise RuntimeError('syntax_device')
    return dependency, model_metadata(torch.__version__, ['tokenize', 'pos', 'lemma', 'depparse'])


class SyntaxRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=MAX_CHARACTERS)


def install(worker):
    """Extend the existing app/port; install only for the selected CPU profile."""
    if getattr(worker, '_syntax_extension_installed', False):
        return
    cache = {'pipeline': None, 'model': None}
    original_load_pipeline = worker.load_pipeline
    original_runner = worker.BoundedRunner

    def load_pipeline(device):
        import torch
        original_torch_load = torch.load
        pipeline, model, diagnostics = original_load_pipeline(device)
        cache.update(pipeline=None, model=None)
        try:
            if device != 'cpu':
                raise RuntimeError('syntax_requires_cpu')
            cache['pipeline'], cache['model'] = _dependency_pipeline(worker, original_torch_load, torch)
        except Exception:
            # Optional syntax never prevents the ordinary analysis service from starting.
            pass
        return pipeline, model, diagnostics

    class SyntaxRunner(original_runner):
        def compute_syntax(self, text):
            started = time.perf_counter()
            doc = cache['pipeline'](self.pipeline(text))
            return {'status': 'ok', 'analysis': analysis_result(doc, text, cache['model']),
                    'inference_seconds': round(time.perf_counter() - started, 6)}

        async def syntax(self, text):
            if cache['pipeline'] is None:
                raise HTTPException(503, detail={'code': 'syntax_unavailable'})
            if not self.slot.acquire(blocking=False):
                raise HTTPException(503, detail={'code': 'backend_busy'})
            future = asyncio.get_running_loop().run_in_executor(self.executor, self.compute_syntax, text)

            def completed(done):
                self.slot.release()
                if not done.cancelled():
                    done.exception()

            future.add_done_callback(completed)
            try:
                return await asyncio.wait_for(asyncio.shield(future), self.timeout)
            except TimeoutError:
                raise HTTPException(504, detail={'code': 'backend_timeout'}) from None
            except (ValueError, KeyError, TypeError):
                raise HTTPException(502, detail={'code': 'annotation_alignment_invalid'}) from None
            except Exception:
                raise HTTPException(503, detail={'code': 'syntax_unavailable'}) from None

    worker.load_pipeline = load_pipeline
    worker.BoundedRunner = SyntaxRunner

    @worker.app.get('/syntax/health')
    def syntax_health():
        return {'status': 'ok' if cache['pipeline'] is not None else 'unavailable',
                'ready': cache['pipeline'] is not None, 'model': cache['model'],
                'limits': {'max_text_chars': MAX_CHARACTERS}}

    @worker.app.post('/syntax')
    async def syntax(request: SyntaxRequest):
        if not request.text.strip() or '\0' in request.text:
            raise HTTPException(400, detail={'code': 'invalid_selected_text'})
        return await worker.app.state.runner.syntax(request.text)

    worker._syntax_extension_installed = True
