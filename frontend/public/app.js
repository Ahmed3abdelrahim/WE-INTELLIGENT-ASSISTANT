// App wiring (SPEC.md section 8). Render model/document output with textContent only —
// never innerHTML — except for the small set of elements we build ourselves below.

// ---------- Settings (per browser) ----------
function loadSetting(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch (_) {
    return fallback;
  }
}
function saveSetting(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_) { /* storage blocked: setting lasts for this page only */ }
}

const state = {
  conversationId: null,
  conversations: [],
  documents: [],
  selectedDocIds: new Set(),
  settings: {
    uiLang: loadSetting("we_ui_lang", "en"),
    theme: loadSetting("we_theme", "system"),
    answerLang: loadSetting("we_answer_lang", "auto"),
  },
  search: "",
  topic: "assistant",
  isRunning: false,
  recorder: new Recorder(60),
  provider: "local",
  health: null,
  micMode: "idle",
  lastHealthError: null,
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

// ---------- Theme / language ----------
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function applyTheme() {
  const { theme } = state.settings;
  const dark = theme === "dark" || (theme === "system" && darkQuery.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  el("qa-theme").replaceChildren(icon(dark ? "sun" : "moon"));
}
darkQuery.addEventListener("change", applyTheme);

function applyLanguage() {
  I18n.apply(state.settings.uiLang);
  // Everything built in JS (history, sources, tags, quick questions) re-renders in the new language.
  renderConversationList();
  renderTopicNav();
  renderBadge();
  renderDocChips();
  setMicState(state.micMode === "recording" ? "recording" : "idle");
  if (state.conversationId) selectConversation(state.conversationId, { keepFocus: true });
}

function setSetting(key, value) {
  state.settings[key] = value;
  saveSetting({ uiLang: "we_ui_lang", theme: "we_theme", answerLang: "we_answer_lang" }[key], value);
  if (key === "theme") applyTheme();
  if (key === "uiLang") applyLanguage();
  syncSettingsDialog();
}

function syncSettingsDialog() {
  document.querySelectorAll(".segmented").forEach((group) => {
    const current = state.settings[group.dataset.setting];
    group.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.value === current));
  });
}

document.querySelectorAll(".segmented").forEach((group) => {
  group.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => setSetting(group.dataset.setting, b.dataset.value))
  );
});

// ---------- Health / provider badge ----------
function renderBadge() {
  const badge = el("provider-badge");
  const cloud = state.provider === "openrouter";
  badge.textContent = cloud ? t("cloudBadge") : state.provider;
  badge.className = "provider-badge" + (cloud ? " cloud" : "");
}

async function loadHealth() {
  try {
    const health = await Api.health();
    state.provider = health.provider;
    renderBadge();
    const down = Object.entries(health.components || {}).filter(
      ([, v]) => v && v.status && v.status !== "ok" && v.status !== "unavailable"
    );
    if (down.length > 0) showError(t("serviceDown", down.map(([k]) => k).join(", ")));
    else clearError();
  } catch (e) {
    showError(t("backendDown") + e.message);
  }
}

