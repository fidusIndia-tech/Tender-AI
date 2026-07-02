const GEM_ALL_BIDS_URL = "https://bidplus.gem.gov.in/all-bids";
const APP_ORIGIN_KEY = "tenderAiAppOrigin";
const GEM_DATA_URL_KEY = "gemAllBidsDataRequestUrl";
const WATCHER_ALARM_NAME = "gem-result-watcher";
const GEM_SESSION_MISSING_MESSAGE = "Open GeM once in this browser, then retry.";

function randomDelayMs() {
  return 5000 + Math.floor(Math.random() * 5001);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizeBidNumber(value) {
  return String(value || "").trim().toUpperCase();
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

function buildGemRequestBody(payload, csrfToken) {
  return `payload=${encodeURIComponent(JSON.stringify(payload))}&csrf_bd_gem_nk=${encodeURIComponent(csrfToken || "")}`;
}

function makeGemRequestError(message, debug = {}) {
  const error = new Error(message);
  error.gemDebug = debug;
  return error;
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

async function ensureAlarmRegistered() {
  await chrome.alarms.clear(WATCHER_ALARM_NAME);
  chrome.alarms.create(WATCHER_ALARM_NAME, { periodInMinutes: 360 });
}

async function storeAppOrigin(appOrigin) {
  if (!appOrigin) return;
  await chrome.storage.local.set({ [APP_ORIGIN_KEY]: appOrigin });
}

async function getStoredAppOrigin() {
  const stored = await chrome.storage.local.get(APP_ORIGIN_KEY);
  return stored[APP_ORIGIN_KEY] || "";
}

async function getGemCookie(name) {
  return chrome.cookies.get({ url: GEM_ALL_BIDS_URL, name });
}

async function waitForTabLoaded(tabId, timeoutMs = 45000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const tab = await chrome.tabs.get(tabId);
    if (tab && tab.status === "complete") return tab;
    await sleep(500);
  }
  throw new Error("GeM page did not finish loading. Open GeM once in this browser, then retry.");
}

async function getOrCreateGemTab() {
  const existing = await chrome.tabs.query({ url: "https://bidplus.gem.gov.in/all-bids*" });
  if (existing && existing.length) {
    const tab = existing[0];
    await chrome.tabs.update(tab.id, { active: false });
    return waitForTabLoaded(tab.id);
  }
  const tab = await chrome.tabs.create({ url: GEM_ALL_BIDS_URL, active: false });
  return waitForTabLoaded(tab.id);
}

async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "GEM_PAGE_PING" });
  } catch (_) {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  }
}

async function getStoredGemDataRequestUrl() {
  const stored = await chrome.storage.local.get(GEM_DATA_URL_KEY);
  return String(stored[GEM_DATA_URL_KEY] || "").trim();
}

async function discoverGemDataRequestUrl(tabId) {
  const configuredUrl = await getStoredGemDataRequestUrl();
  if (configuredUrl) {
    return { requestUrl: configuredUrl, source: "chrome.storage.local" };
  }

  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: () => {
      const entries = performance.getEntriesByType("resource")
        .map((entry) => entry.name || "")
        .filter((name) => /all-bids-data/i.test(name));
      const latest = entries.length ? entries[entries.length - 1] : "";
      return {
        requestUrl: latest,
        source: latest ? "performance.resource" : "not-found",
        pageUrl: window.location.href,
      };
    },
  });

  return {
    requestUrl: result?.requestUrl || "",
    source: result?.source || "not-found",
    pageUrl: result?.pageUrl || "",
  };
}

