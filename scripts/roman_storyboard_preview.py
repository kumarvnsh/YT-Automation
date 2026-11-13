"""Render four cinematic storyboard frames on the existing preview runner."""
from __future__ import annotations
import argparse,json,shutil,subprocess,time,os
from pathlib import Path
from roman_style_preview import build as build_styles, diagram, STYLES, SOURCE

SHOTS=[
 ('01-floor-reveal','00–03 SEC / THE HOOK','Warm floors.<br><em>No electricity.</em>','This Roman room was heated from below.'),
 ('02-external-furnace','03–12 SEC / THE SOURCE','The fire<br><em>stayed outside.</em>','An external furnace supplied the heat.'),
 ('03-air-path','12–25 SEC / THE MECHANISM','Hot air.<br><em>Hidden below.</em>','Hot air circulated between stacks of tiles.'),
 ('04-whole-system','25–35 SEC / THE PAYOFF','The floor became<br><em>the heater.</em>','The raised floor warmed the room above.')]

def svg(content):
 return '''<svg viewBox="0 0 540 500" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="slab" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#f0c18a"/><stop offset="1" stop-color="#9b6c42"/></linearGradient><radialGradient id="warm"><stop stop-color="#f4a04c" stop-opacity=".55"/><stop offset="1" stop-color="#ed8c3900"/></radialGradient><linearGradient id="ember"><stop stop-color="#fcce81"/><stop offset="1" stop-color="#ba4c27"/></linearGradient><marker id="tip" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#efad68"/></marker></defs>'''+content+'</svg>'

def floor():
 tiles=''.join(f'<path d="M{55+225*i/6} {230-99*i/6} L{270+225*i/6} {330-99*i/6} M{55+215*i/6} {230+100*i/6} L{280+215*i/6} {131+100*i/6}"/>' for i in range(1,6))
 return svg('''<ellipse cx="270" cy="333" rx="240" ry="110" fill="url(#warm)"/>
 <path d="M55 230L280 131L495 225L270 330Z" fill="url(#slab)" stroke="#e4b682"/>
 <path d="M55 230V265L270 371V330Z" fill="#906447"/><path d="M270 330V371L495 262V225Z" fill="#b17a49"/>
 <g stroke="#f0d5ac" stroke-width="1" opacity=".32">'''+tiles+'''</g>
 <path d="M74 272L271 368L479 268" stroke="#efad68" stroke-width="5" fill="none"/>
 <g stroke="#efad68" fill="none" stroke-width="2" opacity=".8"><path d="M181 183q-11-24 0-43t0-43"/><path d="M264 178q-11-24 0-43t0-43"/><path d="M348 183q-11-24 0-43t0-43"/></g>
 <path d="M290 339L372 397H463" fill="none" stroke="#e8d4b3"/><circle cx="290" cy="339" r="3" fill="#edaa62"/>
 <text x="325" y="424" class="label">HEAT FROM BELOW</text>
 <text x="72" y="65" class="micro">THE ROOM ABOVE</text>''')

