/**
 * BEN Chrome Extension — popup.js
 * Handles: chat (streaming SSE), summarize page, explain selection, save to memory.
 * API key is NEVER present here — all AI calls go through the local backend.
 */

"use strict";

const BEN_API = "http://127.0.0.1:8000";

// ── DOM refs ──────────────────────────────────────────────────────────────
const statusDot      = document.getElementById("statusDot");
const statusText     = document.getElementById("statusText");
const messages       = document.getElementById("messages");
const chatInput      = document.getElementById("chatInput");
const btnSend        = document.getElementById("btnSend");
const btnStop        = document.getElementById("btnStop");
const btnNewChat     = document.getElementById("btnNewChat");
const btnSummarize   = document.getElementById("btnSummarize");
const btnExplain     = document.getElementById("btnExplain");
const btnSave        = document.getElementById("btnSave");
const categorySelect = document.getElementById("categorySelect");
const toolResult     = document.getElementById("toolResult");
const toolResultLabel= document.getElementById("toolResultLabel");
const toolResultBody = document.getElementById("toolResultBody");
const btnCopyResult  = document.getElementById("btnCopyResult");

// ── State ─────────────────────────────────────────────────────────────────
let isStreaming   = false;
let activeReader  = null;   // ReadableStreamDefaultReader for SSE
let currentModel  = "gemini-3.8-flash";

// ── Tabs ──────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ── Status check ──────────────────────────────────────────────────────────
async function checkStatus() {
  try {
    const res = await fetch(`${BEN_API}/api/extension/status`, { signal: AbortSignal.timeout(4000) });
    if (res.ok) {
      const data = await res.json();
      currentModel = data.model || currentModel;
      setStatus("online", `BEN online · ${data.model}`);
    } else {
      setStatus("offline", "Backend error");
    }
  } catch {
    setStatus("offline", "Backend offline — start run.py");
  }
}

function setStatus(state, text) {
  statusDot.className = `status-dot ${state}`;
  statusText.textContent = text;
}

// ── Chat: render helpers ──────────────────────────────────────────────────
function appendMessage(role, text, isError = false) {
  // Remove welcome message on first real message
  const welcome = messages.querySelector(".welcome-msg");
  if (welcome) welcome.remove();

  const wrap = document.createElement("div");
  wrap.className = `msg ${isError ? "error" : role}`;

  const label = document.createElement("div");
  label.className = "msg-label";
  label.textContent = role === "user" ? "You" : "BEN";

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.textContent = text;

  wrap.appendChild(label);
  wrap.appendChild(bubble);
  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function appendStreamingBubble() {
  const welcome = messages.querySelector(".welcome-msg");
  if (welcome) welcome.remove();

  const wrap = document.createElement("div");
  wrap.className = "msg ai";

  const label = document.createElement("div");
  label.className = "msg-label";
  label.textContent = "BEN";

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble cursor";
  bubble.textContent = "";

  wrap.appendChild(label);
  wrap.appendChild(bubble);
  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

// ── Chat: send via SSE streaming ──────────────────────────────────────────
async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || isStreaming) return;

  chatInput.value = "";
  autoResize();
  appendMessage("user", text);

  const bubble = appendStreamingBubble();
  setStreamingState(true);

  try {
    const res = await fetch(`${BEN_API}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      bubble.classList.remove("cursor");
      bubble.textContent = `Error ${res.status}: ${err.detail || res.statusText}`;
      bubble.closest(".msg").classList.add("error");
      setStreamingState(false);
      return;
    }

    const reader = res.body.getReader();
    activeReader  = reader;
    const decoder = new TextDecoder();
    let accumulated = "";
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep incomplete line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let evt;
        try { evt = JSON.parse(line.slice(6)); } catch { continue; }

        if (evt.type === "token") {
          accumulated += evt.text;
          bubble.textContent = accumulated;
          messages.scrollTop = messages.scrollHeight;

        } else if (evt.type === "done") {
          bubble.classList.remove("cursor");

        } else if (evt.type === "cancelled") {
          bubble.classList.remove("cursor");
          if (!accumulated) bubble.textContent = "(generation stopped)";

        } else if (evt.type === "error") {
          bubble.classList.remove("cursor");
          bubble.textContent = `Error: ${evt.detail}`;
          bubble.closest(".msg").classList.add("error");
        }
      }
    }

  } catch (err) {
    bubble.classList.remove("cursor");
    if (err.name === "AbortError") {
      if (!bubble.textContent) bubble.textContent = "(generation stopped)";
    } else {
      bubble.textContent = `Network error: ${err.message}`;
      bubble.closest(".msg").classList.add("error");
    }
  } finally {
    activeReader = null;
    setStreamingState(false);
    bubble.classList.remove("cursor");
  }
}

function setStreamingState(streaming) {
  isStreaming = streaming;
  btnSend.disabled = streaming;
  btnSend.classList.toggle("hidden", streaming);
  btnStop.classList.toggle("hidden", !streaming);
  chatInput.disabled = streaming;
}

// ── Chat: stop / new ──────────────────────────────────────────────────────
btnStop.addEventListener("click", async () => {
  try { await fetch(`${BEN_API}/api/chat/stop`, { method: "POST" }); } catch {}
  if (activeReader) { try { await activeReader.cancel(); } catch {} }
});

btnNewChat.addEventListener("click", async () => {
  if (isStreaming) return;
  try { await fetch(`${BEN_API}/api/chat/clear`, { method: "POST" }); } catch {}
  messages.innerHTML = `<div class="welcome-msg">
    <p>Started a new conversation.</p>
    <p>Previous session has been archived to memory.</p>
  </div>`;
  setStatus("online", `BEN online · ${currentModel}`);
});

// ── Chat: input auto-resize + keyboard shortcut ───────────────────────────
chatInput.addEventListener("input", autoResize);
chatInput.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
btnSend.addEventListener("click", sendMessage);

function autoResize() {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 100) + "px";
}

// ── Tools: show result ────────────────────────────────────────────────────
function showToolResult(label, text) {
  toolResultLabel.textContent = label;
  toolResultBody.textContent  = text;
  toolResult.classList.remove("hidden");
  toolResult.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function hideToolResult() {
  toolResult.classList.add("hidden");
  toolResultBody.textContent = "";
}

btnCopyResult.addEventListener("click", () => {
  const text = toolResultBody.textContent;
  navigator.clipboard.writeText(text).then(() => {
    btnCopyResult.textContent = "✓ Copied!";
    setTimeout(() => (btnCopyResult.textContent = "⧉ Copy"), 1800);
  });
});

// ── Tools: extract page text via scripting API ────────────────────────────
async function getActiveTabInfo() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function extractPageText(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      // Remove script/style nodes then get innerText
      const clone = document.body.cloneNode(true);
      clone.querySelectorAll("script, style, noscript, iframe").forEach(n => n.remove());
      return (clone.innerText || clone.textContent || "").replace(/\s{3,}/g, "\n\n").trim();
    },
  });
  return results?.[0]?.result || "";
}

async function getSelectedText(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => window.getSelection()?.toString()?.trim() || "",
  });
  return results?.[0]?.result || "";
}

// ── Tools: Summarize ─────────────────────────────────────────────────────
btnSummarize.addEventListener("click", async () => {
  hideToolResult();
  btnSummarize.disabled = true;
  btnSummarize.textContent = "Extracting page…";

  try {
    const tab = await getActiveTabInfo();
    if (!tab?.id) throw new Error("No active tab found.");

    const pageText = await extractPageText(tab.id);
    if (!pageText) throw new Error("Could not extract page text. Try on a regular webpage.");

    // Truncate to backend limit
    const content = pageText.slice(0, 12000);
    const url = tab.url || "";

    btnSummarize.textContent = "Summarizing…";

    const res = await fetch(`${BEN_API}/api/extension/summarize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, content }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    showToolResult("📄 Summary", data.summary);

  } catch (err) {
    showToolResult("Error", `Summarize failed: ${err.message}`);
  } finally {
    btnSummarize.disabled = false;
    btnSummarize.textContent = "Summarize Current Page";
  }
});

