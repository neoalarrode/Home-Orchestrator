// init-script inyectado en el navegador ANTES de cualquier script del panel
window.__CAPTURED=[]; window.__RENDER=[]; window.__LOG=[];
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
  window.getNativeKits=function(){ return {P2PKit:{}, DeviceKit:DeviceKit()}; };
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
(function(){
  function done(obj){ try{ console.log("__RESULT__"+JSON.stringify(obj)); }catch(e){ console.log("__RESULT__{\"err\":\"stringify\"}"); } }
  var tries=0;
  function boot(){
    tries++;
    if(!(window.ROUTER&&window.ROUTER.launch&&window.ServiceJSBridge)){ if(tries<120){return setTimeout(boot,100);} return done({error:"no ROUTER/ServiceJSBridge tras espera", have:{ROUTER:typeof window.ROUTER,SB:typeof window.ServiceJSBridge}}); }
    var SB=window.ServiceJSBridge, op=SB.publish;
    SB.publish=function(n,d,c){try{op.call(SB,n,d,function(){});}catch(e){} if(typeof c==="function"){try{c({});}catch(e){}} else if(typeof d==="function"){try{d({});}catch(e){}}};
    var devId=window.__DEVICE.devId;
    try{ window.ROUTER.launch({path:"/pages/home/index",query:"deviceId="+devId+"&devId="+devId+"&groupId="},"1"); }catch(e){ return done({launchErr:e.message}); }
    setTimeout(function(){ try{SB.trigger("window.onViewLoad",{pageId:"1",options:{ua:""}},function(){});}catch(e){} },120);
    window.__RENDER=[];
    function patchSetData(){ var cp=[]; try{cp=window.getCurrentPages();}catch(e){} (cp||[]).forEach(function(p){ if(p&&typeof p.setData==="function"&&!p.__patched){ p.__patched=true; var o=p.setData; p.setData=function(d){ try{window.__RENDER.push(d);}catch(e){} return o.apply(this,arguments); }; } }); }
    var pv=setInterval(patchSetData,60);
    setTimeout(function(){ // forzar re-render alimentando cambios de DP
      var upd={}; for(var k in window.__DEVICE.dps) upd[k]=window.__DEVICE.dps[k];
      (window.__dpCbs||[]).forEach(function(f){ try{f({dps:upd,dpsTime:{}});}catch(e){} try{f(upd);}catch(e){} });
    }, 1500);
    setTimeout(function(){
      clearInterval(pv);
      var cp=[]; try{cp=window.getCurrentPages();}catch(e){}
      var R=(window.__RENDER||[]); var big=null,bl=0; R.forEach(function(d){var l=0;try{l=JSON.stringify(d).length;}catch(e){} if(l>bl){bl=l;big=d;}});

      // probe: store/acciones reachable
      try{
        var stores=[], acts=[]; var seen=new Set();
        (function scan(o,p,d){ if(d>4||!o||seen.size>60000)return; if(typeof o!=="object"&&typeof o!=="function")return; if(seen.has(o))return; try{seen.add(o);}catch(e){return;}
          var ks; try{ks=Object.getOwnPropertyNames(o);}catch(e){return;}
          for(var i=0;i<ks.length;i++){ var k=ks[i],v; try{v=o[k];}catch(e){continue;}
            if(v&&typeof v==="object"&&typeof v.dispatch==="function"&&typeof v.getState==="function"&&stores.length<3) stores.push(p+"."+k);
            if(typeof v==="function"&&/setRoomClean|setZoneClean/.test(k)&&acts.length<5) acts.push(p+"."+k);
            if(v&&(typeof v==="object")&&d<4&&!/^(document|location|navigator|window|self|top|parent|frames|globalThis|__DEVICE|__CAPTURED|__RENDER)$/.test(k)) scan(v,p+"."+k,d+1);
          }
        })(window,"win",0);
        window.__PROBE={stores:stores, acts:acts, seen:seen.size};
      }catch(e){ window.__PROBE={err:e.message}; }
      done({ pages:cp?cp.length:0, captured:(window.__CAPTURED||[]).map(function(c){return c.t;}), getDeviceInfo:(window.__LOG||[]).filter(function(x){return x==='getDeviceInfo';}).length, dpCbs:(window.__dpCbs||[]).length, renderCount:R.length, renderMaxLen:bl, renderKeys: big&&typeof big==="object"?Object.keys(big).slice(0,20):null, probe:window.__PROBE });
      try{ if(big) window.__BIGRENDER=JSON.stringify(big); }catch(e){}
    }, 3000);
  }
  if(document.readyState==="complete") setTimeout(boot,300); else window.addEventListener("load",function(){setTimeout(boot,300);});
})();
