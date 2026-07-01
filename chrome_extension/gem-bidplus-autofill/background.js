// Service worker: fetches GeM Bid/RA result data from the user's own browser/IP.
// GeM blocks the tender-app server (cloud datacenter IP), so the result check is
// performed here and the raw JSON is handed back to the page, which forwards it
// to the backend for parsing.

const GEM_ALL_BIDS_URL = "https://bidplus.gem.gov.in/all-bids";
const GEM_ALL_BIDS_DATA_URL = "https://bidplus.gem.gov.in/all-bids-data";

function buildPayload(bidNumber) {
  const body = {
    param: { searchBid: bidNumber, searchType: "fullText" },
    filter: {
      bidStatusType: "bidrastatus",
      byType: "all",
      highBidValue: "",
      byEndDate: { from: "", to: "" },
      sort: "Bid-End-Date-Latest",
    },
  };
  return "payload=" + encodeURIComponent(JSON.stringify(body));
}

function getCsrfToken() {
  return new Promise((resolve) => {
    try {
      chrome.cookies.get(
        { url: GEM_ALL_BIDS_URL, name: "csrf_gem_cookie" },
        (cookie) => resolve(cookie ? cookie.value : "")
      );
    } catch (_) {
      resolve("");
    }
  });
}

async function fetchGemResult(bidNumber) {
  // Land on all-bids first so GeM issues/refreshes the CSRF cookie.
  await fetch(GEM_ALL_BIDS_URL, {
    method: "GET",
    credentials: "include",
    headers: {
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
  });

  const csrf = await getCsrfToken();
  if (!csrf) {
    throw new Error(
      "GeM CSRF cookie not found. Open https://bidplus.gem.gov.in/all-bids once in this browser, then retry."
    );
  }

  const body = buildPayload(bidNumber) + "&csrf_bd_gem_nk=" + encodeURIComponent(csrf);
  const res = await fetch(GEM_ALL_BIDS_DATA_URL, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json, text/javascript, */*; q=0.01",
      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
      "X-Requested-With": "XMLHttpRequest",
    },
    body,
  });

  if (!res.ok) {
    throw new Error("GeM responded HTTP " + res.status);
  }
  return await res.json();
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "GEM_FETCH_RESULT") return undefined;
  const bidNumber = String(message.bidNumber || "").trim();
  if (!bidNumber) {
    sendResponse({ ok: false, error: "Missing bid number" });
    return undefined;
  }
  fetchGemResult(bidNumber)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: String((err && err.message) || err) }));
  return true; // keep the message channel open for the async response
});
