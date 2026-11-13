"""A camera-led 3D re-edit, using the earlier measured narration as a control."""
from __future__ import annotations
import argparse,json,math,os,shutil,sys
from pathlib import Path
from types import SimpleNamespace
import roman_video_preview as base
from roman_storyboard_preview import build as still_assets
from src.alignment import normalized_tokens,build_storyboard

SHOTS=[
 dict(start=0,cue='HEATED FLOORS.',note='',from_=[.4,2.25,3.7],to=[.1,1.95,2.7],look=[0,1.47,0]),
 dict(start=1.8,cue='NO ELECTRICITY.',note='Look beneath the surface.',from_=[7,5.4,10],to=[5.2,4.4,8],look=[-.4,1.4,0]),
 dict(start=4.633333,cue='THE FURNACE.',note='',from_=[-4.9,1.2,3.6],to=[-4.6,1.0,2.7],look=[-4.55,.65,1.0]),
 dict(start=6.28,cue='OUTSIDE THE ROOM.',note='',from_=[-9,5.3,12],to=[-8,5,11],look=[-1.6,1,.5]),
 dict(start=7.84,cue='FOLLOW THE HEAT.',note='The space beneath the floor.',from_=[-3.8,.65,0],to=[-1.5,.63,0],look=[-1.8,.60,0],lookTo=[.8,.60,0],fov=65),
 dict(start=12.066667,cue='STACKS OF TILES.',note='',from_=[.0,.52,1.9],to=[-.3,.72,1.5],look=[.8,.65,.5],fov=58),
 dict(start=14.34,cue='SPACE TO FLOW.',note='',from_=[-1.6,.55,.1],to=[1.1,.6,.1],look=[.6,.6,0],lookTo=[2.7,.6,-.2],fov=65),
 dict(start=17.19,cue='THE FLOOR WARMS.',note='',from_=[1.8,2.65,3.7],to=[.5,2.35,2.7],look=[0,1.52,.3]),
 dict(start=20.166667,cue='UP THE WALL.',note='Gases leave through a flue.',from_=[4.1,1.6,.7],to=[4.1,3.5,.7],look=[2.74,1.3,-1.7],lookTo=[2.74,3.55,-1.7],fov=55),
 dict(start=23.77,cue='HYPOCAUST.',note='Roman underfloor heating.',from_=[10,8,14],to=[8,8,15],look=[-1,1.0,0],fov=52),
 dict(start=26.29,cue='THE ROOM ABOVE.',note='',from_=[1.7,2.5,3.8],to=[.6,2.05,2.8],look=[0,1.52,0])]
for s in SHOTS:s['from']=s.pop('from_')
REFERENCE=None;THREE=None

def reuse_narration(out):
 ref=Path(REFERENCE)
 request=json.loads((ref/'voice-request.json').read_text())
 assert normalized_tokens(request['text'])==normalized_tokens(' '.join(base.TEXTS)), 'Reference narration differs from current script'
 assert request['speed']==.95,'Use the faster, originally measured voice for this re-edit'
 for name in ('voiceover.mp3','word-timings.json','voice-request.json','narration.txt'):shutil.copyfile(ref/name,out/name)
 duration=float(base.probe(out/'voiceover.mp3')['format']['duration'])
 words=[SimpleNamespace(**x) for x in json.loads((out/'word-timings.json').read_text())['words']]
 board=build_storyboard([dict(narration=t) for t in base.TEXTS],words,duration,30,'native',True)
 board['duration']=math.ceil((duration+.35)*30)/30;board['scenes'][-1]['end']=board['duration'];board['scenes'][-1]['end_frame']=round(board['duration']*30)
 board['visual_cuts']=[s['start'] for s in SHOTS[1:]];board['air_effect_start']=7.84
 board['snapshot_times']=[(s['start']+(SHOTS[i+1]['start'] if i+1<len(SHOTS) else board['duration']))/2 for i,s in enumerate(SHOTS)]
 (out/'storyboard-measured.json').write_text(json.dumps(board,indent=2));return board

def composition(out,board,gsap):
 scratch=out/'_fonts';paths=still_assets(scratch);shutil.copytree(paths[0]/'assets',out/'assets',dirs_exist_ok=True)
 first=(paths[0]/'index.html').read_text();fonts=first.split('<style>',1)[1].split('*{box-sizing',1)[0];shutil.rmtree(scratch)
 for name in ('three.module.js','three.core.js'):shutil.copyfile(Path(THREE)/name,out/'assets'/name)
 shutil.copyfile(Path(__file__).with_name('roman_dynamic_scene.js'),out/'assets/scene.js')
 css='''*{box-sizing:border-box}html,body{margin:0;width:540px;height:960px;overflow:hidden;background:#102124;color:#fff2d9;font-family:'DM Sans',sans-serif}#film{position:relative;width:540px;height:960px;overflow:hidden}canvas{position:absolute;inset:0;width:540px;height:960px}#topshade{position:absolute;top:0;left:0;right:0;height:250px;background:linear-gradient(#081315ee,transparent);pointer-events:none}.brand{position:absolute;left:26px;top:33px;font-size:12px;font-weight:700;letter-spacing:2px;color:#f4cf91;text-shadow:0 1px 8px #000}.recon{position:absolute;right:25px;top:35px;font-size:9px;letter-spacing:1px;color:#eee6d8}#cue{position:absolute;left:28px;right:28px;top:92px;font-size:32px;line-height:1.05;letter-spacing:-1px;font-weight:700;text-shadow:0 2px 20px #0009;pointer-events:none}#subcue{position:absolute;top:135px;left:29px;right:29px;font-size:13px;color:#e9d7bb;text-shadow:0 1px 9px #000}#caption-shade{position:absolute;bottom:0;left:0;right:0;height:220px;background:linear-gradient(transparent,#081315a8 48%,#081315e0);pointer-events:none}'''
 html=f'''<!doctype html><html><head><meta charset="utf-8"><title>HISTOLD — Follow the heat</title><style>{fonts}{css}</style></head><body><main id="film" data-composition-id="film" data-width="540" data-height="960" data-duration="{board['duration']}" data-no-timeline><div id="topshade"></div><div class="brand">HISTOLD</div><div id="cutaway" class="recon">CUTAWAY RECONSTRUCTION</div><div id="cue"></div><div id="subcue"></div><div id="caption-shade"></div></main><script>window.ROMAN_SHOTS={json.dumps(SHOTS)};window.ROMAN_DURATION={board['duration']};</script><script type="module" src="assets/scene.js"></script></body></html>'''
 (out/'index.html').write_text(html);(out/'camera-shots.json').write_text(json.dumps(SHOTS,indent=2))
 assert len(SHOTS)==11 and all(SHOTS[i]['start']<SHOTS[i+1]['start'] for i in range(10))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',default='output/roman-dynamic-preview');p.add_argument('--cli',required=True);p.add_argument('--gsap',required=True);p.add_argument('--three',required=True);p.add_argument('--reference',required=True);a=p.parse_args();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True)
 REFERENCE=Path(a.reference).resolve();THREE=Path(a.three).resolve();base.narrate=reuse_narration;base.composition=composition
 base.render(out,str(Path(a.cli).resolve()),str(Path(a.gsap).resolve()))
 metrics=json.loads((out/'metrics.json').read_text());metrics.update(voice_reused=True,camera_shots=len(SHOTS),visual_style='camera-led 3D cutaway')
 (out/'metrics.json').write_text(json.dumps(metrics,indent=2))
