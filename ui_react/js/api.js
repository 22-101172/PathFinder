/* ============================================================
   PathFinder — API layer
   All network calls to the FastAPI backend live here.
   window: PF_API
   ============================================================ */

const PF_API_BASE = (window.__PF_API_BASE__) || "http://localhost:8000";

async function _getJSON(path, params) {
  const url = new URL(PF_API_BASE + path);
  if (params) Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  });
  try {
    const r = await fetch(url.toString());
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
      return { __error: true, status: r.status, detail };
    }
    return await r.json();
  } catch (e) {
    return { __error: true, status: 0, detail: "Cannot reach server" };
  }
}

async function _postJSON(path, body, params) {
  const url = new URL(PF_API_BASE + path);
  if (params) Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  });
  try {
    const r = await fetch(url.toString(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
      return { __error: true, status: r.status, detail };
    }
    return await r.json();
  } catch (e) {
    return { __error: true, status: 0, detail: "Cannot reach server" };
  }
}

async function _deleteJSON(path) {
  try {
    const r = await fetch(PF_API_BASE + path, { method: "DELETE" });
    return r.ok;
  } catch (e) {
    return false;
  }
}

const PF_API = {
  // ── Identity ────────────────────────────────────────────────────────────
  detectUserType(id) {
    const v = (id || "").trim().toUpperCase();
    if (v.startsWith("STU")) return "student";
    if (v.startsWith("ADV")) return "advisor";
    return "unknown";
  },

  // ── Chat (PathFinder core) ──────────────────────────────────────────────
  async chat(userText, studentId, sessionId) {
    const body = { user_text: userText, student_id: studentId };
    if (sessionId) body.session_id = sessionId;
    return _postJSON("/chat", body);
  },
  async getSessions(studentId) {
    const r = await _getJSON(`/sessions/${encodeURIComponent(studentId)}`);
    return r.__error ? [] : (r.sessions || []);
  },
  async getSessionHistory(studentId, sessionId) {
    const r = await _getJSON(`/students/${encodeURIComponent(studentId)}/sessions/${encodeURIComponent(sessionId)}/history`);
    if (r.__error) return { messages: [], sessionName: "" };
    const msgs = [];
    (r.turns || []).forEach(t => {
      msgs.push({ who: "user", text: t.user || "" });
      msgs.push({ who: "bot", text: t.answer || "", citations: [] });
    });
    return { messages: msgs, sessionName: r.session_name || "" };
  },
  async deleteSession(studentId, sessionId) {
    return _deleteJSON(`/students/${encodeURIComponent(studentId)}/sessions/${encodeURIComponent(sessionId)}`);
  },

  // ── SAE — student ────────────────────────────────────────────────────────
  async getStudentAnalysis(studentId) {
    return _getJSON(`/sae/student/${encodeURIComponent(studentId)}`);
  },
  async simulateGpa(studentId, grades) {
    return _postJSON(`/sae/student/${encodeURIComponent(studentId)}/simulate`, { grades });
  },

  // ── SAE — advisor ────────────────────────────────────────────────────────
  async getAdvisorOverview(advisorId) {
    return _getJSON("/sae/advisor/overview", { advisor_id: advisorId });
  },
  async getAdvisorAnalysis(studentId) {
    return _getJSON(`/sae/student/${encodeURIComponent(studentId)}/analysis`);
  },

  // ── SAE — shared ─────────────────────────────────────────────────────────
  async getCourseRisk(level) {
    return _getJSON("/sae/courses/risk", level ? { level } : null);
  },
};

window.PF_API = PF_API;
