"""Narrated HyperFrames preview. No upload or paid generation paths."""
from __future__ import annotations
import argparse,json,math,os,re,shutil,subprocess,sys,time,wave,random,struct
from pathlib import Path
from types import SimpleNamespace
from roman_storyboard_preview import build as build_stills, SHOTS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.alignment import build_storyboard, normalized_tokens

TEXTS=[
 'This Roman floor had heating, without electricity.',
 'The fire burned in a furnace outside the room. Hot air entered the empty space underneath the floor.',
 'Stacks of tiles held the floor up, leaving gaps for the hot air to circulate. The heat warmed the floor above.',
 'Hot gases could then escape through flues in the walls. This system was called a hypocaust. The floor itself helped warm the room.'
]

def probe(path):return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))

def narrate(out):
 text='\n\n'.join(TEXTS);(out/'narration.txt').write_text(text)
 request=dict(text=text,voice='am_michael',speed=.84,output=str(out/'voiceover.mp3'))
 (out/'voice-request.json').write_text(json.dumps(request))
 subprocess.run([os.environ.get('KOKORO_PYTHON',sys.executable),'-m','src.kokoro_voice',str(out/'voice-request.json'),str(out/'word-timings.json')],check=True,cwd=Path(__file__).resolve().parents[1])
 duration=float(probe(out/'voiceover.mp3')['format']['duration'])
 words=[SimpleNamespace(**w) for w in json.loads((out/'word-timings.json').read_text())['words']]
 # Measured voice timing determines scene cuts; no invented uniform word alignment.
 board=build_storyboard([dict(narration=t) for t in TEXTS],words,duration,30,'native',strict=True)
 board['scenes'][-1]['end_frame']=math.ceil((duration+.35)*30)
 board['scenes'][-1]['end']=board['scenes'][-1]['end_frame']/30
 board['duration']=board['scenes'][-1]['end']
 assert board['coverage']==1 and 25<board['duration']<50
 (out/'storyboard-measured.json').write_text(json.dumps(board,indent=2))
 return board