// ── Tools: Explain ───────────────────────────────────────────────────────
btnExplain.addEventListener("click", async () => {
  hideToolResult();
  btnExplain.disabled = true;
  btnExplain.textContent = "Getting selection…";

  try {
    const tab = await getActiveTabInfo();
    if (!tab?.id) throw new Error("No active tab found.");

    // Check for pending action from context menu first
    const { pendingAction } = await chrome.storage.session.get("pendingAction");
    let text = "";
    let sourceUrl = tab.url || "";

    if (pendingAction?.type === "explain" && Date.now() - pendingAction.timestamp < 30000) {
      text = pendingAction.text;
      sourceUrl = pendingAction.sourceUrl || sourceUrl;
      await chrome.storage.session.remove("pendingAction");
      chrome.runtime.sendMessage({ type: "clearBadge" });
    } else {
      text = await getSelectedText(tab.id);
    }

    if (!text) throw new Error("No text selected. Highlight some text on the page first.");

    btnExplain.textContent = "Explaining…";

    const res = await fetch(`${BEN_API}/api/extension/explain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 4000), context_url: sourceUrl }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    showToolResult("🔍 Explanation", data.explanation);

  } catch (err) {
    showToolResult("Error", `Explain failed: ${err.message}`);
  } finally {
    btnExplain.disabled = false;
    btnExplain.textContent = "Explain Selected Text";
  }
});

// ── Tools: Save to memory ─────────────────────────────────────────────────
btnSave.addEventListener("click", async () => {
  hideToolResult();
  btnSave.disabled = true;
  btnSave.textContent = "Getting selection…";

  try {
    const tab = await getActiveTabInfo();
    if (!tab?.id) throw new Error("No active tab found.");

    const text = await getSelectedText(tab.id);
    if (!text) throw new Error("No text selected. Highlight text on the page first.");

    const category = categorySelect.value;
    btnSave.textContent = "Saving…";

    const res = await fetch(`${BEN_API}/api/extension/save-note`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: text.slice(0, 2000),
        category,
        source_url: tab.url || "",
      }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    showToolResult("🧠 Saved to Memory", `✓ Saved as "${category}"\n\n${data.content_preview}`);

  } catch (err) {
    showToolResult("Error", `Save failed: ${err.message}`);
  } finally {
    btnSave.disabled = false;
    btnSave.textContent = "Save Selection";
  }
});

// ── Init ──────────────────────────────────────────────────────────────────
(async () => {
  await checkStatus();

  // If context menu triggered an explain, switch to tools tab automatically
  const { pendingAction } = await chrome.storage.session.get("pendingAction");
  if (pendingAction?.type === "explain" && Date.now() - pendingAction.timestamp < 30000) {
    document.querySelector('[data-tab="tools"]').click();
    chrome.runtime.sendMessage({ type: "clearBadge" });
  }
})();
