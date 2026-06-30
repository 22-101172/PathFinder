/* ============================================================
   PathFinder — components + rich cards
   window: Icon, LogoMark, MsgAvatar, Block, Caps
   ============================================================ */
const { useEffect, useRef, useState } = React;

/* ---------------- EUI logo mark (chevron pathfinder arrow) ---------------- */
function LogoMark({ className, style }){
  return (
    <svg className={className} style={style} viewBox="0 0 91 99" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M86.7649 41.4642C87.8298 42.4108 88.6876 43.5674 89.2843 44.8612C89.881 46.155 90.2039 47.5583 90.2326 48.9828C90.2613 50.4073 89.9953 51.8224 89.4513 53.1393C88.9072 54.4561 88.0968 55.6463 87.0709 56.6352C80.1279 63.6282 73.1339 70.5822 66.1599 77.5552C59.4006 84.3252 52.6379 91.0915 45.8719 97.8542C45.6369 98.0892 45.3829 98.3132 45.3109 98.3742C33.8409 86.8842 22.4186 75.4552 11.0439 64.0872L48.4709 26.6602C50.7449 28.9032 53.181 31.3092 55.6489 33.7462L25.6639 63.7002L45.0969 83.1322L86.7649 41.4642Z" fill="#0046AD"/>
      <path d="M9.31074 63.1901C6.32188 60.7257 3.72931 57.8168 1.62374 54.5651C0.344337 52.5287 -0.205144 50.1181 0.0654012 47.7284C0.335946 45.3387 1.41046 43.1119 3.11274 41.4131C13.0127 31.4011 23.0127 21.4811 32.9747 11.5301C33.0457 11.4591 33.1377 11.4301 33.0667 11.4691C35.5337 13.9571 37.9807 16.4141 40.5297 18.9931C40.3967 19.1361 40.0197 19.5641 39.6227 19.9621C29.9774 29.6174 20.3291 39.2658 10.6777 48.9071C10.5447 49.0401 10.4127 49.1821 10.2697 49.3151C6.22174 53.0461 5.32474 55.2991 8.51574 61.6921C8.68974 62.0691 8.90274 62.4361 9.31074 63.1901Z" fill="#00877C"/>
      <path d="M53.1006 22.0111C55.3436 19.7271 57.7496 17.2911 60.0436 14.9561L82.4206 37.3351C80.1206 39.6091 77.6796 42.0251 75.3756 44.2981C68.0266 36.9371 60.5026 29.4131 53.1006 22.0111Z" fill="#C1A03E"/>
      <path d="M45.2319 0C47.4439 2.192 49.8709 4.6 52.2569 6.963C49.8299 9.349 47.3839 11.755 45.0489 14.049C42.7749 11.775 40.3489 9.359 38.0859 7.096C40.3759 4.833 42.8019 2.416 45.2319 0Z" fill="#C1A03E"/>
    </svg>
  );
}

