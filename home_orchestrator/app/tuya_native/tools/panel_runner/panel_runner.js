// Panel-runner headless (Chromium real via playwright-core). Interno: deriva capacidades+comandos.
const http=require('http'), fs=require('fs'), path=require('path'), {chromium}=require('playwright-core');
const HERE=__dirname, WEB=path.join(HERE,'web');
const DEVICE=fs.readFileSync(path.join(HERE,'device_full.json'),'utf8');
const MIME={'.js':'application/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.svg':'image/svg+xml'};
const server=http.createServer((req,res)=>{ let u=decodeURIComponent(req.url.split('?')[0]); if(u==='/')u='/host.html'; const fp=path.join(WEB,u);
  fs.readFile(fp,(e,data)=>{ if(e){res.writeHead(404);res.end('nf');return;} res.writeHead(200,{'Content-Type':MIME[path.extname(fp)]||'text/plain'}); res.end(data); }); });
(async()=>{
  await new Promise(r=>server.listen(0,r)); const port=server.address().port;
  const exe=require('os').homedir()+'/Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
  const browser=await chromium.launch({executablePath:exe||undefined, headless:true, args:['--no-sandbox']});
  const page=await browser.newPage();
  let RESULT=null;
  page.on('console',m=>{ const t=m.text(); if(t.startsWith('__RESULT__')){RESULT=t.slice(10);} if(/ERR|error|Cannot|undefined|throw|page setData|__RESULT__/i.test(t)) fs.appendFileSync('/tmp/runner_console.log', t.slice(0,400)+'\n'); });
  page.on('pageerror',e=>fs.appendFileSync('/tmp/runner_console.log','PAGEERROR: '+e.message+'\n'));
  let bridge=fs.readFileSync(path.join(HERE,'run_bridge.js'),'utf8').replace('__DEVICE_JSON__', DEVICE);
  await page.addInitScript(bridge);
  const devId=JSON.parse(DEVICE).devId||'';
  const url='http://localhost:'+port+'/host.html?path='+encodeURIComponent('/pages/home/index')+'&query='+encodeURIComponent('deviceId='+devId+'&devId='+devId+'&groupId=');
  try{ fs.unlinkSync('/tmp/runner_console.log'); }catch(e){}
  await page.goto(url,{waitUntil:'load',timeout:30000});
  for(let i=0;i<60&&!RESULT;i++){ await page.waitForTimeout(200); }
  console.log("RESULT:", RESULT||"(sin resultado)");
  try{ console.log("=== console errores ==="); console.log(fs.readFileSync('/tmp/runner_console.log','utf8').split('\n').slice(0,15).join('\n')); }catch(e){}
  await browser.close(); server.close();
})().catch(e=>{console.error("RUNNER ERR:",e.message); process.exit(1);});
