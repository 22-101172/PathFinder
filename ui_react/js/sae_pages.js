/* ============================================================
   PathFinder — Student Analysis Engine pages
   window: StudentAnalysisPage, AdvisorConsole
   Rebuilt against the real SAE payloads:
     GET /sae/student/{id}            → student analytics profile
     GET /sae/student/{id}/analysis   → advisor analysis (key_points…)
     GET /sae/advisor/overview        → flat array of student summaries
   ============================================================ */
const { useState: useAS, useRef: useAR, useEffect: useAE, useMemo: useAM } = React;

let _gpaUid = 0;

/* ---------------- helpers ---------------- */

/* Some backend strings arrive UTF-8-bytes-as-latin1 ("â€”", "ðŸ”´").
   Re-decode when high-latin lead bytes are present; fall back untouched. */
function fixText(s){
  if (typeof s !== "string" || !/[\u00c2-\u00f4][\u0080-\u00bf]/.test(s)) return s;
  try { return decodeURIComponent(escape(s)); } catch (e) { return s; }
}

const RISK_META = {
  high:     { label: "At Risk",         cls: "atrisk" },
  moderate: { label: "Needs Attention", cls: "warn2"  },
  low:      { label: "On Track",        cls: "ontrack"},
};

function cohortStandingLabel(pct, level){
  if (pct === null || pct === undefined) return "Not enough cohort data";
  const isTop = pct >= 50;
  const display = Math.round(isTop ? (100 - pct) : pct);
  return `${isTop ? "Top" : "Bottom"} ${display}% of ${level || "student"}s`;
}

/* Grade-point → bar color: A range green, B range blue, C range orange, below red */
function gradeBarColor(gp){
  if (gp >= 3.4) return "var(--success)";
  if (gp >= 2.8) return "var(--info)";
  if (gp >= 2.0) return "var(--warning)";
  return "var(--danger)";
}

/* Credits progress: color by how far along vs expected pace */
function creditsBarColor(earned, expected){
  if (!expected) return "var(--teal)";
  const ratio = earned / expected;
  if (ratio >= 0.95) return "var(--success)";
  if (ratio >= 0.8)  return "var(--warning)";
  return "var(--danger)";
}

/* category_performance arrives as { categories: {...}, strongest_category, weakest_category }
   from both /sae/student/{id} and /sae/student/{id}/analysis (verified live).
   Accept a flat {name: stats} dict too, in case the shape ever changes. */
function getCategoryMap(cp){
  if (!cp || typeof cp !== "object") return {};
  if (cp.categories && typeof cp.categories === "object") return cp.categories;
  const out = {};
  Object.keys(cp).forEach(k => {
    const v = cp[k];
    if (v && typeof v === "object" && ("pass_rate" in v || "avg_grade_points" in v)) out[k] = v;
  });
  return out;
}

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
  const [hovered, setHovered] = useAS(null);   // index of the hovered data point
  useAE(()=>{ const t=setTimeout(()=>setDraw(true),80); return ()=>clearTimeout(t); },[]);

  function renderTooltip(){
    if (hovered === null || !history[hovered]) return null;
    const p = pts[hovered], d = history[hovered];
    const label = `${d.term}: ${Number(d.gpa).toFixed(2)}`;
    const tw = Math.max(96, label.length * 6.8 + 18), th = 27;
    let tx = p[0] - tw / 2;                              // centred on the dot…
    tx = Math.max(padL, Math.min(tx, W - padR - tw));    // …shifted back inside the plot near the edges
    const above = p[1] - th - 12 >= 2;
    const ty = above ? p[1] - th - 12 : p[1] + 14;
    return (
      <g pointerEvents="none">
        <rect x={tx} y={ty} width={tw} height={th} rx="7" fill="var(--text)" opacity="0.93" />
        <text x={tx + tw/2} y={ty + 17.5} textAnchor="middle"
              fill="var(--bg)" fontFamily="var(--font-mono)" fontSize="11.5" fontWeight="700">{label}</text>
      </g>
    );
  }

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
          r={hovered===i ? (i===pts.length-1?6.5:5.5) : (i===pts.length-1?5:4)}
          style={{opacity:draw?1:0,transition:`opacity .4s ease ${0.5+i*0.06}s`}} />
      ))}

      {/* invisible enlarged hit areas so hovering is easy */}
      {pts.map((p,i)=>(
        <circle key={"hit"+i} cx={p[0]} cy={p[1]} r="13" fill="transparent" style={{cursor:"pointer"}}
          onMouseEnter={()=>setHovered(i)} onMouseLeave={()=>setHovered(null)} />
      ))}

      {history.map((d,i)=>(
        <text key={i} className="x-t" x={x(i)} y={padT+plotH+18}
          textAnchor="end" transform={`rotate(-32 ${x(i)} ${padT+plotH+18})`}>{d.term}</text>
      ))}

      {renderTooltip()}
    </svg>
  );
}

