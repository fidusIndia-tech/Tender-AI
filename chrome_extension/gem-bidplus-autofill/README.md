# GeM BidPlus Autofill Extension

This Chrome extension does two things:

**1. Autofill** — watches `https://bidplus.gem.gov.in/all-bids` for a bid number encoded in the URL and then:

- switches the page to `Bid/RA Status`
- sets search mode to `Exact Search`
- fills the GeM bid number
- clicks the search button

**2. Result check (v1.1.0+)** — when you click `Check Result` in the tender app, the
extension fetches the GeM Bid/RA status data **from your own browser/IP** (a background
service worker) and hands the raw response back to the app, which forwards it to the
backend for parsing. This is required because GeM blocks the hosted server's IP, so the
server cannot query GeM directly. The result is fetched on your machine instead.

> After updating to v1.1.0, reload the extension: open `chrome://extensions/`, find
> `GeM BidPlus Autofill`, and click the reload icon (or re-`Load unpacked`). The first
> time, open `https://bidplus.gem.gov.in/all-bids` once so GeM sets its session cookie.

## Expected URL format

The tender app opens GeM using this pattern:

```text
https://bidplus.gem.gov.in/all-bids#bidrastatus-search-7530121
```

The extension reads the bid number from the hash and autofills the page.

## Install locally

1. Open `chrome://extensions/` in Chrome or Edge.
2. Turn on `Developer mode`.
3. Click `Load unpacked`.
4. Select this folder:

```text
chrome_extension/gem-bidplus-autofill
```

After installation, your tender app will show a small status in the `Results` tab:

- `Extension detected` means GeM autofill should run automatically
- `Extension not detected` means GeM will still open, but manual paste/search is needed

## Package for team sharing

To create a zip of the extension files, run:

```powershell
powershell -ExecutionPolicy Bypass -File chrome_extension/package-extension.ps1
```

That creates:

```text
chrome_extension/gem-bidplus-autofill.zip
```

## Notes

- This is a browser-side production-friendly approach.
- No server-side browser automation is required.
- If the GeM page changes its DOM ids or JavaScript functions, the extension may need a small update.