/* ---------------- icons ---------------- */
const PATHS = {
  compass:["M12 2a10 10 0 100 20 10 10 0 000-20z","M16 8l-2.5 5.5L8 16l2.5-5.5L16 8z"],
  send:["M22 2L11 13","M22 2l-7 20-4-9-9-4 20-7z"],
  plus:["M12 5v14","M5 12h14"],
  menu:["M3 6h18","M3 12h18","M3 18h18"],
  sparkle:["M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"],
  arrow:["M5 12h14","M13 6l6 6-6 6"],
  check:["M20 6L9 17l-5-5"],
  calendar:["M8 2v4","M16 2v4","M3 10h18","M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z"],
  trend:["M23 6l-9.5 9.5-5-5L1 18","M17 6h6v6"],
  route:["M6 19a3 3 0 100-6 3 3 0 000 6z","M18 11a3 3 0 100-6 3 3 0 000 6z","M9 16h6a3 3 0 003-3V8"],
  book:["M4 19.5A2.5 2.5 0 016.5 17H20","M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"],
  chart:["M18 20V10","M12 20V4","M6 20v-6"],
  target:["M12 2a10 10 0 100 20 10 10 0 000-20z","M12 6a6 6 0 100 12 6 6 0 000-12z","M12 10a2 2 0 100 4 2 2 0 000-4z"],
  swap:["M16 3l4 4-4 4","M20 7H8","M8 21l-4-4 4-4","M4 17h12"],
  graph:["M5 5a2 2 0 100-4 2 2 0 000 4z","M19 23a2 2 0 100-4 2 2 0 000 4z","M5 19a2 2 0 100-4 2 2 0 000 4z","M5 5v10","M5 17l9-9h5"],
  ale:["M9 11l3 3 8-8","M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"],
  ai:["M12 2a3 3 0 013 3 3 3 0 010 6 3 3 0 01-3 3 3 3 0 01-3-3 3 3 0 010-6 3 3 0 013-3z","M12 14v8","M8 22h8"],
  code:["M16 18l6-6-6-6","M8 6l-6 6 6 6"],
  data:["M12 2a8 3 0 100 6 8 3 0 000-6z","M4 5v14a8 3 0 0016 0V5","M4 12a8 3 0 0016 0"],
  shield:["M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6l8-4z"],
  user:["M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2","M12 11a4 4 0 100-8 4 4 0 000 8z"],
  paperclip:["M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"],
  quote:["M3 21c3 0 7-1 7-8V5a2 2 0 00-2-2H4a2 2 0 00-2 2v6a2 2 0 002 2h2","M14 21c3 0 7-1 7-8V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v6a2 2 0 002 2h2"],
  cap:["M22 10L12 5 2 10l10 5 10-5z","M6 12v5c0 1 2.5 3 6 3s6-2 6-3v-5"],
  gear:["M12 15a3 3 0 100-6 3 3 0 000 6z","M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"],
};
function Icon({name, className, style}){
  const d = PATHS[name] || PATHS.sparkle;
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {d.map((p,i)=><path key={i} d={p} />)}
    </svg>
  );
}

function MsgAvatar({ who, initials }){
  if (who === "bot") return <div className="msg-av bot"><LogoMark /></div>;
  return <div className="msg-av usr">{initials}</div>;
}

const ENGINE = {
  kg:{ cls:"kg", icon:"graph", label:"Knowledge Graph" },
  rag:{ cls:"rag", icon:"book", label:"Handbook · RAG" },
  ale:{ cls:"ale", icon:"ale", label:"Academic Logic" },
};
function CardHead({ engine, title }){
  const e = ENGINE[engine] || ENGINE.kg;
  return (
    <div className="card-head">
      <div className={"ic "+e.cls}><Icon name={e.icon} /></div>
      <h4>{title}</h4>
      <span className="tag"><span className={"td "+e.cls} />{e.label}</span>
    </div>
  );
}

const STAT = {
  done:{cls:"st-done",label:"Completed"}, prog:{cls:"st-prog",label:"In progress"},
  elig:{cls:"st-elig",label:"Eligible"}, lock:{cls:"st-lock",label:"Locked"},
};
function Course({ c }){
  const s = STAT[c.status] || STAT.lock;
  return (
    <div className="course">
      <div style={{flex:1,minWidth:0}}>
        <div className="cc-code">{c.code}</div>
        <div className="cc-title">{c.title}</div>
        <div className="cc-meta">
          <span className="chiplet mono">{c.credits} cr</span>
          {c.grade && c.grade!=="—" && <span className="chiplet">Grade {c.grade}</span>}
          {c.term && <span className="chiplet">{c.term}</span>}
        </div>
      </div>
      <span className={"cc-status "+s.cls}>{s.label}</span>
    </div>
  );
}

function Prereq({ b }){
  return (
    <div className="card">
      <CardHead engine={b.engine} title={b.title} />
      <div className="card-body">
        <div className="chain">
          {b.forward ? (
            <>
              <div className="node target"><span className="nm">{b.target.code}</span><span>{b.target.name}</span></div>
              <div className="arrow"><Icon name="arrow" /></div>
              <div style={{display:"flex",flexDirection:"column",gap:8}}>
                {b.nodes.map((n,i)=>(<div key={i} className="node"><span className="nm">{n.code}</span><span>{n.name}</span></div>))}
              </div>
            </>
          ) : (
            <>
              {b.nodes.map((n,i)=>(
                <React.Fragment key={i}>
                  <div className={"node "+(n.met?"met":"miss")}>
                    {n.met && <Icon name="check" className="tick" style={{width:14,height:14}} />}
                    <span className="nm">{n.code}</span><span>{n.name}</span>
                  </div>
                  <div className="arrow"><Icon name="arrow" /></div>
                </React.Fragment>
              ))}
              <div className="node target"><span className="nm">{b.target.code}</span><span>{b.target.name}</span></div>
            </>
          )}
        </div>
        {b.verdict && (
          <div className={"verdict "+(b.verdict.ok?"yes":"no")}>
            <div className="vic"><Icon name={b.verdict.ok?"check":"target"} style={{width:15,height:15}} /></div>
            <div>{b.verdict.head}<small>{b.verdict.sub}</small></div>
          </div>
        )}
      </div>
    </div>
  );
}