/* ---------------- mapping: /sae/student/{id} JSON → banner/metric shape ---------------- */
function mapStudentToDashboard(data){
  const profile = data.profile || {};
  const name = profile.name || profile.id || "—";
  const initials = name.split(" ").map(w=>w[0]).filter(Boolean).slice(0,2).join("").toUpperCase() || "?";
  const riskMeta = RISK_META[data.risk_level] || RISK_META.low;
  // Real field names: semester_label + cgpa_at_end_of_semester
  const history = (data.cgpa_trend_history || [])
    .map(t => ({ term: t.semester_label, gpa: t.cgpa_at_end_of_semester }))
    .filter(t => t.gpa !== null && t.gpa !== undefined);
  const cgpa = data.official_cgpa;
  const cohortStats = data.cohort_stats || {};
  const rules = data.rules || {};

  return {
    name, initials,
    program: profile.program || "",
    level: profile.level || "",
    id: profile.id || "",
    status: riskMeta.label,
    statusCls: riskMeta.cls,
    gpa: cgpa !== null && cgpa !== undefined ? cgpa.toFixed(2) : "—",
    gpaTrend: data.gpa_trend || "",                    // "improving" | "declining" | "stable"
    creditsEarned: Math.round(data.credits_completed || profile.official_credits_passed || 0),
    creditsTotal: rules.total_credits_required || 133,
    creditsExpected: data.expected_credits,
    cohortStanding: cohortStandingLabel(cohortStats.student_percentile, profile.level),
    cohortAvg: cohortStats.cohort_avg_cgpa,
    minLine: rules.minimum_cgpa || 2.0,
    gpaHistory: history,
    raw: data,
  };
}