async function fetchGemResultDataInMainWorld(tabId, bidNumber, csrfToken, requestUrlInfo) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [bidNumber, csrfToken, requestUrlInfo],
    func: async (rawBidNumber, rawCsrfToken, rawRequestUrlInfo) => {
      const finalBidNumber = String(rawBidNumber || "").trim().toUpperCase();
      const requestUrlInfo = rawRequestUrlInfo || {};
      const requestUrl = String(requestUrlInfo.requestUrl || "").trim();
      const csrfFromCookie = (() => {
        const match = document.cookie.match(/(?:^|;\s*)csrf_gem_cookie=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
      })();
      const csrfToken = csrfFromCookie || String(rawCsrfToken || "");
      if (!finalBidNumber) {
        return { ok: false, error: "Valid GeM Bid Number not found. Please check extraction." };
      }
      if (!csrfToken) {
        return { ok: false, error: "GeM CSRF cookie was not found. Reload the GeM all-bids tab, then retry." };
      }
      if (!requestUrl) {
        return { ok: false, error: "GeM endpoint URL is missing. Check all-bids-data Request URL." };
      }

      const payload = {
        param: { searchBid: finalBidNumber, searchType: "fullText" },
        filter: {
          bidStatusType: "bidrastatus",
          byType: "all",
          highBidValue: "",
          byEndDate: { from: "", to: "" },
          sort: "Bid-End-Date-Latest",
        },
      };
      const body = `payload=${encodeURIComponent(JSON.stringify(payload))}&csrf_bd_gem_nk=${encodeURIComponent(csrfToken)}`;
      const requestMethod = "POST";
      const debugBase = {
        requestUrl,
        requestUrlSource: requestUrlInfo.source || "unknown",
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

      try {
        const response = await fetch(requestUrl, {
          method: requestMethod,
          credentials: "same-origin",
          cache: "no-store",
          headers: {
            Accept: "application/json, text/javascript, */*; q=0.01",
            "Cache-Control": "no-cache",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            Pragma: "no-cache",
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
            debug.responseInterpretation = "NO_DATA_FOUND";
            return { ok: true, data: noDataResponse, debug };
          }
          return {
            ok: false,
            error: response.status === 404
              ? "GeM endpoint URL is wrong. Check all-bids-data Request URL."
              : `GeM main-page request failed with HTTP ${response.status}.`,
            debug,
          };
        }
        try {
          return { ok: true, data: JSON.parse(text), debug };
        } catch (_) {
          return {
            ok: false,
            error: "GeM returned invalid JSON.",
            debug,
          };
        }
      } catch (error) {
        return {
          ok: false,
          error: error?.message || "GeM main-page request failed.",
          debug: debugBase,
        };
      }
    },
  });

  if (!result || !result.ok) {
    throw makeGemRequestError(result?.error || "GeM main-page request failed.", result?.debug || {});
  }
  return { data: result.data, debug: result.debug || {} };
}

async function fetchGemResultDataViaPage(bidNumber) {
  const tab = await getOrCreateGemTab();
  await ensureContentScript(tab.id);
  const csrfCookie = await getGemCookie("csrf_gem_cookie");
  const requestUrlInfo = await discoverGemDataRequestUrl(tab.id);
  try {
    const result = await fetchGemResultDataInMainWorld(tab.id, bidNumber, csrfCookie?.value || "", requestUrlInfo);
    return result.data;
  } catch (mainWorldError) {
    const message = mainWorldError?.message || "";
    if (/endpoint URL is wrong|HTTP 404/i.test(message)) {
      throw mainWorldError;
    }
    if (!/HTTP 403|CSRF cookie|invalid JSON/i.test(message)) {
      throw mainWorldError;
    }
  }
  const response = await chrome.tabs.sendMessage(tab.id, {
    type: "GEM_PAGE_FETCH_RESULT",
    bidNumber,
    csrfToken: csrfCookie?.value || "",
    requestUrl: requestUrlInfo.requestUrl,
    requestUrlSource: requestUrlInfo.source,
  });
  if (!response || !response.ok) {
    throw makeGemRequestError(response?.error || "GeM page-context fetch failed.", response?.debug || {});
  }
  return response.data;
}

async function fetchGemResultData(bidNumber) {
  const finalBidNumber = normalizeBidNumber(bidNumber);
  if (!finalBidNumber) {
    throw new Error("Valid GeM Bid Number not found. Please check extraction.");
  }

  try {
    return await fetchGemResultDataViaPage(finalBidNumber);
  } catch (pageError) {
    throw pageError;
  }
}

async function fetchPendingTenders(appOrigin) {
  const response = await fetch(`${appOrigin}/api/result-watcher/pending`, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new Error("Please log in to Tender AI again, then retry.");
    }
    throw new Error(`Failed to load pending tenders (HTTP ${response.status}).`);
  }
  return response.json();
}

