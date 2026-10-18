import{n as e,s as t,t as n}from"./react-jqdG9Xun.js";import{Ar as r,cr as i,jr as a,kt as o,x as s}from"./vanilla-DJGUO-iM.js";import{t as c}from"./proxy-B14B1eZA.js";import{a as l,s as u}from"./react-three-fiber.esm-CdPjKGo4.js";import{t as d}from"./View-CSlz1t8h.js";import{t as f}from"./OrbitControls-Dr4E-YCi.js";function p(e,t,n,s){var c;return c=class extends i{constructor(i){super({vertexShader:t,fragmentShader:n,...i});for(let t in e)this.uniforms[t]=new r(e[t]),Object.defineProperty(this,t,{get(){return this.uniforms[t].value},set(e){this.uniforms[t].value=e}});this.uniforms=a.clone(this.uniforms),s?.(this)}},c.key=o.generateUUID(),c}var m=t(n(),1),h=e();l({SpectralMaterial:p({uTime:0,uColor:new s(`#ff0000`),uIntensity:.5,uNoiseFreq:2,uPulse:0},`
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vPosition;
  uniform float uTime;
  uniform float uNoiseFreq;
  uniform float uPulse;

  float hash(float n) { return fract(sin(n) * 43758.5453123); }
  float noise(vec3 x) {
    vec3 p = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    float n = p.x + p.y * 57.0 + 113.0 * p.z;
    return mix(mix(mix(hash(n + 0.0), hash(n + 1.0), f.x),
                   mix(hash(n + 57.0), hash(n + 58.0), f.x), f.y),
               mix(mix(hash(n + 113.0), hash(n + 114.0), f.x),
                   mix(hash(n + 170.0), hash(n + 171.0), f.x), f.y), f.z);
  }

  void main() {
    vUv = uv;
    vNormal = normalize(normalMatrix * normal);
    vPosition = position;
    float n = noise(position * uNoiseFreq + uTime * 0.5);
    vec3 pos = position + normal * n * 0.12 * (1.0 + uPulse);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
  }
  `,`
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vPosition;
  uniform vec3 uColor;
  uniform float uIntensity;
  uniform float uTime;

  void main() {
    float fresnel = pow(1.5 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 3.0);
    vec3 color = uColor * (fresnel + 0.2);
    float scanline = sin(vPosition.y * 45.0 - uTime * 5.0) * 0.08;
    gl_FragColor = vec4(color + scanline, fresnel * uIntensity);
  }
  `)});function g({isOnline:e,status:t,systemPulse:n,themeColor:r,vitals:i}){let a=(0,m.useRef)(),c=(0,m.useRef)(),l=(0,m.useMemo)(()=>new s(typeof r==`string`&&r.startsWith(`rgba`)?r.replace(/rgba?\((\d+),\s*(\d+),\s*(\d+).*\)/,`rgb($1,$2,$3)`):r),[r]);return u(()=>{if(!c.current)return;let e=performance.now()/1e3;c.current.uTime=e,c.current.uIntensity=o.lerp(c.current.uIntensity,n?.9:.4,.05),c.current.uColor.lerp(l,.08),c.current.uPulse=Math.sin(e*3.5)*.05;let r=.005+(i?.cpu_load?parseFloat(i.cpu_load):0)/1e3;a.current&&(a.current.rotation.y+=t===`thinking`?r*3:r)}),(0,m.useEffect)(()=>{let e=a.current;return()=>{e&&(e.geometry.dispose(),e.material&&e.material.dispose())}},[]),(0,h.jsxs)(`mesh`,{ref:a,rotation:[.4,.2,0],children:[(0,h.jsx)(`sphereGeometry`,{args:[2.5,64,64]}),(0,h.jsx)(`spectralMaterial`,{ref:c,transparent:!0,depthWrite:!1,blending:2})]})}function _({isOnline:e=!1,status:t=`idle`,systemPulse:n=!1,themeColor:r=`#00f0ff`,vitals:i,minimal:a=!1}){let o=a?60:100;return(0,h.jsxs)(c.div,{initial:{opacity:0,x:-50},animate:{opacity:.9,x:0},className:`relative z-[120] pointer-events-none flex flex-col items-center ${a?`gap-0`:`gap-4`}`,children:[(0,h.jsxs)(`div`,{className:`relative flex items-center justify-center rounded-full transition-shadow duration-1000 ${a?`border-none p-0`:`border p-1 shadow-[0_0_60px_rgba(var(--stark-glow-rgb),0.1)]`}`,style:{width:`${o}px`,height:`${o}px`,borderColor:a?`transparent`:`rgba(var(--stark-glow-rgb), 0.1)`},children:[!a&&(0,h.jsx)(c.div,{animate:{rotate:360},transition:{duration:25,repeat:1/0,ease:`linear`},className:`absolute inset-[-15px] border rounded-full`,style:{borderColor:`rgba(var(--stark-glow-rgb), 0.1)`},children:(0,h.jsx)(`div`,{className:`absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-black px-2 text-[5px] font-mono tracking-widest uppercase`,style:{color:`var(--stark-glow)`},children:`ZENITH_NAV_ACTIVE`})}),(0,h.jsx)(c.div,{animate:{scale:n?1.1:1,opacity:n?a?.3:.4:.1},className:`absolute inset-0 border rounded-full transition-colors duration-1000`,style:{borderColor:`var(--stark-glow)`}}),(0,h.jsx)(`div`,{style:{width:`${o}px`,height:`${o}px`},className:`flex items-center justify-center`,children:(0,h.jsxs)(d,{className:`w-full h-full`,children:[(0,h.jsx)(`ambientLight`,{intensity:.2}),(0,h.jsx)(g,{isOnline:e,status:t,systemPulse:n,themeColor:r,vitals:i}),(0,h.jsx)(f,{enableZoom:!1,enablePan:!1,autoRotate:!0,autoRotateSpeed:2})]})})]}),!a&&(0,h.jsxs)(`div`,{className:`flex flex-col items-center gap-1`,children:[(0,h.jsxs)(`span`,{className:`text-[5px] transition-colors duration-1000 font-mono tracking-[0.5em] uppercase`,style:{color:n?`var(--stark-glow)`:`rgba(var(--stark-glow-rgb), 0.3)`},children:[`Sector_Scan_`,t===`thinking`?`Deep`:i?.cpu_load?`LOAD_${i.cpu_load}`:`Standby`]}),(0,h.jsxs)(`div`,{className:`flex gap-1 items-center`,children:[(0,h.jsx)(`div`,{className:`w-1 h-1 rounded-full transition-all duration-1000`,style:{backgroundColor:n?`var(--stark-glow)`:`rgba(var(--stark-glow-rgb), 0.1)`,boxShadow:n?`0 0 8px var(--stark-glow)`:`none`}}),(0,h.jsx)(`div`,{className:`w-12 h-[0.5px]`,style:{backgroundImage:`linear-gradient(to right, rgba(var(--stark-glow-rgb), 0.4), transparent)`}})]})]})]})}export{_ as default};