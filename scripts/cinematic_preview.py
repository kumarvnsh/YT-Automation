"""Re-render an existing artifact with cinematic visuals; never call upload."""
import json
import sys
from pathlib import Path

from src.config import load_config
from src import pipeline


def main(root):
    states = list(Path(root).rglob('state.json'))
    if len(states) != 1:
        raise RuntimeError(f'Expected one stage, found {len(states)}')
    path = states[0]
    stage = path.parent
    st = json.loads(path.read_text())
    cfg = load_config()
    st.update(visual_mode='cinematic_2d', no_upload=True)
    # Preserve the original script/audio evidence, with current visual settings.
    if st.get('production_config'):
        st['production_config']['video'] = cfg.raw['video']
        cfg = pipeline.stage_config(cfg, st)
    try:
        for step in ('captions', 'render', 'compose', 'quality'):
            print(f'CINEMATIC PREVIEW: {step}', flush=True)
            getattr(pipeline, '_step_' + step)(cfg, stage, st)
    finally:
        path.write_text(json.dumps(st, indent=2))
    print('CINEMATIC PREVIEW: complete; no upload called', flush=True)


if __name__ == '__main__':
    main(sys.argv[1])
