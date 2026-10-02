// App wiring (SPEC.md section 8). Render model/document output with textContent only —
// never innerHTML — except for the small set of elements we build ourselves below.

const state = {
  conversationId: null,
  conversations: [],
  documents: [],
  selectedDocIds: new Set(),
  lang: "auto",
  reviewBeforeSend: false,
  isRunning: false,
  recorder: new Recorder(60),
  provider: "local",
};

const el = (id) => document.getElementById(id);
const messagesEl = el("messages");
const stageLineEl = el("stage-line");
const composerInput = el("composer-input");
const sendBtn = el("send-btn");
const micBtn = el("mic-btn");
const recTimerEl = el("rec-timer");
const errorBanner = el("error-banner");

function showError(msg) {
  errorBanner.textContent = msg;
  errorBanner.classList.remove("hidden");
}
function clearError() {
  errorBanner.classList.add("hidden");
  errorBanner.textContent = "";
}

// ---------- Health / provider badge ----------
async function loadHealth() {
  try {
    const health = await Api.health();
    state.provider = health.provider;
    const badge = el("provider-badge");
    badge.textContent = health.provider === "openrouter" ? "Cloud LLM (comparison) — data leaves this machine" : health.provider;
    badge.className = "provider-badge" + (health.provider === "openrouter" ? " cloud" : "");

    const down = Object.entries(health.components || {}).filter(([, v]) => v && v.status && v.status !== "ok" && v.status !== "unavailable");
    if (down.length > 0) {
      showError(`Service unavailable: ${down.map(([k]) => k).join(", ")}`);
    } else {
      clearError();
    }
  } catch (e) {
    showError("Could not reach the backend: " + e.message);
  }
}

// ---------- Conversations ----------
function renderConversationList() {
  const container = el("conv-list");
  container.textContent = "";
  for (const conv of state.conversations) {
    const div = document.createElement("div");
    div.className = "conv-item" + (conv.id === state.conversationId ? " active" : "");
    div.textContent = conv.title || "Untitled chat";
    div.addEventListener("click", () => selectConversation(conv.id));
    container.appendChild(div);
  }
}

async function loadConversations() {
  state.conversations = await Api.listConversations();
  if (state.conversations.length === 0) {
    const conv = await Api.createConversation(null);
    state.conversations = [conv];
  }
  renderConversationList();
  await selectConversation(state.conversations[0].id);
}

async function selectConversation(id) {
  state.conversationId = id;
  renderConversationList();
  const messages = await Api.getMessages(id);
  messagesEl.textContent = "";
  for (const m of messages) {
    renderStoredMessage(m);
  }
  scrollToBottom();
}

async function newChat() {
  const conv = await Api.createConversation(null);
  state.conversations.unshift(conv);
  await selectConversation(conv.id);
}

// ---------- Message rendering ----------
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderStoredMessage(m) {
  const row = addMessageRow(m.role, m.text, { messageId: m.id });
  if (m.citations && m.citations.length > 0) {
    addCitationChips(row.bubble, m.citations);
  }
  if (m.audio_path) {
    addAudioPlayer(row.bubble, m.id);
  }
}

