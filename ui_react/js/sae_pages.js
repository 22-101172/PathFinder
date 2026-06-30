/* ============================================================
   PathFinder — Student Analysis Engine pages
   window: StudentAnalysisPage, AdvisorConsole
   Wired to the real backend via PF_API (no mock data).
   ============================================================ */
const { useState: useAS, useRef: useAR, useEffect: useAE } = React;

let _gpaUid = 0;

/* ---------------- GPA-over-time line chart (theme-aware SVG) ---------------- */
function GpaChart({ history, min, minLabel }){
  const uid = useAR("gpaFill_" + (++_gpaUid)).current;
  const W = 760, H = 320, padL = 42, padR = 66, padT = 18, padB = 56;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const yMax = 4;
  const x = (i) => padL + (history.length === 1 ? plotW / 2 : (i / (history.length - 1)) * plotW);
  const y = (g) => padT + (1 - g / yMax) * plotH;
  const pts = history.map((d, i) => [x(i), y(d.gpa)]);
  const linePath = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const areaPath = linePath + ` L ${pts[pts.length-1][0].toFixed(1)} ${(padT+plotH).toFixed(1)} L ${pts[0][0].toFixed(1)} ${(padT+plotH).toFixed(1)} Z`;
  const yTicks = [0,1,2,3,4];

  const [draw, setDraw] = useAS(false);
  useAE(()=>{ const t=setTimeout(()=>setDraw(true),80); return ()=>clearTimeout(t); },[]);

  return (
    <svg className="gpa-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="CGPA over time">
      <defs>
        <linearGradient id={uid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--primary-bright)" stopOpacity="0.28" />
          <stop offset="100%" stopColor="var(--primary-bright)" stopOpacity="0" />
        </linearGradient>
      </defs>

      {yTicks.map(t=>(
        <g key={t}>
          <line className="grid-l" x1={padL} y1={y(t)} x2={W-padR} y2={y(t)} />
          <text className="axis-t" x={padL-10} y={y(t)+4} textAnchor="end">{t}</text>
        </g>
      ))}

      <line className="min-l" x1={padL} y1={y(min)} x2={W-padR} y2={y(min)} />
      <text className="min-t" x={W-padR+8} y={y(min)+4}>{minLabel}</text>

      <path className="area" d={areaPath} fill={`url(#${uid})`} style={{opacity:draw?0.5:0,transition:"opacity .8s ease .3s"}} />
      <path className="line" d={linePath}
        style={{strokeDasharray:1400, strokeDashoffset: draw?0:1400, transition:"stroke-dashoffset 1.1s cubic-bezier(.4,0,.2,1)"}} />

      {pts.map((p,i)=>(
        <circle key={i} className={"dot"+(i===pts.length-1?" dot-last":"")} cx={p[0]} cy={p[1]}
          r={i===pts.length-1?5:4} style={{opacity:draw?1:0,transition:`opacity .4s ease ${0.5+i*0.06}s`}} />
      ))}

      {history.map((d,i)=>(
        <text key={i} className="x-t" x={x(i)} y={padT+plotH+18}
          textAnchor="end" transform={`rotate(-32 ${x(i)} ${padT+plotH+18})`}>{d.term}</text>
      ))}
    </svg>
  );
}

/* ---------------- mapping helpers: raw SAE JSON -> UI shape ---------------- */
const RISK_META = {
  high:     { label: "At Risk",         cls: "atrisk" },
  moderate: { label: "Needs Attention", cls: "warn2"  },
  low:      { label: "On Track",        cls: "ontrack"},
};

function cohortStandingLabel(pct, level){
  if (pct === null || pct === undefined) return "Not enough cohort data";
  const isTop = pct >= 50;
  const display = isTop ? (100 - pct) : pct;
  return `${isTop ? "Top" : "Bottom"} ${display}% of ${level || "students"}s`;
}

function letterFromGp(gp){
  if (gp === null || gp === undefined) return "—";
  if (gp >= 4.0) return "A+"; if (gp >= 3.7) return "A"; if (gp >= 3.4) return "A-";
  if (gp >= 3.2) return "B+"; if (gp >= 3.0) return "B"; if (gp >= 2.8) return "B-";
  if (gp >= 2.6) return "C+"; if (gp >= 2.4) return "C"; if (gp >= 2.2) return "C-";
  if (gp >= 2.0) return "D+"; if (gp >= 1.5) return "D"; if (gp >= 1.0) return "D-";
  return "F";
}

