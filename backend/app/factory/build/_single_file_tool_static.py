"""Static behaviour for the single-file tool generator.

Generated from the owner's exec-programs.html reference: the CSS/tokens/layout,
markup and the JS engine (STATE/ENGINE/VIEWS/EVENTS) after the DATA block. None
of this is track content -- the DATA block is injected by render_single_file_tool
from the domain packs.
"""

HEAD = """<title>Executive Programs</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Spectral:wght@500;600&family=Libre+Franklin:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: one column tool, sticky program bar, tab strip, cards only for the working surfaces (session, memo, grade). */
:root{
  --bg:#f4f5f7; --panel:#ffffff; --ink:#15202b; --muted:#5b6672; --line:#d6dbe1;
  --accent:#1f3b63; --accent-ink:#ffffff; --tint:#e7edf5; --grade:#7a1f2b; --good:#1d6b45; --warn:#9a5a00;
  --display:"Spectral",Georgia,"Times New Roman",serif;
  --body:"Libre Franklin","Helvetica Neue",Arial,sans-serif;
  --mono:"IBM Plex Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#12171d; --panel:#1b222b; --ink:#e8ecf1; --muted:#9aa5b1; --line:#2e3844;
  --accent:#8fb3e6; --accent-ink:#0f1a2a; --tint:#1f2b3a; --grade:#e39aa5; --good:#6fcf97; --warn:#e3b35a; color-scheme:dark } }
:root[data-theme="dark"]{
  --bg:#12171d; --panel:#1b222b; --ink:#e8ecf1; --muted:#9aa5b1; --line:#2e3844;
  --accent:#8fb3e6; --accent-ink:#0f1a2a; --tint:#1f2b3a; --grade:#e39aa5; --good:#6fcf97; --warn:#e3b35a; color-scheme:dark }
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.5;margin:0}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--accent)}
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:9;background:var(--accent);color:var(--accent-ink);
  padding-block:12px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.bar h1{font-family:var(--display);font-weight:600;font-size:20px;margin:0 0 8px;letter-spacing:.01em;text-wrap:balance}
.bar .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.bar select,.bar input[type=date]{font:inherit;font-size:14px;padding:6px 8px;border:1px solid transparent;border-radius:4px;background:var(--panel);color:var(--ink);min-width:0}
.bar .prog{font-family:var(--mono);font-size:12px;margin-left:auto;white-space:nowrap}
.tabs{display:flex;gap:0;overflow-x:auto;border-bottom:1px solid var(--line);background:var(--bg);
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.tabs button{font:inherit;font-size:13px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:none;border:0;border-bottom:3px solid transparent;color:var(--muted);padding:10px 10px 8px;cursor:pointer;white-space:nowrap}
.tabs button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--accent)}
.tabs button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
main{padding-block:16px 48px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));max-width:760px;margin:0 auto}
h2{font-family:var(--display);font-weight:600;font-size:22px;margin:0 0 4px;text-wrap:balance}
h3{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:18px 0 6px}
p{margin:0 0 8px}
.lead{color:var(--muted);margin-bottom:12px}
.stack{display:flex;flex-direction:column;gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 14px;min-width:0}
.card.session{border-left:4px solid var(--accent)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:14px}
.kv dt{color:var(--muted)}
.kv dd{margin:0;min-width:0}
.btn{font:inherit;font-size:13px;font-weight:600;padding:7px 11px;border:1px solid var(--line);border-radius:4px;background:var(--panel);color:var(--ink);cursor:pointer}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.btn.danger{color:var(--grade);border-color:var(--grade)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
textarea{width:100%;min-height:160px;font:inherit;font-size:15px;line-height:1.5;padding:10px;border:1px solid var(--line);border-radius:4px;background:var(--panel);color:var(--ink);resize:vertical}
textarea.mono{font-family:var(--mono);font-size:12px;min-height:120px}
input[type=number],input[type=text]{font:inherit;font-size:14px;padding:6px 8px;border:1px solid var(--line);border-radius:4px;background:var(--panel);color:var(--ink);width:100%;min-width:0}
input[type=number]{font-family:var(--mono);width:64px;text-align:right}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);font-weight:600}
.tnum{font-family:var(--mono);font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto}
.pill{display:inline-block;font-family:var(--mono);font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.pill.A,.pill.B{color:var(--good);border-color:var(--good)}
.pill.C{color:var(--warn);border-color:var(--warn)}
.pill.R{color:var(--grade);border-color:var(--grade)}
.grade-grid{display:grid;grid-template-columns:1fr auto;gap:8px 10px;align-items:start}
.grade-grid label{font-size:14px}
.grade-grid small{display:block;color:var(--muted);font-size:12px}
.total{font-family:var(--display);font-size:28px;font-weight:600;margin-top:8px}
.total .band{font-family:var(--mono);font-size:14px;margin-left:8px}
.note{font-size:13px;color:var(--muted)}
.toast{position:fixed;left:50%;bottom:calc(24px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:8px 14px;border-radius:4px;font-size:13px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:20}
.toast.show{opacity:1}
.rowlink{cursor:pointer}
.rowlink:hover td{background:var(--tint)}
.mic{font-size:13px}
.ok{color:var(--good)} .bad{color:var(--grade)}
@media (prefers-reduced-motion:reduce){.toast{transition:none}}
</style>

<div class="bar">
  <h1>Executive Programs</h1>
  <div class="row">
    <select id="track" aria-label="Track"></select>
    <input type="date" id="start" aria-label="Start date">
    <span class="prog" id="prog">0 / 16 graded</span>
  </div>
</div>
<div class="tabs" role="tablist" id="tabs">
  <button role="tab" data-v="today" aria-selected="true">Today</button>
  <button role="tab" data-v="calendar">Calendar</button>
  <button role="tab" data-v="week">Week</button>
  <button role="tab" data-v="gradebook">Gradebook</button>
  <button role="tab" data-v="export">Export</button>
</div>
<main id="main"></main>
<div class="toast" id="toast" role="status"></div>

"""

