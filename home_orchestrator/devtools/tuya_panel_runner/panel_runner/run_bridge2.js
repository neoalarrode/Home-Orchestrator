// init-script inyectado en el navegador ANTES de cualquier script del panel
window.__CAPTURED=[]; window.__RENDER=[]; window.__LOG=[]; window.__webviewId__="1";
// React DevTools hook: capturar las raíces de fibras del reconciler del panel
window.__FIBERROOTS=[];
window.__REACT_DEVTOOLS_GLOBAL_HOOK__={ supportsFiber:true, renderers:new Map(), _id:0,
  inject:function(r){ var id=++this._id; this.renderers.set(id,r); try{console.log('HOOK:inject id='+id);}catch(e){} return id; },
  onScheduleFiberRoot:function(){}, onCommitFiberUnmount:function(){},
  onCommitFiberRoot:function(id,root){ try{ if(window.__FIBERROOTS.indexOf(root)<0){ window.__FIBERROOTS.push(root); console.log('HOOK:commit root#'+window.__FIBERROOTS.length); } }catch(e){} },
  onPostCommitFiberRoot:function(){}, checkDCE:function(){} };
 try{ if(window.top!==window.self){ console.log("SVCLOC:"+location.search);
  var us=new URLSearchParams(location.search); var q=us.get("query")||""; var qq=new URLSearchParams(q);
  console.log("PARSE: query.deviceId="+qq.get("deviceId")+" path="+us.get("path")); } }catch(e){console.log("PARSEERR:"+e.message);}
window.__DEVICE=__DEVICE_JSON__;
(function(){
  var D=window.__DEVICE;
  function cb(o,d){try{if(o&&typeof o.success==="function")o.success(d);if(o&&typeof o.complete==="function")o.complete(d);}catch(e){}return d;}
  window.__dpCbs=[];
  function DeviceKit(){ return new Proxy({
    getDeviceInfo:function(o){window.__LOG.push("getDeviceInfo");return cb(o,D);},
    getDeviceOnlineStatus:function(o){return cb(o,{online:true,isOnline:true});},
    getDp:function(o){return cb(o,{value:D.dps[o&&(o.dpId||o.dpCode)]});},
    getDpsInfo:function(o){return cb(o,D.dps);}, getDataPointInfo:function(o){return cb(o,D.dps);},
    onDpDataChange:function(o){ if(typeof o==="function")window.__dpCbs.push(o); else if(o&&o.success)window.__dpCbs.push(o.success); return cb(o,{}); },
    subscribeDeviceRepDps:function(o){ if(o&&typeof o.callback==="function")window.__dpCbs.push(o.callback); return cb(o,{}); },
    publishDps:function(o){window.__CAPTURED.push({t:"publishDps",dps:o&&o.dps});return cb(o,{success:true});},
    sendMqttMessage:function(o){window.__CAPTURED.push({t:"sendMqttMessage",raw:o});return cb(o,{success:true});},
    publishMessage:function(o){window.__CAPTURED.push({t:"publishMessage",raw:o});return cb(o,{success:true});},
    registerMQTTProtocolListener:function(o){ window.__mqttListeners=window.__mqttListeners||[]; if(typeof o==="function")window.__mqttListeners.push(o); else if(o&&o.callback)window.__mqttListeners.push(o.callback); return cb(o,{}); },
    unRegisterMQTTProtocolListener:function(o){return cb(o,{});},
    onMqttMessageReceived:function(o){ window.__mqttListeners=window.__mqttListeners||[]; if(typeof o==="function")window.__mqttListeners.push(o); return cb(o,{}); },
    onSubFunctionDataChange:function(o){return cb(o,{});},
    dispatchSubFunctionTouchEvent:function(o){window.__CAPTURED.push({t:"dispatchSubFunctionTouchEvent",raw:o});return cb(o,{});},
  },{ get:function(t,p){ if(typeof p==="symbol")return t[p]; if(p in t)return t[p]; return function(o){ if(typeof o==="function")window.__dpCbs.push(o); return cb(o,{}); }; } }); }
  function anyKit(){ return new Proxy({}, { get:function(t,p){ if(typeof p==="symbol")return undefined; return function(o){ return cb(o,{}); }; } }); }
  window.getNativeKits=function(){ return new Proxy({ P2PKit:{}, DeviceKit:DeviceKit() }, { get:function(t,p){ if(typeof p==="symbol")return t[p]; if(p in t)return t[p]; return anyKit(); } }); };
})();
(function(){
  var _sys={platform:"android",appVersion:"7.9.0",version:"7.9.0",SDKVersion:"2.30.27",language:"es",theme:"light",screenWidth:1080,screenHeight:1920,windowWidth:1080,windowHeight:1920,pixelRatio:3,statusBarHeight:24,safeArea:{top:24,bottom:1920,left:0,right:1080,width:1080,height:1896}};
  function cb(o,d){try{if(o&&typeof o.success==="function")o.success(d);if(o&&typeof o.complete==="function")o.complete(d);}catch(e){}return d;}
  var _lo={path:"/pages/home/index",query:{deviceId:(window.__DEVICE||{}).devId,devId:(window.__DEVICE||{}).devId,groupId:""},scene:10001};
  var ov={getLaunchOptionsSync:function(){return _lo;},getLaunchOptions:function(o){return cb(o,_lo);},getEnterOptionsSync:function(){return _lo;},getEnterOptions:function(o){return cb(o,_lo);},getSystemInfoSync:function(){return _sys;},getSystemInfo:function(o){return cb(o,_sys);},getAppBaseInfo:function(){return _sys;},getWindowInfo:function(){return _sys;},getDeviceInfo:function(){return _sys;},getLogManager:function(){return new Proxy({},{get:function(){return function(){};}});}};
  function mk(name){ var fn=function(){return mk(name+"()");}; return new Proxy(fn,{get:function(t,p){ if(typeof p==="symbol")return p===Symbol.toPrimitive?function(){return "";}:t[p]; if(p==="then")return undefined; if(name==="ty"&&ov[p]!==undefined)return ov[p]; return mk(name+"."+String(p)); }, apply:function(){return fn();}}); }
  window.ty=mk("ty");
  for(var _g of ["gzlJSBridge","nativeRequest","mothra","releaseApi","onViewLoad","setGZLEventHandler","slots"]) window[_g]=mk(_g);
})();
// GANAR LA CARRERA al auto-boot: en cuanto exista ROUTER, launch con webviewId "1" (setea fg) 
(function(){
  if(window.top===window.self) return; // solo en el iframe service (no en el parent)
  var did=false;
  function tryLaunch(){ if(did)return; if(window.ROUTER&&window.ROUTER.launch&&window.__DEVICE){ did=true; try{ var id=window.__DEVICE.devId; window.ROUTER.launch({path:"/pages/home/index",query:{deviceId:id,devId:id,groupId:""}},"1"); window.__EARLYLAUNCH=true; }catch(e){ window.__EARLYLAUNCH="err:"+e.message; } return; } }
  var iv=null;
  // también lo antes posible tras cada script
  var mo=null;
  
})();
