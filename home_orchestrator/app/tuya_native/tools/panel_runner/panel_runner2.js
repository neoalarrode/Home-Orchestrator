const http=require('http'), fs=require('fs'), path=require('path'), {chromium}=require('playwright-core');
const HERE=__dirname, WEB=path.join(HERE,'web');
const DEVICE=fs.readFileSync(path.join(HERE,'device_full.json'),'utf8');
const MIME={'.js':'application/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.svg':'image/svg+xml'};
const server=http.createServer((req,res)=>{ let u=decodeURIComponent(req.url.split('?')[0]); if(u==='/')u='/parent.html'; const fp=path.join(WEB,u);
  fs.readFile(fp,(e,data)=>{ if(e){res.writeHead(404);res.end('nf');return;} res.writeHead(200,{'Content-Type':MIME[path.extname(fp)]||'text/plain'}); res.end(data); }); });
(async()=>{
  await new Promise(r=>server.listen(0,r)); const port=server.address().port;
  const exe=require('os').homedir()+'/Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
  const browser=await chromium.launch({executablePath:exe, headless:true, args:['--no-sandbox']});
  const page=await browser.newPage();
  page.on('pageerror',e=>fs.appendFileSync('/tmp/r2.log','PAGEERR: '+e.message+'\n'+(e.stack||'').split('\n').slice(1,14).join('\n')+'\n---\n'));
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
    await new Promise(r=>setTimeout(r,2500));
    out.streamLen=(window.__STREAM||[]).length; out.acks=window.__ACKS;
    out.streamEvents={}; (window.__STREAM||[]).forEach(m=>{const k=m&&(m.$eventName||(m.data&&m.data.eventName)||m.eventName||Object.keys(m||{}).slice(0,2).join(","));out.streamEvents[k]=(out.streamEvents[k]||0)+1;});
    try{ out.captured=(f.__CAPTURED||[]).map(c=>c.t); out.getDeviceInfo=(f.__LOG||[]).filter(x=>x==='getDeviceInfo').length; }catch(e){ out.capErr=e.message; }
    return out;
  });
  try{ const full=await page.evaluate(()=>JSON.stringify(window.__STREAM||[])); fs.writeFileSync("/tmp/stream.json", full); }catch(e){}
  console.log(JSON.stringify(res,null,1));
  try{console.log("=== pageerrors ===\n"+fs.readFileSync('/tmp/r2.log','utf8').split('\n').slice(0,8).join('\n'));}catch(e){}
  await browser.close(); server.close();
})().catch(e=>{console.error("ERR:",e.message);process.exit(1);});