function VerdictCard({ b }){
  return (<div className="card"><div className="card-body" style={{padding:14}}>
    <div className={"verdict "+(b.ok?"yes":"no")} style={{marginTop:0}}>
      <div className="vic"><Icon name="check" style={{width:15,height:15}} /></div>
      <div>{b.head}<small>{b.sub}</small></div>
    </div></div></div>);
}

function CourseList({ b }){
  return (<div className="card"><CardHead engine={b.engine} title={b.title} />
    <div className="card-body">{b.items.map((c,i)=><Course key={i} c={c} />)}</div></div>);
}

function Audit({ b }){
  const [p,setP] = useState(0);
  useEffect(()=>{ const t=setTimeout(()=>setP(b.percent),120); return ()=>clearTimeout(t); },[b.percent]);
  return (
    <div className="card">
      <CardHead engine={b.engine} title={b.title} />
      <div className="card-body">
        {!b.compact && (
          <div className="audit-top">
            <div className="ring" style={{["--p"]:p}}><div className="rv"><b>{b.percent}%</b><span>complete</span></div></div>
            <div className="audit-stats">
              <div className="mini"><div className="mk">{b.skills?"Skills":"Credits"}</div><div className="mv">{b.earned}<small> / {b.total}</small></div></div>
              <div className="mini"><div className="mk">{b.skills?"Gaps":"Remaining"}</div><div className="mv">{b.remaining}</div></div>
              {b.gpa!=="—" && <div className="mini"><div className="mk">Cum. GPA</div><div className="mv">{b.gpa}</div></div>}
              <div className="mini"><div className="mk">Standing</div><div className="mv" style={{fontSize:13,fontFamily:"var(--font-ui)"}}>{b.standing}</div></div>
            </div>
          </div>
        )}
        <div className="reqbar">
          {b.reqs.map((r,i)=>{const pct=Math.round((r.done/r.total)*100);return (
            <div className="reqrow" key={i}>
              <div className="rl"><span style={{color:"var(--text)",fontWeight:700}}>{r.label}</span><span>{r.note?r.note:`${r.done}/${r.total}`}</span></div>
              <div className="trackbar"><i className={r.kind} style={{width:pct+"%"}} /></div>
            </div>);})}
        </div>
      </div>
    </div>
  );
}

function Gpa({ b }){
  return (<div className="card"><CardHead engine={b.engine} title={b.title} />
    <div className="card-body">
      <div className="gpa-grid">
        <div className="gpa-now"><div className="gpa-k">Current</div><div className="gpa-v">{b.now}</div><div className="gpa-k" style={{marginTop:4,textTransform:"none",fontWeight:600}}>{b.creditsNow} credits</div></div>
        <div className="gpa-arrow"><Icon name="arrow" /></div>
        <div className="gpa-proj"><div className="gpa-k">Projected</div><div className="gpa-v">{b.proj}</div><div className="gpa-delta">{b.delta} this term</div></div>
      </div>
      <div className="assume">
        {b.rows.map((r,i)=>(<div className="ar" key={i}>
          <span><b className="mono" style={{fontSize:12,color:"var(--primary-bright)",marginInlineEnd:8}}>{r.code}</b>{r.title}</span>
          <span className="ac2">{r.credits} cr · <b style={{color:"var(--text)"}}>{r.grade}</b> ({r.pts})</span>
        </div>))}
      </div>
    </div></div>);
}

