// ══════════════════════════════════════════════════════════════════════
// srag — Chat + Docs JavaScript
// ══════════════════════════════════════════════════════════════════════
"use strict";

// ── Simple Markdown to HTML ─────────────────────────────────────────
function parseMarkdown(text) {
  if (!text) return "";
  let html = text;

  // Escape HTML entities
  html = html.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // Code blocks (``` ... ```)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code>${code.trim()}</code></pre>`);

  // Inline code (`...`)
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

  // Bold (**...**)
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // Blockquotes (> ...)
  html = html.replace(/^&gt;\s?(.*)$/gm, "<blockquote>$1</blockquote>");

  // Unordered list items (- ...)
  // Wrap consecutive <li> in <ul>
  html = html.replace(/^- (.*)$/gm, "<li>$1</li>");
  html = html.replace(/((?:<li>.*<\/li>\s*)+)/g, "<ul>$1</ul>");

  // Paragraphs: double newlines
  html = html.split(/\n\n+/).map(p => {
    p = p.trim();
    if (!p) return "";
    // Don't wrap block elements in <p>
    if (/^<(pre|ul|ol|blockquote|table)/.test(p)) return p;
    return `<p>${p.replace(/\n/g, "<br>")}</p>`;
  }).join("");

  return html;
}

// ── Parse assistant response for sources + follow-ups ───────────────
function parseAssistantResponse(text) {
  const result = { body: text, sources: null, followups: [] };

  // Extract sources block
  const sourcesMatch = text.match(/\n---\n\*\*Sources:\*\*\s*([\s\S]*?)(?=\n\n\*\*Follow-up questions:|$)/);
  if (sourcesMatch) {
    result.sources = sourcesMatch[1].trim();
    result.body = text.replace(sourcesMatch[0], "");
  }

  // Extract follow-up questions
  const fuMatch = text.match(/\*\*Follow-up questions:\*\*\s*([\s\S]*)$/);
  if (fuMatch) {
    result.followups = fuMatch[1].trim()
      .split("\n")
      .filter(l => l.startsWith("- "))
      .map(l => l.slice(2).trim());
    result.body = text.replace(fuMatch[0], "");
  }

  result.body = result.body.trim();
  return result;
}

// ══════════════════════════════════════════════════════════════════════
// Chat
// ══════════════════════════════════════════════════════════════════════
const chatMessages = document.getElementById("chat-messages");
const emptyState = document.getElementById("empty-state");
const questionInput = document.getElementById("question-input");
const sendBtn = document.getElementById("send-btn");

if (sendBtn) {
  sendBtn.addEventListener("click", () => sendQuestion());
  questionInput.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
  });

  // Suggestion chips
  document.querySelectorAll(".suggestion-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      questionInput.value = chip.dataset.q;
      sendQuestion();
    });
  });
}

function sendQuestion(q) {
  q = q || questionInput.value.trim();
  if (!q) return;
  questionInput.value = "";
  sendBtn.disabled = true;

  // Hide empty state
  if (emptyState) emptyState.remove();

  appendMsg("user", q);
  const assistantRow = appendMsg("assistant", "", true);
  showTyping(assistantRow);

  fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: q }),
  }).then(resp => {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", fullText = "";

    assistantRow.classList.remove("streaming");

    function read() {
      reader.read().then(({ done, value }) => {
        if (done) {
          assistantRow.classList.remove("streaming");
          renderAssistantFinal(assistantRow, fullText);
          sendBtn.disabled = false;
          questionInput.focus();
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6).trim();
          if (data === "[DONE]") {
            assistantRow.classList.remove("streaming");
            renderAssistantFinal(assistantRow, fullText);
            sendBtn.disabled = false;
            questionInput.focus();
            return;
          }
          try {
            const { token } = JSON.parse(data);
            fullText += token;
            renderStreaming(assistantRow, fullText);
          } catch {}
        }
        read();
      });
    }
    read();
  }).catch(() => {
    assistantRow.classList.remove("streaming");
    sendBtn.disabled = false;
  });
}

function appendMsg(role, text, streaming) {
  const row = document.createElement("div");
  row.className = "msg-row " + role + (streaming ? " streaming" : "");

  const content = document.createElement("div");
  content.className = "msg-content";

  if (role === "assistant" && text) {
    content.innerHTML = parseMarkdown(text);
  } else {
    content.textContent = text;
  }

  row.appendChild(content);
  chatMessages.appendChild(row);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return row;
}

function renderStreaming(row, fullText) {
  const content = row.querySelector(".msg-content");
  // During streaming, show raw markdown — parse happens on [DONE]
  const parsed = parseAssistantResponse(fullText);
  content.innerHTML = parseMarkdown(parsed.body);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function renderAssistantFinal(row, fullText) {
  const content = row.querySelector(".msg-content");
  const parsed = parseAssistantResponse(fullText);

  // Render body as markdown
  content.innerHTML = parseMarkdown(parsed.body);

  // Add sources
  if (parsed.sources) {
    const sourcesDiv = document.createElement("div");
    sourcesDiv.className = "sources";
    sourcesDiv.innerHTML = "<strong>Sources:</strong> " + parsed.sources;
    content.appendChild(sourcesDiv);
  }

  // Add follow-up chips
  if (parsed.followups.length) {
    const fuDiv = document.createElement("div");
    fuDiv.className = "followups";
    parsed.followups.forEach(q => {
      const chip = document.createElement("button");
      chip.className = "followup-chip";
      chip.textContent = q;
      chip.addEventListener("click", () => {
        questionInput.value = q;
        sendQuestion();
      });
      fuDiv.appendChild(chip);
    });
    content.appendChild(fuDiv);
  }

  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function showTyping(row) {
  const content = row.querySelector(".msg-content");
  content.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
}

// ══════════════════════════════════════════════════════════════════════
// Documents
// ══════════════════════════════════════════════════════════════════════
const docsTable = document.getElementById("docs-tbody");
const addInput = document.getElementById("add-input");
const addBtn = document.getElementById("add-btn");

if (docsTable) loadDocs();

if (addBtn) {
  addBtn.addEventListener("click", () => {
    const val = addInput.value.trim();
    if (!val) return;
    const body = val.startsWith("http") ? { url: val } : { path: val };
    fetch("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(r => {
      if (!r.ok) return r.json().then(err => { throw err; });
      addInput.value = "";
      loadDocs();
    }).catch(err => {
      alert(err.detail || "Ingest failed");
    });
  });
}

function loadDocs() {
  fetch("/api/documents").then(r => r.json()).then(docs => {
    docsTable.textContent = "";
    docs.forEach(doc => {
      const tr = document.createElement("tr");
      const cells = [
        doc.title || doc.source_path,
        doc.file_type,
        doc.chunk_count,
        doc.ingested_at ? doc.ingested_at.slice(0, 19) : "",
      ];
      cells.forEach(val => {
        const td = document.createElement("td");
        td.textContent = val;
        tr.appendChild(td);
      });
      const tdDel = document.createElement("td");
      const btn = document.createElement("button");
      btn.className = "del-btn";
      btn.textContent = "Delete";
      btn.addEventListener("click", () => {
        fetch(`/api/documents/${doc.id}`, { method: "DELETE" }).then(() => loadDocs());
      });
      tdDel.appendChild(btn);
      tr.appendChild(tdDel);
      docsTable.appendChild(tr);
    });
  });
}
