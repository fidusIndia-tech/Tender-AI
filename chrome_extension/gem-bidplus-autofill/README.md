# GeM BidPlus Autofill Extension

This Chrome extension watches `https://bidplus.gem.gov.in/all-bids` for a bid number encoded in the URL and then:

- switches the page to `Bid/RA Status`
- sets search mode to `Exact Search`
- fills the GeM bid number
- clicks the search button

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
