/**
 * BEN Chrome Extension — Service Worker (background.js)
 * Manifest V3 — uses fetch() only (no XMLHttpRequest).
 *
 * Responsibilities:
 *  - Register context menu items on install
 *  - Handle context menu clicks: explain selection, save to memory
 *  - Store pending actions in chrome.storage.session so popup can pick them up
 */

const BEN_API = "http://127.0.0.1:8000";

// ---------------------------------------------------------------------------
// Install: create context menu items
// ---------------------------------------------------------------------------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "ben-explain",
    title: "Ask BEN to Explain",
    contexts: ["selection"],
  });

  chrome.contextMenus.create({
    id: "ben-save",
    title: "Save to BEN Memory",
    contexts: ["selection"],
  });

  console.log("[BEN] Context menus registered.");
});

// ---------------------------------------------------------------------------
// Context menu click handler
// ---------------------------------------------------------------------------
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const selectedText = (info.selectionText || "").trim();
  if (!selectedText) return;

  const sourceUrl = tab?.url || "";

  if (info.menuItemId === "ben-explain") {
    // Store intent in session storage — popup reads it on open
    await chrome.storage.session.set({
      pendingAction: {
        type: "explain",
        text: selectedText,
        sourceUrl,
        timestamp: Date.now(),
      },
    });

    // Open the popup (works when popup is not already open)
    // Note: chrome.action.openPopup() requires user gesture in MV3;
    // we fall back to showing a notification-style badge instead.
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#1a7fe8" });

  } else if (info.menuItemId === "ben-save") {
    // Directly POST to save-note without requiring popup
    try {
      const resp = await fetch(`${BEN_API}/api/extension/save-note`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: selectedText.slice(0, 2000),
          category: "note",
          source_url: sourceUrl,
        }),
      });

      if (resp.ok) {
        // Flash green badge as visual feedback
        chrome.action.setBadgeText({ text: "✓" });
        chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
        setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2500);
      } else {
        chrome.action.setBadgeText({ text: "✗" });
        chrome.action.setBadgeBackgroundColor({ color: "#ef4444" });
        setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2500);
      }
    } catch (err) {
      console.error("[BEN] Save-note failed:", err);
      chrome.action.setBadgeText({ text: "✗" });
      chrome.action.setBadgeBackgroundColor({ color: "#ef4444" });
      setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2500);
    }
  }
});

// ---------------------------------------------------------------------------
// Message listener — popup can send messages here for background tasks
// ---------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "clearBadge") {
    chrome.action.setBadgeText({ text: "" });
    sendResponse({ ok: true });
  }
  // Return false = synchronous (no async response needed here)
  return false;
});
