(function () {
  const EXTENSION_VERSION = "1.1.7";
  const root = document.documentElement;
  if (!root) return;
  root.dataset.gemBidplusExtension = "installed";
  root.dataset.gemBidplusVersion = EXTENSION_VERSION;
  root.dataset.gemBidplusResultFetch = "1";
  root.dataset.gemBidplusBrowserWatcher = "1";

  function postToPage(payload) {
    window.postMessage({ source: "gem-bidplus-extension", extensionVersion: EXTENSION_VERSION, ...payload }, "*");
  }

  function sendRuntimeMessage(message, responseType) {
    chrome.runtime.sendMessage(message, (response) => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) {
        postToPage({
          type: responseType,
          requestId: message.requestId,
          ok: false,
          error: runtimeError.message || "Extension background did not respond.",
        });
        return;
      }
      postToPage({
        type: responseType,
        requestId: message.requestId,
        ...(response || { ok: false, error: "No response from extension background." }),
      });
    });
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || message.source !== "gem-bidplus-extension") return;
    if (message.type === "GEM_BATCH_PROGRESS") {
      postToPage(message);
    }
  });

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "tender-app" || !data.type) return;

    if (data.type === "GEM_FETCH_RESULT") {
      sendRuntimeMessage(
        {
          source: "tender-app",
          type: "GEM_FETCH_RESULT",
          requestId: data.requestId,
          bidNumber: data.bidNumber,
          appOrigin: window.location.origin,
        },
        "GEM_FETCH_RESULT_RESULT",
      );
      return;
    }

    if (data.type === "RUN_BROWSER_RESULT_WATCHER") {
      sendRuntimeMessage(
        {
          source: "tender-app",
          type: "RUN_BROWSER_RESULT_WATCHER",
          requestId: data.requestId,
          appOrigin: window.location.origin,
        },
        "RUN_BROWSER_RESULT_WATCHER_RESULT",
      );
    }
  });

  try {
    chrome.runtime.sendMessage({
      source: "tender-app",
      type: "REGISTER_TENDER_APP_CONTEXT",
      appOrigin: window.location.origin,
    });
  } catch (_) {}

  document.dispatchEvent(new CustomEvent("gem-bidplus-extension-ready", {
    detail: { resultFetch: true, browserWatcher: true, version: EXTENSION_VERSION }
  }));
})();
