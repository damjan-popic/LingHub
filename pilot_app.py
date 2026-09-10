"""Factory entry point: uvicorn pilot_app:app_factory --factory --no-access-log."""
import json,os
from pathlib import Path
from pilot_backend.api import create_app

def app_factory():
    path=Path(os.environ['KLIKAI_PILOT_CONFIG'])
    config=json.loads(path.read_text())
    for key in ('gateway_token','edge_token'):
        if config.get(key+'_file'):
            p=Path(config.pop(key+'_file'))
            if p.is_symlink()or p.stat().st_mode&0o077:raise ValueError('Protected pilot token file required')
            config[key]=p.read_text().strip()
    return create_app(config)
