import streamlit as st
import requests
import os

API = os.getenv("PATHFINDER_API_URL", "http://localhost:8000")

st.set_page_config(page_title="PathFinder", page_icon="🎓", layout="wide")

for k, v in [("student_id", None), ("session_id", None), ("session_name", None),
              ("messages", []), ("all_sessions", [])]:
    if k not in st.session_state:
        st.session_state[k] = v


def api_get_sessions(student_id):
    try:
        r = requests.get(f"{API}/sessions/{student_id}", timeout=5)
        return r.json().get("sessions", []) if r.status_code == 200 else []
    except Exception:
        return []


def api_load_history(student_id, session_id):
    try:
        r = requests.get(
            f"{API}/students/{student_id}/sessions/{session_id}/history", timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            msgs = []
            for t in data.get("turns", []):
                msgs.append({"role": "user", "content": t.get("user", "")})
                msgs.append({"role": "assistant", "content": t.get("answer", "")})
            return msgs, data.get("session_name", "")
    except Exception:
        pass
    return [], ""


def api_delete_session(student_id, session_id):
    """Call DELETE /students/{student_id}/sessions/{session_id}. Returns True on success."""
    try:
        r = requests.delete(
            f"{API}/students/{student_id}/sessions/{session_id}", timeout=5
        )
        return r.status_code == 200
    except Exception:
        return False


def _apply_delete_to_state(state, deleted_session_id):
    """
    Pure helper: update a state dict after a session is deleted.
    Removes deleted session from all_sessions.
    Clears session_id/session_name/messages if the deleted session was the active one.
    Returns the mutated state dict.
    """
    state["all_sessions"] = [
        s for s in state.get("all_sessions", [])
        if s["session_id"] != deleted_session_id
    ]
    if state.get("session_id") == deleted_session_id:
        state["session_id"] = None
        state["session_name"] = None
        state["messages"] = []
    return state


def api_chat(user_text, student_id, session_id=None):
    payload = {"user_text": user_text, "student_id": student_id}
    if session_id:
        payload["session_id"] = session_id
    try:
        r = requests.post(f"{API}/chat", json=payload, timeout=60)
        if r.status_code == 200:
            return r.json()
        return {"answer_text": f"Server error {r.status_code}: {r.text}", "status": "error",
                "session_id": session_id, "session_name": "", "citations": []}
    except Exception as exc:
        return {"answer_text": f"Cannot reach backend: {exc}", "status": "error",
                "session_id": session_id, "session_name": "", "citations": []}


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎓 PathFinder")
    st.caption("Academic & Career Advisor — EUI")
    st.divider()

    if not st.session_state.student_id:
        st.subheader("Student Login")
        sid = st.text_input("Student ID", placeholder="STU000001")
        if st.button("Enter", use_container_width=True) and sid.strip():
            st.session_state.student_id = sid.strip()
            st.session_state.all_sessions = api_get_sessions(sid.strip())
            st.rerun()
    else:
        st.success(f"Logged in: `{st.session_state.student_id}`")
        if st.button("Logout", use_container_width=True):
            for k in ["student_id", "session_id", "session_name"]:
                st.session_state[k] = None
            st.session_state.messages = []
            st.session_state.all_sessions = []
            st.rerun()

        st.divider()
        if st.button("➕ New Chat", use_container_width=True):
            st.session_state.session_id = None
            st.session_state.session_name = None
            st.session_state.messages = []
            st.rerun()

        if st.session_state.all_sessions:
            st.subheader("Sessions")
            for s in st.session_state.all_sessions:
                label = s.get("session_name", s["session_id"])[:38]
                col1, col2 = st.columns([0.85, 0.15])
                with col1:
                    if st.button(f"💬 {label}", key=s["session_id"], use_container_width=True):
                        msgs, name = api_load_history(st.session_state.student_id, s["session_id"])
                        st.session_state.session_id = s["session_id"]
                        st.session_state.session_name = name
                        st.session_state.messages = msgs
                        st.rerun()
                with col2:
                    if st.button("🗑", key=f"del_{s['session_id']}", use_container_width=True):
                        if api_delete_session(st.session_state.student_id, s["session_id"]):
                            fresh = api_get_sessions(st.session_state.student_id)
                            state = {
                                "all_sessions": fresh,
                                "session_id": st.session_state.session_id,
                                "session_name": st.session_state.session_name,
                                "messages": list(st.session_state.messages),
                            }
                            _apply_delete_to_state(state, s["session_id"])
                            st.session_state.all_sessions = state["all_sessions"]
                            st.session_state.session_id = state["session_id"]
                            st.session_state.session_name = state["session_name"]
                            st.session_state.messages = state["messages"]
                        else:
                            st.warning("Could not delete session. Please try again.")
                        st.rerun()


# ── Main ──────────────────────────────────────────────────────────────────────
if not st.session_state.student_id:
    st.markdown("## Welcome to PathFinder 🎓")
    st.markdown("Please enter your Student ID in the sidebar to begin.")
    st.stop()

st.markdown(f"### {st.session_state.session_name or 'New Conversation'}")
st.divider()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_input = st.chat_input("Ask about your courses, career, graduation, or university rules...")

if user_input and user_input.strip():
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            resp = api_chat(user_input, st.session_state.student_id, st.session_state.session_id)
        answer = resp.get("answer_text", "Something went wrong.")
        citations = resp.get("citations", [])
        st.markdown(answer)
        if citations:
            with st.expander("📚 Sources"):
                for c in citations:
                    page = f", p.{c['page']}" if c.get("page") else ""
                    st.markdown(f"- {c.get('source', '')}{page}")

    st.session_state.messages.append({"role": "assistant", "content": answer})
    if resp.get("session_id"):
        st.session_state.session_id = resp["session_id"]
    if resp.get("session_name"):
        st.session_state.session_name = resp["session_name"]
        st.session_state.all_sessions = api_get_sessions(st.session_state.student_id)
    st.rerun()
