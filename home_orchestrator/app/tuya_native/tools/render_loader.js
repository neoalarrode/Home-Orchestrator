// F4.1: mini render-loader fiel. Replica la semántica de require de view.js
// (args de factory: [require, module, exports, nativeApi, Promise, ...requireParams])
// resolviendo deps relativas contra el store __renderScriptModules__, para EJECUTAR
// el módulo de encoders de verdad y extraer encodeRoomClean0x14 (determinista).
const fs=require('fs'), vm=require('vm'), path=require('path');
const {JSDOM}=require('jsdom');
const HERE=__dirname, FW=path.join(HERE,'fw'), P=path.join(HERE,'panel');
function bridge(n){const f=function(){return bridge(n+"()");};return new Proxy(f,{get(t,p){if(typeof p==="symbol")return p===Symbol.toPrimitive?()=>"":t[p];if(p==="then")return undefined;return bridge(n+"."+String(p));},apply(){return f();}});}
const dom=new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>',{runScripts:"outside-only",pretendToBeVisual:true,url:"https://localhost/"});
const win=dom.window, ctx=dom.getInternalVMContext();
for(const g of ["gzlJSBridge","ViewJSBridge","ServiceJSBridge","nativeRequest","mothra","releaseApi","onViewLoad","setGZLEventHandler","slots","__nativeCall__","requestAnimationFrame"]) win[g]=bridge(g);
win.Worker=class{postMessage(){}terminate(){}addEventListener(){}removeEventListener(){}};
win.MessageChannel=class{constructor(){this.port1={postMessage(){},close(){},start(){}};this.port2=this.port1;}};
win.importScripts=function(){}; win.requestIdleCallback=cb=>setTimeout(cb,0); win.ty=bridge("ty"); win.__appCode__={}; win.$reportError=function(){};
vm.runInContext(fs.readFileSync(path.join(FW,"view.js"),'utf8'),ctx,{filename:'view.js'});
// cargar todos los chunks para que defineRenderScript los registre
const chunks=fs.readdirSync(P).filter(f=>/\.(rjs|tpl)\.js$/.test(f));
const deps=chunks.filter(f=>f.startsWith('chunk-')), rest=chunks.filter(f=>!f.startsWith('chunk-'));
for(let pass=0;pass<2;pass++) for(const f of deps.concat(rest)){ try{ vm.runInContext(fs.readFileSync(path.join(P,f),'utf8'),ctx,{filename:f}); }catch(e){} }
const STORE=win.__renderScriptModules__;
console.log("módulos registrados:", Object.keys(STORE).length);

// nativeApi permisivo (getAppApi) — respaldado por jsdom donde tiene sentido
const nativeApi=new Proxy({
  getSystemInfo:()=>({}), getSystemInfoSync:()=>({}),
  createElement:(t)=>win.document.createElement(t||"div"),
  getCanvasById:()=>null, getBoundingClientRectById:()=>({}), document:win.document, documentElement:win.document.documentElement, window:win,
},{ get(t,p){ if(p in t) return t[p]; return function(){return undefined;}; } });
const PromiseCtor=win.Promise||Promise;
if(!win.matchMedia) win.matchMedia=function(){return {matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}};};
function deepStub(){ const f=function(){return deepStub();}; return new Proxy(f,{ get(t,p){ if(p==="then")return undefined; if(typeof p==="symbol")return p===Symbol.toPrimitive?()=>0:t[p]; return deepStub(); }, apply(){return deepStub();} }); }
const env=new Proxy({},{ get(_,p){ if(typeof p==="symbol") return undefined;
  try{ if(win[p]!==undefined){ const v=win[p]; return typeof v==="function"?v.bind(win):v; } }catch(e){}
  try{ const d=win.document; if(d[p]!==undefined){ const v=d[p]; return typeof v==="function"?v.bind(d):v; } }catch(e){}
  return deepStub(); } });

const cache={};
function dirname(id){ const s=id.match(/(.*)\/([^\/]+)?$/); return s&&s[1]?s[1]:"."; }
function norm(base, dep){ if(!dep.startsWith(".")) return dep; const parts=(base==="."?[]:base.split("/")).concat(dep.split("/")); const out=[]; for(const p of parts){ if(p===""||p===".") continue; if(p==="..") out.pop(); else out.push(p); } return out.join("/"); }
function makeRequire(fromId){ const base=dirname(fromId); return function(dep){ const id=norm(base,dep); return load(id); }; }
function load(id){
  if(cache[id]) return cache[id].exports;
  const rec=STORE[id]; if(!rec){ throw new Error("no module "+id); }
  const module={exports:{}}; cache[id]=module;
  if(rec.factory){ try{ const extra=[]; for(let i=0;i<16;i++) extra.push(env); extra[2]=win.document; // idx global 7 = s = document
      const ret=rec.factory.apply(null,[makeRequire(id), module, module.exports, nativeApi, PromiseCtor].concat(extra)); if((!module.exports||Object.keys(module.exports).length===0)&&ret) module.exports=ret; }catch(e){ console.log("  [factory "+id+" throw toler.: "+e.message+"]"); } }
  return module.exports;
}
try{
  const ENC="c-a6f984cf.rjs.js";
  const ex=load(ENC);
  const keys=Object.keys(ex);
  console.log("ENC exports n=",keys.length,"keys:",keys.slice(0,10));
  for(const k of keys){ const v=ex[k]; console.log("  key",k,"->",typeof v, v&&typeof v==="object"?Object.keys(v).filter(x=>/encode|request/.test(x)).slice(0,6):(typeof v==="function"?"fn":"")); }
  // buscar encoders en profundidad (default, nested)
  const cands=[ex, ex.default, ex.default&&ex.default.default];
  for(const cc of cands){ if(cc&&cc.encodeRoomClean0x14){ ex.encodeRoomClean0x14=cc.encodeRoomClean0x14; break; } }
  if(ex.encodeRoomClean0x14){ const fr=ex.encodeRoomClean0x14({version:"1.0.0",roomIds:[4],cleanTimes:1});
    let hex; try{hex=Buffer.from(fr).toString("hex");}catch(e){hex=JSON.stringify(fr);}
    console.log(">>> encodeRoomClean0x14([4],1) len",fr&&fr.length,"=>",(hex||"").slice(0,240)); }
}catch(e){ console.log("LOAD ERR:",e.message,"\n",(e.stack||"").split("\n").slice(1,4).join("\n")); }
