const st=require("/tmp/stream.json");
const setd=st.filter(m=>m.data&&m.data.action==="setData");
const tree={};
function setDotted(obj,dotted,val){ const parts=dotted.split("."); let o=obj; for(let i=0;i<parts.length;i++){ const k=parts[i]; if(k==="__proto__"||k==="constructor"||k==="prototype")return; if(i===parts.length-1){ o[k]=val; } else { if(o[k]==null)o[k]={}; o=o[k]; } } }
setd.forEach(m=>{ const pd=m.data.data&&m.data.data.patchData; if(pd) for(const k in pd) setDotted(tree,k,pd[k]); });
const root=tree.root;
console.log("root keys:", root?Object.keys(root):"(no root)");
console.log("root.CH:", root&&JSON.stringify(root.CH));
console.log("root.NS ids:", root&&root.NS?Object.keys(root.NS).length:0);
// navegación: child(node,i) = root.NS[node.CH[i]]
function childAt(node,i){ const id=node&&node.CH&&node.CH[i]; return id!=null&&node.NS?node.NS[id]:null; }
const taps=[];
function walk(node,path,depth){ if(!node||depth>40)return; const ps=node.PS||{};
  for(const k in ps){ if(ps[k]==="eh"&&/tap|click/i.test(k)){ let tx=""; const c0=childAt(node,0); tx=(c0&&c0.TX)||node.TX||""; taps.push({path:path.slice(),TG:node.TG,prop:k,sid:ps["data-sid"],tx:tx}); } }
  const CH=node.CH||[]; for(let i=0;i<CH.length;i++) walk(childAt(node,i), path.concat(i), depth+1);
}
// root es un nodo con CH; empezar por sus hijos
if(root){ const CH=root.CH||[]; for(let i=0;i<CH.length;i++) walk(childAt(root,i),[i],0); }
console.log("\nnodos tap ("+taps.length+"):");
taps.forEach(t=>console.log("  path="+JSON.stringify(t.path)+" "+t.TG+" sid="+t.sid+" tx="+JSON.stringify(t.tx)));
require("fs").writeFileSync("/tmp/taps.json", JSON.stringify(taps));
