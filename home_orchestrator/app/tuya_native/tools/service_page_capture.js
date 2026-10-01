// F4.3: arnés service que CAPTURA los configs de Page/Component/App (con sus métodos/acciones)
const fs=require('fs'), vm=require('vm'), path=require('path');
const {JSDOM}=require('jsdom');
const HERE=__dirname, FW=path.join(HERE,'fw'), P=path.join(HERE,'panel');
const calls=[]; const log=s=>{if(calls.length<3000)calls.push(s);};
function bridge(name){const fn=function(){return bridge(name+"()");};return new Proxy(fn,{get(t,p){if(typeof p==="symbol")return p===Symbol.toPrimitive?()=>"":t[p];if(p==="then")return undefined;if(name==="ty"&&_ov[p]!==undefined)return _ov[p];return bridge(name+"."+String(p));},apply(){return fn();}});}
const _sys={platform:"android",appVersion:"7.9.0",version:"7.9.0",SDKVersion:"2.30.27",language:"es",theme:"light",screenWidth:1080,screenHeight:1920,windowWidth:1080,windowHeight:1920,pixelRatio:3,statusBarHeight:24,safeArea:{top:24,bottom:1920,left:0,right:1080,width:1080,height:1896}};
const DEVICE=JSON.parse(fs.readFileSync(path.join(HERE,"device.json"),"utf8"));
const captured=[];
function cb(o,d){try{if(o&&typeof o.success==="function")o.success(d);if(o&&typeof o.complete==="function")o.complete(d);}catch(e){}return d;}
const _ov={getSystemInfoSync:()=>_sys,getSystemInfo:o=>{o&&o.success&&o.success(_sys);return _sys},getAppBaseInfo:()=>_sys,getWindowInfo:()=>_sys,getDeviceInfo:()=>_sys,getLogManager:()=>new Proxy({},{get:()=>function(){}})};
function mkDeviceKit(){return{
  getDeviceInfo:o=>cb(o,DEVICE), getDeviceOnlineStatus:o=>cb(o,{online:true,isOnline:true}),
  getDp:o=>cb(o,{value:DEVICE.dps[o&&(o.dpId||o.dpCode)]}), getDpsInfo:o=>cb(o,DEVICE.dps), getDataPointInfo:o=>cb(o,DEVICE.dps),
  publishDps:o=>{captured.push({t:"publishDps",dps:o&&o.dps,raw:o});log("*** publishDps "+JSON.stringify(o&&o.dps));return cb(o,{success:true});},
  sendMqttMessage:o=>{captured.push({t:"sendMqttMessage",raw:o});log("*** sendMqttMessage "+JSON.stringify(o).slice(0,400));return cb(o,{success:true});},
  publishMessage:o=>{captured.push({t:"publishMessage",raw:o});return cb(o,{success:true});},
  subscribeDeviceRepDps:o=>cb(o,{}),unSubscribeDeviceRepDps:o=>cb(o,{}),subscribeDeviceInfoChange:o=>cb(o,{}),unSubscribeDeviceInfoChange:o=>cb(o,{}),onDpDataChange:o=>cb(o,{}),offDpDataChange:o=>cb(o,{}),getDeviceProperty:o=>cb(o,{}),subscribeMqttMessage:o=>cb(o,{}),unSubscribeMqttMessage:o=>cb(o,{}),
};}
const dom=new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>',{runScripts:"outside-only",pretendToBeVisual:true,url:"https://localhost/"});
const win=dom.window, ctx=dom.getInternalVMContext();
for(const g of ["gzlJSBridge","ServiceJSBridge","nativeRequest","mothra","releaseApi","onViewLoad","setGZLEventHandler","slots"]) win[g]=bridge(g);
win.ty=bridge("ty"); win.__appCode__={}; win.__currentPath__="";
win.getNativeKits=function(){return {P2PKit:{},DeviceKit:mkDeviceKit()};};
win.$reportError=e=>log("reportError:"+String(e&&e.message||e).split('\n')[0]);
// framework
vm.runInContext(fs.readFileSync(path.join(FW,'service.js'),'utf8'),ctx,{filename:'service.js'});
// === WRAP App/Page/Component para capturar configs ===
const pages=[], comps=[], apps=[];
const _App=win.App,_Page=win.Page,_Component=win.Component;
win.App=function(c){apps.push(c);return _App&&_App.call(this,c);};
win.Page=function(c){pages.push(c);return _Page&&_Page.call(this,c);};
win.Component=function(c){comps.push(c);return _Component&&_Component.call(this,c);};
// panel + main
try{win.__appConfig__=JSON.parse(fs.readFileSync(path.join(P,"app-config.json"),"utf8"));}catch(e){}
const svc=JSON.parse(fs.readFileSync(path.join(P,"app-service.json"),"utf8"));
for(const sc of svc.scripts.map(x=>x.replace(/^\//,"")).filter(x=>!x.startsWith("framework/"))){ const fp=path.join(P,sc); if(fs.existsSync(fp)){ try{vm.runInContext(fs.readFileSync(fp,"utf8"),ctx,{filename:sc});}catch(e){log("LOAD-ERR["+sc+"]:"+e.message);} } }
try{vm.runInContext(fs.readFileSync(path.join(P,"main.js"),"utf8"),ctx,{filename:"main.js"});}catch(e){log("MAIN-ERR:"+e.message);}
console.log("apps:",apps.length,"pages:",pages.length,"components:",comps.length);
// inspeccionar métodos de páginas/componentes que suenen a room clean
function scanMethods(list,label){ list.forEach((c,i)=>{ if(!c)return; const ks=Object.keys(c); const meth=ks.filter(k=>/room|clean|start|mode|select/i.test(k)); const hasData=ks.includes("data"); const path=c.path||c.route||c.__route__||""; if(meth.length||/clean|room|main/i.test(path)) console.log(label+"["+i+"]",path||"", "métodos:",meth.slice(0,10), "| keys:",ks.slice(0,8)); }); }
scanMethods(pages,"PAGE"); scanMethods(comps,"COMP");
global.__f43={win,pages,comps,apps,captured,DEVICE};
module.exports={win,pages,comps,apps,captured};