STATIC_JS = """const KEY="execprog.v1";
let S={track:"COO",start:"",data:{}};
try{const r=localStorage.getItem(KEY);if(r){const o=JSON.parse(r);if(o&&o.data)S=o;}}catch(e){}
if(!TRACKS[S.track])S.track="COO";
if(!S.start){const d=new Date();S.start=iso(d);}
function save(){try{localStorage.setItem(KEY,JSON.stringify(S));}catch(e){}}
function wk(n){const t=S.data[S.track]=S.data[S.track]||{};return t[n]=t[n]||{memo:"",scores:["","","","",""],verdicts:["","","","",""],note:"",defense:"",jury:""};}
function iso(d){return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");}
function parse(s){const p=s.split("-").map(Number);return new Date(p[0],p[1]-1,p[2]);}
function addDays(d,n){const x=new Date(d);x.setDate(x.getDate()+n);return x;}
function fmt(d){return DAYN[d.getDay()]+" "+d.getDate()+" "+d.toLocaleString("en-GB",{month:"short"});}
function fmtLong(d){return DAYN[d.getDay()]+" "+d.getDate()+" "+d.toLocaleString("en-GB",{month:"short"})+" "+d.getFullYear();}
function weekStart(n){return addDays(parse(S.start),7*(n-1));}
function termOf(n){return Math.ceil(n/4);}
function total(w){const s=w.scores.map(Number);if(w.scores.some(v=>v==="")||s.some(isNaN))return null;return s.reduce((a,b)=>a+b,0);}
function band(t){if(t==null)return null;return t>=90?"A":t>=80?"B":t>=70?"C":"R";}
function bandText(b){return b==="R"?"Rewrite":b;}
function gradedCount(){let c=0;for(let n=1;n<=16;n++){if(total(wk(n))!=null)c++;}return c;}
function todayWeek(){const d=parse(iso(new Date()));const s=parse(S.start);const diff=Math.floor((d-s)/864e5);if(diff<0)return 0;const n=Math.floor(diff/7)+1;return n>16?17:n;}
function toast(m){const t=document.getElementById("toast");t.textContent=m;t.classList.add("show");clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove("show"),1800);}
function copy(text,label){const done=()=>toast(label||"Copied");
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(done).catch(()=>fallback(text,done));}else fallback(text,done);}
function fallback(text,done){const ta=document.createElement("textarea");ta.value=text;document.body.appendChild(ta);ta.select();try{document.execCommand("copy");done();}catch(e){toast("Select the text and copy it");}document.body.removeChild(ta);}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

// ---------- Faculty prompts ----------
const MODES={
 framework:"Mode: LECTURE. Teach the week's framework from the reading in executive register, then make me restate it in five sentences for my own company. Push back on vague restatements. End by handing me the case and its decision question for Friday, and by spelling out this week's deliverable and its due date.",
 case_prep:"Mode: BRIEF. Hand me the case and the decision question. Help me structure a one-page position: the decision, the numbers I would want, the alternative I reject. Refuse to discuss or reveal the real outcome.",
 case_class:"Mode: SOCRATIC. Run a case class: cold-call me, change the facts mid-way, force me to commit to a decision with numbers before you reveal what actually happened. Then give the outcome and what it teaches. 120 minutes of material; do not let me off with opinions.",
 lab:"Mode: SPARRING. I am writing this week's deliverable about my own company. You never draft it for me. Challenge every claim without a number, cut every sentence that is not a decision, and tell me what a demanding board member would ask. If I ask you to write it, refuse and ask the next hard question instead.",
 exam:"Mode: EXAMINER. I will paste my memo. Grade it on the five criteria below, 20 points each, one line per criterion with the score, then a total and band (A 90+, B 80–89, C 70–79, below 70 rewrite). Then run a 20-minute oral quiz on the week. End with one line I can paste into my gradebook: scores, total, band, one-sentence verdict.",
 defense:"Mode: EXAMINER (term defense, 45 minutes). I will present my term paper. Attack the weakest number and the weakest assumption first. Record PASS or FAIL with one sentence of reasoning. A fail is re-sat within a week.",
 jury:"Mode: JURY (capstone, 90 minutes). I will present the capstone. You are one of three jurors; grade it as an investor and an operator would. Record ACCEPT or REVISE with the reasons."};
function prompt(n,sess){const T=TRACKS[S.track],w=T.weeks[n-1],term=termOf(n);
 const lines=["FACULTY SESSION — "+T.name,"Week "+n+" of 16 · Term "+term+": "+T.terms[term-1][0]+" · Session: "+sess.name+" ("+sess.m+" min)","",
 "You are faculty, case instructor and examiner for an executive program run on the case method. Everything applies to MY company, not a textbook one. Executive level, no basics unless asked. Agreement is not teaching: push back hard.","",
 "THIS WEEK","Theme: "+w[0],"Reading: "+w[1],"Case: "+w[2],"Decision question: "+w[3],"Deliverable: "+w[4]+" ("+w[6]+" page"+(w[6]>1?"s":"")+") — "+w[5],"",
 MODES[sess.k],"","RUBRIC (20 points each)"].concat(RUBRIC.map(r=>"- "+r[0]+": "+r[1]));
 lines.push("","Start now with the first item of this session.");return lines.join("\n");}
function defensePrompt(n){const T=TRACKS[S.track],term=termOf(n);return ["FACULTY SESSION — "+T.name,"Term "+term+" defense: "+T.terms[term-1][0],"","Term outcome: "+T.terms[term-1][1],"",MODES.defense].join("\n");}
function juryPrompt(){const T=TRACKS[S.track];return ["FACULTY SESSION — "+T.name,"Capstone jury","",MODES.jury].join("\n");}

// ---------- Views ----------
let view="today",curWeek=null;
function render(){const main=document.getElementById("main");document.getElementById("prog").textContent=gradedCount()+" / 16 graded";
 document.querySelectorAll("#tabs button").forEach(b=>b.setAttribute("aria-selected",b.dataset.v===view));
 main.innerHTML={today:vToday,calendar:vCalendar,week:vWeek,gradebook:vGradebook,export:vExport}[view]();bind(main);}
function vToday(){const T=TRACKS[S.track];const n=todayWeek();const today=parse(iso(new Date()));
 if(n===0)return `<h2>Starts ${fmtLong(parse(S.start))}</h2><p class="lead">${esc(T.name)}. ${esc(T.audience)}</p><p>Before day one: get the week 1 reading, <em>${esc(T.weeks[0][1])}</em>.</p>${weekCard(1)}`;
 if(n===17)return `<h2>Program complete</h2><p class="lead">Ended ${fmtLong(addDays(weekStart(16),6))}. ${gradedCount()} of 16 memos graded.</p>`;
 const ws=weekStart(n);const off=Math.floor((today-ws)/864e5);const sess=SESSIONS.find(s=>s.d===off);const w=T.weeks[n-1];const g=wk(n);
 let h=`<h2>Week ${n} · ${esc(w[0])}</h2><p class="lead">Term ${termOf(n)}: ${esc(T.terms[termOf(n)-1][0])} · ${fmt(ws)} to ${fmt(addDays(ws,6))}</p>`;
 if(sess){h+=sessionCard(n,sess,true);}else{h+=`<div class="card"><strong>Sunday — no session.</strong> Rest, or catch up on the reading.</div>`;}
 h+=`<h3>This week's deliverable</h3><div class="card"><strong>${esc(w[4])}</strong> · ${w[6]} page${w[6]>1?"s":""} · due ${fmt(addDays(ws,5))}<p>${esc(w[5])}</p>`;
 const t=total(g);h+=t!=null?`<p>Graded: <span class="pill ${band(t)}">${t} · ${bandText(band(t))}</span></p>`:`<p class="note">${g.memo?"Memo drafted, not graded yet.":"No memo yet."}</p>`;
 h+=`<div class="btns"><button class="btn primary" data-go="${n}">Open week ${n}</button></div></div>`;return h;}
function sessionCard(n,sess,big){const ws=weekStart(n);const d=addDays(ws,sess.d);
 return `<div class="card session"><div class="kv"><dt>Session</dt><dd><strong>${esc(sess.name)}</strong> · ${sess.m} min</dd><dt>Date</dt><dd>${fmt(d)}</dd><dt>Do</dt><dd>${esc(sessDo(sess.k))}</dd></div>
 <div class="btns"><button class="btn primary" data-prompt="${n}:${SESSIONS.indexOf(sess)}">Copy faculty prompt</button>${big?`<button class="btn" data-show="${n}:${SESSIONS.indexOf(sess)}">Show prompt</button>`:""}</div><pre class="hidden" hidden id="pv${n}-${SESSIONS.indexOf(sess)}" style="white-space:pre-wrap;font:12px/1.5 var(--mono);margin:10px 0 0"></pre></div>`;}
function sessDo(k){return{framework:"Read the chapters first. Faculty lectures; you restate it for your own company.",case_prep:"Read the case cold. Write a one-page position: decision, numbers, rejected alternative.",case_class:"Oral case discussion. Defend a decision, not an opinion.",lab:"Write the deliverable about your own company. Faculty spars; it never drafts.",exam:"Paste the memo, get it graded on the rubric, then a 20-minute oral quiz. Record the grade here."}[k];}
function weekCard(n){const T=TRACKS[S.track],w=T.weeks[n-1],ws=weekStart(n);return `<div class="card"><strong>Week ${n} · ${esc(w[0])}</strong><div class="note">${fmt(ws)} to ${fmt(addDays(ws,6))}</div><p>Reading: ${esc(w[1])}</p><div class="btns"><button class="btn" data-go="${n}">Open week ${n}</button></div></div>`;}
function vCalendar(){const T=TRACKS[S.track];let h=`<h2>${esc(T.name)}</h2><p class="lead">16 weeks from ${fmtLong(parse(S.start))}. Weeks run Thursday to Wednesday when you start on a Thursday; change the start date in the bar.</p><div class="wrap"><table><tr><th>Wk</th><th>Dates</th><th>Theme</th><th>Grade</th></tr>`;
 for(let n=1;n<=16;n++){const ws=weekStart(n);const t=total(wk(n));if(n%4===1)h+=`<tr><td colspan="4"><h3 style="margin:8px 0 0">Term ${termOf(n)} · ${esc(T.terms[termOf(n)-1][0])}</h3></td></tr>`;
  h+=`<tr class="rowlink" data-go="${n}"><td class="tnum">${n}</td><td class="tnum">${fmt(ws)}–${fmt(addDays(ws,6))}</td><td>${esc(T.weeks[n-1][0])}</td><td>${t!=null?`<span class="pill ${band(t)}">${bandText(band(t))}</span>`:""}</td></tr>`;}
 return h+"</table></div>";}
function vWeek(){const T=TRACKS[S.track];const n=curWeek||Math.min(Math.max(todayWeek(),1),16);curWeek=n;const w=T.weeks[n-1],ws=weekStart(n),g=wk(n),term=termOf(n);
 let h=`<div class="btns" style="margin:0 0 10px"><button class="btn" data-nav="-1" ${n===1?"disabled":""}>‹ Week ${n-1}</button><button class="btn" data-nav="1" ${n===16?"disabled":""}>Week ${n+1} ›</button></div>
 <h2>Week ${n} · ${esc(w[0])}</h2><p class="lead">Term ${term}: ${esc(T.terms[term-1][0])} · ${fmt(ws)} to ${fmt(addDays(ws,6))}</p>
 <div class="kv card"><dt>Reading</dt><dd>${esc(w[1])}</dd><dt>Case</dt><dd>${esc(w[2])}</dd><dt>Question</dt><dd>${esc(w[3])}</dd><dt>Deliverable</dt><dd><strong>${esc(w[4])}</strong> · ${w[6]} page${w[6]>1?"s":""} · due ${fmt(addDays(ws,5))}<br>${esc(w[5])}</dd></div>
 <h3>Sessions</h3><div class="stack">${SESSIONS.map(s=>sessionCard(n,s,false)).join("")}</div>
 <h3>Memo</h3><div class="card"><textarea id="memo" placeholder="Write or dictate the deliverable here. It saves on this device as you type.">${esc(g.memo)}</textarea>
 <div class="btns"><button class="btn mic" id="mic" hidden>🎤 Dictate</button><button class="btn" data-copymemo="1">Copy memo</button><span class="note" id="memoWords"></span></div></div>
 <h3>Grade</h3><div class="card"><p class="note">Paste what faculty gave you: a score out of 20 per criterion and its one-line verdict.</p><div class="grade-grid">`;
 RUBRIC.forEach((r,i)=>{h+=`<div><label for="sc${i}">${esc(r[0])}<small>${esc(r[1])}</small></label><input type="text" id="vd${i}" data-vd="${i}" placeholder="Verdict line" value="${esc(g.verdicts[i])}" style="margin-top:4px"></div><div><input type="number" id="sc${i}" data-sc="${i}" min="0" max="20" value="${esc(g.scores[i])}" aria-label="${esc(r[0])} score"></div>`;});
 const t=total(g);h+=`</div><div class="total" id="tot">${t!=null?t+" <span class='band pill "+band(t)+"'>"+bandText(band(t))+"</span>":"—"}</div></div>`;
 if(n%4===0&&n<16){h+=`<h3>Term ${term} defense · ${fmt(addDays(ws,6))}</h3><div class="card"><p>${esc(T.terms[term-1][1])}</p><div class="btns"><button class="btn primary" data-defense="${n}">Copy defense prompt</button></div><label style="display:block;margin-top:8px">Result <input type="text" id="defense" placeholder="PASS or FAIL, one sentence" value="${esc(g.defense)}"></label></div>`;}
 if(n===16){h+=`<h3>Capstone jury · ${fmt(addDays(ws,6))}</h3><div class="card"><p>Jury of three: faculty plus two reviewers you invite, ideally one investor and one operator. Accepted when all three accept.</p><div class="btns"><button class="btn primary" data-jury="1">Copy jury prompt</button></div><label style="display:block;margin-top:8px">Verdicts <input type="text" id="jury" placeholder="Juror 1 / Juror 2 / Juror 3" value="${esc(g.jury)}"></label></div>`;}
 return h;}
function vGradebook(){const T=TRACKS[S.track];let sum=0,c=0,h=`<h2>Gradebook</h2><p class="lead">${esc(T.name)}. Memos 60%, defenses 20%, capstone 20%. Graduation: B average, four passes, capstone accepted.</p><div class="wrap"><table><tr><th>Wk</th><th>Deliverable</th><th>Total</th><th>Band</th><th>Verdict</th></tr>`;
 for(let n=1;n<=16;n++){const g=wk(n),t=total(g);if(t!=null){sum+=t;c++;}h+=`<tr class="rowlink" data-go="${n}"><td class="tnum">${n}</td><td>${esc(T.weeks[n-1][4])}</td><td class="tnum">${t!=null?t:""}</td><td>${t!=null?`<span class="pill ${band(t)}">${bandText(band(t))}</span>`:""}</td><td class="note">${esc(g.verdicts.filter(Boolean)[0]||"")}</td></tr>`;}
 h+=`</table></div><h3>Defenses</h3><div class="wrap"><table><tr><th>Term</th><th>Date</th><th>Result</th></tr>`;
 for(let k=1;k<=3;k++){const n=k*4;h+=`<tr class="rowlink" data-go="${n}"><td>Term ${k} · ${esc(T.terms[k-1][0])}</td><td class="tnum">${fmt(addDays(weekStart(n),6))}</td><td>${esc(wk(n).defense)}</td></tr>`;}
 h+=`<tr class="rowlink" data-go="16"><td>Capstone jury</td><td class="tnum">${fmt(addDays(weekStart(16),6))}</td><td>${esc(wk(16).jury)}</td></tr></table></div>`;
 const avg=c?(sum/c):null;h+=`<div class="total">${avg!=null?avg.toFixed(1)+` <span class="band pill ${band(avg)}">average of ${c}</span>`:"No grades yet"}</div>`;return h;}
function vExport(){return `<h2>Export and backup</h2><p class="lead">Everything you type stays on this device. Copy a backup now and then; paste it back here on another device.</p>
 <div class="stack"><div class="card"><strong>Answers as Markdown</strong><p class="note">Memos, grades and verdicts for the current track, readable anywhere.</p><div class="btns"><button class="btn primary" data-export="md">Copy Markdown</button>${location.protocol==="file:"?`<button class="btn" data-dl="md">Download .md</button>`:""}</div></div>
 <div class="card"><strong>Backup</strong><p class="note">Both tracks, every field, as JSON.</p><div class="btns"><button class="btn" data-export="json">Copy backup</button>${location.protocol==="file:"?`<button class="btn" data-dl="json">Download .json</button>`:""}</div></div>
 <div class="card"><strong>Restore</strong><p class="note">Paste a backup here. It replaces what is on this device.</p><textarea class="mono" id="restore" placeholder='{"track":"COO", ...}'></textarea><div class="btns"><button class="btn" data-restore="1">Restore from paste</button><label class="btn">Load .json file <input type="file" id="restoreFile" accept=".json,application/json" hidden></label></div></div>
 <div class="card"><strong>Clear this device</strong><p class="note">Removes every memo and grade for both tracks.</p><div class="btns"><button class="btn danger" data-clear="ask">Clear everything</button></div></div></div>`;}
function markdown(){const T=TRACKS[S.track];const L=["# "+T.name,"","Started "+fmtLong(parse(S.start))+" · exported "+fmtLong(new Date()),""];
 for(let n=1;n<=16;n++){const g=wk(n),w=T.weeks[n-1],t=total(g);L.push("## Week "+n+" — "+w[0],"","Deliverable: "+w[4]+" · due "+fmt(addDays(weekStart(n),5)),"");
  L.push(g.memo?g.memo:"_(no memo)_","");if(t!=null){L.push("Grade: "+t+" · "+bandText(band(t)));RUBRIC.forEach((r,i)=>L.push("- "+r[0]+": "+g.scores[i]+"/20 — "+g.verdicts[i]));L.push("");}
  if(g.defense)L.push("Term "+termOf(n)+" defense: "+g.defense,"");if(g.jury)L.push("Capstone jury: "+g.jury,"");}
 return L.join("\n");}
function download(name,text){const a=document.createElement("a");a.href="data:text/plain;charset=utf-8,"+encodeURIComponent(text);a.download=name;document.body.appendChild(a);a.click();a.remove();}

// ---------- Events ----------
function bind(main){
 main.querySelectorAll("[data-go]").forEach(el=>el.addEventListener("click",()=>{curWeek=Number(el.dataset.go);view="week";render();window.scrollTo(0,0);}));
 main.querySelectorAll("[data-nav]").forEach(el=>el.addEventListener("click",()=>{curWeek+=Number(el.dataset.nav);render();window.scrollTo(0,0);}));
 main.querySelectorAll("[data-prompt]").forEach(el=>el.addEventListener("click",()=>{const [n,i]=el.dataset.prompt.split(":").map(Number);copy(prompt(n,SESSIONS[i]),"Faculty prompt copied");}));
 main.querySelectorAll("[data-show]").forEach(el=>el.addEventListener("click",()=>{const [n,i]=el.dataset.show.split(":").map(Number);const pre=document.getElementById("pv"+n+"-"+i);pre.textContent=prompt(n,SESSIONS[i]);pre.hidden=!pre.hidden;}));
 main.querySelectorAll("[data-defense]").forEach(el=>el.addEventListener("click",()=>copy(defensePrompt(Number(el.dataset.defense)),"Defense prompt copied")));
 main.querySelectorAll("[data-jury]").forEach(el=>el.addEventListener("click",()=>copy(juryPrompt(),"Jury prompt copied")));
 main.querySelectorAll("[data-copymemo]").forEach(el=>el.addEventListener("click",()=>copy(wk(curWeek).memo,"Memo copied")));
 const memo=main.querySelector("#memo");if(memo){const words=()=>{const c=memo.value.trim()?memo.value.trim().split(/\s+/).length:0;main.querySelector("#memoWords").textContent=c+" words";};words();
  memo.addEventListener("input",()=>{wk(curWeek).memo=memo.value;save();words();});setupMic(main,memo);}
 main.querySelectorAll("[data-sc]").forEach(el=>el.addEventListener("input",()=>{let v=el.value;if(v!==""){v=Math.max(0,Math.min(20,Number(v)));el.value=v;}wk(curWeek).scores[Number(el.dataset.sc)]=v===""?"":String(v);save();const t=total(wk(curWeek));main.querySelector("#tot").innerHTML=t!=null?t+" <span class='band pill "+band(t)+"'>"+bandText(band(t))+"</span>":"—";document.getElementById("prog").textContent=gradedCount()+" / 16 graded";}));
 main.querySelectorAll("[data-vd]").forEach(el=>el.addEventListener("input",()=>{wk(curWeek).verdicts[Number(el.dataset.vd)]=el.value;save();}));
 const df=main.querySelector("#defense");if(df)df.addEventListener("input",()=>{wk(curWeek).defense=df.value;save();});
 const jr=main.querySelector("#jury");if(jr)jr.addEventListener("input",()=>{wk(curWeek).jury=jr.value;save();});
 main.querySelectorAll("[data-export]").forEach(el=>el.addEventListener("click",()=>copy(el.dataset.export==="md"?markdown():JSON.stringify(S,null,1),el.dataset.export==="md"?"Markdown copied":"Backup copied")));
 main.querySelectorAll("[data-dl]").forEach(el=>el.addEventListener("click",()=>el.dataset.dl==="md"?download("executive-program-"+S.track.toLowerCase()+".md",markdown()):download("executive-program-backup.json",JSON.stringify(S,null,1))));
 main.querySelectorAll("[data-restore]").forEach(el=>el.addEventListener("click",()=>restore(main.querySelector("#restore").value)));
 const rf=main.querySelector("#restoreFile");if(rf)rf.addEventListener("change",()=>{const f=rf.files[0];if(!f)return;const r=new FileReader();r.onload=()=>restore(String(r.result));r.readAsText(f);});
 main.querySelectorAll("[data-clear]").forEach(el=>el.addEventListener("click",()=>{if(el.dataset.clear==="ask"){el.textContent="Tap again to confirm";el.dataset.clear="yes";}else{S.data={};save();toast("Cleared");render();}}));
}
function restore(txt){try{const o=JSON.parse(txt);if(!o||typeof o!=="object"||!o.data)throw 0;S=o;if(!TRACKS[S.track])S.track="COO";save();initBar();toast("Restored");render();}catch(e){toast("That is not a backup from this app");}}
function setupMic(main,memo){const SR=window.SpeechRecognition||window.webkitSpeechRecognition;const btn=main.querySelector("#mic");if(!SR||!btn)return;btn.hidden=false;let rec=null,on=false;
 btn.addEventListener("click",()=>{if(on){rec.stop();return;}try{rec=new SR();rec.lang="en-GB";rec.continuous=true;rec.interimResults=false;
  rec.onresult=e=>{let t="";for(let i=e.resultIndex;i<e.results.length;i++)if(e.results[i].isFinal)t+=e.results[i][0].transcript+" ";if(t){memo.value=(memo.value.trim()+" "+t).trim()+" ";memo.dispatchEvent(new Event("input"));}};
  rec.onend=()=>{on=false;btn.textContent="🎤 Dictate";};rec.onerror=()=>{on=false;btn.textContent="🎤 Dictate";btn.hidden=true;toast("Dictation is not available here; open the saved file in a browser for the mic");};
  rec.start();on=true;btn.textContent="■ Stop dictating";}catch(e){btn.hidden=true;toast("Dictation is not available here");}});}
function initBar(){const sel=document.getElementById("track");sel.innerHTML=Object.keys(TRACKS).map(k=>`<option value="${k}">${esc(TRACKS[k].name)}</option>`).join("");sel.value=S.track;document.getElementById("start").value=S.start;}
document.getElementById("track").addEventListener("change",e=>{S.track=e.target.value;curWeek=null;save();render();});
document.getElementById("start").addEventListener("change",e=>{if(e.target.value){S.start=e.target.value;save();render();}});
document.getElementById("tabs").addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;view=b.dataset.v;render();});
initBar();render();
"""
