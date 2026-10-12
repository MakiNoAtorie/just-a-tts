const DAEMON = "http://127.0.0.1:8765";
const post = (path, body = "") => fetch(DAEMON + path, { method: "POST", body });

chrome.runtime.onInstalled.addListener(() => {
  // One item per context, so each sits at the top level of the right-click menu.
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "selection", title: "Read selection aloud", contexts: ["selection"] });
    chrome.contextMenus.create({ id: "page", title: "Read page aloud", contexts: ["page"] });
  });
});

// Asks the page's own script to start a read (it shows the controls). Falls back for pages it cannot run on.
async function trigger(tab, info = {}) {
  const ask = () => chrome.tabs.sendMessage(tab.id, { trigger: true });
  try {
    if (await ask()) return;
  } catch {
    // No page script yet (the tab was open before the extension was loaded): add it and ask again.
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
      if (await ask()) return;
    } catch {}
  }
  // PDFs and pages Chrome will not let extensions touch: no on-page controls, LocalTTS shows its own text window.
  try {
    if (info.selectionText) return await post("/speak", info.selectionText);
    try {
      // Fetched here because Chrome has the user's login for the site and LocalTTS does not.
      const file = await (await fetch(tab.url)).blob();
      if (file.type === "application/pdf") return await post("/pdf", file);
    } catch {}
    await post("/page", tab.url);
  } catch {}
}

chrome.contextMenus.onClicked.addListener((info, tab) => trigger(tab, info));
chrome.action.onClicked.addListener((tab) => trigger(tab));

// The page script starts reads, asks for state, and sends pause / skip / stop through here.
chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  const work =
    message.cmd === "state"
      ? fetch(`${DAEMON}/state?have=${message.arg}`).then((r) => r.json())
      : message.cmd === "read"
        ? post(message.arg[0] + "?ui=browser", message.arg[1]).then((r) => r.json())
        : post("/" + message.cmd, message.arg ?? "").then(() => true);
  work.then(reply, () => reply(null));
  return true;
});
