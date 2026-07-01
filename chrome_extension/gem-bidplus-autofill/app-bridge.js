(function () {
  const root = document.documentElement;
  if (!root) return;
  root.dataset.gemBidplusExtension = "installed";
  // Signals that this build can fetch GeM results on the user's IP (v1.1.0+).
  root.dataset.gemBidplusResultFetch = "1";
  document.dispatchEvent(
    new CustomEvent("gem-bidplus-extension-ready", { detail: { resultFetch: true } })
  );

  // Bridge: the app page asks the extension to fetch a GeM result, the service
  // worker performs the request from the user's browser, and we post the raw
  // response back to the page.
  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "tender-app" || data.type !== "GEM_FETCH_RESULT") return;

    const requestId = data.requestId;
    const bidNumber = data.bidNumber;

    function reply(payload) {
      window.postMessage(
        Object.assign(
          { source: "gem-bidplus-extension", type: "GEM_FETCH_RESULT_RESULT", requestId },
          payload
        ),
        "*"
      );
    }

    try {
      chrome.runtime.sendMessage({ type: "GEM_FETCH_RESULT", bidNumber }, (response) => {
        const err = chrome.runtime.lastError;
        if (err) {
          reply({ ok: false, error: err.message || "Extension messaging error" });
          return;
        }
        reply(response || { ok: false, error: "No response from extension" });
      });
    } catch (e) {
      reply({ ok: false, error: String((e && e.message) || e) });
    }
  });
})();
