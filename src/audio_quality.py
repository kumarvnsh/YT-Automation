"""Conservative dry-narration diagnostics. Never edits or trims audio."""
from __future__ import annotations
import math
import re
import subprocess
from pathlib import Path
from .tts import _ffprobe_duration


def parse_silences(stderr: str, duration: float) -> list[dict]:
    intervals, start = [], None
    for line in stderr.splitlines():
        if 'silence_start:' in line:
            match = re.search(r'silence_start:\s*([\d.eE+-]+)', line)
            if not match: raise ValueError('Malformed FFmpeg silence start')
            if start is not None: raise ValueError('Duplicate FFmpeg silence start')
            start = float(match.group(1))
        if 'silence_end:' in line:
            match = re.search(r'silence_end:\s*([\d.eE+-]+)', line)
            if not match or start is None: raise ValueError('Malformed FFmpeg silence end')
            end = min(duration,float(match.group(1)))
            if not math.isfinite(start) or not math.isfinite(end) or end < start: raise ValueError('Invalid silence interval')
            intervals.append({'start':max(0.,start),'end':end}); start=None
    if start is not None:
        if not math.isfinite(start) or start > duration: raise ValueError('Invalid open silence interval')
        intervals.append({'start':max(0.,start),'end':duration})
    return intervals


def evaluate_audio(intervals, duration, storyboard, tolerance=.15, max_edge=.8):
    if not all(math.isfinite(v) for v in (duration, tolerance, max_edge)) or duration <= 0 or tolerance < 0 or max_edge < 0:
        raise ValueError('Invalid audio diagnostic limits')
    allowed=storyboard.get('allowed_pauses',[])
    for pause in allowed:
        if not (0 <= pause['start'] < pause['end'] <= duration): raise ValueError('Intentional pauses require bounded start/end')
    warnings,errors,annotated=[],[],[]
    for interval in intervals:
        intentional=any(p['start'] <= interval['start']+tolerance and p['end'] >= interval['end']-tolerance for p in allowed)
        edge='leading' if interval['start']<=tolerance else 'trailing' if interval['end']>=duration-tolerance else 'internal'
        annotated.append({**interval,'intentional':intentional,'kind':edge})
        if not intentional and (edge=='internal' or interval['end']-interval['start']>max_edge):
            warnings.append(f"Unexpected {edge} silence: {interval['start']:.3f}–{interval['end']:.3f}s")
    last=storyboard.get('last_word_end',0)
    if duration+tolerance < last: errors.append('Audio ends before the last aligned spoken word')
    if any(i['start']<=tolerance and i['end']>=duration-tolerance for i in intervals): errors.append('Narration is entirely silent')
    return {'schema_version':1,'narration_hash':storyboard.get('narration_hash'),'audio_duration':duration,'last_word_end':last,'timing_coverage':storyboard.get('coverage'), 'allowed_pauses':allowed,'intervals':annotated,'edge_padding':{'leading':next((i['end'] for i in intervals if i['start']<=tolerance),0),'trailing':next((duration-i['start'] for i in intervals if i['end']>=duration-tolerance),0)},'warnings':warnings,'errors':errors,'status':'fail' if errors else 'warn' if warnings else 'pass'}


def analyze_audio(cfg, audio_path, storyboard):
    path=Path(audio_path)
    if not path.is_file() or path.stat().st_size==0: raise ValueError(f'Missing or empty narration audio: {path}')
    duration=_ffprobe_duration(path)
    if not math.isfinite(duration) or duration<=0: raise ValueError('Narration audio has no usable duration')
    threshold=float(cfg.get('audio_quality.silence_db',-40))
    minimum=float(cfg.get('audio_quality.silence_duration',.8))
    if not math.isfinite(threshold) or not math.isfinite(minimum) or minimum<=0: raise ValueError('Invalid silence detection settings')
    result=subprocess.run(['ffmpeg','-hide_banner','-nostdin','-i',str(path),'-vn','-af',f'silencedetect=noise={threshold}dB:d={min(minimum, duration/2)}','-f','null','-'],capture_output=True,text=True,check=True)
    intervals = parse_silences(result.stderr, duration)
    # Retain complete silence even for clips shorter than the detection limit.
    intervals = [i for i in intervals if i['end']-i['start'] >= minimum or (i['start'] <= .01 and i['end'] >= duration-.01)]
    report=evaluate_audio(intervals,duration,storyboard,float(cfg.get('audio_quality.endpoint_tolerance',.15)),float(cfg.get('audio_quality.max_edge_silence',.8)))
    report['settings']={'silence_db':threshold,'silence_duration':minimum}
    return report


def validate_endpoints(audio_duration, video_duration, last_word_end, tolerance=.15):
    """Check final output duration against the measured final spoken endpoint."""
    return [f'{name} ends before final narration' for name,value in [('audio',audio_duration),('video',video_duration)] if value is None or not math.isfinite(value) or not math.isfinite(last_word_end) or value+tolerance < last_word_end]
