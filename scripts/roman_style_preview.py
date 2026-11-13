"""Four deterministic HTML/SVG style frames; no AI API or publishing calls."""
from __future__ import annotations
import argparse, json, math, re, shutil, subprocess, time, urllib.request
from pathlib import Path

STYLES = {
 '01-cinematic':dict(label='CINEMATIC CUTAWAY',bg='#131b1c',ink='#f6e8cd',muted='#b7b5a5',accent='#ec9c54',stone='#b99b75',side='#806347',brick='#a76440',wire=False),
 '02-field-journal':dict(label='ARCHAEOLOGIST’S JOURNAL',bg='#ede1c7',ink='#423a2c',muted='#7c6b4e',accent='#a44932',stone='#d6bd91',side='#bea47c',brick='#a56e4c',wire=False),
 '03-editorial':dict(label='BOLD EDITORIAL COLLAGE',bg='#f5cf43',ink='#1f2727',muted='#343d36',accent='#e94a31',stone='#f5eed8',side='#d9c7a9',brick='#d8593c',wire=False),
 '04-blueprint':dict(label='ENGINEERING BLUEPRINT',bg='#103455',ink='#e6f0ee',muted='#9ebfc5',accent='#f0b866',stone='#285572',side='#163d59',brick='#34718a',wire=True),
}
SOURCE='https://www.english-heritage.org.uk/learn/story-of-england/romans/roman-bathing/'

def diagram(c):
    def p(x,y,z):return (248+(x-y)*.92,235+(x+y)*.4-z)
    def pts(points):return ' '.join(f'{x:.2f},{y:.2f}' for x,y in points)
    def face(coords,color,extra=''):
        fill='none' if c['wire'] else color
        return f'<polygon points="{pts(coords)}" fill="{fill}" stroke="{c["ink"] if c["wire"] else "#271e172b"}" stroke-width="1.2" {extra}/>'
    def box(x,y,z,w,d,h,top,front,side):
        a,b,cc,dd=[p(x,y,z+h),p(x+w,y,z+h),p(x+w,y+d,z+h),p(x,y+d,z+h)]
        aa,bb,ccc,ddd=[p(x,y,z),p(x+w,y,z),p(x+w,y+d,z),p(x,y+d,z)]
        return face([dd,cc,ccc,ddd],front)+face([b,cc,ccc,bb],side)+face([a,b,cc,dd],top)
    s='<svg viewBox="0 0 540 520" xmlns="http://www.w3.org/2000/svg">'
    s+='''<defs><radialGradient id="heat"><stop stop-color="#ef8e3b" stop-opacity=".37"/><stop offset="1" stop-color="#ef8e3b" stop-opacity="0"/></radialGradient><marker id="arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="'''+c['accent']+'"/></marker></defs>'
    s+='<g transform="translate(28,0) scale(.92)">'
    if not c['wire']:s+='<ellipse cx="260" cy="380" rx="230" ry="90" fill="#00000018"/><ellipse cx="250" cy="295" rx="220" ry="125" fill="url(#heat)"/>'
    s+=box(-20,-20,-25,260,220,25,c['side'],c['side'],c['side'])
    # Pilae: stacked tile supports below the suspended floor.
    for y in [12,68,124,180]:
        for x in [16,72,128,184]:
            for z in range(0,100,12):s+=box(x,y,z,19,19,10,c['brick'],c['brick'],c['side'])
    # Two slabs are lifted apart to expose the support structure; schematic, not a building survey.
    s+=box(-4,-4,121,228,105,16,c['stone'],c['side'],c['side'])
    s+=box(-4,107,150,228,94,16,c['stone'],c['side'],c['side'])
    for y in range(2,202,22):
        z=167 if y>105 else 138
        aa,bb=p(0,y,z),p(220,y,z)
        s+=f'<path d="M{aa[0]},{aa[1]} L{bb[0]},{bb[1]}" stroke="{c["ink"]}" opacity=".18" fill="none"/>'
    for x in range(0,225,28):
        for y0,y1,z in [(0,100,138),(108,200,167)]:
            aa,bb=p(x,y0,z),p(x,y1,z)
            s+=f'<path d="M{aa[0]},{aa[1]} L{bb[0]},{bb[1]}" stroke="{c["ink"]}" opacity=".18"/>'
    # The off-room furnace feeds the void, never the occupied room.
    s+=box(-90,135,0,78,55,65,c['brick'],c['side'],c['side'])
    a=p(-65,191,10);s+=f'<ellipse cx="{a[0]+26}" cy="{a[1]-7}" rx="20" ry="24" fill="{c["bg"]}"/>'
    s+=f'<path d="M{a[0]+12},{a[1]+10} Q{a[0]+1},{a[1]-6} {a[0]+22},{a[1]-26} Q{a[0]+17},{a[1]-10} {a[0]+32},{a[1]-20} Q{a[0]+50},{a[1]+10} {a[0]+12},{a[1]+10}" fill="{c["accent"]}"/>'
    for off in [0,31,62]:
        s+=f'<path d="M105 {333-off} C155 {302-off} 226 {316-off} 310 {277-off}" stroke="{c["accent"]}" stroke-width="3" fill="none" stroke-linecap="round" marker-end="url(#arrow)"/>'
    # An external rear flue carries gases upwards; highlight it as a diagrammatic route.
    s+=box(218,3,0,20,24,205,c['side'],c['side'],c['stone'])
    s+=f'<path d="M444 270 Q469 245 463 198 V105" fill="none" stroke="{c["accent"]}" stroke-width="3" marker-end="url(#arrow)"/>'
    labels=[(388,70,'RAISED FLOOR',353,140),(301,409,'TILE SUPPORTS',306,312),(35,437,'FURNACE',51,340)]
    for x,y,text,xx,yy in labels:
        s+=f'<path d="M{x} {y-7} L{xx} {yy}" fill="none" stroke="{c["muted"]}" stroke-width="1"/><circle cx="{xx}" cy="{yy}" r="2.5" fill="{c["accent"]}"/><text x="{x}" y="{y}" fill="{c["ink"]}" font-family="DM Sans" font-size="11" letter-spacing="1.1">{text}</text>'
    s+='</g></svg>'
    return s

