import * as T from './three.module.js';
const P=window.THREE_PLAN,W=540,H=960;
const renderer=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true,powerPreference:'high-performance'});
renderer.setPixelRatio(P.render_scale||1);renderer.setSize(W,H);renderer.setClearColor(P.background||'#102124');renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=.95;
document.querySelector('#film').prepend(renderer.domElement);
const scene=new T.Scene(),camera=new T.PerspectiveCamera(50,W/H,.035,600);
scene.add(new T.HemisphereLight(0xe7f2ec,0x47352b,2.2));const key=new T.DirectionalLight(0xffe0b2,2.8);key.position.set(-8,14,8);scene.add(key);const fill=new T.DirectionalLight(0xa5dbe1,.9);fill.position.set(8,6,-6);scene.add(fill);
const materials=new Map(),objects=new Map(),flows=[],baseTransforms=new Map();
function material(color){if(!materials.has(color))materials.set(color,new T.MeshLambertMaterial({color}));return materials.get(color);}
function mesh(geometry,color,parent,x=0,y=0,z=0){const m=new T.Mesh(geometry,material(color));m.position.set(x,y,z);parent.add(m);return m;}
function box(parent,w,h,d,x,y,z,c){return mesh(new T.BoxGeometry(w,h,d),c,parent,x,y,z);}
function channel(g,w,h,d,c,y=0){box(g,w,.12,d,0,y+.06,0,c);box(g,w,h,.10,0,y+h/2,d/2,c);box(g,w,h,.10,0,y+h/2,-d/2,c);box(g,w-.08,.018,d-.16,0,y+.15,0,'#58b9c8');}
function arch(g,width,height,depth,spans,tiers,color){
 const cell=width/spans,level=height/tiers,r=cell*.42,band=Math.max(.08,cell*.12),pier=Math.max(.08,cell*.16),stem=Math.max(.12,level-r);
 const s=new T.Shape();s.moveTo(-r,0);s.absarc(0,0,r,Math.PI,0,true);s.lineTo(r-band,0);s.absarc(0,0,r-band,0,Math.PI,false);s.closePath();
 const geometry=new T.ExtrudeGeometry(s,{depth,bevelEnabled:false,curveSegments:12});geometry.translate(0,0,-depth/2);
 for(let row=0;row<tiers;row++){
  for(let i=0;i<spans;i++)mesh(geometry,color,g,-width/2+cell*(i+.5),row*level+stem,0);
  for(let i=0;i<=spans;i++)box(g,pier,stem,depth,-width/2+cell*i,row*level+stem/2,0,color);
  box(g,width+cell*.12,.15,depth,0,(row+1)*level+.06,0,color);
 }
 channel(g,width+cell*.1,.32,depth*.7,color,height+.13);
}
function composite(o,g){const [w,h,d]=o.size,c=o.color||'#b79570';
 switch(o.kind){
 case 'arch_bridge':arch(g,w,h,d,o.spans||4,o.tiers||1,c);break;
 case 'building':box(g,w,.15,d,0,.075,0,c);box(g,w,h,.15,0,h/2,-d/2,c);box(g,.15,h,d,-w/2,h/2,0,c);box(g,w,.12,d,0,h,0,c);break;
 case 'water_channel':channel(g,w,h,d,c);break;
 case 'wheel':{const m=mesh(new T.TorusGeometry(w/2,Math.min(w*.06,.16),6,28),c,g);for(let i=0;i<6;i++){const a=i*Math.PI/3,m=box(g,w*.85,.06,.08,0,0,0,c);m.rotation.z=a;}mesh(new T.CylinderGeometry(w*.08,w*.08,d,10),c,g).rotation.x=Math.PI/2;break;}
 case 'person':{box(g,w*.45,h*.42,d*.5,0,h*.55,0,c);mesh(new T.SphereGeometry(w*.21,10,8),'#c8a17e',g,0,h*.88,0);box(g,w*.16,h*.35,d*.23,-w*.13,h*.19,0,'#78664f');box(g,w*.16,h*.35,d*.23,w*.13,h*.19,0,'#78664f');break;}
 case 'tree':mesh(new T.CylinderGeometry(w*.06,w*.09,h*.55,7),'#77523a',g,0,h*.27,0);mesh(new T.ConeGeometry(w*.55,h*.8,8),c,g,0,h*.72,0);break;
 case 'boat':{box(g,w,h*.4,d*.65,0,h*.25,0,c);const hull=mesh(new T.CylinderGeometry(d*.42,d*.18,w,4,1,false),c,g,0,h*.23,0);hull.rotation.z=Math.PI/2;box(g,.05,h*1.5,.05,0,h*.9,0,'#8b6546');const sail=mesh(new T.PlaneGeometry(w*.45,h*.9),'#e5d6b7',g,w*.12,h*1.15,0);sail.material.side=T.DoubleSide;break;}
 case 'bowl':{const points=[new T.Vector2(w*.16,0),new T.Vector2(w*.23,h*.08),new T.Vector2(w*.4,h*.5),new T.Vector2(w*.5,h)];mesh(new T.LatheGeometry(points,24),c,g).material.side=T.DoubleSide;break;}
 default:{let geo;
  switch(o.kind){case 'sphere':geo=new T.SphereGeometry(.5,16,10);break;case 'cylinder':geo=new T.CylinderGeometry(.5,.5,1,16);break;case 'cone':geo=new T.ConeGeometry(.5,1,12);break;case 'torus':geo=new T.TorusGeometry(.4,.08,6,24);break;case 'plane':geo=new T.PlaneGeometry(1,1);break;default:geo=new T.BoxGeometry(1,1,1);}
  const m=mesh(geo,c,g);m.scale.set(w,h,d);if(o.kind==='plane')m.material.side=T.DoubleSide;
 }
 }
}
for(const o of P.objects){const g=new T.Group();g.position.fromArray(o.position||[0,0,0]);g.rotation.fromArray([...(o.rotation||[0,0,0]),'XYZ']);scene.add(g);objects.set(o.id,g);
 if(o.kind==='flow'){
  const curve=new T.CatmullRomCurve3(o.points.map(v=>new T.Vector3(...v)));const count=o.count||35,radius=o.radius||.02;
  const particles=new T.InstancedMesh(new T.SphereGeometry(radius,6,4),new T.MeshBasicMaterial({color:o.color||'#89ddeb'}),count);g.add(particles);flows.push({curve,particles,count,speed:o.speed||.2,group:g});
 }else composite(o,g);
 baseTransforms.set(o.id,{position:g.position.clone(),rotation:g.rotation.clone(),scale:g.scale.clone()});
}
const dummy=new T.Object3D(),clamp=v=>Math.max(0,Math.min(1,v)),ease=v=>v*v*(3-2*v),vec=(a,b,t)=>new T.Vector3(...a).lerp(new T.Vector3(...b),t);
function renderAt(t){
 let i=P.shots.findIndex((s,j)=>t>=s.start&&(j===P.shots.length-1||t<P.shots[j+1].start));if(i<0)i=0;
 const shot=P.shots[i],end=P.shots[i+1]?.start??P.duration,elapsed=t-shot.start,u=ease(clamp(elapsed/(end-shot.start)));
 camera.position.copy(vec(shot.from,shot.to,u));camera.lookAt(vec(shot.look,shot.lookTo||shot.look,u));camera.fov=shot.fov||50;camera.updateProjectionMatrix();
 for(const [id,g] of objects){const b=baseTransforms.get(id);g.position.copy(b.position);g.rotation.copy(b.rotation);g.scale.copy(b.scale);}
 for(const a of P.animations||[]){if(t<a.start)continue;const u=clamp((t-a.start)/(a.end-a.start));objects.get(a.object)[a.property][a.axis]=a.from+(a.to-a.from)*u;}
 for(const f of flows){for(let k=0;k<f.count;k++){const p=f.curve.getPointAt(((t*f.speed+k/f.count)%1+1)%1),world=f.group.localToWorld(p.clone());dummy.position.copy(p);dummy.scale.setScalar(camera.position.distanceTo(world)<.6?0:1);dummy.updateMatrix();f.particles.setMatrixAt(k,dummy.matrix);}f.particles.instanceMatrix.needsUpdate=true;}
 const alpha=clamp(elapsed/.18)*clamp((Math.min(1.9,end-shot.start-.1)-elapsed)/.2);
 const cue=document.querySelector('#cue'),note=document.querySelector('#note');cue.textContent=shot.cue||'';note.textContent=shot.note||'';cue.style.opacity=alpha;note.style.opacity=alpha;
 document.querySelector('#locator').style.opacity=t<2.8?1:0;
 renderer.render(scene,camera);window.__threeDebug={shot:i,camera:camera.position.toArray(),triangles:renderer.info.render.triangles};
}
window.__threeRender=renderAt;window.addEventListener('hf-seek',event=>renderAt(event.detail.time));renderAt(0);