/** Maps a /sae/student/{id} response into the shape StudentDashboard expects. */
function mapAnalysisToDashboard(data){
  const profile = data.profile || {};
  const name = profile.name || data.student_id || "—";
  const initials = name.split(" ").map(w=>w[0]).filter(Boolean).slice(0,2).join("").toUpperCase() || "?";
  const riskMeta = RISK_META[data.risk_level] || RISK_META.low;
  const history = (data.cgpa_trend_history || []).map(t => ({ term: t.semester_label, gpa: t.cgpa_at_end_of_semester }));
  const cgpa = data.official_cgpa;

  const cohortStats = data.cohort_stats || {};
  const metrics = [];
  metrics.push({
    label: "Cumulative GPA",
    value: cgpa != null ? cgpa.toFixed(2) : "N/A",
    note: cohortStats.cohort_avg_cgpa != null ? `cohort avg ${cohortStats.cohort_avg_cgpa.toFixed(2)}` : "",
    good: cohortStats.cohort_avg_cgpa == null ? true : (cgpa >= cohortStats.cohort_avg_cgpa),
  });
  metrics.push({
    label: "Passed Credits",
    value: String(Math.round(data.credits_completed || 0)),
    note: data.expected_credits != null ? `expected ${Math.round(data.expected_credits)}` : "",
    good: data.expected_credits == null ? true : (data.credits_completed >= data.expected_credits),
  });

  return {
    name, initials,
    program: profile.program || "",
    level: profile.level || "",
    id: profile.id || "",
    status: riskMeta.label,
    statusCls: riskMeta.cls,
    gpa: cgpa != null ? cgpa.toFixed(2) : "—",
    creditsEarned: Math.round(data.credits_completed || 0),
    creditsTotal: 133,
    cohortStanding: cohortStandingLabel(cohortStats.student_percentile, profile.level),
    minLine: 2.0,
    gpaHistory: history.length ? history : [{ term: "—", gpa: 0 }],
    metrics,
    raw: data,
  };
}

/* ---------------- small stub button (kept for not-yet-wired actions) ---------------- */
function StubBtn({ icon, label, primary, sm }){
  const [toast, setToast] = useAS(false);
  function ping(){ setToast(true); clearTimeout(ping._t); ping._t = setTimeout(()=>setToast(false), 1700); }
  return (
    <span className="act-stub">
      <button className={"act-btn"+(primary?" primary":"")+(sm?" sm":"")} onClick={ping}>
        {icon && <Icon name={icon} />}{label}
      </button>
      {toast && <span className="stub-toast">Coming soon</span>}
    </span>
  );
}