function addMessageRow(role, text, opts = {}) {
  const row = document.createElement("div");
  row.className = `msg-row ${role}${opts.isError ? " error" : ""}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.dir = "auto";
  bubble.textContent = text; // textContent only — never innerHTML for model/document text
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
  return { row, bubble };
}

function addCitationChips(bubble, citations) {
  const wrap = document.createElement("div");
  wrap.className = "citations";
  for (const c of citations) {
    const chip = document.createElement("button");
    chip.className = "citation-chip";
    chip.textContent = `[${c.label.replace("S", "")}]`;
    chip.addEventListener("click", () => openSources(citations, c.label));
    wrap.appendChild(chip);
  }
  bubble.parentElement.appendChild(wrap);
}

function addStatusTag(row, status, warning) {
  if (status === "insufficient_evidence" || status === "clarify") {
    const tag = document.createElement("div");
    tag.className = "status-tag" + (status === "insufficient_evidence" ? " insufficient" : "");
    tag.textContent = status === "clarify" ? "Needs clarification" : "Insufficient evidence";
    row.appendChild(tag);
  }
  if (warning) {
    const tag = document.createElement("div");
    tag.className = "warning-tag";
    tag.textContent = "⚠ a number in this answer could not be fully verified against sources";
    row.appendChild(tag);
  }
}

function addAudioPlayer(bubble, messageId) {
  const audio = document.createElement("audio");
  audio.className = "answer-audio";
  audio.controls = true;
  audio.src = Api.audioUrl(messageId);
  bubble.parentElement.appendChild(audio);
}

// ---------- Sources panel ----------
function openSources(citations, focusLabel) {
  const content = el("sources-content");
  content.textContent = "";
  for (const c of citations) {
    const card = document.createElement("div");
    card.className = "source-card";
    if (c.label === focusLabel) card.style.borderColor = "var(--purple)";

    const title = document.createElement("div");
    title.className = "src-title";
    title.textContent = `[${c.label}] ${c.title}`;
    card.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "src-meta";
    const parts = [];
    if (c.url) parts.push(c.url);
    if (c.filename) parts.push(c.filename);
    if (c.page) parts.push(`p.${c.page}`);
    if (c.section) parts.push(c.section);
    meta.textContent = parts.join(" · ");
    card.appendChild(meta);

    const excerpt = document.createElement("div");
    excerpt.className = "src-excerpt";
    excerpt.textContent = c.excerpt;
    card.appendChild(excerpt);

    content.appendChild(card);
  }
  el("sources-overlay").classList.remove("hidden");
}

// ---------- Sending ----------
function setRunning(running) {
  state.isRunning = running;
  sendBtn.disabled = running;
  composerInput.disabled = running;
}

async function sendMessage(text, inputMode) {
  if (!text.trim() || state.isRunning) return;
  setRunning(true);
  clearError();

  addMessageRow("user", text);
  const assistantRow = addMessageRow("assistant", "");
  assistantRow.bubble.textContent = "";

  let accumulated = "";
  let finalData = null;

  try {
    for await (const evt of Api.chat({
      conversationId: state.conversationId,
      text,
      inputMode,
      lang: state.lang,
      docIds: Array.from(state.selectedDocIds),
    })) {
      if (evt.event === "stage") {
        const labels = { searching: "Searching", generating: "Generating" };
        stageLineEl.textContent = labels[evt.data.name] || evt.data.name;
      } else if (evt.event === "token") {
        accumulated += evt.data.text;
        assistantRow.bubble.textContent = accumulated;
        scrollToBottom();
      } else if (evt.event === "final") {
        finalData = evt.data;
        assistantRow.bubble.textContent = finalData.answer;
        if (finalData.citations && finalData.citations.length > 0) {
          addCitationChips(assistantRow.bubble, finalData.citations);
        }
        addStatusTag(assistantRow.row, finalData.status, finalData.warning);
      } else if (evt.event === "error") {
        assistantRow.row.classList.add("error");
        assistantRow.bubble.textContent = `Error: ${evt.data.message || evt.data.code}`;
      }
    }
  } catch (e) {
    assistantRow.row.classList.add("error");
    assistantRow.bubble.textContent = "Something went wrong talking to the assistant: " + e.message;
  }

  stageLineEl.textContent = "";
  setRunning(false);

  if (inputMode === "voice" && finalData && finalData.message_id) {
    stageLineEl.textContent = "Speaking";
    try {
      await Api.speech(finalData.message_id);
      addAudioPlayer(assistantRow.bubble, finalData.message_id);
      const audio = assistantRow.bubble.parentElement.querySelector("audio");
      if (audio) audio.autoplay = true;
    } catch (e) {
      // SPEC.md section 7.9: a TTS failure never removes the text
      console.warn("TTS failed:", e);
    }
    stageLineEl.textContent = "";
  }
}

sendBtn.addEventListener("click", () => {
  const text = composerInput.value;
  composerInput.value = "";
  sendMessage(text, "text");
});
composerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendBtn.click();
  }
});

// ---------- Mic / voice ----------
micBtn.addEventListener("click", async () => {
  if (!state.recorder.isRecording) {
    try {
      await state.recorder.start();
      micBtn.classList.add("recording");
      state.recorder.onTick = (secs) => {
        const m = String(Math.floor(secs / 60)).padStart(2, "0");
        const s = String(secs % 60).padStart(2, "0");
        recTimerEl.textContent = `${m}:${s}`;
      };
    } catch (e) {
      showError("Microphone access denied or unavailable: " + e.message);
    }
  } else {
    const blob = await state.recorder.stop();
    micBtn.classList.remove("recording");
    recTimerEl.textContent = "";
    stageLineEl.textContent = "Transcribing";
    try {
      const result = await Api.transcribe(blob, state.lang);
      stageLineEl.textContent = "";
      if (result.rejected) {
        showError("Couldn't hear speech clearly — please re-record.");
        return;
      }
      if (el("review-toggle").checked) {
        composerInput.value = result.text;
        composerInput.focus();
      } else {
        await sendMessage(result.text, "voice");
      }
    } catch (e) {
      stageLineEl.textContent = "";
      showError("Transcription failed: " + e.message);
    }
  }
});

// ---------- Documents ----------
function renderDocuments() {
  const container = el("doc-list");
  container.textContent = "";
  for (const doc of state.documents) {
    const row = document.createElement("div");
    row.className = "doc-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedDocIds.has(doc.id);
    checkbox.disabled = doc.status !== "ready";
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedDocIds.add(doc.id);
      else state.selectedDocIds.delete(doc.id);
    });
    row.appendChild(checkbox);

    const name = document.createElement("span");
    name.className = "doc-name";
    name.textContent = doc.filename;
    row.appendChild(name);

    const status = document.createElement("span");
    status.className = "doc-status" + (doc.status === "error" ? " error" : "");
    status.textContent = doc.status;
    row.appendChild(status);

    container.appendChild(row);
  }
}

async function refreshDocuments() {
  state.documents = await Api.listDocuments();
  renderDocuments();
}

async function uploadFile(file) {
  try {
    await Api.uploadDocument(file);
    await refreshDocuments();
  } catch (e) {
    showError("Upload failed: " + e.message);
  }
}

const dropzone = el("dropzone");
const fileInput = el("file-input");
dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
  fileInput.value = "";
});
dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
});

// ---------- Language switch ----------
document.querySelectorAll(".lang-switch button").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.lang = btn.dataset.lang;
    document.querySelectorAll(".lang-switch button").forEach((b) => b.classList.toggle("active", b === btn));
  });
});

// ---------- Insights ----------
el("insights-btn").addEventListener("click", async () => {
  const content = el("insights-content");
  content.textContent = "Loading...";
  el("insights-overlay").classList.remove("hidden");
  try {
    const insights = await Api.getInsights(state.conversationId);
    content.textContent = "";
    const grid = document.createElement("div");
    grid.className = "insights-grid";
    for (const [key, value] of Object.entries(insights)) {
      const row = document.createElement("div");
      row.className = "insight-row";
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = key;
      const v = document.createElement("span");
      v.textContent = Array.isArray(value) ? value.join(", ") : String(value);
      row.appendChild(k);
      row.appendChild(v);
      grid.appendChild(row);
    }
    content.appendChild(grid);
  } catch (e) {
    content.textContent = "Could not load insights: " + e.message;
  }
});

// ---------- Overlays ----------
document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => el(btn.dataset.close).classList.add("hidden"));
});
document.querySelectorAll(".overlay").forEach((overlay) => {
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.classList.add("hidden");
  });
});

// ---------- New chat ----------
el("new-chat-btn").addEventListener("click", newChat);
el("review-toggle").addEventListener("change", (e) => {
  state.reviewBeforeSend = e.target.checked;
});

// ---------- Init ----------
(async function init() {
  await loadHealth();
  await loadConversations();
  await refreshDocuments();
  setInterval(loadHealth, 15000);
})();
