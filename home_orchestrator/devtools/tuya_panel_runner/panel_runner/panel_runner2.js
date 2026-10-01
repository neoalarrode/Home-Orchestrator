const http=require('http'), fs=require('fs'), path=require('path'), {chromium}=require('playwright-core');
const HERE=__dirname, WEB=path.join(HERE,'web');
const DEVICE=fs.readFileSync(path.join(HERE,'device_full.json'),'utf8');
const MIME={'.js':'application/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.svg':'image/svg+xml'};
const server=http.createServer((req,res)=>{ let u=decodeURIComponent(req.url.split('?')[0]); if(u==='/')u='/parent.html'; const fp=path.join(WEB,u); if(!path.resolve(fp).startsWith(path.resolve(WEB)+path.sep)){res.writeHead(403);res.end('forbidden');return;}
  fs.readFile(fp,(e,data)=>{ if(e){res.writeHead(404);res.end('nf');return;} res.writeHead(200,{'Content-Type':MIME[path.extname(fp)]||'text/plain'}); res.end(data); }); });
(async()=>{
  await new Promise(r=>server.listen(0,r)); const port=server.address().port;
  const exe=require('os').homedir()+'/Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
  const browser=await chromium.launch({executablePath:exe, headless:true, args:['--no-sandbox']});
  const page=await browser.newPage();
  page.on('requestfailed',r=>fs.appendFileSync('/tmp/r2.log','404REQ: '+r.url()+'\n')); page.on('response',r=>{if(r.status()>=400)fs.appendFileSync('/tmp/r2.log','HTTP'+r.status()+': '+r.url()+'\n');}); page.on('pageerror',e=>{ let extra=''; try{ if(e&&e.message==='Object'){ extra=JSON.stringify(e); } }catch(x){} fs.appendFileSync('/tmp/r2.log','PAGEERR: '+(e&&e.message)+' '+extra+'\n'+((e&&e.stack)||'').split('\n').slice(1,10).join('\n')+'\n---\n'); });
  page.on('console',m=>{ const t=m.text(); if(/SVCLOC|PARSE|HOOK:/.test(t))fs.appendFileSync('/tmp/r2.log','>>> '+t+'\n'); if(/error|fail|exception|reject/i.test(t)) fs.appendFileSync('/tmp/r2.log','CONSOLE: '+t.slice(0,300)+'\n'); });
  let bridge=fs.readFileSync(path.join(HERE,'run_bridge2.js'),'utf8').replace('__DEVICE_JSON__', DEVICE);
  await page.addInitScript(bridge);
  try{fs.unlinkSync('/tmp/r2.log');}catch(e){}
  const devId=JSON.parse(DEVICE).devId;
  const qs='?path='+encodeURIComponent('/pages/home/index')+'&query='+encodeURIComponent('deviceId='+devId+'&devId='+devId+'&groupId=')+'&webviewId=1';
  await page.goto('http://localhost:'+port+'/parent.html',{waitUntil:'load',timeout:30000});
  // arrancar el iframe service
  await page.evaluate(qs=>window.__startSvc(qs), qs);
  await page.waitForTimeout(1500);
  // lanzar la página dentro del iframe (mismo origen) + esperar render
  const res=await page.evaluate(async ()=>{
    const f=document.getElementById('svc').contentWindow;
    const out={ROUTER:typeof (f.ROUTER), SB:typeof (f.ServiceJSBridge)};
    out.earlyLaunch=f.__EARLYLAUNCH;
    await new Promise(r=>setTimeout(r,600));
    // el parent (como vista) avisa onViewLoad -> el service procede a renderizar y envia ops
    try{ f.postMessage({$eventName:"window.onViewLoad", pageId:"1", options:{ua:""}}, "*"); }catch(e){ out.vlErr=e.message; }
    // esperar a que aparezca el op de la home (contiene 开始) y tapear YA (antes del error-boundary)
    function tap(path,sid){ try{ f.postMessage({$eventName:"domEvent", pageId:"1", options:{eventList:[{ ev:{type:"tap", timeStamp:Date.now(), currentTarget:{id:sid,dataset:{sid:sid},offsetLeft:0,offsetTop:0}, target:{id:sid,dataset:{sid:sid}}, detail:{x:10,y:10}, touches:[{clientX:10,clientY:10}], changedTouches:[{clientX:10,clientY:10}] }, paths:path, eventName:"tap" }]}}, "*"); }catch(e){} }
    let homeSeen=false;
    for(let i=0;i<120;i++){ await new Promise(r=>setTimeout(r,25)); if((window.__STREAM||[]).some(m=>{try{return JSON.stringify(m).indexOf("开始")>=0;}catch(e){return false;}})){ homeSeen=true; break; } }
    out.homeSeen=homeSeen; out.capBeforeTap=(f.__CAPTURED||[]).length;
    const SEGS=[{"seg": [[0, 0, 0], [0]], "tx": "", "sid": 5}, {"seg": [[0, 0, 2, 0], [0]], "tx": "", "sid": 29}, {"seg": [[0, 0, 2, 1], [0]], "tx": "", "sid": 32}, {"seg": [[0, 0, 3]], "tx": "\u5f53\u524d\u5730\u56fe", "sid": 60}, {"seg": [[0, 0, 3], [0, 0, 1, 0]], "tx": "\u7981\u533a\u7f16\u8f91", "sid": 44}, {"seg": [[0, 0, 3], [0, 0, 1, 1]], "tx": "\u623f\u95f4\u7f16\u8f91", "sid": 49}, {"seg": [[0, 0, 3], [0, 0, 1, 2]], "tx": "\u5730\u677f\u6750\u8d28", "sid": 54}, {"seg": [[0, 0, 3], [0, 1]], "tx": "\u5173\u95ed\u5f39\u7a97", "sid": 58}, {"seg": [[0, 0, 4], [0, 1, 0]], "tx": "Smart", "sid": 64}, {"seg": [[0, 0, 4], [0, 1, 1]], "tx": "Room", "sid": 67}, {"seg": [[0, 0, 4], [0, 1, 2]], "tx": "Zone", "sid": 70}, {"seg": [[0, 0, 4], [0, 1, 3]], "tx": "Pose", "sid": 73}, {"seg": [[0, 0, 4], [0, 3, 0, 0]], "tx": "\u5f00\u59cb", "sid": 79}, {"seg": [[0, 0, 4], [0, 3, 1, 0]], "tx": "\u57fa\u7ad9\u529f\u80fd", "sid": 84}, {"seg": [[0, 0, 4], [0, 3, 1, 1]], "tx": "\u57fa\u7ad9\u529f\u80fd", "sid": 109}, {"seg": [[0, 0, 4], [0, 3, 1, 1], [0, 0, 1, 0, 0]], "tx": "\u96c6\u5c18", "sid": 92}, {"seg": [[0, 0, 4], [0, 3, 1, 1], [0, 0, 1, 0, 1]], "tx": "\u6d17\u62d6\u5e03", "sid": 97}, {"seg": [[0, 0, 4], [0, 3, 1, 1], [0, 0, 1, 0, 2]], "tx": "\u70d8\u5e72", "sid": 102}, {"seg": [[0, 0, 4], [0, 3, 1, 1], [0, 1]], "tx": "\u5173\u95ed\u5f39\u7a97", "sid": 107}, {"seg": [[0, 0, 4], [0, 3, 1, 3]], "tx": "\u6e05\u6d01\u504f\u597d", "sid": 114}, {"seg": [[0, 0, 4], [0, 3, 1, 4]], "tx": "\u5168\u5c40\u6a21\u5f0f", "sid": 124}, {"seg": [[0, 0, 4], [0, 3, 1, 4], [0, 0, 0], [0]], "tx": "\u5168\u5c40\u6a21\u5f0f", "sid": 116}, {"seg": [[0, 0, 4], [0, 3, 1, 4], [0, 0, 0], [1]], "tx": "\u81ea\u5b9a\u4e49\u6a21\u5f0f", "sid": 118}, {"seg": [[0, 0, 4], [0, 3, 1, 4], [0, 1]], "tx": "\u53bb\u8bbe\u7f6e", "sid": 122}, {"seg": [[0, 0, 5], [0, 0]], "tx": "", "sid": 129}, {"seg": [[0, 0, 5], [0, 1, 2]], "tx": "\u91cd\u7f6e\u5bc6\u7801", "sid": 148}, {"seg": [[0, 3], [0, 0, 1, 0]], "tx": "\u597d\u7684", "sid": 174}, {"seg": [[0, 3], [0, 0, 1, 1]], "tx": "\u8df3\u8fc7", "sid": 177}];
    out.perTap=[];
    for(const c of SEGS){ const before=(f.__CAPTURED||[]).length; try{ f.postMessage({$eventName:"domEvent", pageId:"1", options:{eventList:[{ ev:{type:"tap",timeStamp:Date.now(),currentTarget:{id:c.sid,dataset:{sid:c.sid},offsetLeft:5,offsetTop:5},target:{id:c.sid,dataset:{sid:c.sid}},detail:{x:5,y:5},touches:[{clientX:5,clientY:5}],changedTouches:[{clientX:5,clientY:5}]}, paths:c.seg, eventName:"tap" }]}}, "*"); }catch(e){} await new Promise(r=>setTimeout(r,200)); const after=(f.__CAPTURED||[]); if(after.length>before){ for(let i=before;i<after.length;i++) out.perTap.push({tx:c.tx, t:after[i].t, msg:after[i].raw&&after[i].raw.message}); } }
    out.capturedAll=(f.__CAPTURED||[]).map(c=>({t:c.t,msg:c.raw&&c.raw.message}));
    out.streamLen=(window.__STREAM||[]).length; out.acks=window.__ACKS;
    out.streamEvents={}; (window.__STREAM||[]).forEach(m=>{const k=m&&(m.$eventName||(m.data&&m.data.eventName)||m.eventName||Object.keys(m||{}).slice(0,2).join(","));out.streamEvents[k]=(out.streamEvents[k]||0)+1;});
    function tap(path,sid){ try{ f.postMessage({$eventName:"domEvent", pageId:"1", options:{eventList:[{ ev:{type:"tap", timeStamp:Date.now(), currentTarget:{id:sid,dataset:{sid:sid},offsetLeft:0,offsetTop:0}, target:{id:sid,dataset:{sid:sid}}, detail:{x:10,y:10}, touches:[{clientX:10,clientY:10}], changedTouches:[{clientX:10,clientY:10}] }, paths:path, eventName:"tap" }]}}, "*"); }catch(e){} }
    out.capBeforeTap=(f.__CAPTURED||[]).length;
    tap([0,0,4,0,1,1],67);  // Room mode
    await new Promise(r=>setTimeout(r,400));
    tap([0,0,4,0,3,0,0],79);  // Start
    await new Promise(r=>setTimeout(r,1500));
    out.capAfterTap=(f.__CAPTURED||[]).length;
    out.capturedAll=(f.__CAPTURED||[]).map(c=>({t:c.t, msg:c.raw&&c.raw.message}));
    out.captured=(f.__CAPTURED||[]).map(c=>c.t); out.getDeviceInfo=(f.__LOG||[]).filter(x=>x==='getDeviceInfo').length;
    return out;
  });
  try{ const full=await page.evaluate(()=>JSON.stringify(window.__STREAM||[])); fs.writeFileSync("/tmp/stream.json", full); }catch(e){}
  console.log(JSON.stringify(res,null,1));
  try{console.log("=== pageerrors ===\n"+fs.readFileSync('/tmp/r2.log','utf8').split('\n').slice(0,8).join('\n'));}catch(e){}
  await browser.close(); server.close();
})().catch(e=>{console.error("ERR:",e.message);process.exit(1);});
