/* ============================================================
   PathFinder — EUI Academic Advisor · main app
   Wired to the real backend (chat, sessions, SAE) via PF_API.
   ============================================================ */
const { useState: useS, useEffect: useE, useRef: useR } = React;

/* ---------------- Login screen ---------------- */
function LoginScreen({ lang, setLang, onLogin }){
  const L = PF_I18N[lang];
  const rtl = lang === "ar";
  const [id, setId] = useS("");
  const [err, setErr] = useS("");
  const [busy, setBusy] = useS(false);

  async function submit(){
    const v = id.trim();
    if (!v) return;
    const type = PF_API.detectUserType(v);
    if (type === "unknown") { setErr(L.loginErrUnknown); return; }
    setBusy(true); setErr("");

    if (type === "student") {
      const res = await PF_API.getStudentAnalysis(v);
      if (res.__error) { setErr(L.loginErrNotFound); setBusy(false); return; }
    }
    // Advisors aren't individually validated server-side (simulated assignment) —
    // any ADV-prefixed ID is accepted and routed to the Advisor Console.

    setBusy(false);
    onLogin(v, type);
  }
  function onKey(e){ if (e.key === "Enter") submit(); }

  return (
    <div className="pf-login" dir={rtl ? "rtl" : "ltr"} lang={lang}>
      <div className="pf-login-card">
        <LogoMark className="pf-login-logo" />
        <h1>{L.loginTitle}</h1>
        <p>{L.loginSub}</p>
        <div className="id-row">
          <input className="id-field" value={id} onChange={e=>setId(e.target.value)} onKeyDown={onKey}
                 placeholder={L.loginPlaceholder} autoFocus />
          <button className="id-go" onClick={submit} disabled={busy}>
            <Icon name="check" />{busy ? "…" : L.loginBtn}
          </button>
        </div>
        {err && <div className="pf-login-err">{err}</div>}
        <div className="lang-toggle" style={{justifyContent:"center",marginTop:18}}>
          <button className={lang==="en"?"on":""} onClick={()=>setLang("en")}>EN</button>
          <button className={lang==="ar"?"on":""} onClick={()=>setLang("ar")}>ع</button>
        </div>
      </div>
    </div>
  );
}