// ---------- History (grouped by day, searchable, rename / delete) ----------
function dayBucket(iso) {
  const d = new Date(iso);
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const days = Math.floor((startOfToday - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return "last7";
  if (days < 30) return "last30";
  return "older";
}

function shortTime(iso) {
  const d = new Date(iso);
  const locale = state.settings.uiLang === "ar" ? "ar-EG" : "en-GB";
  return dayBucket(iso) === "today"
    ? d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(locale, { day: "numeric", month: "short" });
}

function renderConversationList() {
  const container = el("conv-list");
  container.textContent = "";
  const q = state.search.trim().toLowerCase();
  // Empty chats are hidden (except the one open now) so "New chat" clicks don't pile up.
  const visible = state.conversations.filter(
    (c) => (c.message_count > 0 || c.id === state.conversationId) && (!q || (c.title || "").toLowerCase().includes(q))
  );
  if (visible.length === 0) {
    const empty = document.createElement("div");
    empty.className = "conv-empty";
    empty.textContent = q ? t("noMatches") : t("noChats");
    container.appendChild(empty);
    return;
  }
  const order = ["today", "yesterday", "last7", "last30", "older"];
  const buckets = new Map(order.map((k) => [k, []]));
  for (const c of visible) buckets.get(dayBucket(c.last_message_at || c.created_at)).push(c);
  for (const key of order) {
    const items = buckets.get(key);
    if (items.length === 0) continue;
    const heading = document.createElement("div");
    heading.className = "conv-group";
    heading.textContent = t(key);
    container.appendChild(heading);
    for (const conv of items) container.appendChild(conversationItem(conv));
  }
}

function conversationItem(conv) {
  const item = document.createElement("div");
  item.className = "conv-item" + (conv.id === state.conversationId ? " active" : "");
  item.tabIndex = 0;
  item.dir = "auto";

  const title = document.createElement("span");
  title.className = "conv-title";
  title.textContent = conv.title || t("untitled");
  item.appendChild(icon("chats", 16));
  const time = document.createElement("span");
  time.className = "conv-time";
  time.textContent = shortTime(conv.last_message_at || conv.created_at);

  const actions = document.createElement("span");
  actions.className = "conv-actions";
  const renameBtn = document.createElement("button");
  renameBtn.className = "mini-btn";
  renameBtn.appendChild(icon("pencil", 13));
  renameBtn.title = t("rename");
  const deleteBtn = document.createElement("button");
  deleteBtn.className = "mini-btn";
  deleteBtn.appendChild(icon("trash", 13));
  deleteBtn.title = t("delete");
  actions.append(renameBtn, deleteBtn);

  item.append(title, time, actions);
  item.addEventListener("click", (e) => {
    if (!e.target.closest(".conv-actions, .conv-confirm, input")) selectConversation(conv.id);
  });
  item.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target === item) selectConversation(conv.id);
  });
  renameBtn.addEventListener("click", () => startRename(item, conv));
  deleteBtn.addEventListener("click", () => confirmDelete(item, conv));
  return item;
}