def furnace():
 bricks=''.join(f'<path d="M88 {242+i*22}H263"/>' for i in range(7))
 return svg('''<ellipse cx="244" cy="350" rx="230" ry="95" fill="url(#warm)"/>
 <rect x="76" y="224" width="178" height="166" fill="#93613f" stroke="#c38b5c"/>
 <path d="M76 224L126 196H286L254 224Z" fill="#b27b50"/><path d="M254 224L286 196V363L254 390Z" fill="#654632"/>
 <g stroke="#4d372a" opacity=".5">'''+bricks+'''<path d="M135 224V244M198 246V266M131 268V288M200 290V310"/></g>
 <path d="M112 390V300a53 53 0 0 1 106 0V390Z" fill="#231f1b" stroke="#c49362" stroke-width="6"/>
 <path d="M134 370q-32-33 20-84q-1 34 22 24q18-16 18-40q49 79-10 100Z" fill="url(#ember)"/>
 <path d="M157 370q-13-23 9-46q-1 22 18 21q7 26-27 25Z" fill="#f8d89a"/>
 <rect x="319" y="149" width="26" height="244" fill="#9b8b72"/>
 <path d="M345 272H504V296H345Z" fill="url(#slab)"/>
 <rect x="386" y="296" width="22" height="95" fill="#a96d47"/><rect x="468" y="296" width="22" height="95" fill="#a96d47"/>
 <path d="M248 348H320V316H454" stroke="#efad68" stroke-width="4" fill="none" marker-end="url(#tip)"/>
 <path d="M30 416H505" stroke="#695d49"/>
 <text x="83" y="462" class="label">OUTSIDE</text><text x="366" y="462" class="label">BENEATH THE ROOM</text>
 <text x="338" y="111" class="micro">ROOM WALL</text><path d="M345 123L333 166" stroke="#d2c5ad"/>
 <text x="365" y="247" class="micro">RAISED FLOOR</text>''')

def air_path():
 stacks=''
 for x in (87,160,233,306,379,452):
  for y in range(226,358,18):
   stacks+=f'<rect x="{x}" y="{y}" width="24" height="15" fill="#a56e49" stroke="#bd8b5a" stroke-width=".6"/>'
 return svg('''<ellipse cx="270" cy="306" rx="238" ry="120" fill="url(#warm)"/>
 <path d="M43 195H500V226H43Z" fill="url(#slab)"/>
 <path d="M43 195L69 174H518L500 195Z" fill="#d6ac7a"/>
 <path d="M43 366H500V393H43Z" fill="#67533c"/>
 '''+stacks+'''<g fill="none" stroke="#efad68" stroke-width="3.5" marker-end="url(#tip)"><path d="M51 253C139 239 164 269 218 253S351 242 430 253"/><path d="M51 306C122 293 163 323 229 306S355 294 487 306"/><path d="M148 343H432"/></g>
 <text x="310" y="133" class="label">RAISED FLOOR</text><path d="M374 145V175" stroke="#dec8a7"/>
 <text x="55" y="115" class="label hot">HOT AIR</text><path d="M89 132V234" stroke="#efad68"/>
 <text x="280" y="445" class="label">TILE SUPPORTS</text><path d="M337 420L320 354" stroke="#dec8a7"/>
 <g stroke="#efad68" opacity=".6" fill="none"><path d="M146 164v-30"/><path d="M228 164v-30"/><path d="M310 164v-30"/></g>''')