/* ---------------- chat answer renderer (real backend text + citations) ---------------- */
function ChatAnswer({ text, citations, L }){
  const paras = (text || "").split(/\n\n+/).filter(Boolean);
  return (
    <div className="bubble">
      {paras.length ? paras.map((p,i)=><p key={i} className={i===0?"lead":""}>{p}</p>) : <p className="lead">{text}</p>}
      {citations && citations.length > 0 && (
        <div style={{marginTop:14,borderTop:"1px solid var(--border)",paddingTop:13}}>
          <div style={{fontSize:10.5,fontWeight:700,textTransform:"uppercase",letterSpacing:".05em",color:"var(--text-faint)",marginBottom:9,display:"flex",alignItems:"center",gap:6}}>
            <Icon name="quote" style={{width:13,height:13}} />{L.citations}
          </div>
          {citations.map((c,i)=>(
            <div className="src" key={i}>
              <div className="sn">{i+1}</div>
              <div className="sb"><div className="sm">{c.source}{c.page ? `, p.${c.page}` : ""}</div></div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function fallbackBlocks(L){
  return [{ type:"text", paras:[
    `I'm <strong>${PF_DATA.bot.name}</strong>, your EUI academic advisor — ask me about courses, eligibility, your plan, or university policies.` ]}];
}

function App(){
  const [lang, setLang] = useS("en");
  const [theme] = useS("heritage");
  const [density] = useS("regular");

  const [userId, setUserId] = useS(null);
  const [userType, setUserType] = useS(null);

  const [messages, setMessages] = useS([]);
  const [typing, setTyping] = useS(false);
  const [input, setInput] = useS("");
  const [collapsed, setCollapsed] = useS(false);
  const [activeConv, setActiveConv] = useS(null);
  const [page, setPage] = useS("chat");
  const [sessionId, setSessionId] = useS(null);
  const [sessionName, setSessionName] = useS(null);
  const [allSessions, setAllSessions] = useS([]);
  const [analysisCache, setAnalysisCache] = useS({});

  const scrollRef = useR(null);
  const taRef = useR(null);
  const idc = useR(0);

  const L = PF_I18N[lang];
  const rtl = lang === "ar";

  const DENSITY = { compact:0.84, regular:1, comfy:1.16 };
  useE(()=>{
    const r = document.documentElement;
    r.setAttribute("data-theme", theme);
    r.style.setProperty("--pad", DENSITY[density] ?? 1);
    r.setAttribute("dir", rtl?"rtl":"ltr");
    r.setAttribute("lang", lang);
  },[theme, density, rtl, lang]);

  useE(()=>{
    if(messages.length===0) return;
    const el = scrollRef.current; if(!el) return;
    el.scrollTop = el.scrollHeight;
  },[messages, typing]);

  /* ---------- login / logout ---------- */
  function handleLogin(id, type){
    setUserId(id); setUserType(type);
    setPage(type === "advisor" ? "advisor" : "chat");
    if (type === "student") {
      PF_API.getSessions(id).then(setAllSessions);
    }
  }
  function logout(){
    setUserId(null); setUserType(null);
    setMessages([]); setSessionId(null); setSessionName(null); setAllSessions([]);
    setAnalysisCache({});
    setPage("chat");
  }

  /* ---------- chat ---------- */
  async function sendMessage(text){
    if (!text.trim() || typing) return;
    setMessages(m=>[...m,{id:++idc.current,who:"user",text}]);
    setTyping(true);
    // Advisor chat runs without student context — general policy/KG/handbook questions.
    const res = await PF_API.chat(text, userType === "advisor" ? "" : userId, sessionId);
    setTyping(false);
    if (res.__error) {
      setMessages(m=>[...m,{id:++idc.current,who:"bot",text:`I couldn't reach the server (${res.detail}). Please try again.`,citations:[]}]);
      return;
    }
    setMessages(m=>[...m,{id:++idc.current,who:"bot",text:res.answer_text,citations:res.citations||[]}]);
    if (res.session_id) setSessionId(res.session_id);
    if (res.session_name) {
      setSessionName(res.session_name);
      if (userType === "student") PF_API.getSessions(userId).then(setAllSessions);
    }
  }
  function submit(){
    const text = input.trim(); if(!text||typing) return;
    setInput(""); if(taRef.current) taRef.current.style.height="auto";
    sendMessage(text);
  }
  function newChat(){ setPage("chat"); setMessages([]); setActiveConv(null); setInput(""); setSessionId(null); setSessionName(null); }
  async function openConv(s){
    setPage("chat"); setActiveConv(s.session_id);
    const { messages: msgs, sessionName: name } = await PF_API.getSessionHistory(userId, s.session_id);
    setSessionId(s.session_id); setSessionName(name); setMessages(msgs.map(m=>({...m,id:++idc.current})));
  }
  async function removeConv(sessionIdToDelete){
    const ok = await PF_API.deleteSession(userId, sessionIdToDelete);
    if (ok) {
      const fresh = await PF_API.getSessions(userId);
      setAllSessions(fresh);
      if (sessionId === sessionIdToDelete) { setSessionId(null); setSessionName(null); setMessages([]); }
    }
  }
  function goPage(p){ setPage(p); }
  function onKey(e){ if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); submit(); } }
  function autosize(e){ setInput(e.target.value); e.target.style.height="auto"; e.target.style.height=Math.min(e.target.scrollHeight,140)+"px"; }

  /* ---------- not logged in ---------- */
  if (!userId) {
    return <LoginScreen lang={lang} setLang={setLang} onLogin={handleLogin} />;
  }

  const isHome = messages.length===0 && !typing;
  const firstName = userId;

  const Composer = (
    <div className="inputbar">
      <textarea ref={taRef} rows="1" value={input} placeholder={L.placeholder} onChange={autosize} onKeyDown={onKey} />
      <div className="input-row">
        <button className="send" onClick={submit} disabled={!input.trim()||typing}><Icon name="send" /></button>
      </div>
    </div>
  );

  return (
    <div className={"app"+(collapsed?" collapsed":"")}>
      {/* ---------------- SIDEBAR ---------------- */}
      <aside className="side">
        <div className="side-head">
          <LogoMark className="logo-mark" />
          <div>
            <div className="brand-name">{PF_DATA.bot.name}</div>
            <div className="brand-sub">EUI · {rtl?"الإرشاد الأكاديمي":"Academic Advising"}</div>
          </div>
        </div>

        {userType === "student" && (
          <button className="side-new" onClick={newChat}><Icon name="plus" style={{width:16,height:16}} />{L.newChat}</button>
        )}

        <div className="side-nav">
          <div className="side-label" style={{paddingTop:6}}>{L.pagesLabel}</div>
          {userType === "student" && (
            <>
              <div className={"nav-item"+(page==="chat"?" on":"")} onClick={()=>goPage("chat")}><Icon name="compass" />{L.navChat}</div>
              <div className={"nav-item"+(page==="student"?" on":"")} onClick={()=>goPage("student")}><Icon name="chart" />{L.navStudent}</div>
            </>
          )}
          {userType === "advisor" && (
            <>
              <div className={"nav-item"+(page==="advisor"?" on":"")} onClick={()=>goPage("advisor")}><Icon name="user" />{L.navAdvisor}</div>
              <div className={"nav-item"+(page==="chat"?" on":"")} onClick={()=>goPage("chat")}><Icon name="compass" />{L.navChat}</div>
            </>
          )}
        </div>

        <div className="side-scroll">
          {page==="chat" && userType === "student" && (<>
            <div className="side-label">{L.recent}</div>
            {allSessions.map(s=>(
              <div key={s.session_id} className={"conv"+(activeConv===s.session_id?" active":"")} style={{display:"flex",alignItems:"center",gap:6}}>
                <span style={{flex:1,display:"flex",alignItems:"center",gap:8,cursor:"pointer",minWidth:0}} onClick={()=>openConv(s)}>
                  <span className="dot" /><span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{s.session_name || s.session_id}</span>
                </span>
                <button className="gear" style={{width:22,height:22}} onClick={()=>removeConv(s.session_id)}>✕</button>
              </div>
            ))}
          </>)}
        </div>

        <div className="side-foot">
          <div className="avatar">{userId.slice(0,2).toUpperCase()}</div>
          <div className="who"><b>{userId}</b><span>{userType === "advisor" ? "Advisor" : "Student"}</span></div>
          <button className="gear" onClick={logout} title={L.logout}><Icon name="gear" style={{width:17,height:17}} /></button>
        </div>
      </aside>

      {/* ---------------- MAIN ---------------- */}
      <div className="main">
        <div className="topbar">
          <button className="icon-btn" onClick={()=>setCollapsed(c=>!c)}><Icon name="menu" style={{width:19,height:19}} /></button>
          <div className="title">
            {page==="student"?L.studentTitle:page==="advisor"?L.advisorConsoleTitle:L.advisorTitle}
            <small>{page==="student"?L.studentSub:page==="advisor"?L.advisorConsoleSub:userId}</small>
          </div>
          <div className="spring" />
          <div className="lang-toggle">
            <button className={lang==="en"?"on":""} onClick={()=>setLang("en")}>EN</button>
            <button className={lang==="ar"?"on":""} onClick={()=>setLang("ar")}>ع</button>
          </div>
        </div>

        {page==="student" ? <StudentAnalysisPage L={L} rtl={rtl} studentId={userId} analysisCache={analysisCache} setAnalysisCache={setAnalysisCache} />
        : page==="advisor" ? <AdvisorConsole L={L} rtl={rtl} advisorId={userId} />
        : isHome ? (
          /* ---------------- LANDING ---------------- */
          <div className="home" ref={scrollRef}>
            <div className="hero">
              <div className="hero-img" style={{background:"linear-gradient(150deg,var(--eui-navy),var(--eui-blue) 60%,var(--eui-teal))"}} />
              <div className="hero-inner">
                <LogoMark className="logo-badge" />
                <h1>{L.greet(firstName)}<br/><span className="accent">{rtl?"كيف أساعدك اليوم؟":"How can I guide you today?"}</span></h1>
                <div className="motto">{PF_DATA.bot.name} · {rtl?PF_DATA.motto.ar:PF_DATA.motto.en}<span className="div" /></div>
              </div>
            </div>
            <div className="home-body">
              <div className="home-input">{Composer}</div>
              <div className="cap-label">{L.capsLabel}</div>
              <Caps caps={L.caps} onPick={(intent,q)=>sendMessage(q)} />
              <div className="home-foot" dangerouslySetInnerHTML={{__html:L.footNote}} />
            </div>
          </div>
        ) : (
          /* ---------------- THREAD ---------------- */
          <>
            <div className="thread-wrap" ref={scrollRef}>
              <div className="thread">
                {messages.map(m=>(
                  <div key={m.id} className={"msg "+(m.who==="user"?"user":"bot")}>
                    <MsgAvatar who={m.who} initials={userId.slice(0,2).toUpperCase()} />
                    <div className="msg-body">
                      {m.who==="bot" && <div className="msg-name">{PF_DATA.bot.name}</div>}
                      {m.who==="user"
                        ? <div className="bubble">{m.text}</div>
                        : <ChatAnswer text={m.text} citations={m.citations} L={L} />}
                    </div>
                  </div>
                ))}
                {typing && (
                  <div className="msg bot">
                    <MsgAvatar who="bot" />
                    <div className="msg-body"><div className="msg-name">{PF_DATA.bot.name}</div><div className="bubble"><div className="typing"><i/><i/><i/></div></div></div>
                  </div>
                )}
              </div>
            </div>
            <div className="composer-wrap">
              <div className="composer">
                <div className="suggest">
                  {L.suggest.map((s,i)=>(<button key={i} className="schip" onClick={()=>sendMessage(s.q)}>
                    <span className={s.cls} style={{width:22,height:22}}><Icon name={s.icon} /></span>{s.label}</button>))}
                </div>
                {Composer}
                <div className="compose-foot" dangerouslySetInnerHTML={{__html:L.footNote}} />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
