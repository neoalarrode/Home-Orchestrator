const st=require("/tmp/stream.json");
const setd=st.filter(m=>m.data&&m.data.action==="setData");
// árbol de la home (op con más textos)
let home=null,best=-1;
setd.forEach(m=>{let c=0;(function w(o,d){if(!o||typeof o!=="object"||d>50)return;if(o.TX)c++;for(const k in o)try{w(o[k],d+1)}catch(e){}})(m.data.data,0);if(c>best){best=c;home=m.data;}});
const tree={}; function setDotted(ob,dk,v){const p=dk.split(".");let o=ob;for(let i=0;i<p.length-1;i++){if(o[p[i]]==null)o[p[i]]={};o=o[p[i]];}o[p[p.length-1]]=v;}
for(const k in home.data.patchData) setDotted(tree,k,home.data.patchData[k]);
const root=tree.root;
function childAt(n,i){const id=n&&n.CH&&n.CH[i];return id!=null&&n.NS?n.NS[id]:null;}
function isComp(n){ return n&&n.TG&&(/-index-/.test(n.TG)||/^c-/.test(n.TG)||n.TG==="cover-view"||n.TG==="page-container"); }
function firstText(n){if(!n)return"";if(n.TX)return n.TX;const CH=n.CH||[];for(let i=0;i<CH.length;i++){const t=firstText(childAt(n,i));if(t)return t;}return"";}
// convertir path plano -> segmentos (break tras seleccionar un nodo componente)
function segment(flat){ const segs=[]; let cur=[]; let node=root;
  for(let i=0;i<flat.length;i++){ const idx=flat[i]; cur.push(idx); node=childAt(node,idx);
    if(!node) return null;
    if(isComp(node) && i<flat.length-1){ segs.push(cur); cur=[]; }
  }
  segs.push(cur); return segs;
}
// recolectar controles bind:tap con su path plano, y segmentarlos
const out=[];
function walk(n,path,d){ if(!n||d>50)return; const ps=n.PS||{}; for(const k in ps){ if(ps[k]==="eh"&&/tap|click/i.test(k)){ out.push({flat:path.slice(),tx:firstText(n).slice(0,16),sid:ps["data-sid"],TG:n.TG}); break; } } const CH=n.CH||[]; for(let i=0;i<CH.length;i++) walk(childAt(n,i),path.concat(i),d+1); }
if(root){const CH=root.CH||[];for(let i=0;i<CH.length;i++)walk(childAt(root,i),[i],d=0);}
out.forEach(c=>{ c.seg=segment(c.flat); });
require("fs").writeFileSync("/tmp/segpaths.json",JSON.stringify(out));
out.slice(0,30).forEach(c=>console.log(JSON.stringify(c.tx)+" flat="+JSON.stringify(c.flat)+" SEG="+JSON.stringify(c.seg)));
