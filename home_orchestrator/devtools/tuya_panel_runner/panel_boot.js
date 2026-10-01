// NIVEL 2: arranque del panel headless via ServiceJSBridge.trigger("router.call",reLaunch)
const fs=require('fs'), vm=require('vm'), path=require('path');
const {JSDOM}=require('jsdom');
const HERE=__dirname, FW=path.join(HERE,'fw'), P=path.join(HERE,'panel');
const calls=[]; const log=s=>{if(calls.length<5000)calls.push(s);};
function bridge(name){const fn=function(){return bridge(name+"()");};return new Proxy(fn,{get(t,p){if(typeof p==="symbol")return p===Symbol.toPrimitive?()=>"":t[p];if(p==="then")return undefined;if(name==="ty"&&_ov[p]!==undefined)return _ov[p];return bridge(name+"."+String(p));},apply(){return fn();}});}
const _sys={platform:"android",appVersion:"7.9.0",version:"7.9.0",SDKVersion:"2.30.27",language:"es",theme:"light",screenWidth:1080,screenHeight:1920,windowWidth:1080,windowHeight:1920,pixelRatio:3,statusBarHeight:24,safeArea:{top:24,bottom:1920,left:0,right:1080,width:1080,height:1896}};
const DEVICE=JSON.parse(fs.readFileSync(path.join(HERE,"device.json"),"utf8"));
const captured=[];
function cb(o,d){try{if(o&&typeof o.success==="function")o.success(d);if(o&&typeof o.complete==="function")o.complete(d);}catch(e){log("cberr:"+e.message);}return d;}
const _ov={getSystemInfoSync:()=>_sys,getSystemInfo:o=>{o&&o.success&&o.success(_sys);return _sys},getAppBaseInfo:()=>_sys,getWindowInfo:()=>_sys,getDeviceInfo:()=>_sys,getLogManager:()=>new Proxy({},{get:()=>function(){}})};
function mkDeviceKit(){return{
  getDeviceInfo:o=>{log("DeviceKit.getDeviceInfo");return cb(o,DEVICE);}, getDeviceOnlineStatus:o=>cb(o,{online:true,isOnline:true}),
  getDp:o=>cb(o,{value:DEVICE.dps[o&&(o.dpId||o.dpCode)]}), getDpsInfo:o=>cb(o,DEVICE.dps), getDataPointInfo:o=>cb(o,DEVICE.dps),
  publishDps:o=>{captured.push({t:"publishDps",dps:o&&o.dps});log("*** publishDps "+JSON.stringify(o&&o.dps));return cb(o,{success:true});},
  sendMqttMessage:o=>{captured.push({t:"sendMqttMessage",raw:o});log("*** sendMqttMessage "+JSON.stringify(o).slice(0,300));return cb(o,{success:true});},
  publishMessage:o=>{captured.push({t:"publishMessage",raw:o});return cb(o,{success:true});},
  subscribeDeviceRepDps:o=>cb(o,{}),unSubscribeDeviceRepDps:o=>cb(o,{}),subscribeDeviceInfoChange:o=>cb(o,{}),unSubscribeDeviceInfoChange:o=>cb(o,{}),onDpDataChange:o=>cb(o,{}),offDpDataChange:o=>cb(o,{}),getDeviceProperty:o=>cb(o,{}),subscribeMqttMessage:o=>cb(o,{}),unSubscribeMqttMessage:o=>cb(o,{}),
};}
const _devId=DEVICE.devId||DEVICE.deviceId||"";
const _launch="https://localhost/";
const dom=new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>',{runScripts:"outside-only",pretendToBeVisual:true,url:_launch});
const win=dom.window, ctx=dom.getInternalVMContext();
win.Worker=class{postMessage(){}terminate(){}addEventListener(){}removeEventListener(){}};
win.MessageChannel=class{constructor(){this.port1={postMessage(){},close(){},start(){},onmessage:null};this.port2=this.port1;}};
win.importScripts=function(){}; win.requestIdleCallback=cb=>setTimeout(cb,0);
for(const g of ["gzlJSBridge","nativeRequest","mothra","releaseApi","onViewLoad","setGZLEventHandler","slots"]) win[g]=bridge(g);
win.ty=bridge("ty"); win.__appCode__={}; win.__currentPath__="";
win.getNativeKits=function(){return {P2PKit:{},DeviceKit:mkDeviceKit()};};
win.$reportError=e=>log("reportError:"+String(e&&e.message||e).split('\n')[0]);
function run(file,fp){try{vm.runInContext(fs.readFileSync(fp,'utf8'),ctx,{filename:file});return true;}catch(e){log("ERR["+file+"]:"+String(e.message||e).split('\n')[0]);return false;}}
run("service.js",path.join(FW,'service.js'));
try{win.__appConfig__=JSON.parse(fs.readFileSync(path.join(P,"app-config.json"),"utf8"));}catch(e){}
const svc=JSON.parse(fs.readFileSync(path.join(P,"app-service.json"),"utf8"));
for(const sc of svc.scripts.map(x=>x.replace(/^\//,"")).filter(x=>!x.startsWith("framework/"))){ const fp=path.join(P,sc); if(fs.existsSync(fp)) run(sc,fp); }
run("main.js",path.join(P,"main.js"));
console.log("=== cargado. ServiceJSBridge:",typeof win.ServiceJSBridge, "getCurrentPages:",typeof win.getCurrentPages);
// intento de arranque
const devId=DEVICE.devId||DEVICE.deviceId||"";
const url="/pages/home/index?deviceId="+devId+"&devId="+devId;
log("location.search="+win.location.search.slice(0,80));
// esperar microtareas/timers y observar
setTimeout(function(){
  let cp=[]; try{cp=win.getCurrentPages();}catch(e){}
  console.log("getCurrentPages n=", cp&&cp.length);
  console.log("captured transporte:", captured.length, captured.slice(0,3).map(c=>c.t));
  console.log("--- eventos/errores relevantes ---");
  calls.filter(c=>/router|launch|createPage|ERR|reportError|getDeviceInfo|publishDps|sendMqtt|Cannot|not a function|missing/i.test(c)).slice(0,30).forEach(c=>console.log("  "+c.slice(0,160)));
}, 2200);

module.exports={win,captured,DEVICE,calls};
global.__H={win,captured,DEVICE,calls};