function startRename(item, conv) {
  const input = document.createElement("input");
  input.className = "conv-rename";
  input.value = conv.title || "";
  input.maxLength = 120;
  item.textContent = "";
  item.appendChild(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const title = input.value.trim();
    if (save && title && title !== conv.title) {
      try {
        await Api.renameConversation(conv.id, title);
        conv.title = title;
      } catch (e) {
        showError(t("errorPrefix") + e.message);
      }
    }
    renderConversationList();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

function confirmDelete(item, conv) {
  item.textContent = "";
  const box = document.createElement("div");
  box.className = "conv-confirm";
  const label = document.createElement("span");
  label.textContent = t("confirmDelete");
  const yes = document.createElement("button");
  yes.className = "mini-btn danger";
  yes.textContent = t("yes");
  const no = document.createElement("button");
  no.className = "mini-btn";
  no.textContent = t("cancel");
  box.append(label, yes, no);
  item.appendChild(box);
  no.addEventListener("click", () => renderConversationList());
  yes.addEventListener("click", async () => {
    try {
      await Api.deleteConversation(conv.id);
    } catch (e) {
      showError(t("errorPrefix") + e.message);
      return renderConversationList();
    }
    state.conversations = state.conversations.filter((c) => c.id !== conv.id);
    if (conv.id === state.conversationId) {
      const next = state.conversations.find((c) => c.message_count > 0);
      if (next) await selectConversation(next.id);
      else await newChat();
    } else {
      renderConversationList();
    }
  });
}

el("chat-search").addEventListener("input", (e) => {
  state.search = e.target.value;
  renderConversationList();
});

async function refreshConversations() {
  state.conversations = await Api.listConversations();
  renderConversationList();
}

async function loadConversations() {
  await refreshConversations();
  const first = state.conversations.find((c) => c.message_count > 0) || state.conversations[0];
  if (first) await selectConversation(first.id);
  else await newChat();
}

async function selectConversation(id, opts = {}) {
  state.conversationId = id;
  renderConversationList();
  closeSidebarOnMobile();
  const messages = await Api.getMessages(id);
  messagesEl.textContent = "";
  for (const m of messages) renderStoredMessage(m);
  if (messages.length === 0) renderWelcome();
  else state.topic = "assistant";
  renderChatHeader();
  renderTopicNav();
  scrollToBottom();
  if (!opts.keepFocus) composerInput.focus();
}

async function newChat(opts = {}) {
  state.topic = opts.topic || "assistant";
  // Reuse the open chat if nothing has been asked in it yet.
  const current = state.conversations.find((c) => c.id === state.conversationId);
  if (current && !current.message_count) {
    messagesEl.textContent = "";
    renderWelcome();
    renderTopicNav();
    renderChatHeader();
    closeSidebarOnMobile();
    composerInput.focus();
    return;
  }
  const conv = await Api.createConversation(null);
  state.conversations.unshift({ ...conv, message_count: 0, last_message_at: null });
  await selectConversation(conv.id);
}

// ---------- Topics (quick access) + welcome ----------
const TOPIC_ICONS = { assistant: "message", internet: "wifi", mobile: "phone", weair: "router", payments: "wallet", roaming: "plane", support: "headset" };

function renderTopicNav() {
  const nav = el("topic-nav");
  nav.textContent = "";
  const topics = t("topics");
  for (const key of Object.keys(TOPIC_ICONS)) {
    const b = document.createElement("button");
    b.className = "side-link" + (state.topic === key ? " active" : "");
    b.append(icon(TOPIC_ICONS[key]));
    const label = document.createElement("span");
    label.textContent = topics[key].label;
    b.appendChild(label);
    b.addEventListener("click", () => openTopic(key));
    nav.appendChild(b);
  }
}

async function openTopic(key) {
  state.topic = key;
  const current = state.conversations.find((c) => c.id === state.conversationId);
  if (current && !current.message_count) {
    messagesEl.textContent = "";
    renderWelcome();
    renderTopicNav();
    closeSidebarOnMobile();
    composerInput.focus();
  } else {
    await newChat({ topic: key });
  }
}

function renderWelcome() {
  const topic = t("topics")[state.topic] || t("topics").assistant;
  const questions = topic.questions || t("quick");
  const wrap = document.createElement("div");
  wrap.className = "welcome";
  const badge = icon(TOPIC_ICONS[state.topic] || "bot", 26);
  badge.classList.add("welcome-icon");
  const h = document.createElement("h2");
  h.textContent = topic.questions ? topic.label : t("welcomeTitle");
  const p = document.createElement("p");
  p.textContent = t("welcomeBody");
  const grid = document.createElement("div");
  grid.className = "welcome-grid";
  for (const q of questions) {
    const b = document.createElement("button");
    b.className = "welcome-card";
    b.dir = "auto";
    b.textContent = q;
    b.addEventListener("click", () => sendMessage(q, "text"));
    grid.appendChild(b);
  }
  wrap.append(badge, h, p, grid);
  messagesEl.appendChild(wrap);
}

// ---------- Chat header: title + clear conversation ----------
function renderChatHeader() {
  const conv = state.conversations.find((c) => c.id === state.conversationId);
  el("chat-title").textContent = (conv && conv.title) || t("untitled");
  const clear = el("clear-chat");
  clear.classList.toggle("hidden", !conv || !conv.message_count);
  clear.replaceChildren(icon("trash", 16), Object.assign(document.createElement("span"), { textContent: t("clearConversation") }));
  clear.dataset.confirm = "";
}

el("clear-chat").addEventListener("click", async (e) => {
  const clear = el("clear-chat");
  if (!clear.dataset.confirm) {
    // two-step: first click asks, second click deletes
    clear.dataset.confirm = "1";
    clear.classList.add("confirming");
    clear.replaceChildren(icon("trash", 16), Object.assign(document.createElement("span"), { textContent: t("confirmDelete") }));
    setTimeout(() => {
      if (clear.dataset.confirm) {
        clear.classList.remove("confirming");
        renderChatHeader();
      }
    }, 4000);
    return;
  }
  clear.classList.remove("confirming");
  const id = state.conversationId;
  try {
    await Api.deleteConversation(id);
  } catch (err) {
    showError(t("errorPrefix") + err.message);
    return;
  }
  state.conversations = state.conversations.filter((c) => c.id !== id);
  await newChat();
});

// ---------- Message rendering ----------
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderStoredMessage(m) {
  const row = addMessageRow(m.role, m.role === "assistant" ? "" : m.text);
  if (m.role === "assistant") {
    renderAnswer(row.bubble, m.text, m.citations || []);
    addSourcesList(row.bubble, m.citations || []);
  }
  if (m.audio_path) addAudioPlayer(row.bubble, m.id);
}

function addMessageRow(role, text, opts = {}) {
  const welcome = messagesEl.querySelector(".welcome");
  if (welcome) welcome.remove();
  const row = document.createElement("div");
  row.className = `msg-row ${role}${opts.isError ? " error" : ""}`;
  const avatar = icon(role === "assistant" ? "bot" : "user", 17);
  avatar.classList.add("avatar");
  const body = document.createElement("div");
  body.className = "msg-body";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.dir = "auto";
  bubble.textContent = text; // textContent only — never innerHTML for model/document text
  body.appendChild(bubble);
  row.append(avatar, body);
  messagesEl.appendChild(row);
  scrollToBottom();
  return { row, bubble, body };
}

function addStatusTag(row, status, warning) {
  if (status === "insufficient_evidence" || status === "clarify") {
    const tag = document.createElement("div");
    tag.className = "status-tag" + (status === "insufficient_evidence" ? " insufficient" : "");
    tag.textContent = status === "clarify" ? t("needsClarification") : t("insufficient");
    row.appendChild(tag);
  }
  if (warning) {
    const tag = document.createElement("div");
    tag.className = "warning-tag";
    tag.textContent = t("numericWarning");
    row.appendChild(tag);
  }
}

function addAudioPlayer(bubble, messageId) {
  const audio = document.createElement("audio");
  audio.className = "answer-audio";
  audio.controls = true;
  audio.src = Api.audioUrl(messageId);
  bubble.parentElement.appendChild(audio);
  return audio;
}

// ---------- Answer formatting ----------
// The LLM writes light markdown ("- item", "1. item", "**bold**") with [S#] source markers.
// Build real lists / bold / numbered reference badges from it — as DOM nodes, never innerHTML.
const BULLET_RE = /^\s*[-*•]\s+(.*)$/;
const NUMBERED_RE = /^\s*\d+[.)]\s+(.*)$/;
const HEADING_RE = /^\s*#{1,4}\s+(.*)$/;
// "text [S1]." -> "text. [S1]": punctuation after a marker otherwise lands after the badge,
// which in right-to-left Arabic lines shows up stranded at the far left.
const PUNCT_AFTER_CITE_RE = /((?:\s*\[S\d+\])+)\s*([.,،؛;:!?؟])/g;

// Several passages of one document are cited as S1, S3, S4...; the reader cares about the
// document. Group by url/filename: one number and one Sources entry per document.
function groupCitations(citations) {
  const groups = [];
  const byKey = new Map();
  const byLabel = new Map();
  for (const c of citations || []) {
    const key = c.url || c.filename || c.title || c.label;
    let g = byKey.get(key);
    if (!g) {
      g = { num: groups.length + 1, title: c.title, url: c.url, filename: c.filename, passages: [] };
      groups.push(g);
      byKey.set(key, g);
    }
    g.passages.push(c);
    byLabel.set(c.label, g);
  }
  return { groups, byLabel };
}

// grouping: from groupCitations(); null while streaming, so markers stay hidden until the
// final event says which sources are valid.
function appendInline(parent, text, grouping) {
  const parts = text.split(/(\*\*[^*]+\*\*|\s*\[S\d+\])/);
  for (const part of parts) {
    if (!part) continue;
    const cite = /^\s*\[S(\d+)\]$/.exec(part);
    if (cite) {
      const g = grouping ? grouping.byLabel.get(`S${cite[1]}`) : null;
      if (!g) continue;
      const last = parent.lastChild;
      if (last && last.dataset && last.dataset.num === String(g.num)) continue; // [S1][S3] same doc
      const badge = document.createElement("button");
      badge.className = "cite-badge";
      badge.dataset.num = String(g.num);
      badge.textContent = String(g.num);
      badge.title = cleanTitle(g.title || g.filename || "");
      badge.addEventListener("click", () => openSources(grouping, g.num));
      parent.appendChild(badge);
    } else if (part.startsWith("**") && part.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = part.slice(2, -2);
      parent.appendChild(strong);
    } else {
      parent.appendChild(document.createTextNode(part));
    }
  }
}

function renderAnswer(bubble, text, citations) {
  bubble.textContent = "";
  bubble.classList.add("rich");
  const grouping = citations ? groupCitations(citations) : null;
  let list = null; // current <ul>/<ol> while consecutive list lines continue
  for (const rawLine of text.split("\n")) {
    const line = rawLine.replace(PUNCT_AFTER_CITE_RE, "$2$1");
    const bullet = BULLET_RE.exec(line);
    const numbered = NUMBERED_RE.exec(line);
    if (bullet || numbered) {
      const tag = numbered ? "OL" : "UL";
      if (!list || list.tagName !== tag) {
        list = document.createElement(tag);
        bubble.appendChild(list);
      }
      const li = document.createElement("li");
      appendInline(li, (numbered || bullet)[1], grouping);
      list.appendChild(li);
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    const heading = HEADING_RE.exec(line);
    const p = document.createElement("p");
    if (heading) p.className = "answer-heading";
    appendInline(p, heading ? heading[1] : line, grouping);
    bubble.appendChild(p);
  }
}

function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (_) {
    return "";
  }
}

function cleanTitle(title) {
  return (title || "").replace(/\s*[|-]\s*Telecom Egypt\s*$/i, "");
}

function groupMeta(g) {
  const sameAsTitle = !g.url && g.filename && g.filename === g.title;
  const parts = [g.url ? hostOf(g.url) : sameAsTitle ? t("document") : g.filename || t("document")];
  if (g.passages.length > 1) parts.push(t("passages", g.passages.length));
  else if (g.passages[0].page) parts.push(t("page", g.passages[0].page));
  return parts.join(" · ");
}

function addSourcesList(bubble, citations) {
  if (!citations || citations.length === 0) return;
  const grouping = groupCitations(citations);
  const wrap = document.createElement("div");
  wrap.className = "sources-list";
  const label = document.createElement("div");
  label.className = "sources-label";
  label.textContent = t("sources");
  wrap.appendChild(label);
  for (const g of grouping.groups) {
    const item = document.createElement("button");
    item.className = "source-item";
    item.title = g.url || g.filename || "";
    const num = document.createElement("span");
    num.className = "cite-badge";
    num.textContent = String(g.num);
    const title = document.createElement("span");
    title.className = "source-title";
    title.dir = "auto";
    title.textContent = cleanTitle(g.title || g.filename || g.url);
    const meta = document.createElement("span");
    meta.className = "source-meta";
    meta.textContent = groupMeta(g);
    item.append(num, title, meta);
    item.addEventListener("click", () => openSources(grouping, g.num));
    wrap.appendChild(item);
  }
  bubble.appendChild(wrap);
}

// ---------- Sources drawer: one card per document, each cited passage inside it ----------
function openSources(grouping, focusNum) {
  const content = el("sources-content");
  content.textContent = "";
  for (const g of grouping.groups) {
    const card = document.createElement("div");
    card.className = "source-card" + (g.num === focusNum ? " focused" : "");
    card.dir = "auto";

    const title = document.createElement("div");
    title.className = "src-title";
    const num = document.createElement("span");
    num.className = "cite-badge";
    num.textContent = String(g.num);
    title.appendChild(num);
    if (g.url && /^https?:\/\//.test(g.url)) {
      const link = document.createElement("a");
      link.href = g.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = cleanTitle(g.title) || g.url;
      title.appendChild(link);
    } else {
      title.appendChild(document.createTextNode(cleanTitle(g.title || g.filename || "")));
    }
    card.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "src-meta";
    meta.textContent = [g.url, g.filename].filter(Boolean).join(" · ");
    card.appendChild(meta);

    for (const c of g.passages) {
      const passage = document.createElement("div");
      passage.className = "src-passage";
      const where = [c.section, c.page ? t("page", c.page) : null].filter(Boolean).join(" · ");
      if (where) {
        const w = document.createElement("div");
        w.className = "src-where";
        w.textContent = where;
        passage.appendChild(w);
      }
      const excerpt = document.createElement("div");
      excerpt.className = "src-excerpt";
      excerpt.textContent = c.excerpt;
      passage.appendChild(excerpt);
      card.appendChild(passage);
    }
    content.appendChild(card);
  }
  el("sources-overlay").classList.remove("hidden");
  const focused = content.querySelector(".source-card.focused");
  if (focused) focused.scrollIntoView({ block: "nearest" });
}

// ---------- Sending ----------
// Only Send is locked while an answer streams; the textbox stays enabled and focused so the
// next question can be typed straight away (disabling it dropped focus after every send).
function setRunning(running) {
  state.isRunning = running;
  sendBtn.disabled = running && state.micMode !== "recording";
}

async function sendMessage(text, inputMode) {
  if (!text.trim() || state.isRunning) return;
  setRunning(true);
  clearError();

  addMessageRow("user", text);
  const assistantRow = addMessageRow("assistant", "");
  assistantRow.bubble.classList.add("typing");

  let accumulated = "";
  let finalData = null;

  try {
    for await (const evt of Api.chat({
      conversationId: state.conversationId,
      text,
      inputMode,
      lang: state.settings.answerLang,
      docIds: Array.from(state.selectedDocIds),
    })) {
      if (evt.event === "stage") {
        stageLineEl.textContent = t(evt.data.name === "generating" ? "generating" : "searching");
      } else if (evt.event === "token") {
        assistantRow.bubble.classList.remove("typing");
        accumulated += evt.data.text;
        renderAnswer(assistantRow.bubble, accumulated, null);
        scrollToBottom();
      } else if (evt.event === "final") {
        assistantRow.bubble.classList.remove("typing");
        finalData = evt.data;
        renderAnswer(assistantRow.bubble, finalData.answer, finalData.citations || []);
        addSourcesList(assistantRow.bubble, finalData.citations);
        addStatusTag(assistantRow.body, finalData.status, finalData.warning);
        scrollToBottom();
      } else if (evt.event === "error") {
        assistantRow.bubble.classList.remove("typing");
        assistantRow.row.classList.add("error");
        assistantRow.bubble.textContent = t("errorPrefix") + (evt.data.message || evt.data.code);
      }
    }
  } catch (e) {
    assistantRow.bubble.classList.remove("typing");
    assistantRow.row.classList.add("error");
    assistantRow.bubble.textContent = t("chatFailed") + e.message;
  }

  stageLineEl.textContent = "";
  setRunning(false);
  composerInput.focus();
  refreshConversations().then(renderChatHeader).catch(() => {}); // new title / activity time

  if (inputMode === "voice" && finalData && finalData.message_id) {
    stageLineEl.textContent = t("speaking");
    try {
      await Api.speech(finalData.message_id);
      const audio = addAudioPlayer(assistantRow.bubble, finalData.message_id);
      audio.autoplay = true;
    } catch (e) {
      // SPEC.md section 7.9: a TTS failure never removes the text
      console.warn("TTS failed:", e);
    }
    stageLineEl.textContent = "";
  }
}

sendBtn.addEventListener("click", () => {
  // While recording, Send means "stop and send what I said".
  if (state.recorder.isRecording) {
    stopRecordingAndSend();
    return;
  }
  const text = composerInput.value;
  if (state.isRunning || !text.trim()) return; // keep what was typed until it can be sent
  composerInput.value = "";
  autoGrow();
  composerInput.focus();
  sendMessage(text, "text");
});
composerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendBtn.click();
  }
});
function autoGrow() {
  composerInput.style.height = "auto";
  composerInput.style.height = Math.min(composerInput.scrollHeight, 160) + "px";
}
composerInput.addEventListener("input", autoGrow);

