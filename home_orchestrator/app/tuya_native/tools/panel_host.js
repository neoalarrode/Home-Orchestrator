// Arnes headless del panel Tuya (motor "Todo lo gordo") — PERSISTENTE.
// node host5.js  (usa ./fw, ./panel, ./device.json de este dir)
const fs=require('fs'), vm=require('vm'), path=require('path');
const {JSDOM}=require('jsdom');
const HERE=__dirname, FW=path.join(HERE,'fw'), PANELDIR=path.join(HERE,'panel');
const calls=[]; function log(s){ if(calls.length<1500) calls.push(s); }
function noop(){}
function bridge(name){
  const fn=function(...a){ log(name+"("+a.map(x=>{try{return JSON.stringify(x)}catch(e){return String(x)}}).join(",").slice(0,120)+")"); return bridge(name+"()"); };
  return new Proxy(fn,{ get(t,p){ if(typeof p==="symbol"){ if(p===Symbol.toPrimitive)return ()=>""; return t[p]; } if(p==="then")return undefined; if(name==="ty"&&_tyOv[p]!==undefined)return _tyOv[p]; return bridge(name+"."+String(p)); }, apply(t,th,a){return t(...a);} });
}
const _sys={platform:"android",appVersion:"7.9.0",version:"7.9.0",brand:"tuya",model:"node",system:"Android 13",SDKVersion:"2.30.27",language:"es",theme:"light",screenWidth:1080,screenHeight:1920,windowWidth:1080,windowHeight:1920,pixelRatio:3,statusBarHeight:24,safeArea:{top:24,bottom:1920,left:0,right:1080,width:1080,height:1896}};
const _tyOv={ getSystemInfoSync:()=>_sys, getSystemInfo:o=>{o&&o.success&&o.success(_sys);return _sys}, getAppBaseInfo:()=>_sys, getWindowInfo:()=>_sys, getDeviceInfo:()=>_sys, getLogManager:()=>new Proxy({},{get:()=>function(){}}) };
// ---- DeviceKit (inyecta el dispositivo real + captura el transporte) ----
const DEVICE=JSON.parse(fs.readFileSync(path.join(HERE,"device.json"),"utf8"));
const captured=[];
function cb(o,data){ try{ if(o&&typeof o.success==="function")o.success(data); if(o&&typeof o.complete==="function")o.complete(data);}catch(e){log("cb-err:"+e.message);} return data; }
function mkDeviceKit(){ return {
  getDeviceInfo:o=>{log("DeviceKit.getDeviceInfo");return cb(o,DEVICE);},
  getDeviceOnlineStatus:o=>cb(o,{online:true,isOnline:true}),
  getDp:o=>cb(o,{value:DEVICE.dps[o&&(o.dpId||o.dpCode)]}),
  getDpsInfo:o=>cb(o,DEVICE.dps), getDataPointInfo:o=>cb(o,DEVICE.dps),
  publishDps:o=>{captured.push({t:"publishDps",dps:o&&o.dps,raw:o});log("*** publishDps "+JSON.stringify(o&&o.dps));return cb(o,{success:true});},
  sendMqttMessage:o=>{captured.push({t:"sendMqttMessage",raw:o});log("*** sendMqttMessage "+JSON.stringify(o).slice(0,300));return cb(o,{success:true});},
  publishMessage:o=>{captured.push({t:"publishMessage",raw:o});log("*** publishMessage "+JSON.stringify(o).slice(0,300));return cb(o,{success:true});},
  subscribeMqttMessage:o=>cb(o,{}), unSubscribeMqttMessage:o=>cb(o,{}),
  subscribeDeviceRepDps:o=>cb(o,{}), unSubscribeDeviceRepDps:o=>cb(o,{}),
  subscribeDeviceInfoChange:o=>cb(o,{}), unSubscribeDeviceInfoChange:o=>cb(o,{}),
  onDpDataChange:o=>cb(o,{}), offDpDataChange:o=>cb(o,{}), getDeviceProperty:o=>cb(o,{}),
}; }
const dom=new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>',{runScripts:"outside-only",pretendToBeVisual:true,url:"https://localhost/"});
const win=dom.window, ctx=dom.getInternalVMContext();
for(const g of ["gzlJSBridge","ServiceJSBridge","nativeRequest","mothra","releaseApi","onViewLoad","setGZLEventHandler","slots"]) win[g]=bridge(g);
win.ty=bridge("ty"); win.__appCode__={}; win.__currentPath__="";
win.getNativeKits=function(){ return { "P2PKit":{}, "DeviceKit": mkDeviceKit() }; };
win.$reportError=function(e){log("reportError:"+String(e&&e.message||e).split('\n')[0]);};
// 1) framework real
try{ vm.runInContext(fs.readFileSync(path.join(FW,'service.js'),'utf8'),ctx,{filename:'service.js'}); }catch(e){ console.log("service.js ERROR:",String(e.message||e).split('\n')[0]); }
console.log("framework: define="+typeof win.define+" require="+typeof win.require+" defineSafeScript="+typeof win.defineSafeScript);
// 2) panel
try{ win.__appConfig__=JSON.parse(fs.readFileSync(path.join(PANELDIR,"app-config.json"),"utf8")); }catch(e){}
const svc=JSON.parse(fs.readFileSync(path.join(PANELDIR,"app-service.json"),"utf8"));
let ok=0,err=0;
for(const sc of svc.scripts.map(x=>x.replace(/^\//,"")).filter(x=>!x.startsWith("framework/"))){
  const fp=path.join(PANELDIR,sc); if(!fs.existsSync(fp))continue;
  try{ vm.runInContext(fs.readFileSync(fp,"utf8"),ctx,{filename:sc}); ok++; }catch(e){ err++; log("LOAD-ERR["+sc+"]:"+String(e.message||e).split("\n")[0]); }
}
try{ vm.runInContext(fs.readFileSync(path.join(PANELDIR,"main.js"),"utf8"),ctx,{filename:"main.js"}); log("main.js EJECUTADO"); }catch(e){ log("MAIN-ERR:"+String(e.message||e).split("\n")[0]); }
console.log("panel: ok="+ok+" err="+err);
// exponer para invocar acciones (F3 ultima milla)
global.__win=win; global.__calls=calls; global.__captured=captured;
console.log("=== ERRORES/tips ==="); calls.filter(c=>/ERR|error|exits|Cannot|reading/i.test(c)).slice(0,15).forEach(c=>console.log("  ",c));
console.log("=== captured transporte ==="); captured.forEach(c=>console.log("  ",JSON.stringify(c).slice(0,200)));
console.log("main.js EJECUTADO?", calls.includes("main.js EJECUTADO"), "| errores:", calls.filter(c=>/ERR|Cannot|exits/i.test(c)).length);
module.exports={win,calls,captured};
