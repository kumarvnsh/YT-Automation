"""Measured narration alignment and a shared, frame-quantized storyboard."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from difflib import SequenceMatcher

_SMALL = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']

def _number(n):
    if n < 20: return [_SMALL[n]]
    if n < 100: return [_TENS[n//10]] + (_number(n%10) if n%10 else [])
    if n < 1000: return _number(n//100) + ['hundred'] + (_number(n%100) if n%100 else [])
    if n < 1000000: return _number(n//1000) + ['thousand'] + (_number(n%1000) if n%1000 else [])
    return [str(n)]

def normalized_tokens(text):
    text = unicodedata.normalize('NFKC', text).lower().replace('’', "'")
    text = re.sub(r"\bwon't\b", 'will not', text)
    text = re.sub(r"\bcan't\b", 'can not', text)
    text = re.sub(r"n't\b", ' not', text)
    for suffix, expansion in [("'re",' are'),("'ve",' have'),("'ll",' will'),("'m",' am')]:
        text = text.replace(suffix, expansion)
    text = re.sub(r'(?<=\d),(?=\d)', '', text)
    result = []
    for token in re.findall(r"[a-z]+(?:'[a-z]+)?|\d+", text):
        result.extend(_number(int(token)) if token.isdigit() else [token.replace("'", '')])
    return result

def narration_hash(segments):
    return hashlib.sha256(' '.join(s['narration'].strip() for s in segments).strip().encode()).hexdigest()

def build_storyboard(segments, words, duration, fps, timing_source, strict=True):
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(fps) or fps <= 0 or not segments or not words:
        raise ValueError('Alignment requires narration, measured words, positive duration and fps')
    if strict and timing_source not in ('native', 'forced_aligned'):
        raise ValueError('Strict timing requires native or forced_aligned timestamps; configure measured alignment')
    previous = 0.0
    spoken, owners = [], []
    for i, word in enumerate(words):
        if not all(math.isfinite(v) for v in (word.start, word.end)) or word.start < previous - 1e-6 or word.end <= word.start or word.end > duration + .15:
            raise ValueError(f'Invalid or overlapping word timestamp at {i}')
        previous = word.end
        tokens = normalized_tokens(word.word)
        spoken.extend(tokens); owners.extend([i]*len(tokens))
    expected, segment_tokens = [], []
    for segment in segments:
        tokens = normalized_tokens(segment['narration'])
        if not tokens: raise ValueError('Empty narration segment')
        segment_tokens.append((len(expected),len(expected)+len(tokens)))
        expected.extend(tokens)
    mapping = {}
    for match in SequenceMatcher(None, expected, spoken, autojunk=False).get_matching_blocks():
        for offset in range(match.size): mapping[match.a+offset] = owners[match.b+offset]
    coverage = len(mapping)/len(expected)
    matched_spoken = {index for match in SequenceMatcher(None,expected,spoken,autojunk=False).get_matching_blocks() for index in range(match.b,match.b+match.size)}
    if strict and (coverage < 1 or len(matched_spoken) != len(spoken)):
        raise ValueError(f'Narration alignment incomplete ({coverage:.1%}); regenerate or force-align narration')
    # A provider chunk must not straddle a segment boundary: no measured split
    # exists inside a chunk, and inventing one would reintroduce timing drift.
    if strict:
        for lo, _ in segment_tokens[1:]:
            if lo in mapping and lo - 1 in mapping and mapping[lo] == mapping[lo - 1]:
                raise ValueError('A timing chunk crosses a scene boundary; obtain word-level alignment')
    boundaries = [0]
    for lo, hi in segment_tokens[1:]:
        if lo not in mapping: raise ValueError('Segment start has no measured word match')
        boundaries.append(round(words[mapping[lo]].start*fps))
    boundaries.append(round(duration*fps))
    if any(b<=a for a,b in zip(boundaries,boundaries[1:])):
        raise ValueError('Narration segments cannot form positive contiguous frame windows')
    scenes = []
    for i, (segment,(lo,hi)) in enumerate(zip(segments,segment_tokens)):
        start,end = boundaries[i]/fps,boundaries[i+1]/fps
        indices = sorted({mapping[j] for j in range(lo,hi) if j in mapping})
        local_words = [{'word':words[j].word,'start':max(0,words[j].start-start),'end':min(end-start,words[j].end-start)} for j in indices]
        cues=[]; last=-3
        for word in local_words:
            if word['start']-last>=2:
                cues.append({'time':word['start'],'word':word['word']}); last=word['start']
        scenes.append({'scene_id':i+1,'segment_id':i,'start':start,'end':end,'start_frame':boundaries[i],'end_frame':boundaries[i+1], 'narration':segment['narration'],'purpose':segment.get('beat','body'),'words':local_words,'cues':cues})
    return {'schema_version':1,'narration_hash':narration_hash(segments),'audio_duration':duration,'fps':fps,'timing_source':timing_source,'coverage':coverage,'unmatched_tokens':[expected[j] for j in range(len(expected)) if j not in mapping],'last_word_end':words[-1].end,'scenes':scenes,'allowed_pauses':[]}