// ---------- Mic / voice ----------
function showMicHelp(title, lines) {
  el("mic-help-title").textContent = title;
  const body = el("mic-help-body");
  body.textContent = "";
  for (const line of lines) {
    const p = document.createElement("p");
    p.textContent = line;
    body.appendChild(p);
  }
  el("mic-help-overlay").classList.remove("hidden");
}

// idle -> starting (opening the mic, can take 1-2s) -> recording -> idle
function setMicState(mode) {
  state.micMode = mode;
  micBtn.classList.toggle("starting", mode === "starting");
  micBtn.classList.toggle("recording", mode === "recording");
  micBtn.title = t(mode === "recording" ? "stopAndSend" : "recordVoice");
  micBtn.replaceChildren(icon(mode === "recording" ? "stop" : "mic"));
  const sendLabel = el("send-label");
  sendLabel.textContent = t("sendVoice");
  sendLabel.classList.toggle("hidden", mode !== "recording");
  sendBtn.classList.toggle("with-label", mode === "recording");
  sendBtn.disabled = mode === "recording" ? false : state.isRunning;
  if (mode === "starting") stageLineEl.textContent = t("startingMic");
  else if (mode === "recording") stageLineEl.textContent = t("listening");
  else stageLineEl.textContent = "";
  if (mode !== "recording") recTimerEl.textContent = "";
}

