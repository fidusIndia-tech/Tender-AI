import re


GEM_BID_NUMBER_PATTERN = re.compile(r"\b(GEM/\d{4}/B/\d+)\b", re.I)


def _normalize_text(value) -> str:
    return " ".join(str(value or "").split()).strip()


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