/* ---------------- profile banner + metrics + chart (shared) ---------------- */
function StudentDashboard({ s, L, actions }){
  const [cw, setCw] = useAS(0);
  useAE(()=>{ const t=setTimeout(()=>setCw(Math.round(s.creditsEarned/s.creditsTotal*100)),120); return ()=>clearTimeout(t); },[s]);
  const h = s.gpaHistory;
  const rising = h.length>1 && h[h.length-1].gpa >= h[h.length-2].gpa;

  return (
    <>
      <div className="sx-profile">
        <div className="sx-ava">{s.initials}</div>
        <div className="sx-meta">
          <div className="sx-name">{s.name}</div>
          <div className="sx-prog">{s.program}</div>
          <div className="sx-level">Level: {s.level}</div>
          {s.id && <div className="sx-id-chip">{s.id}</div>}
        </div>
        <span className={"pill lg "+s.statusCls}><span className="pdot" />{s.status}</span>
      </div>

      {actions && (
        <div className="act-bar">
          <StubBtn icon="send" label={L.actExport} primary />
          <StubBtn icon="target" label={L.actFlag} />
          <StubBtn icon="book" label={L.actTranscript} />
        </div>
      )}

      <div className="metric-row">
        <div className="metric accentv">
          <div className="mk">{L.cumGpa}</div>
          <div className="mv">{s.gpa}{h.length>1 && <span className={rising?"up":"dn"}>{rising?"▲":"▼"}</span>}</div>
        </div>
        <div className="metric">
          <div className="mk">{L.creditsProgress}</div>
          <div className="credits-v">{s.creditsEarned} / {s.creditsTotal} credits</div>
          <div className="credits-bar"><i style={{width:cw+"%"}} /></div>
        </div>
        <div className="metric">
          <div className="mk">{L.cohortStanding}</div>
          <div className="standing-v">{s.cohortStanding}</div>
        </div>
      </div>

      <div className="analysis-grid">
        <div className="an-card">
          <h3 className="an-title"><span className="ti-ic"><Icon name="trend" /></span>{L.gpaOverTime}</h3>
          {h.length >= 2
            ? <GpaChart history={s.gpaHistory} min={s.minLine} minLabel={L.minLine} />
            : <div className="pf-empty-note">Not enough graded semesters yet to chart a trend.</div>}
        </div>
        <div className="an-card standing-card">
          <h3 className="an-title"><span className="ti-ic"><Icon name="chart" /></span>{L.yourStanding}</h3>
          <div className="std-lbl">{L.yourMetrics}</div>
          {s.metrics.map((m,i)=>(
            <div className="std-row" key={i}>
              <div className="sr-l">
                <div className="sr-name">{m.label}</div>
                <div className="sr-sub">{m.note}</div>
              </div>
              <div className="sr-v">{m.value}</div>
              <span className={"sr-dot "+(m.good?"ok":"bad")} />
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/* ---------------- Deeper analysis: real SAE data cards ---------------- */
function DeeperAnalysis({ data }){
  if (!data) return null;
  const cat = data.category_performance || {};
  const catKeys = Object.keys(cat);
  const flags = data.risk_flags || [];
  const suggestions = data.suggestions || [];
  const anomalies = data.anomalies || {};
  const bottleneck = data.prerequisite_bottleneck || {};
  const blockers = bottleneck.blockers || [];
  const sdi = data.semester_difficulty || {};
  const grad = (data.trajectory || {}).graduation_projection;

  const anomalyMsgs = [];
  const anomalyFlags = anomalies.flags || [];
  if (anomalyFlags.includes("silent_decline")) anomalyMsgs.push("CGPA trending down with no formal warning issued yet — act now before it becomes official.");
  if (anomalyFlags.includes("repeated_course")) anomalyMsgs.push("Same course attempted 3 or more times.");
  if (anomalyFlags.includes("undercredited_senior")) anomalyMsgs.push("Behind on credits expected for senior level.");

  const hasAnything = flags.length || suggestions.length || catKeys.length || blockers.length || anomalyMsgs.length || sdi.flag_message || grad;
  if (!hasAnything) return null;

  return (
    <div style={{marginTop:18}}>
      <h3 className="an-title" style={{marginBottom:14}}>Deeper Analysis</h3>
      <div className="pf-card-grid">

        {flags.length > 0 && (
          <div className="an-card">
            <h3 className="an-title"><span className="ti-ic"><Icon name="target" /></span>Risk Flags</h3>
            {flags.map((f,i)=>(
              <div key={i} className={"pf-flag "+(f.severity||"medium")}>
                <span className="pf-flag-ic"><Icon name="target" /></span>
                <span>{f.message}</span>
              </div>
            ))}
          </div>
        )}

        {anomalyMsgs.length > 0 && (
          <div className="an-card">
            <h3 className="an-title"><span className="ti-ic"><Icon name="sparkle" /></span>Anomaly Alerts</h3>
            {anomalyMsgs.map((m,i)=>(
              <div key={i} className="pf-flag high">
                <span className="pf-flag-ic"><Icon name="target" /></span><span>{m}</span>
              </div>
            ))}
          </div>
        )}

        {catKeys.length > 0 && (
          <div className="an-card">
            <h3 className="an-title"><span className="ti-ic"><Icon name="chart" /></span>Performance by Subject Area</h3>
            {catKeys.map(k=>{
              const c = cat[k];
              const letter = c.avg_letter_grade || letterFromGp(c.avg_grade_points);
              return (
                <div className="pf-kv-row" key={k}>
                  <span className="k">{k}</span>
                  <span className="v">{letter}{c.pass_rate != null ? ` · ${Math.round(c.pass_rate*100)}% pass` : ""}</span>
                </div>
              );
            })}
          </div>
        )}

        {blockers.length > 0 && (
          <div className="an-card">
            <h3 className="an-title"><span className="ti-ic"><Icon name="route" /></span>Recommended Retakes</h3>
            {blockers.map((b,i)=>(
              <div className="pf-kv-row" key={i}>
                <span className="k">{b.course_code}</span>
                <span className="v">blocking {b.unlock_count} course{b.unlock_count===1?"":"s"}</span>
              </div>
            ))}
          </div>
        )}

        {sdi.flag_message && (
          <div className="an-card">
            <h3 className="an-title"><span className="ti-ic"><Icon name="gear" /></span>Current Semester Load</h3>
            <div className="pf-kv-row"><span className="k">Difficulty Index</span><span className="v">{(sdi.sdi_score||0).toFixed(2)} / 3.00</span></div>
            <div style={{fontSize:12.5,color:"var(--text-dim)",marginTop:6}}>{sdi.flag_message}</div>
          </div>
        )}

        {grad && (
          <div className="an-card">
            <h3 className="an-title"><span className="ti-ic"><Icon name="calendar" /></span>Graduation Outlook</h3>
            <div className="pf-kv-row"><span className="k">At current pace</span><span className="v">{grad.estimated_graduation || "—"}</span></div>
            {grad.optimistic && <div className="pf-kv-row"><span className="k">Optimistic</span><span className="v">{grad.optimistic}</span></div>}
            {grad.pessimistic && <div className="pf-kv-row"><span className="k">Pessimistic</span><span className="v">{grad.pessimistic}</span></div>}
          </div>
        )}

        {suggestions.length > 0 && (
          <div className="an-card" style={{gridColumn: catKeys.length||blockers.length ? "auto" : "1 / -1"}}>
            <h3 className="an-title"><span className="ti-ic"><Icon name="sparkle" /></span>What You Should Do</h3>
            {suggestions.slice(0,4).map((s,i)=>(
              <div className="pf-suggestion" key={i}>
                <span className="num">{i+1}</span><span>{s}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- STUDENT PAGE ---------------- */
function StudentAnalysisPage({ L, rtl, studentId }){
  const [data, setData] = useAS(null);
  const [loading, setLoading] = useAS(true);
  const [error, setError] = useAS(null);

  async function load(){
    setLoading(true); setError(null);
    const res = await PF_API.getStudentAnalysis(studentId);
    if (res.__error) { setError(res.detail || "Could not load your analysis."); setLoading(false); return; }
    setData(res);
    setLoading(false);
  }
  useAE(()=>{ load(); }, [studentId]);

  return (
    <div className="page">
      <div className="page-inner">
        <div className="page-head">
          <div className="ph-l">
            <h2>{L.studentTitle}</h2>
            <div className="ph-sub"><span className="ph-eng"><Icon name="ale" style={{width:12,height:12}} />SAE</span>{L.studentSub}</div>
          </div>
        </div>
        {loading && <Spinner label="Loading your analysis…" />}
        {!loading && error && <ErrorState message={error} onRetry={load} />}
        {!loading && !error && data && (
          <>
            <StudentDashboard s={mapAnalysisToDashboard(data)} L={L} />
            <DeeperAnalysis data={data} />
          </>
        )}
      </div>
    </div>
  );
}

/* ---------------- ADVISOR CONSOLE ---------------- */
function AdvisorConsole({ L, rtl, advisorId }){
  const [overview, setOverview] = useAS(null);
  const [loadingOv, setLoadingOv] = useAS(true);
  const [ovError, setOvError] = useAS(null);

  const [query, setQuery] = useAS("");
  const [looked, setLooked] = useAS(null);
  const [lookedAnalysis, setLookedAnalysis] = useAS(null);
  const [lookupLoading, setLookupLoading] = useAS(false);
  const [lookupError, setLookupError] = useAS(null);

  async function loadOverview(){
    setLoadingOv(true); setOvError(null);
    const res = await PF_API.getAdvisorOverview(advisorId);
    if (res.__error) { setOvError(res.detail || "Could not load overview."); setLoadingOv(false); return; }
    const students = Array.isArray(res) ? res : (res.students || []);
    setOverview({
      total_students: students.length,
      high_risk: students.filter(s => s.risk_level === "high").length,
      moderate_risk: students.filter(s => s.risk_level === "moderate").length,
      low_risk: students.filter(s => s.risk_level === "low").length,
      students,
    });
    setLoadingOv(false);
  }
  useAE(()=>{ loadOverview(); }, [advisorId]);

  async function lookUp(idOverride){
    const q = (idOverride || query).trim();
    if (!q) return;
    setQuery(q);
    setLookupLoading(true); setLookupError(null); setLooked(null); setLookedAnalysis(null);
    const [base, analysis] = await Promise.all([
      PF_API.getStudentAnalysis(q),
      PF_API.getAdvisorAnalysis(q),
    ]);
    if (base.__error) {
      setLookupError(base.status === 404
        ? "No student found with that ID." : (base.detail || "Lookup failed."));
      setLookupLoading(false);
      return;
    }
    setLooked(mapAnalysisToDashboard(base));
    setLookedAnalysis(analysis.__error ? null : analysis);
    setLookupLoading(false);
  }
  function clearLookup(){ setLooked(null); setLookedAnalysis(null); setQuery(""); setLookupError(null); }
  function onKey(e){ if(e.key==="Enter") lookUp(); }

  const risk = overview ? [
    { cls:"total", k:L.totalStudents, v:overview.total_students },
    { cls:"high",  k:L.highRisk,      v:overview.high_risk },
    { cls:"mod",   k:L.modRisk,       v:overview.moderate_risk },
    { cls:"low",   k:L.lowRisk,       v:overview.low_risk },
  ] : [];

  const attention = overview ? (overview.students || []).filter(s=>s.immediate_action) : [];
  const shownAttention = attention.slice(0, 5);
  const moreCount = Math.max(0, attention.length - shownAttention.length);

  return (
    <div className="page">
      <div className="page-inner">
        <div className="page-head">
          <div className="ph-l">
            <h2>{L.advisorConsoleTitle}</h2>
            <div className="ph-sub"><span className="ph-eng"><Icon name="ale" style={{width:12,height:12}} />SAE</span>{L.advisorConsoleSub}</div>
          </div>
        </div>

        {/* ID search */}
        <div className="id-search">
          <div className="id-lbl">{L.idLabel}</div>
          <div className="id-row">
            <input className="id-field" value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={onKey} placeholder={L.idPlaceholder} />
            <button className="id-go" onClick={()=>lookUp()} disabled={lookupLoading}><Icon name="check" />{L.lookUp}</button>
          </div>
          {!looked && !lookupLoading && !lookupError && <div className="id-hint">Enter any student ID assigned to your caseload.</div>}
          {lookupLoading && <Spinner label="Looking up student…" />}
          {lookupError && <div className="pf-login-err">{lookupError}</div>}
        </div>

        {/* looked-up profile */}
        {looked && (
          <>
            <div className="act-bar" style={{justifyContent:"flex-end"}}>
              <button className="act-btn sm" onClick={clearLookup}><Icon name="plus" style={{transform:"rotate(45deg)"}} />{L.clearLookup}</button>
            </div>
            <StudentDashboard s={looked} L={L} actions />

            {lookedAnalysis && (
              <div className="pf-card-grid" style={{marginTop:16}}>
                {(lookedAnalysis.key_points||[]).length > 0 && (
                  <div className="an-card">
                    <h3 className="an-title"><span className="ti-ic"><Icon name="target" /></span>Academic Key Points</h3>
                    {lookedAnalysis.key_points.map((k,i)=>(
                      <div key={i} className={"pf-flag "+(k.severity||"medium")}>
                        <span>{k.emoji} {k.message}</span>
                      </div>
                    ))}
                  </div>
                )}
                {lookedAnalysis.llm_session_guide && (
                  <div className="an-card">
                    <h3 className="an-title"><span className="ti-ic"><Icon name="quote" /></span>Advising Session Guide</h3>
                    <div className="pf-guide-card pf-guide-open"><b>How to open</b>{lookedAnalysis.llm_session_guide.opening}</div>
                    <div className="pf-guide-card pf-guide-question"><b>Key question to ask</b>{lookedAnalysis.llm_session_guide.key_question}</div>
                    <div className="pf-guide-card pf-guide-goal"><b>Aim to agree on</b>{lookedAnalysis.llm_session_guide.session_goal}</div>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* cohort risk overview */}
        {loadingOv && <Spinner label="Loading advisor overview…" />}
        {!loadingOv && ovError && <ErrorState message={ovError} onRetry={loadOverview} />}
        {!loadingOv && !ovError && overview && (
          <>
            <div className="risk-row">
              {risk.map((r,i)=>(
                <div className={"risk "+r.cls} key={i}>
                  <div className="rk">{r.k}</div>
                  <div className="rv">{r.v}</div>
                </div>
              ))}
            </div>

            <div>
              <div className="attn-head">{L.attentionTitle}</div>
              <div className="attn">
                {shownAttention.length === 0 && <div className="pf-empty-note">No students currently need immediate attention.</div>}
                {shownAttention.map((a,i)=>(
                  <div className="attn-row" key={i}>
                    <div className="attn-l">
                      <div className="attn-top">
                        <span className="attn-name">{a.name}</span>
                        <span className="attn-badge">{a.level}</span>
                      </div>
                      <div className="attn-flag"><Icon name="target" />{a.immediate_reason}</div>
                    </div>
                    <div className="attn-r">
                      <span className="attn-gpa">CGPA {a.official_cgpa != null ? a.official_cgpa.toFixed(2) : "N/A"}</span>
                      <button className="act-btn sm" onClick={()=>lookUp(a.id)}>{L.actReview}</button>
                    </div>
                  </div>
                ))}
                {moreCount > 0 && <div className="attn-more">{L.moreHigh(moreCount)}</div>}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { StudentAnalysisPage, AdvisorConsole, GpaChart });