async function startRecording() {
  // Browsers only expose the microphone on HTTPS or localhost. On plain http://<ip> Chrome
  // hides navigator.mediaDevices entirely and never shows its permission popup.
  if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showMicHelp(t("micInsecureTitle"), [t("micInsecure1", location.origin), t("micInsecure2")]);
    return;
  }
  setMicState("starting");
  state.recorder.onTick = (secs) => {
    const m = String(Math.floor(secs / 60)).padStart(2, "0");
    const s = String(secs % 60).padStart(2, "0");
    recTimerEl.textContent = `${m}:${s}`;
  };
  state.recorder.onAutoStop = () => stopRecordingAndSend(); // 60s cap: send, don't drop it
  try {
    await state.recorder.start(); // first time: the browser's "Allow microphone?" popup
    setMicState("recording");
  } catch (e) {
    setMicState("idle");
    if (e.name === "NotAllowedError") showMicHelp(t("micBlockedTitle"), [t("micBlocked1"), t("micBlocked2")]);
    else if (e.name === "NotFoundError") showMicHelp(t("noMicTitle"), [t("noMic1")]);
    else showError(t("micUnavailable") + e.message);
  }
}

async function stopRecordingAndSend() {
  if (!state.recorder.isRecording) return;
  const blob = await state.recorder.stop();
  setMicState("idle");
  stageLineEl.textContent = t("transcribing");
  try {
    const result = await Api.transcribe(blob, state.settings.answerLang);
    stageLineEl.textContent = "";
    if (result.rejected) {
      showError(t("noSpeech"));
      return;
    }
    if (el("review-toggle").checked) {
      composerInput.value = result.text;
      autoGrow();
      composerInput.focus();
    } else {
      await sendMessage(result.text, "voice");
    }
  } catch (e) {
    stageLineEl.textContent = "";
    showError(t("transcriptionFailed") + e.message);
  }
}