def build(out):
    # Download free font files at build time; the actual snapshots use local files.
    assets=out/'shared-assets';assets.mkdir(parents=True,exist_ok=True)
    req=urllib.request.Request('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;700&family=Libre+Baskerville:ital,wght@0,400;1,400&display=block')
    css=urllib.request.urlopen(req,timeout=30).read().decode()
    for i,url in enumerate(dict.fromkeys(re.findall(r'https://[^)]+',css))):
        name=f'font-{i}.ttf';urllib.request.urlretrieve(url,assets/name);css=css.replace(url,f'assets/{name}')
    paths=[]
    for slug,c in STYLES.items():
        folder=out/slug;folder.mkdir(exist_ok=True);shutil.copytree(assets,folder/'assets',dirs_exist_ok=True)
        title={'01-cinematic':'The heat<br><em>beneath Rome.</em>','02-field-journal':'A Roman secret,<br><em>under your feet.</em>','03-editorial':'HOT FLOOR.<br><span>NO ELECTRICITY.</span>','04-blueprint':'ROMAN<br>UNDERFLOOR<br><em>HEATING.</em>'}[slug]
        subtitle={'01-cinematic':'FIRE → HOT AIR → A WARM FLOOR','02-field-journal':'Field study  /  The hypocaust','03-editorial':'THE ROMANS HAD A SYSTEM FOR THAT.','04-blueprint':'SYSTEM STUDY 001 / HYPOCAUST'}[slug]
        top=f'<div class="mast"><b>H / HISTOLD</b><span>{c["label"]}</span></div>'
        extra=''
        if slug=='02-field-journal':extra='<div class="stamp">PLATE<br>XVII</div><div class="scribble">Heat the space below.<br>Warm the room above.</div>'
        if slug=='03-editorial':extra='<div class="seal">ROMAN<br>ENGINEERING</div><div class="tag">LOOK UNDERNEATH ↓</div>'
        if slug=='04-blueprint':extra='<div class="scale">0 ━━━━━ 1 ━━━━━ 2<br>SCHEMATIC / NOT TO SCALE</div>'
        common=f'''*{{box-sizing:border-box}}html,body{{margin:0;width:540px;height:960px;overflow:hidden}}body{{background:{c['bg']};color:{c['ink']};font-family:'DM Sans',sans-serif}}#film{{position:relative;width:540px;height:960px;overflow:hidden;padding:38px 38px}}.mast{{display:flex;justify-content:space-between;align-items:center;font-size:10px;letter-spacing:1.6px;padding-bottom:18px;border-bottom:1px solid {c['muted']}66}}.mast span{{font-size:8px;letter-spacing:1.3px}}h1{{font-family:'Libre Baskerville',serif;font-size:43px;line-height:1.18;letter-spacing:-1.8px;font-weight:400;margin:45px 0 0}}h1 em{{color:{c['accent']};font-weight:400}}.sub{{font-size:11px;letter-spacing:1.4px;line-height:1.7;margin-top:22px}}.art{{position:absolute;left:0;top:280px;width:540px;height:520px}}.art svg{{width:100%;height:100%}}.bottom{{position:absolute;left:38px;right:38px;top:814px;border-top:1px solid {c['muted']}66;padding-top:19px}}.bottom strong{{font-size:22px;font-weight:400;line-height:1.35;display:block;max-width:440px}}.bottom small{{display:block;font-size:9px;letter-spacing:1px;margin-top:20px;color:{c['muted']}}}'''
        style={
        '01-cinematic':'''#film{background:radial-gradient(ellipse at 50% 54%,#6f3e232b,transparent 60%)}h1{font-size:47px;margin-top:44px}.art{top:290px;filter:drop-shadow(0 18px 28px #0007)}.bottom strong{font-family:'Libre Baskerville';font-style:italic;font-size:23px}.mast b{color:#ec9c54}''',
        '02-field-journal':'''#film{background:repeating-linear-gradient(0deg,#806d3a06 0px,#806d3a06 1px,transparent 1px,transparent 5px)}#film:after{content:'';position:absolute;inset:17px;border:1px solid #8f7f5b44;pointer-events:none}h1{font-size:36px;line-height:1.3;margin-top:48px}.art{top:315px;filter:sepia(.7)}.sub{font-family:'Libre Baskerville';font-style:italic;letter-spacing:0;font-size:15px}.stamp{position:absolute;right:36px;top:262px;border:1px solid #a44932;padding:12px 14px;transform:rotate(7deg);font-size:12px;line-height:1.2;color:#a44932;letter-spacing:2px}.scribble{position:absolute;top:750px;left:45px;font-family:'Libre Baskerville';font-style:italic;font-size:13px;line-height:1.5}.bottom{top:826px}.bottom strong{font-size:20px}''',
        '03-editorial':'''h1{font-family:'DM Sans';font-weight:700;font-size:58px;letter-spacing:-4px;line-height:.94;margin-top:44px;position:relative;z-index:2}h1 span{font-size:40px;letter-spacing:-2px;background:#1f2727;color:#f5eed8;padding:1px 6px;box-decoration-break:clone;-webkit-box-decoration-break:clone}.sub{font-weight:700;font-size:12px;max-width:310px;margin-top:22px}.art{top:310px;transform:rotate(-7deg) scale(.98);background:#f5eed8;clip-path:polygon(4% 8%,96% 0,99% 91%,2% 99%)}.seal{position:absolute;right:30px;top:265px;z-index:4;background:#e94a31;color:#f5eed8;border-radius:50%;width:100px;height:100px;display:flex;justify-content:center;align-items:center;text-align:center;font-size:12px;font-weight:700;transform:rotate(12deg);line-height:1.2}.tag{position:absolute;left:32px;top:740px;background:#1f2727;color:#f5eed8;font-size:12px;letter-spacing:1px;padding:10px 14px;transform:rotate(-4deg);z-index:4}.bottom{border-top:3px solid #1f2727}.bottom strong{font-weight:700;font-size:25px}''',
        '04-blueprint':'''#film{background-image:linear-gradient(#b4d7e00d 1px,transparent 1px),linear-gradient(90deg,#b4d7e00d 1px,transparent 1px);background-size:24px 24px}#film:after{content:'';position:absolute;inset:16px;border:1px solid #d5e4dd55;pointer-events:none}h1{font-family:'DM Sans';font-weight:700;font-size:43px;line-height:1.02;letter-spacing:-1.2px;margin-top:35px}h1 em{font-style:normal}.sub{font-size:10px;letter-spacing:1.8px}.art{top:326px}.scale{position:absolute;top:282px;left:40px;font-size:10px;line-height:1.8;letter-spacing:1px;color:#a8cbd4}.bottom strong{font-size:22px;font-weight:400}.bottom{top:826px}'''}[slug]
        html=f'''<!doctype html><html><head><meta charset="utf-8"><title>{c['label']}</title><style>{css}{common}{style}</style></head><body><main id="film" data-composition-id="film" data-width="540" data-height="960" data-duration="1" data-no-timeline>{top}<h1>{title}</h1><div class="sub">{subtitle}</div><div class="art">{diagram(c)}</div>{extra}<div class="bottom"><strong>Hot air moved beneath a raised floor.</strong><small>ILLUSTRATED RECONSTRUCTION · ROMAN HYPOCAUST</small></div></main></body></html>'''
        (folder/'index.html').write_text(html);paths.append(folder)
    (out/'source.json').write_text(json.dumps({'topic':'Roman hypocaust underfloor heating','source':SOURCE,'styles':list(STYLES),'paid_api_calls':0},indent=2))
    assert len(paths)==4
    return paths

