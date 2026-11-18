"""Bounded local evidence for the repair agent, without credentials or media URLs."""
import json
import re
from pathlib import Path


def snapshot(root):
    root=Path(root)
    try:
        state=json.loads((root/'work/scheduler/state.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return 'Local scheduler evidence unavailable; do not infer a login failure.'
    result={k:state.get(k) for k in ('phase','note','failed_phase','failed_at','last_cycle_at','last_upload_at','manual_job_id')}
    result['recent_events']=state.get('events',[])[-6:]
    result['recent_publications']=[{k:u.get(k) for k in ('status','finished_at','video_id')}
                                   for u in state.get('uploads',[])[-3:]]
    identity=str(state.get('manual_job_id',''))
    if re.fullmatch(r'[a-f0-9]{32}',identity):
        path=root/'work/manual'/identity/'source-inspection.log'
        try:
            # Suppress signed media URLs, headers and authentication values.
            tail=path.read_text(encoding='utf-8',errors='replace')[-4000:]
            tail=re.sub(r'https?://\S+','[URL omitted]',tail)
            tail=re.sub(r'(?im)^.*(?:cookie|authorization|token|secret|password).*$','[authentication detail omitted]',tail)
            result['source_inspection_log']=tail
        except OSError:
            result['source_inspection_log']='Not recorded by the old worker. Exact cause is unknown.'
    return json.dumps(result,ensure_ascii=True)
