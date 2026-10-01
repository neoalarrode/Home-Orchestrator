// "Todo lo gordo" (vía fiel por derivación de fuente): extrae de main.js del panel el
// DESCRIPTOR DE COMANDOS de un producto — exactamente la lógica de dispatch de la app,
// sin ejecutar ni portar encoders a mano. Salida: JSON {proto64:[...], dp:[...], reqTypes:[...]}.
// uso: node panel_commands.js <panelDir> [productId]
const fs=require('fs'), path=require('path');
const dir=process.argv[2]||path.join(__dirname,'panel');
const productId=process.argv[3]||'';
const s=fs.readFileSync(path.join(dir,'main.js'),'utf8');

// 1) enums reqType: X=function(t){return t.set="lit",t.query/rst="lit",t}({})
const enumLit={}; // varName -> {set, query}
let re=/([A-Za-z0-9_]+)=function\(t\)\{return ((?:t\.[a-zA-Z]+="[^"]+",?)+)t\}\(\{\}\)/g,m;
while((m=re.exec(s))){ const o={}; for(const mm of m[2].matchAll(/t\.([a-zA-Z]+)="([^"]+)"/g)) o[mm[1]]=mm[2]; enumLit[m[1]]=o; }

// 2) dispatchers MQTT proto64: Ai({deviceId:..,reqType:VAR.set,message:{campos}})
const proto64=[]; const seen=new Set();
let r2=/reqType:([A-Za-z0-9_]+)\.set,message:\{([^{}]*)\}/g;
while((m=r2.exec(s))){
  const v=m[1]; const reqType=(enumLit[v]&&enumLit[v].set)||(v+".set");
  const fields=[...m[2].matchAll(/([a-zA-Z0-9_]+):/g)].map(x=>x[1]).filter(f=>!["taskId","o"].includes(f));
  if(seen.has(reqType))continue; seen.add(reqType);
  proto64.push({command:reqType, transport:"mqtt_proto64", version:"1.0.0", protocol:64, message_fields:fields});
}
// 3) dispatchers DP command_trans: command_trans.set((0,X.encode/requestNAME)({args}))
const dp=[]; const seen2=new Set();
let r3=/command_trans\.set\(\(0,[A-Za-z0-9_]+\.((?:encode|request)[A-Za-z0-9_]+)\)\(\{([^{}]*)\}/g;
while((m=r3.exec(s))){ const fn=m[1]; if(seen2.has(fn))continue; seen2.add(fn);
  const args=[...m[2].matchAll(/([a-zA-Z0-9_]+):/g)].map(x=>x[1]);
  dp.push({encoder:fn, transport:"dp_command_trans", args});
}
const out={ product_id:productId, source:"panel/main.js (dispatch de la app)",
  reqTypes:Object.values(enumLit).map(o=>o.set).filter(Boolean).sort(),
  proto64_commands:proto64, dp_commands:dp };
console.log(JSON.stringify(out,null,2));
