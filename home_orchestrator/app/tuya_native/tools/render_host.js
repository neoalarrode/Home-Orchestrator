// Render-runtime harness: carga view.js (motor de render) + los chunks .rjs (encoders)
// y extrae los encoders para invocarlos headless. Complementa host5.js (service).
const fs=require('fs'), vm=require('vm'), path=require('path');
const {JSDOM}=require('jsdom');
const HERE=__dirname, FW=path.join(HERE,'fw'), P=path.join(HERE,'panel');
const calls=[]; const log=s=>{if(calls.length<2000)calls.push(s);};
function bridge(name){ const fn=function(){return bridge(name+"()");}; return new Proxy(fn,{get(t,p){if(typeof p==="symbol"){if(p===Symbol.toPrimitive)return()=>"";return t[p];}if(p==="then")return undefined;if(name==="ty"&&_ov[p]!==undefined)return _ov[p];return bridge(name+"."+String(p));},apply(){return fn();}}); }
const _sys={platform:"android",SDKVersion:"2.30.27",language:"es",screenWidth:1080,screenHeight:1920,windowWidth:1080,windowHeight:1920,pixelRatio:3,statusBarHeight:24,safeArea:{top:24,bottom:1920,left:0,right:1080,width:1080,height:1896}};
const _ov={getSystemInfoSync:()=>_sys,getSystemInfo:o=>{o&&o.success&&o.success(_sys);return _sys},getAppBaseInfo:()=>_sys,getWindowInfo:()=>_sys,getLogManager:()=>new Proxy({},{get:()=>function(){}})};
const dom=new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>',{runScripts:"outside-only",pretendToBeVisual:true,url:"https://localhost/"});
const win=dom.window, ctx=dom.getInternalVMContext();
for(const g of ["gzlJSBridge","ViewJSBridge","ServiceJSBridge","nativeRequest","mothra","releaseApi","onViewLoad","setGZLEventHandler","slots","__nativeCall__","requestAnimationFrame"]) win[g]=bridge(g);
win.ty=bridge("ty"); win.__appCode__={}; win.__currentPath__=""; win.getNativeKits=function(){return {P2PKit:{},DeviceKit:{}};};
win.createElement=function(){return {};}; win.h=win.createElement; win.React={createElement:win.createElement,Fragment:{},useContext:()=>({}),useState:x=>[x,function(){}],useEffect:function(){},useMemo:(f)=>f&&f(),useCallback:f=>f,useRef:()=>({current:null})};
win.$reportError=e=>log("reportError:"+String(e&&e.message||e).split('\n')[0]);
function run(file,fp){ try{ vm.runInContext(fs.readFileSync(fp,'utf8'),ctx,{filename:file}); return true; }catch(e){ log("ERR["+file+"]:"+String(e.message||e).split('\n')[0]); return false; } }
win.Worker=class{constructor(){this.onmessage=null;}postMessage(){}terminate(){}addEventListener(){}removeEventListener(){}};
win.MessageChannel=class{constructor(){this.port1={postMessage(){},onmessage:null,close(){},start(){}};this.port2=this.port1;}};
win.MessagePort=win.MessagePort||function(){};
win.importScripts=function(){};
win.requestIdleCallback=win.requestIdleCallback||function(cb){return setTimeout(cb,0);};
// 1) motor de render
run("view.js",path.join(FW,"view.js"));
console.log("defineRenderScript=",typeof win.defineRenderScript,"| requireRenderScript=",typeof win.requireRenderScript,"| __renderScriptModules__=",typeof win.__renderScriptModules__);
// 2) cargar TODOS los chunks del render (dependencias entre .rjs/.tpl)
const ENC="c-a6f984cf.rjs.js";
const chunks=fs.readdirSync(P).filter(f=>/\.(rjs|tpl)\.js$/.test(f));
const deps=chunks.filter(f=>f.startsWith('chunk-')), rest=chunks.filter(f=>!f.startsWith('chunk-'));
  for(const f of deps.concat(rest)) run(f,path.join(P,f));
  for(const f of deps.concat(rest)) run(f,path.join(P,f));
console.log("chunks render cargados:", chunks.length);
// 3) requerir y ejecutar
let mod=null; try{ mod=win.requireRenderScript && win.requireRenderScript(ENC); }catch(e){ console.log("requireRenderScript ERR:",e.message); }
console.log("mod?", mod?("keys="+Object.keys(mod).length):mod);
if(win.__renderScriptModules__){ try{ console.log("módulos render registrados:", (Array.isArray(win.__renderScriptModules__)?win.__renderScriptModules__:Object.keys(win.__renderScriptModules__)).slice(0,10)); }catch(e){} }
if(mod){
  const enc=Object.keys(mod).filter(k=>/^(encode|request)/.test(k));
  console.log("encoders ("+enc.length+"):", enc.slice(0,60).join(", "));
  try{ const fr=mod.encodeRoomClean0x14({version:"1.0.0",roomIds:[4],cleanTimes:1});
    let hex; try{hex=Buffer.from(fr).toString("hex");}catch(e){hex=JSON.stringify(fr);}
    console.log(">>> encodeRoomClean0x14([4],1) => len",fr&&fr.length,"=>",(hex||"").slice(0,220)); }catch(e){ console.log("encode ERR:",e.message); }
}
console.log("errores:", calls.filter(c=>/ERR/.test(c)).slice(0,8));

// DEBUG extracción
try{
  const raw=win.requireRenderScript(ENC);
  console.log("raw type:",typeof raw, raw&&Object.keys(raw).slice(0,8));
  const d=raw&&raw.default; console.log("raw.default:",typeof d, d&&Object.keys(d).filter(k=>/^(encode|request)/.test(k)).slice(0,20));
  const M=win.__renderScriptModules__;
  const arr=Array.isArray(M)?M:Object.values(M);
  const hit=arr.find(m=>m&&(m.id===ENC||m.name===ENC||m.path&&m.path.includes("a6f984cf")));
  console.log("store entry keys:", hit?Object.keys(hit):"(no)");
  if(hit){ const ex=hit.exports||hit.module&&hit.module.exports||hit.factory; console.log("hit.exports:",typeof ex, ex&&Object.keys(ex).filter(k=>/encode/.test(k)).slice(0,10)); }
}catch(e){ console.log("DBG ERR:",e.message); }
