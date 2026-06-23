(function () {
  const root = document.documentElement;
  if (!root) return;
  root.dataset.gemBidplusExtension = "installed";
  document.dispatchEvent(new CustomEvent("gem-bidplus-extension-ready"));
})();