/* ---------------- risk flag banners (critical=red, high=orange) ---------------- */
function RiskFlagBanners({ flags }){
  if (!flags || !flags.length) return null;
  return (
    <div className="pf-flag-stack">
      {flags.map((f,i)=>(
        <div key={i} className={"pf-flag lg "+(f.severity === "critical" ? "critical" : "high")}>
          <span className="pf-flag-ic"><Icon name="target" /></span>
          <span>{fixText(f.message)}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------------- banner + 3 metric cards + CGPA chart (shared) ---------------- */
function StudentDashboard({ s, L }){
  const [cw, setCw] = useAS(0);
  useAE(()=>{ const t=setTimeout(()=>setCw(Math.min(100, Math.round(s.creditsEarned/s.creditsTotal*100))),120); return ()=>clearTimeout(t); },[s]);
  const h = s.gpaHistory;
  // Trend arrow from the real gpa_trend field, falling back to the history slope
  const trend = s.gpaTrend || (h.length>1 ? (h[h.length-1].gpa >= h[h.length-2].gpa ? "improving" : "declining") : "");
  const arrow = trend === "improving" ? <span className="up">▲</span>
              : trend === "declining" ? <span className="dn">▼</span> : null;

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

      <RiskFlagBanners flags={(s.raw || {}).risk_flags} />

      <div className="metric-row">
        <div className="metric accentv">
          <div className="mk">{L.cumGpa}</div>
          <div className="mv">{s.gpa}{arrow}</div>
          {s.cohortAvg !== null && s.cohortAvg !== undefined &&
            <div className="pf-metric-note">cohort avg {s.cohortAvg.toFixed(2)}</div>}
        </div>
        <div className="metric">
          <div className="mk">{L.creditsProgress}</div>
          <div className="credits-v">{s.creditsEarned} / {s.creditsTotal} credits</div>
          <div className="credits-bar"><i style={{width:cw+"%", background:creditsBarColor(s.creditsEarned, s.creditsExpected)}} /></div>
          {s.creditsExpected !== null && s.creditsExpected !== undefined &&
            <div className="pf-metric-note">expected by now: {Math.round(s.creditsExpected)}</div>}
        </div>
        <div className="metric">
          <div className="mk">{L.cohortStanding}</div>
          <div className="standing-v">{s.cohortStanding}</div>
        </div>
      </div>

      <div className="an-card">
        <h3 className="an-title"><span className="ti-ic"><Icon name="trend" /></span>{L.gpaOverTime}</h3>
        {h.length >= 2
          ? <GpaChart history={h} min={s.minLine} minLabel={L.minLine} />
          : <div className="pf-empty-note">Not enough graded semesters yet to chart a trend.</div>}
      </div>
    </>
  );
}

/* ---------------- Performance by Subject Area (horizontal SVG-free bars) ---------------- */
function CategoryBars({ categoryPerformance }){
  const cats = getCategoryMap(categoryPerformance);
  const names = Object.keys(cats);
  if (!names.length) return null;
  return (
    <div className="an-card">
      <h3 className="an-title"><span className="ti-ic"><Icon name="chart" /></span>Performance by Subject Area</h3>
      {names.map(n=>{
        const c = cats[n];
        const gp = c.avg_grade_points || 0;
        return (
          <div className="pf-catbar" key={n}>
            <div className="cb-top">
              <span className="cb-name">{n}</span>
              <span className="cb-meta">
                {c.avg_letter_grade || "—"} · {Math.round((c.pass_rate||0)*100)}% pass · {c.courses_attempted} course{c.courses_attempted===1?"":"s"}
              </span>
            </div>
            <div className="cb-track">
              <i className="cb-fill" style={{width:Math.max(3,(gp/4)*100)+"%", background:gradeBarColor(gp)}} />
            </div>
          </div>
        );
      })}
      {(categoryPerformance && (categoryPerformance.strongest_category || categoryPerformance.weakest_category)) &&
        <div style={{display:"flex",gap:16,flexWrap:"wrap",marginTop:12,fontSize:12.5,fontWeight:700}}>
          {categoryPerformance.strongest_category &&
            <span style={{color:"var(--success)"}}>Strongest: {categoryPerformance.strongest_category}</span>}
          {categoryPerformance.weakest_category &&
            <span style={{color:"var(--danger)"}}>Weakest: {categoryPerformance.weakest_category}</span>}
        </div>}
    </div>
  );
}

/* ---------------- Prerequisite bottlenecks ---------------- */
function BottleneckCard({ bottleneck }){
  const blockers = (bottleneck || {}).blockers || [];
  if (!blockers.length) return null;
  return (
    <div className="an-card">
      <h3 className="an-title"><span className="ti-ic"><Icon name="route" /></span>Prerequisite Bottlenecks</h3>
      {blockers.map((b,i)=>(
        <div key={i} className={"pf-flag "+(b.severity === "high" ? "high" : "medium")}>
          <span className="pf-flag-ic"><Icon name="route" /></span>
          <span>
            <b className="mono">{b.course_code}</b>
            {" is blocking " + b.unlock_count + (b.unlock_count === 1 ? " course" : " courses") + " — recommended retake"}
            {b.blocks_graduation_requirement ? " (graduation requirement)" : ""}
          </span>
        </div>
      ))}
      {bottleneck.total_courses_currently_blocked > 0 &&
        <div className="pf-metric-note" style={{textAlign:"start"}}>
          {bottleneck.total_courses_currently_blocked} course(s) in the catalogue are currently out of reach until these are cleared.
        </div>}
    </div>
  );
}

/* ---------------- Anomaly alerts ---------------- */
const ANOMALY_TEXT = {
  silent_decline:       "CGPA trending down with no formal warning issued yet — act now before it becomes official.",
  repeated_course:      "Same course attempted 3 or more times.",
  undercredited_senior: "Behind on credits expected for senior level.",
};
function AnomalyCard({ anomalies }){
  const a = anomalies || {};
  if (!a.flagged || !(a.flags || []).length) return null;
  const details = a.details || {};
  const repeated = details.repeated_courses || {};
  return (
    <div className="an-card">
      <h3 className="an-title"><span className="ti-ic"><Icon name="sparkle" /></span>Anomaly Alerts</h3>
      {a.flags.map((f,i)=>(
        <div key={i} className="pf-flag high">
          <span className="pf-flag-ic"><Icon name="target" /></span>
          <span>
            {ANOMALY_TEXT[f] || f}
            {f === "repeated_course" && Object.keys(repeated).length > 0 &&
              " (" + Object.keys(repeated).map(c=>`${c}: ${repeated[c]}×`).join(", ") + ")"}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ---------------- Semester difficulty ---------------- */
const SDI_RISK_ROW = {
  high:              { bg: "var(--danger-soft)",  fg: "var(--danger)",   label: "High" },
  medium:            { bg: "var(--warning-soft)", fg: "var(--warning)",  label: "Medium" },
  low:               { bg: "var(--success-soft)", fg: "var(--success)",  label: "Low" },
  insufficient_data: { bg: "var(--surface-3)",    fg: "var(--text-faint)", label: "No data" },
};
function SemesterLoadCard({ sdi }){
  if (!sdi || !sdi.flag_message) return null;
  const courses = sdi.course_list || [];
  return (
    <div className="an-card">
      <h3 className="an-title"><span className="ti-ic"><Icon name="gear" /></span>Current Semester Load{sdi.semester_label ? ` · ${sdi.semester_label}` : ""}</h3>
      <div className="pf-kv-row"><span className="k">Difficulty Index (SDI)</span><span className="v">{(sdi.sdi_score||0).toFixed(2)} / 3.00</span></div>
      <div className="pf-kv-row"><span className="k">Courses this semester</span><span className="v">{sdi.course_count}</span></div>
      <div style={{fontSize:12.5,color:"var(--text-dim)",margin:"6px 0 4px"}}>{fixText(sdi.flag_message)}</div>
      {courses.length > 0 && (
        <div className="pf-tbl-wrap" style={{marginTop:10}}>
          <table className="plan">
            <thead><tr><th>Course Code</th><th>Historical Pass Rate</th><th>Difficulty</th></tr></thead>
            <tbody>
              {courses.map(c=>{
                const meta = SDI_RISK_ROW[c.risk_category] || SDI_RISK_ROW.insufficient_data;
                return (
                  <tr key={c.course_code} style={{background:meta.bg}}>
                    <td className="pcode">{c.course_code}</td>
                    <td className="pcr">{c.pass_rate_pct !== null && c.pass_rate_pct !== undefined ? c.pass_rate_pct.toFixed(1) + "%" : "—"}</td>
                    <td style={{color:meta.fg,fontWeight:700,fontSize:12}}>{meta.label}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ---------------- Suggestions ---------------- */
function SuggestionsCard({ suggestions, wide }){
  if (!suggestions || !suggestions.length) return null;
  return (
    <div className="an-card" style={wide ? {gridColumn:"1 / -1"} : null}>
      <h3 className="an-title"><span className="ti-ic"><Icon name="sparkle" /></span>What You Should Do</h3>
      {suggestions.map((s,i)=>(
        <div className="pf-suggestion" key={i}>
          <span className="num">{i+1}</span><span>{fixText(s)}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------------- LLM focus statement (rendered only when the API sends one) ---------------- */
function FocusStatement({ data }){
  const text = data.llm_focus_statement || data.focus_statement || data.plain_english_reason;
  if (!text) return null;
  return (
    <div className="pf-focus" style={{background:"#141a26", color:"#dfe6f2",
        border:"1px solid #2a3550", borderInlineStart:"4px solid var(--info)"}}>
      <b>Your Situation</b>
      {fixText(text)}
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
            <StudentDashboard s={mapStudentToDashboard(data)} L={L} />
            <FocusStatement data={data} />
            <SuggestionsCard suggestions={data.suggestions} />
            <div className="pf-card-grid">
              <CategoryBars categoryPerformance={data.category_performance} />
              <BottleneckCard bottleneck={data.prerequisite_bottleneck} />
              <AnomalyCard anomalies={data.anomalies} />
              <SemesterLoadCard sdi={data.semester_difficulty} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ---------------- Advisor: key points grouped into Issues / Strengths ---------------- */
function KeyPointsCard({ keyPoints }){
  const pts = keyPoints || [];
  if (!pts.length) return null;
  const issues    = pts.filter(p => p.severity !== "positive");
  const strengths = pts.filter(p => p.severity === "positive");
  const sevCls = (s) => s === "critical" ? "critical" : s === "high" ? "high" : "medium";
  return (
    <div className="an-card">
      <h3 className="an-title"><span className="ti-ic"><Icon name="target" /></span>Academic Key Points</h3>
      {issues.length > 0 && <div className="pf-group-lbl">Issues</div>}
      {issues.map((k,i)=>(
        <div key={"i"+i} className={"pf-flag "+sevCls(k.severity)}>
          <span>{fixText(k.emoji)} {fixText(k.message)}</span>
        </div>
      ))}
      {strengths.length > 0 && <div className="pf-group-lbl">Strengths</div>}
      {strengths.map((k,i)=>(
        <div key={"s"+i} className="pf-flag positive">
          <span>{fixText(k.emoji)} {fixText(k.message)}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------------- Advisor: LLM session guide (only when present in payload) ---------------- */
function SessionGuideCard({ guide }){
  if (!guide || !guide.opening) return null;
  return (
    <div className="an-card" style={{gridColumn:"1 / -1"}}>
      <h3 className="an-title"><span className="ti-ic"><Icon name="quote" /></span>Advising Session Guide</h3>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:10}}>
        <div className="pf-guide-card pf-guide-open" style={{marginBottom:0}}><b>How to Open</b>{fixText(guide.opening)}</div>
        <div className="pf-guide-card pf-guide-question" style={{marginBottom:0}}><b>Key Question to Ask</b>{fixText(guide.key_question)}</div>
        <div className="pf-guide-card pf-guide-goal" style={{marginBottom:0}}><b>Aim to Agree On</b>{fixText(guide.session_goal)}</div>
      </div>
    </div>
  );
}

/* ---------------- Advisor: subject-area table ---------------- */
function CategoryTable({ categoryPerformance }){
  const cats = getCategoryMap(categoryPerformance);
  const names = Object.keys(cats);
  if (!names.length) return null;
  return (
    <div className="an-card">
      <h3 className="an-title"><span className="ti-ic"><Icon name="chart" /></span>Performance by Subject Area</h3>
      <div className="pf-tbl-wrap">
        <table className="plan">
          <thead><tr><th>Category</th><th>Avg Grade</th><th>Attempted</th><th>Pass Rate</th><th>Status</th></tr></thead>
          <tbody>
            {names.map(n=>{
              const c = cats[n];
              const struggling = c.status === "struggling";
              return (
                <tr key={n}>
                  <td className="ptitle">{n}</td>
                  <td className="pcr">{c.avg_letter_grade || "—"}</td>
                  <td className="pcr">{c.courses_attempted}</td>
                  <td className="pcr">{Math.round((c.pass_rate||0)*100)}%</td>
                  <td><span className={"cc-status "+(struggling?"st-lock":"st-done")} style={struggling?{background:"var(--danger-soft)",color:"var(--danger)"}:null}>{c.status}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------------- Advisor: full registration history ---------------- */
function RegistrationsCard({ registrations }){
  const regs = registrations || [];
  if (!regs.length) return null;
  return (
    <div className="an-card" style={{gridColumn:"1 / -1"}}>
      <h3 className="an-title"><span className="ti-ic"><Icon name="book" /></span>Registration History</h3>
      <div className="pf-scroll pf-tbl-wrap">
        <table className="plan">
          <thead><tr><th>Semester</th><th>Course</th><th>Grade</th><th>Status</th></tr></thead>
          <tbody>
            {regs.map((r,i)=>{
              const failed = r.letter_grade === "F" || r.letter_grade === "Abs";
              return (
                <tr key={i}>
                  <td>{r.semester}</td>
                  <td className="pcode">{r.course_code}</td>
                  <td className={"pcr"+(failed?" pf-grade-red":"")}>{r.letter_grade || "—"}</td>
                  <td className="why">{r.registration_status}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------------- Advisor: all-students list (filter + sort) ---------------- */
const RISK_ORDER = { high: 0, moderate: 1, low: 2 };
const SORTS = [
  { key: "risk",     label: "Risk",       fn: (a,b)=>(RISK_ORDER[a.risk_level]-RISK_ORDER[b.risk_level]) || (a.official_cgpa||0)-(b.official_cgpa||0) },
  { key: "cgpa_asc", label: "CGPA ↑",     fn: (a,b)=>(a.official_cgpa||0)-(b.official_cgpa||0) },
  { key: "cgpa_desc",label: "CGPA ↓",     fn: (a,b)=>(b.official_cgpa||0)-(a.official_cgpa||0) },
  { key: "warnings", label: "Warnings",   fn: (a,b)=>(b.consecutive_warning||0)-(a.consecutive_warning||0) },
  { key: "name",     label: "Name",       fn: (a,b)=>String(a.name).localeCompare(String(b.name)) },
];
/* CGPA color tiers: red below 2.0, orange below 2.5, green otherwise */
function cgpaCellColor(g){
  if (g === null || g === undefined) return "var(--text-faint)";
  if (g < 2.0) return "var(--danger)";
  if (g < 2.5) return "var(--warning)";
  return "var(--success)";
}

function AllStudentsList({ students, onView }){
  const [filter, setFilter] = useAS("all");
  const [sortKey, setSortKey] = useAS("risk");
  const [q, setQ] = useAS("");

  const shown = useAM(()=>{
    const needle = q.trim().toLowerCase();
    let list = students;
    if (filter !== "all") list = list.filter(s => s.risk_level === filter);
    if (needle) list = list.filter(s =>
      String(s.name).toLowerCase().includes(needle) || String(s.id).toLowerCase().includes(needle));
    const sort = SORTS.find(s=>s.key===sortKey) || SORTS[0];
    return list.slice().sort(sort.fn);
  }, [students, filter, sortKey, q]);

  return (
    <div className="an-card" style={{marginTop:6}}>
      <h3 className="an-title"><span className="ti-ic"><Icon name="user" /></span>My Caseload <span className="pf-count-chip">{shown.length}</span></h3>

      <div className="pf-filter-row">
        {[["all","All"],["high","At Risk"],["moderate","Needs Attention"],["low","On Track"]].map(([k,lbl])=>(
          <button key={k} className={"pf-fchip"+(filter===k?" on":"")} onClick={()=>setFilter(k)}>{lbl}</button>
        ))}
        <span className="spring" />
        <select className="pf-sort-sel" value={sortKey} onChange={e=>setSortKey(e.target.value)}>
          {SORTS.map(s=><option key={s.key} value={s.key}>Sort: {s.label}</option>)}
        </select>
        <input className="pf-mini-search" value={q} onChange={e=>setQ(e.target.value)} placeholder="Search name or ID…" />
      </div>

      <div className="pf-tbl-wrap" style={{marginTop:12}}>
        <table className="plan">
          <thead><tr><th>Name</th><th>Student ID</th><th>Level</th><th>CGPA</th><th>Consecutive Warnings</th><th>Risk Level</th><th></th></tr></thead>
          <tbody>
            {shown.map(s=>{
              const meta = RISK_META[s.risk_level] || RISK_META.low;
              const warn = s.consecutive_warning || 0;
              return (
                <tr key={s.id}>
                  <td className="ptitle">{s.name}</td>
                  <td className="pcode">{s.id}</td>
                  <td>{s.level}</td>
                  <td className="pcr" style={{color:cgpaCellColor(s.official_cgpa),fontWeight:700}}>{s.official_cgpa !== null && s.official_cgpa !== undefined ? s.official_cgpa.toFixed(2) : "—"}</td>
                  <td className="pcr" style={warn > 0 ? {color:"var(--danger)",fontWeight:700} : null}>{warn}</td>
                  <td><span className={"pill "+meta.cls}><span className="pdot" />{meta.label}</span></td>
                  <td style={{textAlign:"end"}}><button className="act-btn sm" onClick={()=>onView(s.id)}>Review</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------------- Advisor: course risk index (GET /sae/courses/risk) ---------------- */
const RISK_LEVELS = ["All", "Freshman", "Sophomore", "Junior", "Senior"];

function passRateColor(p){
  if (p === null || p === undefined) return "var(--text-faint)";
  if (p < 50) return "var(--danger)";
  if (p < 75) return "var(--warning)";
  return "var(--success)";
}

function CourseRiskIndex(){
  const [level, setLevel] = useAS("All");
  const [rows, setRows] = useAS(null);
  const [loading, setLoading] = useAS(true);
  const [error, setError] = useAS(null);

  useAE(()=>{
    let alive = true;
    (async ()=>{
      setLoading(true); setError(null);
      const res = await PF_API.getCourseRisk(level === "All" ? null : level);
      if (!alive) return;
      if (res.__error) { setError(res.detail || "Could not load the course risk index."); setLoading(false); return; }
      setRows(Array.isArray(res) ? res : (res.courses || []));
      setLoading(false);
    })();
    return ()=>{ alive = false; };
  }, [level]);

  // Riskiest first: pass rate ascending, insufficient_data at the bottom
  const sorted = (rows || []).slice().sort((a,b)=>{
    const an = a.pass_rate_pct === null || a.pass_rate_pct === undefined;
    const bn = b.pass_rate_pct === null || b.pass_rate_pct === undefined;
    if (an !== bn) return an ? 1 : -1;
    return (a.pass_rate_pct||0) - (b.pass_rate_pct||0);
  });

  return (
    <div className="an-card" style={{marginTop:16}}>
      <h3 className="an-title"><span className="ti-ic"><Icon name="chart" /></span>Course Risk Index
        {!loading && !error && <span className="pf-count-chip">{sorted.length}</span>}</h3>

      <div className="pf-filter-row">
        {RISK_LEVELS.map(lv=>(
          <button key={lv} className={"pf-fchip"+(level===lv?" on":"")} onClick={()=>setLevel(lv)}>{lv}</button>
        ))}
      </div>

      {loading && <Spinner label="Loading course risk index…" />}
      {!loading && error && <div className="pf-login-err">{error}</div>}
      {!loading && !error && sorted.length === 0 &&
        <div className="pf-empty-note">No course data for this level.</div>}
      {!loading && !error && sorted.length > 0 && (
        <div className="pf-scroll pf-tbl-wrap" style={{marginTop:12}}>
          <table className="plan">
            <thead><tr><th>Course Code</th><th>Pass Rate</th><th>Avg Grade</th><th>Risk Category</th></tr></thead>
            <tbody>
              {sorted.map(c=>{
                const meta = SDI_RISK_ROW[c.risk_category] || SDI_RISK_ROW.insufficient_data;
                const p = c.pass_rate_pct;
                return (
                  <tr key={c.course_code}>
                    <td className="pcode">{c.course_code}</td>
                    <td className="pcr" style={{color:passRateColor(p),fontWeight:700}}>
                      {p !== null && p !== undefined ? p.toFixed(1) + "%" : "—"}</td>
                    <td className="pcr">{c.avg_letter_grade || "—"}</td>
                    <td><span style={{background:meta.bg,color:meta.fg,fontWeight:700,fontSize:12,
                        padding:"3px 10px",borderRadius:999}}>{meta.label}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ---------------- ADVISOR CONSOLE ---------------- */
/* SAE returns the same 710 students for every advisor_id (verified: ADV001 and
   ADV002 get identical payloads — assignment isn't implemented server-side).
   Simulate a stable per-advisor caseload of ~71 by hashing advisorId + student id. */
function djb2(str){
  let h = 5381;
  for (let i = 0; i < str.length; i++) h = (((h << 5) + h) + str.charCodeAt(i)) >>> 0;
  return h;
}
const CASELOAD_MOD = 10;   // ≈ 710 / 10 ≈ 71 students per advisor
function assignedToAdvisor(advisorId, students){
  return students.filter(s => djb2(advisorId + s.id) % CASELOAD_MOD === 0);
}

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
    // Real payload: a flat array of student summaries — counts are computed here.
    const students = Array.isArray(res) ? res : (res.students || []);
    setOverview({
      total_students: students.length,
      high_risk:     students.filter(s => s.risk_level === "high").length,
      moderate_risk: students.filter(s => s.risk_level === "moderate").length,
      low_risk:      students.filter(s => s.risk_level === "low").length,
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
    setLooked(base);
    setLookedAnalysis(analysis.__error ? null : analysis);
    setLookupLoading(false);
    try { document.querySelector(".page").scrollTo({top:0, behavior:"smooth"}); } catch(e){}
  }
  function clearLookup(){ setLooked(null); setLookedAnalysis(null); setQuery(""); setLookupError(null); }
  function onKey(e){ if(e.key==="Enter") lookUp(); }

  const risk = overview ? [
    { cls:"total", k:L.totalStudents, v:overview.total_students },
    { cls:"high",  k:L.highRisk,      v:overview.high_risk },
    { cls:"mod",   k:L.modRisk,       v:overview.moderate_risk },
    { cls:"low",   k:L.lowRisk,       v:overview.low_risk },
  ] : [];

  // Simulated caseload — the summary tiles above keep full-population counts,
  // but the attention list and the table only show this advisor's students.
  const caseload = overview ? assignedToAdvisor(advisorId, overview.students || []) : [];

  // immediate_action + immediate_reason are the real field names.
  // No severity field exists on overview rows — approximate severity by
  // consecutive warnings (desc), then CGPA (asc).
  const attention = caseload
    .filter(s => s.immediate_action)
    .sort((a,b)=>(b.consecutive_warning||0)-(a.consecutive_warning||0) || (a.official_cgpa||0)-(b.official_cgpa||0));
  const shownAttention = attention.slice(0, 6);
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

        {/* ── looked-up student: full dashboard + advisor-only additions ── */}
        {looked && (
          <>
            <div className="act-bar" style={{justifyContent:"flex-end"}}>
              <button className="act-btn sm" onClick={clearLookup}><Icon name="plus" style={{transform:"rotate(45deg)"}} />{L.clearLookup}</button>
            </div>
            <StudentDashboard s={mapStudentToDashboard(looked)} L={L} />

            <div className="pf-card-grid">
              <KeyPointsCard keyPoints={(lookedAnalysis||{}).key_points} />
              <SessionGuideCard guide={(lookedAnalysis||{}).llm_session_guide} />
              <CategoryTable categoryPerformance={(lookedAnalysis||looked).category_performance} />
              <BottleneckCard bottleneck={looked.prerequisite_bottleneck} />
              <SemesterLoadCard sdi={looked.semester_difficulty} />
              <RegistrationsCard registrations={(lookedAnalysis||looked).registrations} />
            </div>
          </>
        )}

        {/* ── overview: summary tiles + immediate actions + all students ── */}
        {!looked && (
          <>
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
                            <span className="pcode">{a.id}</span>
                          </div>
                          <div className="attn-flag"><Icon name="target" />{fixText(a.immediate_reason)}</div>
                        </div>
                        <div className="attn-r">
                          <span className="attn-gpa" style={(a.official_cgpa||0) >= 2.0 ? {color:"var(--text)",borderColor:"var(--border)"} : null}>
                            CGPA {a.official_cgpa !== null && a.official_cgpa !== undefined ? a.official_cgpa.toFixed(2) : "N/A"}
                          </span>
                          <button className="act-btn sm" onClick={()=>lookUp(a.id)}>View Profile</button>
                        </div>
                      </div>
                    ))}
                    {moreCount > 0 && <div className="attn-more">{L.moreHigh(moreCount)}</div>}
                  </div>
                </div>

                <AllStudentsList students={caseload} onView={(id)=>lookUp(id)} />

                <CourseRiskIndex />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { StudentAnalysisPage, AdvisorConsole, GpaChart });