def composition(out,board,gsap):
 scratch=out/'_stills';paths=build_stills(scratch)
 shutil.copytree(paths[0]/'assets',out/'assets',dirs_exist_ok=True)
 shutil.copyfile(gsap,out/'assets/gsap.min.js')
 first=(paths[0]/'index.html').read_text();css=first.split('<style>',1)[1].split('</style>',1)[0]
 css=css.replace('#film{','.scene{')
 css+='''#film{position:relative;width:540px;height:960px;overflow:hidden}.scene{position:absolute;inset:0}.caption{background:#111b1d;left:0;right:0;bottom:0;padding:0}.foot,.progress{z-index:5}.art{isolation:isolate}'''
 sections=[]
 for i,(path,scene) in enumerate(zip(paths,board['scenes'])):
  html=(path/'index.html').read_text().split('<body>',1)[1].split('</body>',1)[0]
  html=re.sub(r'<main[^>]+>',f'<section id="scene-{i}" class="scene clip" data-start="{scene["start"]}" data-duration="{scene["end"]-scene["start"]}" data-layout-allow-overlap>',html,count=1).replace('</main>','</section>')
  html=re.sub(r'<div class="caption">.*?</div>','<div class="caption"></div>',html,count=1)
  html=re.sub(r'<div class="kicker">.*?</div>',f'<div class="kicker">0{i+1} / '+['THE QUESTION','THE SOURCE','THE MECHANISM','THE PAYOFF'][i]+'</div>',html,count=1)
  if i==0:
   for d in ('M55 230','M55 230V265','M270 330V371'):
    html=html.replace(f'<path d="{d}',f'<path class="floor-surface" d="{d}')
  if i==1:html=html.replace('fill="url(#ember)"','class="flame" fill="url(#ember)"')
  sections.append(html)
 times=[dict(start=s['start'],duration=s['end']-s['start']) for s in board['scenes']]
 script='''const scenes=TIMES;const tl=gsap.timeline({paused:true});
scenes.forEach((s,i)=>{const id='#scene-'+i;
 tl.from(id+' .kicker',{opacity:0,y:6,duration:.35},s.start+.06);
 tl.from(id+' h1',{opacity:0,y:12,duration:.55,ease:'power2.out'},s.start+.10);
 tl.from(id+' .art',{opacity:0,y:15,duration:.7,ease:'power2.out'},s.start+.15);
 tl.to(id+' .art',{scale:1.025,transformOrigin:'50% 55%',duration:Math.max(.1,s.duration-.7),ease:'none'},s.start+.7);
});
tl.to('#scene-0 .floor-surface',{y:-12,duration:1.3,ease:'power2.inOut'},.6);
tl.from('#scene-0 .art ellipse',{opacity:.1,duration:1.7},.2);
const fireStart=scenes[1].start;tl.to('#scene-1 .flame',{scaleY:.89,scaleX:1.06,transformOrigin:'50% 100%',duration:.6,yoyo:true,repeat:7,ease:'sine.inOut'},fireStart+.8);
function drawPaths(selector,start,step,duration){document.querySelectorAll(selector).forEach((p,i)=>{const n=p.getTotalLength();gsap.set(p,{strokeDasharray:n,strokeDashoffset:n});tl.to(p,{strokeDashoffset:0,duration,ease:'power1.inOut'},start+i*step);});}
drawPaths('#scene-1 path[marker-end]',fireStart+2.6,0,1.6);
drawPaths('#scene-2 g[marker-end] path',scenes[2].start+2.3,.55,2.2);
tl.from('#scene-2 .art rect',{opacity:.18,stagger:.012,duration:.5},scenes[2].start+.5);
drawPaths('#scene-3 path[marker-end]',scenes[3].start+.75,.45,2.2);
tl.to({}, {duration:.01},DURATION-.01);window.__timelines={film:tl};'''.replace('TIMES',json.dumps(times)).replace('DURATION',str(board['duration']))
 html=f'''<!doctype html><html><head><meta charset="utf-8"><title>HISTOLD / Roman heating preview</title><style>{css}</style><script src="assets/gsap.min.js"></script></head><body><main id="film" data-composition-id="film" data-width="540" data-height="960" data-duration="{board['duration']}" data-fps="30">{''.join(sections)}</main><script>{script}</script></body></html>'''
 (out/'index.html').write_text(html);shutil.rmtree(scratch)
 assert html.count('class="scene clip"')==4 and 'Math.random' not in html

