process.on('uncaughtException',e=>{});
const {win,DEVICE,captured,calls}=require('./f52boot.js');
const R=win.ROUTER, devId=DEVICE.devId||DEVICE.deviceId||"";
// PATCH: publish con ack inmediato (emula que la vista responde) -> desbloquea applyRender->Cpe
const SB=win.ServiceJSBridge, origPub=SB.publish;
let pubCount=0;
SB.publish=function(name,data,cb){
  pubCount++;
  try{ origPub.call(SB,name,data,function(){}); }catch(e){}
  if(typeof cb==="function"){ try{ cb({}); }catch(e){} }
  else if(typeof data==="function"){ try{ data({}); }catch(e){} }
};
function gdi(){return (calls||[]).filter(c=>/getDeviceInfo/.test(c)).length;}
try{ R.launch({path:"/pages/home/index", query:"deviceId="+devId+"&devId="+devId+"&groupId="}, "1"); }catch(e){console.log("launch throw:",e.message);}
// también dispara onViewLoad por si el render lo espera
setTimeout(function(){ try{ SB.trigger("window.onViewLoad",{pageId:"1",options:{ua:""}},function(){}); }catch(e){} }, 200);
setTimeout(function(){
  const p=win.getCurrentPages()[0];
  let root=false; try{ if(p){const seen=new Set();(function s(o,d){if(root||d>3||!o||seen.has(o))return;seen.add(o);for(const k of Object.getOwnPropertyNames(o)){let v;try{v=o[k]}catch(e){continue}if(k==="_rootContainer"&&v){root=true;return}if(v&&typeof v==="object"&&d<3)s(v,d+1)}})(p,0);} }catch(e){}
  console.log("publishCount:",pubCount,"getDeviceInfo:",gdi(),"root:",root,"captured:",captured.length,captured.map(c=>c.t));
  console.log("señales:");
  (calls||[]).filter(c=>/ERR|Cannot|not a function|publishDps|sendMqtt|render|applyRender|Cpe|reconcil|subscribeDeviceRepDps|getDp/i.test(c)).slice(0,20).forEach(c=>console.log("  "+c.slice(0,150)));
}, 1500);
