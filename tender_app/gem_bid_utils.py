import re


GEM_BID_NUMBER_PATTERN = re.compile(r"\b(GEM/\d{4}/B/\d+)\b", re.I)
# The numeric doc id GeM uses in a bid's PDF URL, e.g.
# https://bidplus.gem.gov.in/showbidDocument/9526913  or  /showradocumentPdf/9526913
# That same id is the number in the downloaded "GeM-Bidding-9526913.pdf" filename.
GEM_PDF_DOC_ID_PATTERN = re.compile(r"/(?:showbidDocument|showradocumentPdf)/(\d+)", re.I)


def _normalize_text(value) -> str:
    return " ".join(str(value or "").split()).strip()


def extractGemBiddingId(candidate) -> str | None:
    """Return the numeric GeM bidding number (e.g. "9526913") for a discovered
    candidate.

    This is distinct from the tender number (GEM/YYYY/B/NNNN). GeM exposes it as
    the doc id in the bid PDF URL, which is also what appears in the downloaded
    "GeM-Bidding-<id>.pdf" filename. Prefer an explicit value if present; else
    parse it from the candidate's stored PDF URL.
    """
    if not candidate:
        return None
    explicit = _normalize_text(candidate.get("gem_bidding_id"))
    if explicit:
        return explicit
    for key in ("pdf_url", "gem_detail_url"):
        match = GEM_PDF_DOC_ID_PATTERN.search(str(candidate.get(key) or ""))
        if match:
            return match.group(1)
    return None


def getCanonicalGemBidNumber(tender) -> str | None:
    if not tender:
        return None

    values = [
        tender.get("gem_bidding_no"),
        tender.get("gem_bidding_number"),
        tender.get("tender_number"),
        tender.get("bid_number"),
        tender.get("gem_bid_number"),
    ]
    for value in values:
        text = _normalize_text(value)
        if not text:
            continue
        match = GEM_BID_NUMBER_PATTERN.search(text)
        if match:
            return match.group(1).upper()
    return None
