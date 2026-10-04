// Only file that talks to the backend (SPEC.md section 8).
const API_BASE = "/api/v1";

function getSessionId() {
  let sid = localStorage.getItem("we_session_id");
  if (!sid) {
    sid = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    localStorage.setItem("we_session_id", sid);
  }
  return sid;
}

async function apiFetch(path, options = {}) {
  const headers = { "X-Session-Id": getSessionId(), ...(options.headers || {}) };
  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch (_) { /* ignore */ }
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp;
}

function parseSseEvent(rawEvent) {
  let event = "message";
  let dataLines = [];
  for (const line of rawEvent.split(/\r\n|\r|\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    // lines starting with ":" are SSE comments/pings — ignored
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch (_) {
    return null;
  }
}

const Api = {
  getSessionId,

  async health() {
    return (await apiFetch("/health")).json();
  },

  async createConversation(title) {
    return (
      await apiFetch("/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title || null }),
      })
    ).json();
  },

  async listConversations() {
    return (await apiFetch("/conversations")).json();
  },

  async renameConversation(conversationId, title) {
    return (
      await apiFetch(`/conversations/${conversationId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      })
    ).json();
  },

  async deleteConversation(conversationId) {
    return (await apiFetch(`/conversations/${conversationId}`, { method: "DELETE" })).json();
  },

  async getMessages(conversationId) {
    return (await apiFetch(`/conversations/${conversationId}/messages`)).json();
  },

  async *chat({ conversationId, text, inputMode, lang, docIds }) {
    const resp = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Id": getSessionId() },
      body: JSON.stringify({
        conversation_id: conversationId,
        text,
        input_mode: inputMode,
        lang,
        doc_ids: docIds || [],
      }),
    });
    if (!resp.ok || !resp.body) {
      throw new Error(`chat request failed: HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE allows CRLF, LF or CR line endings; sse-starlette sends CRLF ("\r\n\r\n" between
      // events), so a plain "\n\n" search never matches and no event is ever parsed.
      let m;
      while ((m = /\r\n\r\n|\n\n|\r\r/.exec(buf)) !== null) {
        const rawEvent = buf.slice(0, m.index);
        buf = buf.slice(m.index + m[0].length);
        const parsed = parseSseEvent(rawEvent);
        if (parsed) yield parsed;
      }
    }
  },

  async transcribe(blob, lang) {
    const form = new FormData();
    form.append("file", blob, "recording.webm");
    if (lang && lang !== "auto") form.append("lang", lang);
    return (await apiFetch("/transcribe", { method: "POST", body: form })).json();
  },

  async speech(messageId) {
    return (await apiFetch(`/messages/${messageId}/speech`, { method: "POST" })).json();
  },

  audioUrl(messageId) {
    return `${API_BASE}/audio/${messageId}`;
  },

  async uploadDocument(file) {
    const form = new FormData();
    form.append("file", file);
    return (await apiFetch("/documents", { method: "POST", body: form })).json();
  },

  async listDocuments() {
    return (await apiFetch("/documents")).json();
  },

  async getInsights(conversationId) {
    return (await apiFetch(`/conversations/${conversationId}/insights`, { method: "POST" })).json();
  },
};
