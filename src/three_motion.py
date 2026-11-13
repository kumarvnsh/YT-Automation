"""Daily camera-led 3D videos from validated data, rendered locally by HyperFrames."""
from __future__ import annotations
import copy,json,math,os,re,shutil,subprocess,time
from pathlib import Path
from .config import Config,env
from .alignment import narration_hash

KINDS={'box','sphere','cylinder','cone','torus','plane','arch_bridge','building','water_channel','wheel','person','tree','boat','bowl','flow'}
ROOT=Path(__file__).resolve().parents[1]

def _number(value,low=-500,high=500,name='value'):
 if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:raise ValueError(f'{name}={value!r} must be a number in [{low}, {high}]')
 return value

def _vector(value,positive=False,name='vector'):
 if not isinstance(value,list) or len(value)!=3:raise ValueError(f'{name}={value!r} must be three numbers')
 return [_number(x,.005 if positive else -500,500,name) for x in value]

def validate_plan(data:dict,duration:float)->dict:
 """Reject malformed/unbounded geometry and static or incomplete camera plans."""
 if not isinstance(data,dict):raise ValueError('3D plan must be an object')
 p=copy.deepcopy(data);objects=p.get('objects');shots=p.get('shots')
 if not isinstance(objects,list) or not 1<=len(objects)<=160:raise ValueError('Plan needs 1–160 bounded objects')
 ids=set()
 for obj in objects:
  if not isinstance(obj,dict):raise ValueError('Invalid object')
  ident=obj.get('id','')
  if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,39}',ident) or ident in ids:raise ValueError('Invalid or duplicate object id')
  ids.add(ident)
  if obj.get('kind') not in KINDS:raise ValueError('Unsupported geometry kind')
  obj['position']=_vector(obj.get('position',[0,0,0]),name=f'{ident}.position');obj['rotation']=_vector(obj.get('rotation',[0,0,0]),name=f'{ident}.rotation')
  obj['size']=_vector(obj.get('size',[1,1,1]),True,f'{ident}.size')
  if not re.fullmatch(r'#[0-9a-fA-F]{6}',obj.get('color','#b79570')):raise ValueError('Invalid geometry color')
  for key,maximum in [('spans',12),('tiers',3),('count',80)]:
   if key in obj and (type(obj[key]) is not int or not 1<=obj[key]<=maximum):raise ValueError(f'Unbounded {key}')
  if obj['kind']=='flow':
   pts=obj.get('points')
   if not isinstance(pts,list) or not 2<=len(pts)<=24:raise ValueError('Flow needs a bounded path')
   for v in pts:_vector(v,name=f'{ident}.points')
   _number(obj.get('speed',.20),.01,2,f'{ident}.speed');_number(obj.get('radius',.025),.005,.15,f'{ident}.radius')
 if not isinstance(shots,list) or not 2<=len(shots)<=28:raise ValueError('Missing camera shots')
 for i,shot in enumerate(shots):
  start=_number(shot.get('start'),0,duration,f'shots[{i}].start')
  if (i==0 and start!=0) or (i and start<=shots[i-1]['start']):raise ValueError('Camera shots must cover the timeline from zero')
  end=shots[i+1]['start'] if i+1<len(shots) else duration
  if not isinstance(end,(int,float)) or not .3<=end-start<=4.5:raise ValueError('Each camera shot must be 0.3–4.5 seconds')
  for k in ('from','to','look'):_vector(shot.get(k),name=f'shots[{i}].{k}')
  _vector(shot.get('lookTo',shot['look']),name=f'shots[{i}].lookTo')
  distance=math.dist(shot['from'],shot['look'])
  if distance<.1 or math.dist(shot['from'],shot['to'])<max(.12,.035*distance):raise ValueError('Each camera must visibly travel through the scene')
  _number(shot.get('fov',50),30,78,f'shots[{i}].fov')
  if len(str(shot.get('cue','')))>36 or len(str(shot.get('note','')))>85:raise ValueError('Camera labels must be brief')
 for a in p.get('animations',[]):
  if a.get('object') not in ids:raise ValueError('Unknown animation target')
  if a.get('property') not in ('position','rotation','scale') or a.get('axis') not in ('x','y','z'):raise ValueError('Invalid animation property')
  for k in ('start','end'):  # models round the final time up past the clip; clamp, don't reject
   if isinstance(a.get(k),(int,float)) and not isinstance(a.get(k),bool) and math.isfinite(a[k]):a[k]=min(max(a[k],0),duration)
  n=f"animation {a.get('object')}.{a.get('property')}.{a.get('axis')}"
  start=_number(a.get('start'),0,duration,n+'.start');end=_number(a.get('end'),0,duration,n+'.end')
  if end<=start:raise ValueError('Invalid animation time window')
  low=.005 if a['property']=='scale' else -500
  _number(a.get('from'),low,500,n+'.from');_number(a.get('to'),low,500,n+'.to')
 if len(p.get('animations',[]))>100:raise ValueError('Too many animations')
 return p