micBtn.addEventListener("click", () => {
  if (state.micMode === "starting") return; // still opening the mic
  if (state.recorder.isRecording) stopRecordingAndSend();
  else startRecording();
});

// ---------- Documents ----------
function renderDocuments() {
  const container = el("doc-list");
  container.textContent = "";
  for (const doc of state.documents) {
    const row = document.createElement("label");
    row.className = "doc-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedDocIds.has(doc.id);
    checkbox.disabled = doc.status !== "ready";
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedDocIds.add(doc.id);
      else state.selectedDocIds.delete(doc.id);
      renderDocChips();
    });
    row.appendChild(checkbox);

    const name = document.createElement("span");
    name.className = "doc-name";
    name.dir = "auto";
    name.textContent = doc.filename;
    row.appendChild(name);

    const status = document.createElement("span");
    status.className = "doc-status" + (doc.status === "error" ? " error" : "");
    status.textContent = doc.status;
    row.appendChild(status);

    container.appendChild(row);
  }
}

// Documents in use are shown above the composer so it's obvious answers may come from them.
function renderDocChips() {
  const box = el("doc-chips");
  box.textContent = "";
  const selected = state.documents.filter((d) => state.selectedDocIds.has(d.id));
  box.classList.toggle("hidden", selected.length === 0);
  if (!selected.length) return;
  const label = document.createElement("span");
  label.className = "doc-chips-label";
  label.textContent = t("selectedDocs");
  box.appendChild(label);
  for (const d of selected) {
    const chip = document.createElement("span");
    chip.className = "doc-chip";
    chip.dir = "auto";
    chip.append(icon("file", 13), document.createTextNode(d.filename));
    const x = document.createElement("button");
    x.title = t("removeDoc");
    x.appendChild(icon("x", 12));
    x.addEventListener("click", () => {
      state.selectedDocIds.delete(d.id);
      renderDocuments();
      renderDocChips();
    });
    chip.appendChild(x);
    box.appendChild(chip);
  }
}

