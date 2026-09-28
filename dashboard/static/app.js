"use strict";
let state, filter = "all";
const el = id => document.getElementById(id);
const node = (tag, text, cls) => {const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e;};
function append(parent, ...children){children.forEach(c=>parent.appendChild(c));}
function renderCatalog(){
  const list=el("catalog"); list.replaceChildren();
  for(const c of state.discovered_protocols||[]){
    if(filter==="issuance" && !["Protocol issuance","Competitive protocol emissions"].includes(c.mechanism))continue;
    if(filter==="demand" && !c.mechanism.startsWith("Customer-funded"))continue;
    const row=node("article",undefined,"candidate"), title=node("div"), body=node("div");
    append(title,node("h3",c.name),node("span",c.mechanism,"status"),node("p",c.decision));
    for(const [label,key] of [["Work","work"],["Entry cost","upfront"],["Payment","payment"],["What remains uncertain","uncertainty"],["Next step","next_step"]]){
      const p=node("p"); append(p,node("span",label+": ","label"),node("span",c[key])); body.appendChild(p);
    }
    const links=node("div",undefined,"links");
    for(const s of c.sources||[]){
      const url=new URL(s.url); if(url.protocol!=="https:")continue;
      const a=node("a",s.title); a.href=url.href; a.target="_blank"; a.rel="noopener noreferrer"; links.appendChild(a);
    }
    body.appendChild(links); append(row,title,body); list.appendChild(row);
  }
}
function render(data){
  state=data;
  el("connection").textContent="Connected to local observer";
  el("updated").textContent=new Date(data.observed_at).toLocaleTimeString();
  el("notice").textContent=data.notice;
  el("mode").textContent=data.mode==="bounded_pilot"?"Bounded CPU pilot":"Observe mode — workloads disabled";
  const p=data.protocols.find(p=>p.name==="Monero / P2Pool");
  el("xmr").textContent=data.totals.wallet_reported_mined_xmr??"Unknown";
  el("hashrate").textContent=p?.work?.hashrate_hs!=null?p.work.hashrate_hs.toFixed(1)+" H/s":"Unknown";
  el("shares").textContent=p?.work? p.work.accepted_shares+" miner-reported accepted shares; not payments.":"No miner evidence connected.";
  el("experiment").replaceChildren(node("span",p?.status??"INITIALIZING","status"),node("p",p?.reason??"Initializing observations."));
  for(const e of p?.errors||[])el("experiment").appendChild(node("p",e,"error"));
  if(p?.payments)el("experiment").appendChild(node("p",p.payments.scope,"fine"));
  el("ideas").replaceChildren();
  for(const item of data.expansion_ideas){
    const section=node("div",undefined,"proposal");
    append(section,node("span",item.status,"status"),node("h4",item.title),node("p",item.action),node("p",item.reason,"fine"));
    el("ideas").appendChild(section);
  }
  el("resources").replaceChildren();
  const m=data.system_metrics;
  for(const [k,v] of [["Observer CPU",m.process_cpu_pct==null?"Unknown":m.process_cpu_pct+"% of one core"],["Priority",m.priority??"Unknown"],["Logical CPUs",m.logical_cpus??"Unknown"],["Pilot",data.pilot.status],["Upfront spending","Disabled"],["Electricity tariff",data.economics.electricity_gbp_per_kwh==null?"Not provided":"£"+data.economics.electricity_gbp_per_kwh+"/kWh"]])append(el("resources"),node("dt",k),node("dd",String(v)));
  el("integrations").replaceChildren();
  for(const p of data.protocols.filter(p=>p.name!=="Monero / P2Pool")){
    const item=node("div",undefined,"integration"); append(item,node("strong",p.name),node("span",p.status+" — "+p.reason)); el("integrations").appendChild(item);
  }
  el("logs").textContent=data.live_logs.map(l=>l.timestamp+" ["+l.protocol+"] "+l.message).join("\n");
  renderCatalog();
}
async function poll(){
  try{
    const response=await fetch("/api/status",{cache:"no-store",signal:AbortSignal.timeout(5000)});
    if(!response.ok)throw new Error("Status request failed");
    render(await response.json());
  }catch(e){el("connection").textContent="Disconnected — displayed evidence may be stale";}
  setTimeout(poll,5000);
}
document.querySelectorAll("[data-filter]").forEach(button=>button.addEventListener("click",()=>{
  filter=button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach(b=>b.classList.toggle("selected",b===button));
  if(state)renderCatalog();
}));
poll();

