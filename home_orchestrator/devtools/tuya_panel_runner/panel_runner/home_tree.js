const st=require("/tmp/stream.json");
const setd=st.filter(m=>m.data&&m.data.action==="setData");
// op con más textos = render principal de la home
let home=null,best=-1;
setd.forEach(m=>{ let c=0;(function w(o,d){if(!o||typeof o!=="object"||d>50)return;if(o.TX)c++;for(const k in o)try{w(o[k],d+1)}catch(e){}})(m.data.data,0); if(c>best){best=c;home=m.data;} });
console.log("home op jobId="+home.jobId+" txts="+best);
// aplicar patchData dotted a un árbol nested
const tree={};
function setDotted(obj,dotted,val){ const p=dotted.split("."); if(p.some(_k=>_k==="__proto__"||_k==="constructor"||_k==="prototype"))return; let o=obj; for(let i=0;i<p.length-1;i++){ if(o[p[i]]==null)o[p[i]]={}; o=o[p[i]]; } o[p[p.length-1]]=val; }
const pd=home.data.patchData; for(const k in pd) setDotted(tree,k,pd[k]);
const root=tree.root;
console.log("root.CH:",JSON.stringify(root.CH),"root.NS ids:",root.NS?Object.keys(root.NS).length:0);
// walk per-node NS; recoger controles bind:tap con path y texto
function childAt(n,i){ const id=n&&n.CH&&n.CH[i]; return id!=null&&n.NS?n.NS[id]:null; }
function firstText(n){ if(!n)return""; if(n.TX)return n.TX; const CH=n.CH||[]; for(let i=0;i<CH.length;i++){const t=firstText(childAt(n,i)); if(t)return t;} return ""; }
const taps=[];
function walk(n,path,d){ if(!n||d>50)return; const ps=n.PS||{};
  for(const k in ps){ if(ps[k]==="eh"&&/tap|click/i.test(k)){ taps.push({path:path.slice(),TG:n.TG,sid:ps["data-sid"],tx:firstText(n).slice(0,20)}); break; } }
  const CH=n.CH||[]; for(let i=0;i<CH.length;i++) walk(childAt(n,i),path.concat(i),d+1);
}
if(root){ const CH=root.CH||[]; for(let i=0;i<CH.length;i++) walk(childAt(root,i),[i],0); }
console.log("controles tap ("+taps.length+"):");
taps.forEach(t=>console.log("  path="+JSON.stringify(t.path)+" "+t.TG+" sid="+t.sid+" tx="+JSON.stringify(t.tx)));
require("fs").writeFileSync("/tmp/home_taps.json",JSON.stringify(taps));
