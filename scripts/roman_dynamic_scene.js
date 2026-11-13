import * as THREE from './three.module.js';

const W=540,H=960,root=document.querySelector('#film');
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,preserveDrawingBuffer:true,powerPreference:'high-performance'});
renderer.setSize(W,H);renderer.setPixelRatio(window.devicePixelRatio||1);
renderer.setClearColor(0x102124);renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=.9;
root.prepend(renderer.domElement);
const scene=new THREE.Scene();scene.fog=new THREE.FogExp2(0x102124,.024);
const camera=new THREE.PerspectiveCamera(48,W/H,.035,80);
scene.add(new THREE.HemisphereLight(0xe6f2ef,0x413026,2.25));
const key=new THREE.DirectionalLight(0xffe2b3,3.0);key.position.set(-4,8,5);scene.add(key);
const fill=new THREE.DirectionalLight(0xa0d9df,.9);fill.position.set(5,4,-3);scene.add(fill);
const flameLight=new THREE.PointLight(0xff982d,9,5,1.3);flameLight.position.set(-4.5,.7,1.0);scene.add(flameLight);
const underLight=new THREE.PointLight(0xf5a23e,5,7,1.2);underLight.position.set(0,.7,0);scene.add(underLight);
const mat=c=>new THREE.MeshLambertMaterial({color:c});
const stone=mat(0x79624e),earth=mat(0x493d31),clay=mat(0xad6845),plaster=mat(0xe4d4b7),red=mat(0x803b2b),black=mat(0x211916);
function box(w,h,d,x,y,z,m,parent=scene){const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),m);mesh.position.set(x,y,z);parent.add(mesh);return mesh;}
box(6.5,.24,4.5,0,-.16,0,earth);
box(25,.1,25,0,-.39,0,mat(0x102124));
// Tile stacks are instanced to keep software rendering practical on the Actions runner.
const brickGeo=new THREE.BoxGeometry(.40,.115,.40),columns=new THREE.InstancedMesh(brickGeo,clay,160);
const dummy=new THREE.Object3D();let n=0;
for(const x of [-2.3,-.8,.8,2.3])for(const z of [-1.5,-.5,.5,1.5])for(let k=0;k<10;k++){dummy.position.set(x,.065+k*.133,z);dummy.rotation.y=(k%2)*.035;dummy.updateMatrix();columns.setMatrixAt(n++,dummy.matrix);}
scene.add(columns);
const floor=new THREE.Group();scene.add(floor);
box(6.1,.16,4.1,0,1.43,0,stone,floor);
const tiles=new THREE.InstancedMesh(new THREE.BoxGeometry(.741,.055,.653),mat(0xbd8152),48);let q=0;
for(let x=0;x<8;x++)for(let z=0;z<6;z++){dummy.position.set((x-3.5)*.754,1.54,(z-2.5)*.668);dummy.rotation.set(0,0,0);dummy.updateMatrix();tiles.setMatrixAt(q,dummy.matrix);tiles.setColorAt(q,new THREE.Color().setHSL(.075+(x%3)*.009,.33,.46+((x+z)%4)*.025));q++;}floor.add(tiles);
const mosaic=new THREE.Group();floor.add(mosaic);
for(let x=0;x<5;x++)for(let z=0;z<5;z++){const t=box(.23,.012,.23,(x-2)*.235,1.574,(z-2)*.235,mat((x+z)%2?0xf0d6a3:0x534633),mosaic);}
// Cutaway back wall; the open front lets the viewer see the mechanism.
const walls=new THREE.Group();scene.add(walls);
box(5.5,2.15,.18,-.25,2.62,-2.04,plaster,walls);
box(5.5,.62,.20,-.25,1.87,-2.025,red,walls);
box(5.5,.07,.24,-.25,2.22,-2.0,mat(0xe4bd78),walls);
// External furnace with an open mouth and an arched terracotta surround.
box(1.38,.26,1.0,-4.55,.03,.8,stone);
box(.29,1.13,1,-5.10,.61,.8,clay);box(.29,1.13,1,-4.00,.61,.8,clay);
box(1.15,.24,1,-4.55,1.17,.8,clay);
box(.90,.91,.045,-4.55,.49,.35,black);
const arch=new THREE.Mesh(new THREE.TorusGeometry(.42,.11,6,20,Math.PI),clay);arch.position.set(-4.55,.63,1.325);scene.add(arch);
box(.20,.64,.14,-4.98,.30,1.32,clay);box(.20,.64,.14,-4.12,.30,1.32,clay);
const flameShape=new THREE.Shape();flameShape.moveTo(-.3,0);flameShape.bezierCurveTo(-.5,.3,.03,.55,-.04,.95);flameShape.bezierCurveTo(.3,.73,.16,.47,.33,.60);flameShape.bezierCurveTo(.65,.18,.24,-.13,-.3,0);
const flames=[];
for(let i=0;i<3;i++){const m=new THREE.Mesh(new THREE.ShapeGeometry(flameShape),new THREE.MeshBasicMaterial({color:[0xf17d24,0xffbb4e,0xffe6a0][i],side:THREE.DoubleSide,transparent:true,opacity:.9}));m.position.set(-4.55,.18,1.04+i*.013);m.scale.setScalar(1-i*.20);scene.add(m);flames.push(m);}
box(1.10,.10,.9,-3.47,.04,.2,stone);box(1.10,.12,.9,-3.47,.90,.2,stone);
// Open-front wall flue: show the rising route without sending gas into the room.
const flue=new THREE.Group();scene.add(flue);
box(.12,3.9,.55,2.49,1.95,-1.7,clay,flue);box(.12,3.9,.55,2.99,1.95,-1.7,clay,flue);box(.62,3.9,.12,2.74,1.95,-1.99,clay,flue);
const route=new THREE.CatmullRomCurve3([new THREE.Vector3(-4.55,.42,.65),new THREE.Vector3(-3.1,.55,.0),new THREE.Vector3(-1.8,.60,0),new THREE.Vector3(.2,.58,0),new THREE.Vector3(2.65,.65,-.15),new THREE.Vector3(2.74,.70,-1.7),new THREE.Vector3(2.74,3.95,-1.7)]);
const packets=new THREE.InstancedMesh(new THREE.SphereGeometry(.016,6,4),new THREE.MeshBasicMaterial({color:0xffd485}),72);scene.add(packets);
const trail=new THREE.Mesh(new THREE.TubeGeometry(route,90,.008,4,false),new THREE.MeshBasicMaterial({color:0xf4b45c,transparent:true,opacity:.22}));scene.add(trail);
const wisps=new THREE.Group();scene.add(wisps);
for(let i=0;i<7;i++){const curve=new THREE.CatmullRomCurve3([new THREE.Vector3((i-3)*.65,1.59,.4),new THREE.Vector3((i-3)*.65+.06,1.90,.4),new THREE.Vector3((i-3)*.65-.06,2.15,.4)]);wisps.add(new THREE.Mesh(new THREE.TubeGeometry(curve,12,.012,3,false),new THREE.MeshBasicMaterial({color:0xffd098,transparent:true,opacity:.40})));}
const shots=window.ROMAN_SHOTS;
const cue=document.querySelector('#cue'),sub=document.querySelector('#subcue');
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));const ease=v=>v*v*(3-2*v);
const vector=(a,b,t)=>new THREE.Vector3(...a).lerp(new THREE.Vector3(...b),t);
function renderAt(t){
 let index=shots.findIndex((s,i)=>t>=s.start&&(i===shots.length-1||t<shots[i+1].start));if(index<0)index=0;
 const s=shots[index],end=index+1<shots.length?shots[index+1].start:window.ROMAN_DURATION,u=clamp((t-s.start)/(end-s.start)),e=ease(u);
 camera.position.copy(vector(s.from,s.to,e));const target=vector(s.look,s.lookTo||s.look,e);camera.lookAt(target);camera.fov=s.fov||48;camera.updateProjectionMatrix();
 floor.position.y=index===1?ease(clamp((t-s.start)/1.05))*1.25:index===9?.55:0;
 walls.visible=![4,5,6].includes(index);
 wisps.visible=[0,7,10].includes(index);
 wisps.children.forEach((m,i)=>{m.position.y=.13*Math.sin(t*1.9+i);m.material.opacity=.18+.18*(.5+.5*Math.sin(t*2+i));});
 flames.forEach((m,i)=>{m.scale.set((1-i*.20)*(1+.08*Math.sin(t*7+i)),(1-i*.20)*(1+.13*Math.sin(t*11+i*2)),1);m.rotation.z=.045*Math.sin(t*8+i);});
 flameLight.intensity=8+1.4*Math.sin(t*13)+.5*Math.sin(t*21);underLight.intensity=index>=7?7:4.5;
 for(let i=0;i<72;i++){const progress=((t*.22+i/72)%1+1)%1;const pos=route.getPointAt(progress);dummy.position.copy(pos);dummy.scale.setScalar(camera.position.distanceTo(pos)<.70?0:1+.3*Math.sin(i*2+t*8));dummy.rotation.set(0,0,0);dummy.updateMatrix();packets.setMatrixAt(i,dummy.matrix);}packets.instanceMatrix.needsUpdate=true;
 packets.visible=t>2.1;trail.visible=t>2.1&&![4,5,6].includes(index);
 tiles.material.emissive.setHex([0,7,10].includes(index)?0x5e2707:0x160b05);tiles.material.emissiveIntensity=[0,7,10].includes(index)?.7:.15;
 const show=clamp((t-s.start)/.18)*clamp((Math.min(2.1,end-s.start-.15)-(t-s.start))/.22);
 cue.textContent=s.cue;sub.textContent=s.note||'';cue.style.opacity=show;sub.style.opacity=show;cue.style.transform=`translateY(${(1-show)*8}px)`;
 document.querySelector('#cutaway').style.opacity=index===1||index===9?1:.55;
 renderer.render(scene,camera);
 window.__shotDebug={index,time:t,position:camera.position.toArray(),target:target.toArray(),triangles:renderer.info.render.triangles};
}
window.__romanRender=renderAt;window.addEventListener('hf-seek',e=>renderAt(e.detail.time));renderAt(0);