function Plan({ b }){
  return (<div className="card"><CardHead engine={b.engine} title={b.title} />
    <div className="card-body" style={{paddingTop:6}}>
      <table className="plan">
        <thead><tr><th>Course</th><th>Why</th><th style={{textAlign:"center"}}>Cr</th></tr></thead>
        <tbody>{b.rows.map((r,i)=>(
          <tr key={i} style={r.hl?{background:"var(--accent-soft)"}:null}>
            <td><div className="pcode">{r.code}</div><div className="ptitle">{r.title}</div></td>
            <td className="why">{r.why}</td><td className="pcr">{r.credits}</td>
          </tr>))}</tbody>
        <tfoot><tr><td>Total load</td><td></td><td className="pcr ttl">{b.total}</td></tr></tfoot>
      </table>
    </div></div>);
}

function Tracks({ b }){
  return (<div className="card"><CardHead engine={b.engine} title={b.title} />
    <div className="card-body"><div className="tracks">
      {b.items.map((t,i)=>(
        <div key={i} className={"trk"+(t.best?" best":"")}>
          {t.best && <div className="tbadge">Best match</div>}
          <div className="tname"><div className="temoji"><Icon name={t.icon} /></div>{t.name}</div>
          <div className="tmatch"><div className="tt"><i style={{width:t.match+"%"}} /></div><b>{t.match}%</b></div>
          <div className="tdesc">{t.desc}</div>
          <div className="troles">{t.roles.map((r,j)=><span key={j}>{r}</span>)}</div>
        </div>))}
    </div></div></div>);
}

function Rag({ b }){
  return (<div className="card"><CardHead engine={b.engine} title={b.title} />
    <div className="card-body">
      {b.answer.map((p,i)=><p key={i} style={{margin:i?"10px 0 0":0,fontSize:14,lineHeight:1.65}} dangerouslySetInnerHTML={{__html:p}} />)}
      <div style={{marginTop:14,borderTop:"1px solid var(--border)",paddingTop:13}}>
        <div style={{fontSize:10.5,fontWeight:700,textTransform:"uppercase",letterSpacing:".05em",color:"var(--text-faint)",marginBottom:9,display:"flex",alignItems:"center",gap:6}}><Icon name="quote" style={{width:13,height:13}} />Sources</div>
        {b.sources.map((s,i)=>(<div className="src" key={i}>
          <div className="sn">{s.n}</div>
          <div className="sb"><div className="st2">{s.title}</div><div className="sq">“{s.quote}”</div><div className="sm">{s.meta}</div></div>
        </div>))}
      </div>
    </div></div>);
}

function Followups({ b, onChip }){
  return (<div className="followups">
    {b.chips.map((c,i)=>(<button key={i} className="chip" onClick={()=>onChip(c)}>{c.icon && <Icon name={c.icon} />}{c.label}</button>))}
  </div>);
}

function Block({ b, onChip }){
  switch(b.type){
    case "text": return (<div className="bubble">{b.paras.map((p,i)=><p key={i} className={i===0?"lead":""} dangerouslySetInnerHTML={{__html:p}} />)}</div>);
    case "courses": return <CourseList b={b} />;
    case "prereq": return <Prereq b={b} />;
    case "verdictcard": return <VerdictCard b={b} />;
    case "audit": return <Audit b={b} />;
    case "gpa": return <Gpa b={b} />;
    case "plan": return <Plan b={b} />;
    case "tracks": return <Tracks b={b} />;
    case "rag": return <Rag b={b} />;
    case "followups": return <Followups b={b} onChip={onChip} />;
    default: return null;
  }
}

function Caps({ caps, onPick }){
  return (<div className="cap-grid">
    {caps.map((c,i)=>(
      <button key={i} className="cap" style={{animationDelay:(0.25+i*0.05)+"s"}} onClick={()=>onPick(c.intent, c.q)}>
        <div className={c.cls}><Icon name={c.icon} /></div>
        <div><b>{c.t}</b><span className="cq">{c.q}</span></div>
      </button>))}
  </div>);
}

/* ---------------- loading / error states (shared) ---------------- */
function Spinner({ label }){
  return (
    <div className="pf-spinner">
      <div className="pf-spin-ring" />
      {label && <span>{label}</span>}
    </div>
  );
}

function ErrorState({ message, onRetry }){
  return (
    <div className="pf-error">
      <div className="pf-error-ic"><Icon name="target" /></div>
      <div className="pf-error-msg">{message || "Something went wrong."}</div>
      {onRetry && <button className="act-btn sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

Object.assign(window, { Icon, LogoMark, MsgAvatar, Block, Caps, Spinner, ErrorState });