def build(out):
 # Reuse the verified style builder's font assets and cutaway; no new rendering dependency.
 scratch=out/'_style-assets';build_styles(scratch)
 assets=scratch/'shared-assets'
 font_css=(scratch/'01-cinematic/index.html').read_text().split('<style>',1)[1].split('*{box-sizing',1)[0]
 diagrams=[floor(),furnace(),air_path(),diagram(STYLES['01-cinematic'])]
 paths=[]
 for i,((slug,timecode,title,caption),art) in enumerate(zip(SHOTS,diagrams)):
  folder=out/slug;folder.mkdir(parents=True,exist_ok=True);shutil.copytree(assets,folder/'assets',dirs_exist_ok=True)
  css='''*{box-sizing:border-box}html,body{margin:0;width:540px;height:960px;overflow:hidden;background:#111b1d;color:#f6e8cd;font-family:'DM Sans',sans-serif}#film{width:540px;height:960px;position:relative;overflow:hidden;padding:37px 38px;background:radial-gradient(ellipse at 50% 52%,#92522f20,transparent 60%)}.mast{display:flex;justify-content:space-between;border-bottom:1px solid #dec9a12b;padding-bottom:18px;font-size:11px;letter-spacing:1.5px}.mast b{color:#edaa62}.kicker{font-size:12px;color:#d5ae79;letter-spacing:1.6px;margin-top:35px}h1{font-family:'Libre Baskerville';font-size:39px;letter-spacing:-1.4px;line-height:1.3;font-weight:400;margin:17px 0 0}h1 em{color:#edaa62;font-weight:400}.art{position:absolute;top:285px;left:0;width:540px;height:500px}.art svg{width:100%;height:100%}.label{font:13px 'DM Sans';letter-spacing:1px;fill:#f1e4ca}.micro{font:11px 'DM Sans';letter-spacing:1px;fill:#c6c2b1}.hot{fill:#efad68}.caption{position:absolute;left:38px;right:38px;top:806px;border-top:1px solid #d6ba8e44;padding-top:20px;font-size:21px;line-height:1.4}.foot{position:absolute;left:38px;right:38px;bottom:33px;display:flex;justify-content:space-between;font-size:9px;letter-spacing:1px;color:#aeb4a9}.progress{position:absolute;left:38px;right:38px;bottom:75px;display:flex;gap:5px}.progress span{height:2px;flex:1;background:#dfccaa30}.progress .on{background:#edaa62}'''
  progress=''.join(f'<span class="{"on" if j<=i else ""}"></span>' for j in range(4))
  html=f'''<!doctype html><html><head><meta charset="utf-8"><title>HISTOLD / Roman hypocaust / Shot {i+1}</title><style>{font_css}{css}</style></head><body><main id="film" data-composition-id="film" data-width="540" data-height="960" data-duration="1" data-no-timeline><div class="mast"><b>H / HISTOLD</b><span>ROMAN ENGINEERING</span></div><div class="kicker">{timecode}</div><h1>{title}</h1><div class="art">{art}</div><div class="caption">{caption}</div><div class="progress">{progress}</div><div class="foot"><span>ILLUSTRATED RECONSTRUCTION</span><span>SHOT {i+1} / 4</span></div></main></body></html>'''
  (folder/'index.html').write_text(html);paths.append(folder)
 shutil.rmtree(scratch)
 (out/'storyboard.json').write_text(json.dumps({'title':'How did Romans heat a room without electricity?','duration_plan_seconds':35,'historical_reference':SOURCE,'shots':[dict(id=s[0],time=s[1],caption=s[3]) for s in SHOTS],'paid_api_calls':0},indent=2))
 assert len(paths)==4
 return paths

def render(out,cli):
 from PIL import Image,ImageDraw
 metrics=[]
 for folder in build(out):
  started=time.monotonic()
  with (folder/'snapshot.log').open('w') as log:
   subprocess.run([cli,'snapshot',str(folder),'--at','0.5','--no-end','--zoom','#film','--zoom-scale','2','--no-browser-gpu','--output',str(folder/'frames')],stdout=log,stderr=subprocess.STDOUT,check=True)
  pngs=list((folder/'frames').glob('*.png'));assert len(pngs)==1
  final=out/f'{folder.name}.png';shutil.copyfile(pngs[0],final)
  with Image.open(final) as im:assert im.size==(1080,1920)
  metrics.append(dict(shot=folder.name,seconds=round(time.monotonic()-started,2)))
 sheet=Image.new('RGB',(1080,2020),'#102b27');draw=ImageDraw.Draw(sheet)
 for i,(slug,label,_,_) in enumerate(SHOTS):
  x=i%2*540;y=i//2*1010;draw.text((x+20,y+17),label.replace('–','-'),fill='#f2dfbd');sheet.paste(Image.open(out/f'{slug}.png').resize((540,960)),(x,y+50))
 sheet.save(out/'storyboard.jpg',quality=94)
 (out/'metrics.json').write_text(json.dumps(dict(shots=metrics,render_host='GitHub Actions' if os.environ.get('GITHUB_ACTIONS') else 'local',paid_api_calls=0),indent=2))
 print(json.dumps(metrics,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',default='output/roman-storyboard-preview');p.add_argument('--cli',default='hyperframes');a=p.parse_args();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True);render(out,a.cli)
