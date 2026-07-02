(function () {
  const HASH_PREFIX = "#bidrastatus-search-";
  const QUERY_KEY = "gemBidNumber";
  const MAX_ATTEMPTS = 40;
  const RETRY_MS = 750;

  function getBidNumber() {
    if (window.location.hash && window.location.hash.startsWith(HASH_PREFIX)) {
      return decodeURIComponent(window.location.hash.slice(HASH_PREFIX.length)).trim();
    }
    const params = new URLSearchParams(window.location.search);
    return (params.get(QUERY_KEY) || "").trim();
  }

  function setBidStatusFilter() {
    const checkbox = document.getElementById("bidrastatus");
    if (!checkbox) return false;
    if (!checkbox.checked) checkbox.click();
    return checkbox.checked;
  }

  function setExactSearch() {
    const current = document.getElementById("search_concept");
    if (current && /exact search/i.test(current.textContent || "")) return true;
    const menuItems = Array.from(document.querySelectorAll(".dropdown-menu a"));
    const exactLink = menuItems.find((link) => /exact search/i.test((link.textContent || "").trim()));
    if (!exactLink) return false;
    exactLink.click();
    return true;
  }

  function fillBidNumber(bidNumber) {
    const input = document.getElementById("searchBid");
    if (!input) return false;
    input.focus();
    input.value = bidNumber;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return input.value === bidNumber;
  }

  function clickSearch() {
    const button = document.getElementById("searchBidRA");
    if (!button) return false;
    button.click();
    return true;
  }

  function getCsrfToken(fallbackToken = "") {
    const match = document.cookie.match(/(?:^|;\s*)csrf_gem_cookie=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : String(fallbackToken || "");
  }

  function buildGemPayload(bidNumber) {
    return {
      param: { searchBid: bidNumber, searchType: "fullText" },
      filter: {
        bidStatusType: "bidrastatus",
        byType: "all",
        highBidValue: "",
        byEndDate: { from: "", to: "" },
        sort: "Bid-End-Date-Latest",
      },
    };
  }

  function normalizeGemNoDataResponse(parsed) {
    const message = String(parsed?.message || "").trim().toLowerCase();
    const code = Number(parsed?.code);
    if (code === 404 && message === "no data found") {
      return {
        response: {
          response: {
            docs: [],
            numFound: 0,
          },
        },
        gem_meta: parsed,
      };
    }
    return null;
  }

  async function fetchBidRaStatusData(bidNumber, csrfTokenFromBackground = "", requestUrlFromBackground = "", requestUrlSource = "") {
    const finalBidNumber = String(bidNumber || "").trim().toUpperCase();
    if (!finalBidNumber) throw new Error("Valid GeM Bid Number not found. Please check extraction.");

    const csrfToken = getCsrfToken(csrfTokenFromBackground);
    if (!csrfToken) throw new Error("GeM CSRF cookie was not found. Reload the GeM all-bids tab, then retry.");

    const payload = buildGemPayload(finalBidNumber);
    const requestUrl = String(requestUrlFromBackground || "").trim();
    const requestMethod = "POST";
    if (!requestUrl) {
      const error = new Error("GeM endpoint URL is missing. Check all-bids-data Request URL.");
      error.gemDebug = {
        requestUrl: "",
        requestUrlSource: requestUrlSource || "not-found",
        requestMethod,
        requestPayload: {
          payload,
          csrf_bd_gem_nk: csrfToken,
        },
        requestBody: "",
        csrfToken,
        csrfFound: Boolean(csrfToken),
        pageUrl: window.location.href,
      };
      throw error;
    }
    const body = `payload=${encodeURIComponent(JSON.stringify(payload))}&csrf_bd_gem_nk=${encodeURIComponent(csrfToken)}`;
    const debugBase = {
      requestUrl,
      requestUrlSource: requestUrlSource || (requestUrlFromBackground ? "background" : "not-found"),
      requestMethod,
      requestPayload: {
        payload,
        csrf_bd_gem_nk: csrfToken,
      },
      requestBody: body,
      csrfToken,
      csrfFound: Boolean(csrfToken),
      pageUrl: window.location.href,
    };
    const response = await fetch(requestUrl, {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
      },
      body,
    });
    const text = await response.text();
    const debug = {
      ...debugBase,
      responseStatus: response.status,
      responseTextSnippet: text.slice(0, 500),
    };

    if (!response.ok) {
      let parsed = null;
      try { parsed = JSON.parse(text); } catch (_) {}
      const noDataResponse = normalizeGemNoDataResponse(parsed);
      if (noDataResponse) {
        return {
          data: noDataResponse,
          debug: {
            ...debug,
            responseInterpretation: "NO_DATA_FOUND",
          },
        };
      }
      const error = new Error(response.status === 404
        ? "GeM endpoint URL is wrong. Check all-bids-data Request URL."
        : `GeM page-context request failed with HTTP ${response.status}.`);
      error.gemDebug = debug;
      throw error;
    }

    try {
      return { data: JSON.parse(text), debug };
    } catch (_) {
      const error = new Error("GeM returned invalid JSON.");
      error.gemDebug = debug;
      throw error;
    }
  }

  function cleanUrl() {
    if (!window.history || typeof window.history.replaceState !== "function") return;
    const cleanUrl = window.location.pathname + window.location.search;
    window.history.replaceState(null, "", cleanUrl);
  }

  function runAutofill(bidNumber) {
    let attempts = 0;
    function tryApply() {
      attempts += 1;
      const filterReady = setBidStatusFilter();
      const bidReady = fillBidNumber(bidNumber);
      const searchReady = clickSearch();
      setExactSearch();
      if (filterReady && bidReady && searchReady) {
        cleanUrl();
        return;
      }
      if (attempts < MAX_ATTEMPTS) {
        window.setTimeout(tryApply, RETRY_MS);
      }
    }
    tryApply();
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message && message.type === "GEM_PAGE_PING") {
      sendResponse({ ok: true });
      return false;
    }
    if (!message || message.type !== "GEM_PAGE_FETCH_RESULT") return false;
    fetchBidRaStatusData(message.bidNumber, message.csrfToken, message.requestUrl, message.requestUrlSource)
      .then((result) => sendResponse({ ok: true, data: result.data, debug: result.debug }))
      .catch((error) => sendResponse({
        ok: false,
        error: error?.message || "GeM page fetch failed.",
        debug: error?.gemDebug || null,
      }));
    return true;
  });

  const bidNumber = getBidNumber();
  if (!bidNumber) return;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => runAutofill(bidNumber), { once: true });
  } else {
    runAutofill(bidNumber);
  }
})();