async function ingestTenderResult(appOrigin, tenderId, rawResponse) {
  const response = await fetch(`${appOrigin}/api/tenders/${tenderId}/ingest-gem-result`, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ raw_response: rawResponse }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Failed to ingest GeM result (HTTP ${response.status}).`);
  }
  return data;
}

function sendProgress(tabId, requestId, progress) {
  if (!tabId) return;
  chrome.tabs.sendMessage(tabId, {
    source: "gem-bidplus-extension",
    type: "GEM_BATCH_PROGRESS",
    requestId,
    progress,
  }).catch(() => {});
}

async function runBrowserResultWatcher({ appOrigin, requestId = null, progressTabId = null, silent = false } = {}) {
  const origin = appOrigin || (await getStoredAppOrigin());
  if (!origin) {
    throw new Error("Open Tender AI in this browser once, then retry.");
  }

  await storeAppOrigin(origin);

  const pending = await fetchPendingTenders(origin);
  const summary = {
    totalPending: Array.isArray(pending) ? pending.length : 0,
    checked: 0,
    resultsFound: 0,
    notAvailable: 0,
    failed: 0,
    skipped: 0,
    failures: [],
  };

  if (!Array.isArray(pending) || !pending.length) {
    return summary;
  }

  for (let index = 0; index < pending.length; index += 1) {
    const tender = pending[index] || {};
    const bidNumber = normalizeBidNumber(tender.bidNumber);
    const progressBase = {
      current: index + 1,
      total: pending.length,
      tenderId: tender.id || null,
      bidNumber,
      checked: summary.checked,
      resultsFound: summary.resultsFound,
      notAvailable: summary.notAvailable,
      failed: summary.failed,
      skipped: summary.skipped,
    };

    sendProgress(progressTabId, requestId, {
      ...progressBase,
      stage: "checking",
      message: `Checking ${index + 1} of ${pending.length}`,
    });

    if (!bidNumber || !tender.id) {
      summary.skipped += 1;
      sendProgress(progressTabId, requestId, {
        ...progressBase,
        stage: "skipped",
        skipped: summary.skipped,
        message: `Skipped ${bidNumber || "unknown bid"} because required fields were missing.`,
      });
      continue;
    }

    try {
        const rawResponse = await fetchGemResultData(bidNumber);
        const ingestResult = await ingestTenderResult(origin, tender.id, rawResponse);
        summary.checked += 1;
        if (ingestResult?.result_found) {
          summary.resultsFound += 1;
        } else {
          summary.notAvailable += 1;
        }
      sendProgress(progressTabId, requestId, {
        ...progressBase,
        stage: ingestResult?.result_found ? "result_found" : "not_available",
        checked: summary.checked,
        resultsFound: summary.resultsFound,
        notAvailable: summary.notAvailable,
        failed: summary.failed,
        skipped: summary.skipped,
        message: ingestResult?.result_found
          ? `Result available for ${bidNumber}`
          : `Result not available yet for ${bidNumber}`,
        ingestResult,
      });
    } catch (error) {
      summary.checked += 1;
      summary.failed += 1;
      summary.failures.push({
        tenderId: tender.id || null,
        bidNumber,
        message: error.message || `Failed to check ${bidNumber}`,
        debug: error?.gemDebug || null,
      });
      sendProgress(progressTabId, requestId, {
        ...progressBase,
        stage: "failed",
        checked: summary.checked,
        resultsFound: summary.resultsFound,
        notAvailable: summary.notAvailable,
        failed: summary.failed,
        skipped: summary.skipped,
        message: error.message || `Failed to check ${bidNumber}`,
      });
    }

    if (index < pending.length - 1) {
      await sleep(randomDelayMs());
    }
  }

  if (silent && summary.resultsFound > 0) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ+wP9s0YvrQAAAABJRU5ErkJggg==",
      title: "Tender AI Result Watcher",
      message: `${summary.resultsFound} tender result(s) became available on GeM.`,
    }).catch(() => {});
  }

  return summary;
}

chrome.runtime.onInstalled.addListener(() => {
  ensureAlarmRegistered().catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  ensureAlarmRegistered().catch(() => {});
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm || alarm.name !== WATCHER_ALARM_NAME) return;
  runBrowserResultWatcher({ silent: true }).catch(() => {});
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !message.type) return false;

  (async () => {
    if (message.type === "REGISTER_TENDER_APP_CONTEXT") {
      await storeAppOrigin(message.appOrigin || "");
      sendResponse({ ok: true });
      return;
    }

    if (message.type === "GEM_FETCH_RESULT") {
      if (message.appOrigin) await storeAppOrigin(message.appOrigin);
      const data = await fetchGemResultData(message.bidNumber);
      sendResponse({ ok: true, data });
      return;
    }

    if (message.type === "RUN_BROWSER_RESULT_WATCHER") {
      const senderOrigin = (() => {
        try {
          return sender?.tab?.url ? new URL(sender.tab.url).origin : "";
        } catch (_) {
          return "";
        }
      })();
      const summary = await runBrowserResultWatcher({
        appOrigin: message.appOrigin || senderOrigin,
        requestId: message.requestId || null,
        progressTabId: sender?.tab?.id || null,
        silent: false,
      });
      sendResponse({ ok: true, summary });
      return;
    }

    sendResponse({ ok: false, error: "Unsupported extension message." });
  })().catch((error) => {
    sendResponse({
      ok: false,
      error: error?.message || "Extension request failed.",
      debug: error?.gemDebug || null,
    });
  });

  return true;
});
