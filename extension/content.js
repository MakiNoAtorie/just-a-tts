// Runs in every page: keyboard shortcuts, plus on-page controls and highlighting for reads started here.
(() => {
  if (window.__localtts) return;
  window.__localtts = true;

  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      .bar { position: fixed; right: 20px; bottom: 20px; z-index: 2147483647; display: flex; align-items: center;
             gap: 4px; padding: 6px 8px 6px 14px; border-radius: 999px; background: #1f1f23; color: #fff;
             font: 13px/1 -apple-system, system-ui, sans-serif; box-shadow: 0 4px 18px rgba(0,0,0,.35); }
      .msg { margin-right: 8px; white-space: nowrap; max-width: 360px; overflow: hidden; text-overflow: ellipsis; }
      button { all: unset; cursor: pointer; width: 30px; height: 30px; border-radius: 50%; text-align: center;
               font-size: 15px; line-height: 30px; }
      button:hover { background: rgba(255,255,255,.16); }
      button:focus-visible { outline: 2px solid #8ab4f8; }
      .error button:not(#stop) { display: none; }
    </style>
    <div class="bar" role="group" aria-label="LocalTTS reading controls">
      <span class="msg" aria-live="polite"></span>
      <button id="back" title="Back one passage" aria-label="Back one passage">⏮</button>
      <button id="pause" title="Pause" aria-label="Pause">⏸</button>
      <button id="forward" title="Forward one passage" aria-label="Forward one passage">⏭</button>
      <button id="stop" title="Stop" aria-label="Stop">✕</button>
    </div>`;
  const bar = root.querySelector(".bar");
  const msg = root.querySelector(".msg");
  const pauseButton = root.querySelector("#pause");
  const highlightStyle = document.createElement("style");
  highlightStyle.textContent = "::highlight(localtts) { background: rgba(255, 214, 0, .45); }";

  let read = null; // id of the read this tab started
  let have = -1; // id of the read whose passages we hold
  let passages = [];
  let shown = -1;
  let index = null; // page text with whitespace removed, plus where each character lives
  let searchFrom = 0;
  let timer = null;

  const send = (cmd, arg) => chrome.runtime.sendMessage({ cmd, arg });

  function show(text, error = false) {
    msg.textContent = text;
    bar.classList.toggle("error", error);
    if (!host.isConnected) document.documentElement.append(host, highlightStyle);
  }

  function close() {
    clearInterval(timer);
    timer = null;
    read = null;
    CSS.highlights?.delete("localtts");
    host.remove();
    highlightStyle.remove();
  }

  // Passages come back with whitespace normalised, so match with all whitespace removed.
  function buildIndex() {
    const chars = [], nodes = [], offsets = [];
    const skip = /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA)$/;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node; (node = walker.nextNode()); ) {
      if (skip.test(node.parentElement?.tagName)) continue;
      const text = node.data;
      for (let i = 0; i < text.length; i++) {
        if (text.charCodeAt(i) > 32 && text[i] !== " ") {
          chars.push(text[i]);
          nodes.push(node);
          offsets.push(i);
        }
      }
    }
    return { text: chars.join(""), nodes, offsets };
  }

  function highlight(passage) {
    if (!CSS.highlights) return;
    CSS.highlights.delete("localtts");
    const key = passage.replace(/[\s ]+/g, "");
    if (!key) return;
    let at = index.text.indexOf(key, searchFrom);
    if (at < 0) at = index.text.indexOf(key);
    let length = key.length;
    if (at < 0) {
      // ponytail: the page text often differs a little from the extracted text (footnote markers,
      // headings run together), so anchor on the first or last 40 characters and highlight roughly
      // the right length
      const head = key.slice(0, 40), tail = key.slice(-40);
      at = index.text.indexOf(head, searchFrom);
      if (at < 0) at = index.text.indexOf(head);
      if (at < 0) {
        let last = index.text.indexOf(tail, searchFrom);
        if (last < 0) last = index.text.indexOf(tail);
        if (last < 0) return;
        at = Math.max(0, last + tail.length - length);
      }
      length = Math.min(length, index.text.length - at);
    }
    const end = at + length - 1;
    const range = new Range();
    range.setStart(index.nodes[at], index.offsets[at]);
    range.setEnd(index.nodes[end], index.offsets[end] + 1);
    CSS.highlights.set("localtts", new Highlight(range));
    searchFrom = at + length;
    const box = range.getBoundingClientRect();
    if (box.top < 0 || box.bottom > innerHeight) {
      index.nodes[at].parentElement?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  async function poll() {
    let state;
    try {
      state = await send("state", have);
    } catch {
      return close(); // the extension was reloaded
    }
    if (read === null) return;
    if (!state) return (show("LocalTTS stopped.", true), clearInterval(timer));
    if (state.read !== read || state.status === "idle") return close();
    if (state.passages) {
      passages = state.passages;
      have = state.read;
    }
    const paused = state.status === "paused";
    pauseButton.textContent = paused ? "▶" : "⏸";
    pauseButton.title = paused ? "Resume" : "Pause";
    pauseButton.setAttribute("aria-label", pauseButton.title);
    const where = state.total > 1 ? ` ${state.index + 1} of ${state.total}` : "";
    show(state.status === "loading" ? "Getting the voice ready…" : (paused ? "Paused" : "Reading") + where);
    if (state.index !== shown && passages[state.index]) {
      if (state.index < shown) searchFrom = 0; // went back
      shown = state.index;
      highlight(passages[shown]);
    }
  }

  function begin(id) {
    read = id;
    shown = -1;
    index = buildIndex();
    // Start matching at the selection, so repeated text earlier on the page is not highlighted instead.
    searchFrom = 0;
    const selection = getSelection();
    if (selection.rangeCount && !selection.isCollapsed) {
      const range = selection.getRangeAt(0);
      const first = index.nodes.indexOf(range.startContainer);
      if (first >= 0) searchFrom = first;
    }
    clearInterval(timer);
    timer = setInterval(poll, 400);
    poll();
  }

  root.querySelector("#back").onclick = () => send("skip", "-1");
  root.querySelector("#forward").onclick = () => send("skip", "1");
  pauseButton.onclick = () => send("pause").then(poll);
  root.querySelector("#stop").onclick = () => {
    if (read !== null) send("stop");
    close();
  };

  // Reads the selection if there is one (unless wholePage), otherwise the page. False if this page cannot be read here.
  function readHere(wholePage = false) {
    if (document.contentType === "application/pdf") return false; // the background script handles PDFs
    const selection = wholePage ? "" : getSelection().toString().trim();
    show("Starting…");
    // The rendered HTML rather than the address, so pages behind a login work.
    send("read", selection ? ["/speak", selection] : ["/html", document.documentElement.outerHTML]).then(
      (reply) => {
        if (reply) begin(reply.read);
        else show("LocalTTS isn’t running. Open it from Applications, then try again.", true);
      },
      close
    );
    return true;
  }

  chrome.runtime.onMessage.addListener((message, _sender, reply) => {
    if (message.trigger) reply(readHere());
  });

  // The same keys as the macOS Services shortcuts, handled here so they work reliably in Chrome.
  // ponytail: top frame only; keys pressed while focus is inside an embedded frame are not seen.
  window.addEventListener(
    "keydown",
    (event) => {
      if (!event.ctrlKey || !event.altKey || event.metaKey) return;
      if (event.code === "KeyR") readHere(event.shiftKey);
      else if (event.shiftKey) return;
      else if (event.code === "KeyP") send("pause").then(poll);
      else if (event.code === "Period") send("stop");
      else return;
      event.preventDefault();
      event.stopImmediatePropagation();
    },
    true
  );
})();