def inspection_errors(foreground,differences,times):
 errors=[]
 if not foreground or min(foreground)<.025:errors.append('Blank or nearly blank central scene sample')
 if len(differences)!=max(0,len(times)-1):errors.append('Incomplete motion samples')
 idle=0
 for d,a,b in zip(differences,times,times[1:]):
  idle=idle+b-a if d<.002 else 0
  if idle>=.9:errors.append('Camera/foreground inactive for at least 0.9 seconds');break
 if differences and max(differences)<.002:errors.append('No measurable central foreground motion')
 return errors

SYSTEM='''You are the production 3D visual director for HISTOLD history Shorts. Return ONE JSON object only, no code. Design a coherent, historically responsible LOW-POLY 3D reconstruction with moving objects and strong depth. The viewer must discover mechanisms, objects, people or consequences, not read posters. Camera framing changes every 1.5–3.5 seconds; no shot >4.5 seconds. Each shot has genuine camera travel (at least 5% of camera-to-target distance), purposeful close-ups and wide reveals, subject large in portrait framing (9:16). Keep key action above the bottom 18% where subtitles go. All diagrams are illustrative, never archival evidence. Do not invent geography/borders or imply an exact reconstruction. Do not add historical claims beyond narration.
Schema:
{"objects":[{"id":"unique_id","kind":"box","position":[0,0,0],"size":[2,2,2],"rotation":[0,0,0],"color":"#b79570"}],"shots":[{"start":0,"from":[7,5,10],"to":[5,4,8],"look":[0,1,0],"lookTo":[0,1,0],"fov":50,"cue":"TWO OR THREE WORDS","note":"optional very short label"}],"animations":[{"object":"unique_id","property":"rotation","axis":"y","start":0,"end":5,"from":0,"to":3.14}]}
Allowed object kinds: box, sphere, cylinder, cone, torus, plane; composite arch_bridge, building, water_channel, wheel, person, tree, boat, bowl; flow. Sizes are world width/height/depth. Every coordinate, size and animation value must stay within ±500. Position is world coordinates, rotations in radians. Y is UP; ground at y=0. Composites are centered horizontally and rest on y=0 before position offset, EXCEPT primitives centered in all axes. arch_bridge: size width/total height/depth; optional spans 1–12, tiers 1–3; arches support an open water channel on top at height +0.2; cross-section is schematic. water_channel is a U-shaped conduit with water, size width along X/side-wall height/depth. wheel has axle along Z and rotates around Z. building has a cutaway roof and front, person is an anonymous stylized figure, boat length along X. Use color palettes stone/terracotta/teal/water cyan. Use geometry to tell the story; do not make unrelated cubes stand for named artifacts. No electricity motifs for periods before electricity. A flow object uses points [[x,y,z],...] relative to its position, color, speed 0.01–2 cycles/s, radius .005–.15, count 1–80; flowing particles are schematic route markers. Keep flow radius <=.025 for close cameras. A flow path must obey mechanism (water runs DOWNHILL, gas exits a flue, never into an occupied room). Each object's id is unique. 1–160 objects, <=100 animations. Detailed dioramas can combine many primitives. Avoid only one generic abstract block for the whole topic. Include at least 8 purposeful shots for a 30s video; shots start at 0, sorted; the last extends to the supplied duration. Brief cues disappear; no persistent headline layout. Explicitly calculate camera view distance so subject fits portrait; a 10-unit-wide subject needs ~20 units of camera distance at FOV50, close-ups need 2–6 units. Never place camera inside solid geometry. Close camera movement should pass through empty corridors. Keep every shot visibly populated. Animations are absolute-time linear interpolations; multiple animations for an object's axis must chain to the previous ending value. Build a complete scene, then reveal/explain the relevant parts. Use source notes only as context; narration is the approved spoken story.'''

