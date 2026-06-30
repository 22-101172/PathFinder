/* ============================================================
   PathFinder — EUI Academic Advisor · static chrome text
   window: PF_DATA, PF_I18N
   (Mock chat flows and mock analysis data have been removed —
    the app now calls the real PathFinder/SAE backend via PF_API.)
   ============================================================ */

const PF_DATA = {
  bot: { name: "PathFinder", nameAr: "المرشد", tagline: "EUI Academic Advisor" },
  motto: { en: "Your Gateway to the Science of the Future", ar: "بوابتك إلى علوم المستقبل" },
};

/* ---------------- i18n (UI chrome) ---------------- */
const PF_I18N = {
  en: {
    newChat: "New advising chat", recent: "Recent", advising: "Advising tools",
    advisorTitle: "Academic Advisor",
    pagesLabel: "Workspace",
    navChat: "Advisor Chat", navStudent: "My Analysis", navAdvisor: "Advisor Console",
    navChatSub: "Ask anything", navStudentSub: "Your dashboard", navAdvisorSub: "Cohort & risk",
    studentTitle: "My Academic Analysis", studentSub: "Student Analysis Engine",
    advisorConsoleTitle: "Advisor Console", advisorConsoleSub: "Student Analysis Engine",
    idLabel: "Student ID", idPlaceholder: "Enter a student ID — e.g. STU000703", lookUp: "Look up", clearLookup: "Clear",
    gpaOverTime: "CGPA Progression", yourStanding: "Your Standing", yourMetrics: "Your Metrics",
    cumGpa: "Cumulative GPA", creditsProgress: "Credits Progress", cohortStanding: "Cohort Standing",
    totalStudents: "Total Students", highRisk: "At Risk", modRisk: "Needs Attention", lowRisk: "On Track",
    attentionTitle: "Students Needing Immediate Attention", moreHigh: (n) => `…and ${n} more high-risk students`,
    minLine: "2.0 min", inProgress: "Live analysis", dropHint: "",
    actExport: "Export report", actFlag: "Flag for outreach", actTranscript: "Open transcript", actReview: "Review",
    greet: (n) => `Good evening, ${n}`,
    capsLabel: "Start with an example", footNote: "PathFinder reasons over your transcript, the prerequisite graph and the CIS handbook. Always confirm with your advisor.",
    placeholder: "Ask about courses, eligibility, your plan or EUI policies…",
    attach: "Attach", citations: "Citations", student: "Student",
    loginTitle: "Sign in to PathFinder", loginSub: "Enter your Student ID or Advisor ID to continue.",
    loginPlaceholder: "e.g. STU000001 or ADV001", loginBtn: "Continue",
    loginErrUnknown: "ID must start with STU (student) or ADV (advisor).",
    loginErrNotFound: "We couldn't find that ID. Please check and try again.",
    logout: "Logout",
    caps: [
      { intent: "eligibility", icon: "check", cls: "ic ale", t: "Check eligibility", q: "Can I take Machine Learning next term?" },
      { intent: "audit", icon: "chart", cls: "ic ale", t: "Degree audit", q: "How close am I to graduating?" },
      { intent: "plan", icon: "calendar", cls: "ic kg", t: "Plan a semester", q: "Build my Spring 2026 schedule." },
      { intent: "policy", icon: "book", cls: "ic rag", t: "Handbook answers", q: "What is the withdrawal policy?" },
    ],
    suggest: [
      { label: "Eligible for ML?", icon: "check", cls: "ic ale", q: "Can I take Machine Learning next term?" },
      { label: "Degree audit", icon: "chart", cls: "ic ale", q: "How close am I to graduating?" },
      { label: "Plan Spring 2026", icon: "calendar", cls: "ic kg", q: "Build my Spring 2026 schedule." },
      { label: "Simulate my GPA", icon: "trend", cls: "ic ale", q: "If I get an A in my current courses, what will my CGPA be?" },
      { label: "Withdrawal policy", icon: "book", cls: "ic rag", q: "What is the withdrawal policy?" },
      { label: "Career tracks", icon: "compass", cls: "ic kg", q: "Which career track fits me best?" },
    ],
  },
  ar: {
    newChat: "محادثة إرشاد جديدة", recent: "الأخيرة", advising: "أدوات الإرشاد",
    advisorTitle: "المرشد الأكاديمي",
    pagesLabel: "مساحة العمل",
    navChat: "محادثة المرشد", navStudent: "تحليلي الأكاديمي", navAdvisor: "لوحة المرشد",
    navChatSub: "اسأل أي شيء", navStudentSub: "لوحتك", navAdvisorSub: "المخاطر والمجموعة",
    studentTitle: "تحليلي الأكاديمي", studentSub: "محرك تحليل الطالب",
    advisorConsoleTitle: "لوحة المرشد", advisorConsoleSub: "محرك تحليل الطالب",
    idLabel: "رقم الطالب", idPlaceholder: "أدخل رقم الطالب — مثال STU000703", lookUp: "بحث", clearLookup: "مسح",
    gpaOverTime: "تطور المعدل التراكمي", yourStanding: "وضعك", yourMetrics: "مقاييسك",
    cumGpa: "المعدل التراكمي", creditsProgress: "تقدم الساعات", cohortStanding: "ترتيبك بين الزملاء",
    totalStudents: "إجمالي الطلاب", highRisk: "في خطر", modRisk: "يحتاج متابعة", lowRisk: "على المسار الصحيح",
    attentionTitle: "طلاب يحتاجون اهتماماً فورياً", moreHigh: (n) => `و ${n} طالباً آخر في خطر مرتفع`,
    minLine: "الحد الأدنى 2.0", inProgress: "تحليل مباشر", dropHint: "",
    actExport: "تصدير التقرير", actFlag: "وضع علامة للتواصل", actTranscript: "فتح كشف الدرجات", actReview: "مراجعة",
    greet: (n) => `مساء الخير، ${n}`,
    capsLabel: "ابدأ بمثال", footNote: "يعتمد المرشد على سجلك الأكاديمي وشبكة المتطلبات ودليل الكلية. يُرجى دائماً التأكد مع مرشدك.",
    placeholder: "اسأل عن المقررات أو الأهلية أو خطتك أو لوائح الجامعة…",
    attach: "إرفاق", citations: "المصادر", student: "طالب",
    loginTitle: "تسجيل الدخول إلى PathFinder", loginSub: "أدخل رقم الطالب أو رقم المرشد للمتابعة.",
    loginPlaceholder: "مثال STU000001 أو ADV001", loginBtn: "متابعة",
    loginErrUnknown: "يجب أن يبدأ الرقم بـ STU (طالب) أو ADV (مرشد).",
    loginErrNotFound: "لم نتمكن من العثور على هذا الرقم. يرجى التحقق والمحاولة مرة أخرى.",
    logout: "تسجيل الخروج",
    caps: [
      { intent: "eligibility", icon: "check", cls: "ic ale", t: "تحقق من الأهلية", q: "هل يمكنني دراسة تعلم الآلة الفصل القادم؟" },
      { intent: "audit", icon: "chart", cls: "ic ale", t: "تدقيق الدرجة", q: "كم تبقى لي على التخرج؟" },
      { intent: "plan", icon: "calendar", cls: "ic kg", t: "خطط لفصل دراسي", q: "اقترح جدول ربيع 2026." },
      { intent: "policy", icon: "book", cls: "ic rag", t: "إجابات الدليل", q: "ما هي سياسة الانسحاب؟" },
    ],
    suggest: [
      { label: "أهلية تعلم الآلة؟", icon: "check", cls: "ic ale", q: "هل يمكنني دراسة تعلم الآلة الفصل القادم؟" },
      { label: "تدقيق الدرجة", icon: "chart", cls: "ic ale", q: "كم تبقى لي على التخرج؟" },
      { label: "خطة ربيع 2026", icon: "calendar", cls: "ic kg", q: "اقترح جدول ربيع 2026." },
      { label: "محاكاة المعدل", icon: "trend", cls: "ic ale", q: "لو حصلت على A في مقرراتي الحالية، ما هو معدلي التراكمي؟" },
      { label: "سياسة الانسحاب", icon: "book", cls: "ic rag", q: "ما هي سياسة الانسحاب؟" },
      { label: "المسارات المهنية", icon: "compass", cls: "ic kg", q: "أي مسار مهني يناسبني؟" },
    ],
  },
};

window.PF_DATA = PF_DATA;
window.PF_I18N = PF_I18N;
