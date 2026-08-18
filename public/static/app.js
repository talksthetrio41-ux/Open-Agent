(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    transcript: $("transcript"),
    prompt: $("prompt"),
    composer: $("composer"),
    send: $("btn-send"),
    status: $("status-line"),
    chipQwen: $("chip-qwen"),
    chipGh: $("chip-github"),
    chipStep: $("chip-step"),
    modalPin: $("modal-pin"),
    modalQwen: $("modal-qwen"),
    modalSettings: $("modal-settings"),
    pinInput: $("pin-input"),
    pinError: $("pin-error"),
    qwenUser: $("qwen-user"),
    qwenPass: $("qwen-pass"),
    qwenError: $("qwen-error"),
    ghToken: $("gh-token"),
    ghRepo: $("gh-repo"),
    ghError: $("gh-error"),
    settingsQwen: $("settings-qwen"),
    settingsGh: $("settings-gh"),
  };

  const state = {
    unlocked: false,
    busy: false,
    streamingEl: null,
    connected: false,
  };

  function show(modal, on) {
    modal.classList.toggle("hidden", !on);
  }

  function setStatus(text) {
    els.status.textContent = text || "Idle";
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function addMsg(role, text, extraClass) {
    hideEmpty();
    const div = document.createElement("article");
    div.className = `msg ${role}${extraClass ? " " + extraClass : ""}`;
    const who =
      role === "user" ? "You" :
      role === "assistant" ? "Qwen" :
      role === "tool" ? "Tool" : "System";
    div.innerHTML = `<span class="who">${who}</span>${esc(text)}`;
    els.transcript.appendChild(div);
    els.transcript.scrollTop = els.transcript.scrollHeight;
    return div;
  }

  function startStream(role) {
    hideEmpty();
    const div = document.createElement("article");
    div.className = `msg ${role}`;
    div.innerHTML = `<span class="who">${role === "assistant" ? "Qwen" : role}</span>`;
    const body = document.createElement("span");
    body.className = "stream-body";
    div.appendChild(body);
    els.transcript.appendChild(div);
    els.transcript.scrollTop = els.transcript.scrollHeight;
    state.streamingEl = body;
    return body;
  }

  function appendStream(text) {
    if (!state.streamingEl) startStream("assistant");
    state.streamingEl.textContent += text;
    els.transcript.scrollTop = els.transcript.scrollHeight;
  }

  function endStream() {
    state.streamingEl = null;
  }

  function hideEmpty() {
    const empty = els.transcript.querySelector(".empty");
    if (empty) empty.remove();
  }

  function showEmpty() {
    els.transcript.innerHTML = `
      <div class="empty">
        <h2>Ready when you are</h2>
        <p>Connect Qwen, optionally link a GitHub repo, then describe a coding task.
        The phone runs commands; files go to your repo.</p>
      </div>`;
  }

  function applyState(s) {
    if (!s) return;
    state.busy = !!s.busy;
    els.send.disabled = state.busy;
    setStatus(s.status_detail || s.status || "Idle");
    els.chipQwen.classList.toggle("on", !!(s.qwen_connected || s.has_qwen_creds));
    els.chipQwen.title = s.qwen_username || "";
    els.chipGh.classList.toggle("on", !!s.github_linked);
    els.chipGh.classList.toggle("warn", !s.github_linked);
    els.chipGh.title = s.github_repo || "not linked";
    els.chipStep.textContent = `step ${s.step || 0}/${s.max_steps || 20}`;
    if (els.settingsQwen) {
      els.settingsQwen.textContent = (s.qwen_connected || s.has_qwen_creds)
        ? `Connected as ${s.qwen_username || "saved account"}`
        : "Not connected";
    }
    if (els.settingsGh) {
      els.settingsGh.textContent = s.github_repo
        ? `${s.github_repo}  (${s.github_linked ? "cloned" : "not cloned yet"})`
        : "No repo linked";
    }
    if (s.github_repo && !els.ghRepo.value) els.ghRepo.value = s.github_repo;
    state.connected = !!(s.qwen_connected || s.has_qwen_creds);
  }

  function renderHistory(messages) {
    els.transcript.innerHTML = "";
    if (!messages || !messages.length) {
      showEmpty();
      return;
    }
    for (const m of messages) {
      const role = m.kind === "tool" ? "tool" : m.role;
      addMsg(role, m.content, m.kind === "compact" ? "compact" : "");
    }
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && data.need_pin) {
      state.unlocked = false;
      show(els.modalPin, true);
      throw new Error("locked");
    }
    if (!res.ok) {
      throw new Error(data.detail || data.error || res.statusText);
    }
    return data;
  }

  async function readSSE(path, body, onEvent) {
    const res = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (res.status === 401) {
      show(els.modalPin, true);
      throw new Error("locked");
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || res.statusText);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const raw = line.slice(6);
        let ev;
        try { ev = JSON.parse(raw); } catch { continue; }
        onEvent(ev);
      }
    }
  }

  async function refresh() {
    const data = await api("/api/state");
    state.unlocked = true;
    applyState(data);
    if (!els.transcript.querySelector(".msg")) {
      renderHistory(data.messages || []);
    }
    if (!data.has_qwen_creds && !data.qwen_connected) {
      show(els.modalQwen, true);
    }
    return data;
  }

  async function unlock() {
    els.pinError.textContent = "";
    try {
      await api("/api/unlock", {
        method: "POST",
        body: JSON.stringify({ pin: els.pinInput.value.trim() }),
      });
      show(els.modalPin, false);
      await refresh();
    } catch (err) {
      els.pinError.textContent = err.message === "locked" ? "Wrong PIN" : err.message;
    }
  }

  async function connectQwen() {
    els.qwenError.textContent = "";
    $("qwen-submit").disabled = true;
    setStatus("Signing in to Qwen…");
    try {
      const data = await api("/api/qwen/login", {
        method: "POST",
        body: JSON.stringify({
          username: els.qwenUser.value.trim(),
          password: els.qwenPass.value,
        }),
      });
      if (!data.ok) {
        els.qwenError.textContent = "Login did not finish. Check credentials or captcha.";
        return;
      }
      show(els.modalQwen, false);
      applyState(data.state);
      addMsg("system", "Connected to Qwen. Session is saved on this device.");
    } catch (err) {
      els.qwenError.textContent = err.message;
    } finally {
      $("qwen-submit").disabled = false;
    }
  }

  async function linkGithub() {
    els.ghError.textContent = "";
    try {
      const data = await api("/api/github", {
        method: "POST",
        body: JSON.stringify({
          token: els.ghToken.value.trim(),
          repo: els.ghRepo.value.trim(),
        }),
      });
      applyState(data.state);
      addMsg("system", data.message || "GitHub workspace linked.");
      show(els.modalSettings, false);
    } catch (err) {
      els.ghError.textContent = err.message;
    }
  }

  function handleEvent(ev) {
    if (!ev || !ev.type) return;
    if (ev.type === "status" && ev.state) applyState(ev.state);
    else if (ev.type === "user") { /* already painted locally */ }
    else if (ev.type === "delta") {
      if (!state.streamingEl) startStream("assistant");
      appendStream(ev.text || "");
    } else if (ev.type === "assistant_done") {
      endStream();
    } else if (ev.type === "tool") {
      endStream();
      const head = `${ev.kind}  ${ev.command || ""}\n`;
      addMsg("tool", head + (ev.output || ""), ev.ok ? "" : "error");
    } else if (ev.type === "error") {
      endStream();
      addMsg("system", ev.text || "Error", "error");
    } else if (ev.type === "compacted") {
      endStream();
    } else if (ev.type === "done" || ev.type === "end") {
      endStream();
    }
  }

  async function sendMessage(text) {
    const value = (text ?? els.prompt.value).trim();
    if (!value || state.busy) return;

    if (value === "/clear" || value === "clear") {
      els.prompt.value = "";
      return clearChat();
    }
    if (value === "/compact" || value === "compact") {
      els.prompt.value = "";
      return compactChat();
    }
    if (value === "/stop" || value === "stop") {
      els.prompt.value = "";
      await api("/api/stop", { method: "POST", body: "{}" });
      return;
    }

    if (!state.connected) {
      show(els.modalQwen, true);
      return;
    }

    els.prompt.value = "";
    autosize();
    addMsg("user", value);
    state.busy = true;
    els.send.disabled = true;
    try {
      await readSSE("/api/chat", { message: value }, handleEvent);
    } catch (err) {
      addMsg("system", err.message, "error");
    } finally {
      endStream();
      state.busy = false;
      els.send.disabled = false;
      try { await refresh(); } catch (_) { /* ignore */ }
    }
  }

  async function clearChat() {
    try {
      const data = await api("/api/clear", { method: "POST", body: "{}" });
      applyState(data.state);
      renderHistory(data.messages || []);
    } catch (err) {
      addMsg("system", err.message, "error");
    }
  }

  async function compactChat() {
    addMsg("system", "Compacting history and starting a new Qwen chat…");
    state.busy = true;
    els.send.disabled = true;
    try {
      await readSSE("/api/compact", {}, handleEvent);
      await refresh();
      renderHistory((await api("/api/state")).messages || []);
    } catch (err) {
      addMsg("system", err.message, "error");
    } finally {
      endStream();
      state.busy = false;
      els.send.disabled = false;
    }
  }

  function autosize() {
    els.prompt.style.height = "auto";
    els.prompt.style.height = Math.min(els.prompt.scrollHeight, 160) + "px";
  }

  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage();
  });
  els.prompt.addEventListener("input", autosize);
  els.prompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  $("pin-submit").addEventListener("click", unlock);
  els.pinInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") unlock();
  });
  $("qwen-submit").addEventListener("click", connectQwen);
  $("gh-submit").addEventListener("click", linkGithub);
  $("btn-settings").addEventListener("click", () => show(els.modalSettings, true));
  $("settings-close").addEventListener("click", () => show(els.modalSettings, false));
  $("settings-relink-qwen").addEventListener("click", () => {
    show(els.modalSettings, false);
    show(els.modalQwen, true);
  });
  $("btn-clear").addEventListener("click", clearChat);
  $("btn-compact").addEventListener("click", compactChat);

  showEmpty();
  setStatus("Connecting…");

  refresh().catch(() => {
    show(els.modalPin, true);
    els.pinInput.focus();
  });
})();