def render(out,cli):
    from PIL import Image,ImageDraw
    metrics=[]
    for folder in build(out):
        start=time.monotonic();frames=folder/'frames'
        with (folder/'snapshot.log').open('w') as log:
            subprocess.run([cli,'snapshot',str(folder),'--at','0.5','--no-end','--zoom','#film','--zoom-scale','2','--no-browser-gpu','--output',str(frames)],stdout=log,stderr=subprocess.STDOUT,check=True)
        candidates=sorted(frames.glob('*.png'));assert candidates,folder
        final=out/f'{folder.name}.png';shutil.copyfile(candidates[0],final)
        with Image.open(final) as image:assert image.size==(1080,1920),image.size
        metrics.append({'style':folder.name,'snapshot_seconds':round(time.monotonic()-start,2),'bytes':final.stat().st_size})
    # A 2×2 contact sheet preserves readability when shown inline.
    sheet=Image.new('RGB',(1080,2020),'#112923');draw=ImageDraw.Draw(sheet)
    for i,(slug,c) in enumerate(STYLES.items()):
        x=(i%2)*540;y=(i//2)*1010
        draw.text((x+18,y+16),f'{i+1}. '+c['label'],fill='#f4e9cf')
        sheet.paste(Image.open(out/f'{slug}.png').resize((540,960)),(x,y+50))
    sheet.save(out/'four-styles.jpg',quality=94)
    (out/'metrics.json').write_text(json.dumps({'styles':metrics,'paid_api_calls':0,'render_host':'GitHub Actions' if __import__('os').environ.get('GITHUB_ACTIONS') else 'local'},indent=2))
    print(json.dumps(metrics,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='output/roman-style-preview');parser.add_argument('--cli',default='hyperframes');parser.add_argument('--build-only',action='store_true');args=parser.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.build_only:build(out)
    else:render(out,args.cli)