async function refreshDocuments() {
  state.documents = await Api.listDocuments();
  renderDocuments();
  renderDocChips();
}

async function uploadFile(file) {
  try {
    const doc = await Api.uploadDocument(file);
    if (doc && doc.status === "ready") state.selectedDocIds.add(doc.id); // use what was just uploaded
    await refreshDocuments();
  } catch (e) {
    showError(t("uploadFailed") + e.message);
  }
}

const dropzone = el("dropzone");
const fileInput = el("file-input");
dropzone.addEventListener("click", () => fileInput.click());
el("attach-btn").addEventListener("click", () => fileInput.click());
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

// ---------- Insights ----------
async function openInsights() {
  const content = el("insights-content");
  content.textContent = t("loading");
  el("insights-overlay").classList.remove("hidden");
  try {
    const insights = await Api.getInsights(state.conversationId);
    content.textContent = "";
    const grid = document.createElement("div");
    grid.className = "insights-grid";
    const labels = t("insightLabels");
    for (const [key, value] of Object.entries(insights)) {
      const row = document.createElement("div");
      row.className = "insight-row";
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = labels[key] || key;
      const v = document.createElement("span");
      v.dir = "auto";
      if (typeof value === "boolean") v.textContent = value ? t("yesValue") : t("noValue");
      else v.textContent = Array.isArray(value) ? value.join(", ") || "—" : value == null || value === "" ? "—" : String(value);
      row.append(k, v);
      grid.appendChild(row);
    }
    content.appendChild(grid);
  } catch (e) {
    content.textContent = t("insightsFailed") + e.message;
  }
}