def captions(out,board):
 def ts(t):
  n=round(t*100);return f'{n//360000}:{n//6000%60:02}:{n//100%60:02}.{n%100:02}'
 header='''[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DM Sans,66,&H00CDE8F6,&H00000000,&H00000000,0,0,1,0,0,2,88,88,180,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
 lines=[];caption_words=[]
 for scene_index,scene in enumerate(board['scenes']):
  punctuation={};position=0
  for token in TEXTS[scene_index].split():
   position+=len(normalized_tokens(token))
   if token[-1] in '.,?!':punctuation[position]=token[-1]
  consumed=0;group=[]
  def flush():
   if not group:return
   start=scene['start']+group[0]['start'];end=min(scene['end'],scene['start']+group[-1]['end']);text=' '.join(w['word'] for w in group)
   lines.append(f'Dialogue: 0,{ts(start)},{ts(end)},Default,,0,0,0,,{{\\fad(50,50)}}{text}');caption_words.append(text)
  for original in scene['words']:
   word=dict(original);consumed+=len(normalized_tokens(word['word']))
   mark=punctuation.get(consumed,'')
   if mark:word['word']=word['word'].rstrip('.,?!')+mark
   if group and (len(' '.join(w['word'] for w in group+[word]))>27 or len(group)>=4):flush();group=[]
   group.append(word)
   if mark in ('.','?','!'):flush();group=[]
  flush()
 assert normalized_tokens(' '.join(caption_words))==normalized_tokens(' '.join(TEXTS))
 (out/'captions.ass').write_text(header+'\n'.join(lines)+'\n')

def effects(out,board):
 rate=24000;n=round(board['duration']*rate);rng=random.Random(27);samples=[];smooth=0
 fire=board['scenes'][1];air=board['scenes'][2];cut=[s['start'] for s in board['scenes'][1:]]
 for k in range(n):
  t=k/rate;noise=rng.uniform(-1,1);smooth=.96*smooth+.04*noise;v=0
  if fire['start']+.4<t<fire['end']-.2:v+=.035*smooth*(.5+.5*math.sin(t*13)**2)
  elapsed=t-(air['start']+2.3)
  if 0<elapsed<2:v+=.022*smooth*math.sin(math.pi*elapsed/2)
  for moment in cut:
   dt=t-moment
   if 0<dt<.24:v+=.028*math.sin(2*math.pi*(220*dt-160*dt*dt))*math.exp(-dt*22)
  samples.append(max(-32767,min(32767,round(v*32767))))
 with wave.open(str(out/'effects.wav'),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(struct.pack('<'+'h'*len(samples),*samples))

def render(out,cli,gsap):
 start=time.monotonic();board=narrate(out);voice_seconds=time.monotonic()-start
 composition(out,board,gsap);captions(out,board);effects(out,board)
 with (out/'check.json').open('w') as log:subprocess.run([cli,'check',str(out),'--json'],stdout=log,check=True)
 with (out/'snapshot.log').open('w') as log:subprocess.run([cli,'snapshot',str(out),'--at',','.join(str(round((s['start']+s['end'])/2,3)) for s in board['scenes']),'--no-end','--output',str(out/'snapshots')],stdout=log,check=True)
 start=time.monotonic()
 with (out/'render.log').open('w') as log:subprocess.run([cli,'render',str(out),'-o',str(out/'silent.mp4'),'--resolution','portrait','--fps','30','--crf','23','--workers','2','--no-browser-gpu','--experimental-fast-capture=false'],stdout=log,stderr=subprocess.STDOUT,check=True)
 render_seconds=time.monotonic()-start
 graph='[0:v]subtitles=filename=captions.ass:fontsdir=assets[v];[1:a]apad[vo];[vo][2:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95:level=0[a]'
 with (out/'compose.log').open('w') as log:subprocess.run(['ffmpeg','-y','-i',str(out/'silent.mp4'),'-i',str(out/'voiceover.mp3'),'-i',str(out/'effects.wav'),'-filter_complex',graph,'-map','[v]','-map','[a]','-t',str(board['duration']),'-r','30','-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',str(out/'roman-heating-preview.mp4')],cwd=out,stdout=log,stderr=subprocess.STDOUT,check=True)
 final=probe(out/'roman-heating-preview.mp4');v=next(s for s in final['streams'] if s['codec_type']=='video');a=next(s for s in final['streams'] if s['codec_type']=='audio')
 assert (v['width'],v['height'],v['r_frame_rate'])==(1080,1920,'30/1')
 assert int(v['nb_frames'])==round(board['duration']*30)
 assert abs(float(a['duration'])-board['duration'])<.1
 subprocess.run(['ffmpeg','-v','error','-i',str(out/'roman-heating-preview.mp4'),'-f','null','-'],check=True)
 metrics=dict(render_host='GitHub Actions' if os.environ.get('GITHUB_ACTIONS') else 'local',voice='Kokoro am_michael, CPU',voice_generation_seconds=round(voice_seconds,2),render_seconds=round(render_seconds,2),duration_seconds=board['duration'],video_frames=int(v['nb_frames']),bytes=int(final['format']['size']),alignment_coverage=board['coverage'],full_decode_ok=True,paid_api_calls=0,published=False)
 (out/'metrics.json').write_text(json.dumps(metrics,indent=2));print(json.dumps(metrics,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',default='output/roman-video-preview');p.add_argument('--cli',required=True);p.add_argument('--gsap',required=True);a=p.parse_args();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True);render(out,str(Path(a.cli).resolve()),str(Path(a.gsap).resolve()))
