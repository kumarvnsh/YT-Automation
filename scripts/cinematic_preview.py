"""Re-render an existing artifact with cinematic visuals; never call upload."""
import json
import shutil
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
    st.update(visual_mode='cinematic_2d', no_upload=True, complete=False)
    st['done'] = [step for step in st.get('done', [])
                  if step not in ('captions', 'render', 'compose', 'quality', 'thumbnail', 'upload')]
    for key in ('video', 'render_quality', 'quality', 'meta', 'youtube_id', 'youtube_url', 'render_fallback'):
        st.pop(key, None)
    shutil.rmtree(stage / 'scenes', ignore_errors=True)
    # Failed previews must not present the source run's old video as new output.
    for name in ('video.mp4', 'silent.mp4', 'captioned.mp4', 'render_quality.json', 'quality.json', 'composition.json'):
        (stage / name).unlink(missing_ok=True)
    # Preserve the original script/audio evidence, with current visual settings.
    if st.get('production_config'):
        st['production_config']['video'] = cfg.raw['video']
        cfg = pipeline.stage_config(cfg, st)
    try:
        for step in ('captions', 'render', 'compose', 'quality'):
            print(f'CINEMATIC PREVIEW: {step}', flush=True)
            getattr(pipeline, '_step_' + step)(cfg, stage, st)
            st['done'].append(step)
    finally:
        path.write_text(json.dumps(st, indent=2))
    print('CINEMATIC PREVIEW: complete; no upload called', flush=True)


if __name__ == '__main__':
    main(sys.argv[1])
