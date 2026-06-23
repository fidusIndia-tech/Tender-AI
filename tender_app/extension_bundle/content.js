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

  const bidNumber = getBidNumber();
  if (!bidNumber) return;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => runAutofill(bidNumber), { once: true });
  } else {
    runAutofill(bidNumber);
  }
})();