// ---------- Overlays / quick actions ----------
document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => el(btn.dataset.close).classList.add("hidden"));
});
document.querySelectorAll(".overlay").forEach((overlay) => {
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.classList.add("hidden");
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.querySelectorAll(".overlay:not(.hidden)").forEach((o) => o.classList.add("hidden"));
});

function closeSidebarOnMobile() {
  document.body.classList.remove("sidebar-open");
}

el("new-chat-btn").addEventListener("click", newChat);
el("qa-new").addEventListener("click", newChat);
el("qa-insights").addEventListener("click", openInsights);
el("qa-settings").addEventListener("click", () => {
  syncSettingsDialog();
  el("settings-overlay").classList.remove("hidden");
});
el("qa-theme").addEventListener("click", () =>
  setSetting("theme", document.documentElement.dataset.theme === "dark" ? "light" : "dark")
);
el("qa-lang").addEventListener("click", () => setSetting("uiLang", state.settings.uiLang === "ar" ? "en" : "ar"));
el("sidebar-toggle").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));

// ---------- Init ----------
(async function init() {
  fillIcons();
  applyTheme();
  I18n.apply(state.settings.uiLang);
  renderTopicNav();
  syncSettingsDialog();
  await loadHealth();
  await loadConversations();
  await refreshDocuments();
  composerInput.focus();
  setInterval(loadHealth, 15000);
})();