def generate_plan(cfg,st,duration):
 import anthropic
 from .script_generator import _extract_json
 model=cfg.get('video.three_d.model',cfg.get('video.claude_motion.model','claude-haiku-5-5'))
 context=dict(title=st['script']['title'],duration=duration,narration=st['script']['segments'],timing=[dict(start=x['start'],end=x['end'],words=x.get('words',[])) for x in st.get('storyboard',{}).get('scenes',[])])
 client=anthropic.Anthropic(api_key=env('ANTHROPIC_API_KEY'),timeout=600,max_retries=1)
 messages=[dict(role='user',content=json.dumps(context))];usage={}
 for attempt in range(2):
  with client.beta.messages.stream(model=model,max_tokens=int(cfg.get('video.three_d.max_tokens',14000)),thinking={'type':'adaptive','display':'summarized'},output_config={'effort':cfg.get('video.claude_motion.effort','medium')},betas=['server-side-fallback-2026-07-01'],extra_body={'fallbacks':'default'},system=SYSTEM,messages=messages) as stream:
   msg=stream.get_final_message()
  text=''.join(b.text for b in msg.content if b.type=='text');usage.setdefault('calls',[]).append(dict(input_tokens=msg.usage.input_tokens,output_tokens=msg.usage.output_tokens))
  try:
   plan=validate_plan(_extract_json(text),duration);return plan,usage
  except (ValueError,TypeError,KeyError) as exc:
   if attempt:raise
   messages.extend([dict(role='assistant',content=text),dict(role='user',content=f'Fix the JSON plan. Validation failed: {exc}. Return the complete corrected plan, preserve the story, no commentary.')])
 raise RuntimeError('3D planning failed')

def _project(cfg,stage,st,plan,duration):
 project=stage/'three';assets=project/'assets';assets.mkdir(parents=True,exist_ok=True)
 for name in ('three.module.js','three.core.js'):shutil.copyfile(ROOT/'node_modules/three/build'/name,assets/name)
 shutil.copyfile(Path(__file__).with_name('three_scene.js'),assets/'scene.js')
 # Use standard locally available fonts for captions; the 3D labels use the browser sans stack.
 maps=''
 from .map_assets import required_maps
 for m in required_maps(st.get('research',{}))[:2]:maps+=f'<div>{m["svg"]}<span>{m["label"]}</span></div>'
 config=dict(duration=duration,objects=plan['objects'],shots=plan['shots'],animations=plan.get('animations',[]),background=plan.get('background','#102124'),render_scale=float(cfg.get('video.three_d.render_scale',1)))
 payload=json.dumps(config).replace('<','\\u003c')
 html='''<!doctype html><html><head><meta charset="utf-8"><style>*{box-sizing:border-box}html,body{margin:0;width:540px;height:960px;overflow:hidden;background:#102124;color:#fff3dd;font-family:Arial,sans-serif}#film{position:relative;width:540px;height:960px;overflow:hidden}canvas{position:absolute;inset:0;width:540px;height:960px}.top{position:absolute;inset:0 0 auto;height:250px;background:linear-gradient(#071315e8,transparent);pointer-events:none}.brand{position:absolute;left:28px;top:30px;letter-spacing:2px;font-size:12px;color:#f0d5a2;font-weight:bold}.recon{position:absolute;right:25px;top:33px;font-size:9px;color:#eee5d4;letter-spacing:1px}#cue{position:absolute;top:92px;left:28px;right:28px;font-size:30px;line-height:1.1;letter-spacing:-.6px;font-weight:bold;text-shadow:0 2px 10px #0009}#note{position:absolute;top:133px;left:29px;right:29px;font-size:13px;color:#f0d8b3}.bottom{position:absolute;bottom:0;left:0;right:0;height:220px;background:linear-gradient(transparent,#071315ca 65%,#071315ed);pointer-events:none}#locator{position:absolute;right:25px;top:165px;width:95px;color:#f4e3c0;display:flex;gap:5px}#locator div{width:90px;text-align:center}#locator svg{width:90px;height:65px;filter:drop-shadow(0 1px 4px #000)}#locator span{font-size:10px;text-shadow:0 1px 4px #000}</style></head><body><main id="film" data-composition-id="film" data-width="540" data-height="960" data-duration="DURATION" data-fps="30" data-no-timeline><div class="top"></div><div class="brand">HISTOLD</div><div class="recon">ILLUSTRATED RECONSTRUCTION</div><div id="cue"></div><div id="note"></div><div id="locator">MAPS</div><div class="bottom"></div></main><script>window.THREE_PLAN=PAYLOAD;</script><script type="module" src="assets/scene.js"></script></body></html>'''
 (project/'index.html').write_text(html.replace('DURATION',str(duration)).replace('MAPS',maps).replace('PAYLOAD',payload));return project

def _inspect(project,plan,duration,cli):
 import numpy as np
 from PIL import Image
 samples={.15,max(.15,duration-1/30)}
 for i,s in enumerate(plan['shots']):
  end=plan['shots'][i+1]['start'] if i+1<len(plan['shots']) else duration
  samples.update([min(end-.08,s['start']+.2),min(end-.05,s['start']+.8),(s['start']+end)/2])
 times=sorted(samples);dest=project/'samples'
 with (project/'snapshot.log').open('w') as log:
  subprocess.run([cli,'snapshot',str(project),'--at',','.join(f'{t:.4f}' for t in times),'--no-end','--no-browser-gpu','--output',str(dest)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
 files=sorted(dest.glob('frame-*.png'));errors=[]
 if len(files)!=len(times):return dict(errors=['Missing rendered scene samples'],warnings=[],sample_times=times)
 frames=[];foreground=[]
 for f in files:
  image=np.asarray(Image.open(f).convert('RGB').resize((108,192)),dtype=float)/255
  body=image[35:160];frames.append(body);foreground.append(float(np.mean(np.max(body,axis=2)-np.min(body,axis=2)>.055)))
 differences=[float(np.mean(np.max(np.abs(b-a),axis=2)>.04)) for a,b in zip(frames,frames[1:])]
 errors.extend(inspection_errors(foreground,differences,times))
 return dict(errors=errors,warnings=[],foreground=foreground,differences=differences,sample_times=times)

def render_motion_video(cfg,st,stage,words,duration):
 stage=Path(stage);fps=int(cfg.get('video.short.fps',30));duration=round(duration*fps)/fps
 plan_file=stage/'three_plan.json';binding=narration_hash(st['script']['segments'])
 if plan_file.exists():
  saved=json.loads(plan_file.read_text())
  if saved.get('narration_hash')!=binding:raise ValueError('3D plan belongs to different narration')
  plan=validate_plan(saved['plan'],duration);usage=saved.get('usage',{})
 else:
  plan,usage=generate_plan(cfg,st,duration);plan_file.write_text(json.dumps(dict(narration_hash=binding,plan=plan,usage=usage),indent=2))
 cli=env('HYPERFRAMES_CLI') or str(ROOT/'node_modules/.bin/hyperframes')
 if not Path(cli).is_file():raise RuntimeError('3D renderer dependencies missing: run npm ci with Node 22+')
 project=_project(cfg,stage,st,plan,duration)
 report=_inspect(project,plan,duration,cli);report.update(narration_hash=binding,renderer='three_motion',camera_shots=len(plan['shots']),usage=usage)
 st['render_quality']=report;(stage/'render_quality.json').write_text(json.dumps(report,indent=2))
 if report['errors']:raise RuntimeError('3D visual checks failed: '+'; '.join(report['errors']))
 start=time.monotonic()
 with (project/'render.log').open('w') as log:subprocess.run([cli,'render',str(project),'-o',str((stage/'silent.mp4').resolve()),'--resolution','portrait','--fps',str(fps),'--crf',str(cfg.get('video.encode.crf',23)),'--workers',str(cfg.get('video.three_d.render_workers',2)),'--no-browser-gpu','--experimental-fast-capture=false'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=2400)
 report['render_seconds']=round(time.monotonic()-start,2);st['render_quality']=report;(stage/'render_quality.json').write_text(json.dumps(report,indent=2))
 return stage/'silent.mp4'
