"""GeM candidate evaluation.

Two-layer design for careful, reliable scoring:

1. JUDGEMENT (LLM): the model reads the actual tender text together with the
   company capability profile and scores the seven admin-defined criteria with
   written justification.
2. GATING (deterministic code): hard eligibility decisions are enforced in code
   from explicit flags/signals so the critical outcomes stay auditable.

If OPENAI_API_KEY is missing or the LLM call fails, the system falls back to
the rule-based scorer so evaluation never crashes and still works offline.
"""
import json
import os
from datetime import date, datetime

from evaluation import evaluate_tender_against_capability

MIN_DEADLINE_DAYS = int(os.environ.get("GEM_MIN_DEADLINE_DAYS", "3"))
AUTO_APPROVE_THRESHOLD = int(os.environ.get("GEM_AUTO_APPROVE_SCORE_THRESHOLD", "8"))
EVAL_MODEL = os.environ.get("GEM_EVAL_MODEL", "gpt-4o-mini")
MAX_TENDER_TEXT_CHARS = 40_000
SKIP_LLM_IF_RULE_SCORE_AT_OR_BELOW = int(os.environ.get("GEM_SKIP_LLM_IF_RULE_SCORE_AT_OR_BELOW", "3"))

CRITERIA = [
    "brand_product_match",
    "technical_eligibility",
    "oem_authorization",
    "turnover_experience",
    "document_availability",
    "deadline_feasibility",
    "location_supply",
]


def _clean_text(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(values, limit: int = 4) -> list[str]:
    if not values:
        return []
    if not isinstance(values, list):
        values = [values]
    cleaned = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _build_reason(summary: str, strengths=None, risks=None, hard_failures=None, manual_checks=None) -> str:
    parts = []
    summary = _clean_text(summary)
    if summary:
        parts.append(summary)
    strengths = _clean_list(strengths, limit=3)
    risks = _clean_list(risks, limit=3)
    hard_failures = _clean_list(hard_failures, limit=3)
    manual_checks = _clean_list(manual_checks, limit=3)
    if strengths:
        parts.append("Why bid: " + "; ".join(strengths))
    if risks:
        parts.append("Risks: " + "; ".join(risks))
    if hard_failures:
        parts.append("Why not bid: " + "; ".join(hard_failures))
    if manual_checks:
        parts.append("Review before bid: " + "; ".join(manual_checks))
    return " ".join(parts).strip() or "Evaluated."


def _parse_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _yes_no(v):
    return "Yes" if v else "No"


def _build_profile_summary(c: dict) -> str:
    years = c.get("years_experience")
    docs_available = [name for name, flag in {
        "GST": c.get("gst_available"),
        "PAN": c.get("pan_available"),
        "MSME": c.get("msme_available"),
        "ITR": c.get("itr_available"),
        "Bank documents": c.get("bank_documents_available"),
        "Letterhead": c.get("letterhead_available"),
        "Stamp": c.get("stamp_available"),
        "Signature": c.get("signature_available"),
    }.items() if flag]
    return (
        f"Core business: {c.get('core_business') or 'N/A'}\n"
        f"Product categories handled: {c.get('product_categories') or 'N/A'}\n"
        f"Brands handled: {c.get('brands_handled') or 'N/A'}\n"
        f"Industries served: {c.get('industries_served') or 'N/A'}\n"
        f"Years in business: {years if years is not None else 'N/A'} "
        f"(established {c.get('year_established') or 'N/A'})\n"
        f"Annual turnover range: {c.get('turnover_range') or 'N/A'}\n"
        f"Typical tender value range handled: {c.get('typical_tender_value_range') or 'N/A'}\n"
        f"OEM support available: {_yes_no(c.get('oem_support_available'))}\n"
        f"OEM authorizations held: {c.get('oem_authorizations') or 'None stated'}\n"
        f"Engineering support: {_yes_no(c.get('engineering_support'))}; "
        f"Installation support: {_yes_no(c.get('installation_support'))}\n"
        f"Import capability: {_yes_no(c.get('import_capability'))}; "
        f"Export capability: {_yes_no(c.get('export_capability'))}\n"
        f"Government experience: {_yes_no(c.get('government_experience'))}; "
        f"PSU experience: {_yes_no(c.get('psu_experience'))}\n"
        f"Documents available: {', '.join(docs_available) or 'None'}\n"
        f"Major customers: {c.get('major_customers') or 'N/A'}\n"
        f"Past orders/projects: {c.get('past_orders_projects') or 'N/A'}"
    )


EVAL_SYSTEM_PROMPT = """You are a meticulous tender bid-evaluation analyst for an Indian industrial-automation supplier bidding on GeM (Government e-Marketplace) tenders.

You will be given (A) the company's capability profile and (B) the tender's details and text. Read the tender carefully and judge whether this company should bid.

Score each of these SEVEN criteria from 0 to 10 and justify each with specific evidence from the tender text. Never invent requirements that are not in the tender.

1. brand_product_match - Does the tender's required product/brand match what the company actually sells?
2. technical_eligibility - Can the company meet the technical specifications/qualifications stated?
3. oem_authorization - Does the tender mandate an OEM Authorization Certificate / MAF, and does the company hold authorization for the required brand?
4. turnover_experience - Does the company meet any stated annual-turnover and past-experience/past-supply eligibility requirements?
5. document_availability - Can the company provide all mandatory documents the tender requires?
6. deadline_feasibility - Is there enough time before the bid end date to prepare and submit?
7. location_supply - Can the company supply/deliver to the required location(s)? If the tender states no specific constraint the company cannot meet, treat this as feasible.

Then set explicit boolean flags used for hard eligibility gating. Be conservative: if a mandatory requirement is stated and the company clearly cannot meet it, mark the corresponding flag false.

Return ONLY a valid JSON object, no markdown, with EXACTLY this structure:
{
  "criteria": {
    "brand_product_match":   {"score": 0, "justification": "..."},
    "technical_eligibility": {"score": 0, "justification": "..."},
    "oem_authorization":     {"score": 0, "justification": "..."},
    "turnover_experience":   {"score": 0, "justification": "..."},
    "document_availability": {"score": 0, "justification": "..."},
    "deadline_feasibility":  {"score": 0, "justification": "..."},
    "location_supply":       {"score": 0, "justification": "..."}
  },
  "flags": {
    "product_related": true,
    "oem_authorization_required": false,
    "oem_authorization_available": false,
    "turnover_eligibility_met": true,
    "experience_eligibility_met": true
  },
  "matched_brands": ["brand names from the company profile that the tender actually requires"],
  "recommendation": "BID",
  "confidence": "HIGH",
  "strengths": ["short evidence-backed reasons to bid"],
  "risks": ["meaningful concerns or gaps"],
  "hard_failures": ["mandatory disqualifiers only; empty if none"],
  "manual_checks_needed": ["items a human should verify before bidding"],
  "overall_score": 0,
  "summary": "2-4 sentence overall recommendation citing the decisive factors."
}

Scoring guidance: overall_score should reflect genuine bid-worthiness on a 0-10 scale where 8-10 = strong fit worth bidding, 5-7 = possible but with gaps, 0-4 = poor fit or ineligible. product_related must be false ONLY when the tender's product is clearly unrelated to the company's business."""


def _llm_evaluate(tender: dict, capability: dict, tender_text: str) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or api_key.strip() in {"", "YOUR_OPENAI_KEY_HERE"}:
        raise RuntimeError("OPENAI_API_KEY not configured")

    from openai import OpenAI

    items = tender.get("boq_items") or []
    item_lines = "\n".join(
        f"- {i.get('part_number') or ''} {i.get('item_description') or ''} (qty {i.get('quantity') or ''})".strip()
        for i in items[:60]
    )
    req_docs = ", ".join(
        (d.get("label") if isinstance(d, dict) else str(d))
        for d in (tender.get("required_documents") or [])
    )

    tender_block = (
        f"GeM Bid Number: {tender.get('gem_bidding_number') or tender.get('tender_number') or 'N/A'}\n"
        f"Title/Items category: {tender.get('make') or 'N/A'}\n"
        f"Organisation: {tender.get('organization_name') or 'N/A'}\n"
        f"Department: {tender.get('department_name') or 'N/A'}\n"
        f"Total quantity: {tender.get('total_quantity') or 'N/A'}\n"
        f"Estimated value: {tender.get('tender_approx_value') or 'N/A'}\n"
        f"Bid end date/time: {tender.get('bid_end_datetime') or 'N/A'}\n"
        f"Required documents (extracted): {req_docs or 'None extracted'}\n"
        f"Line items:\n{item_lines or 'None extracted'}\n\n"
        f"Tender document text (relevant pages):\n{(tender_text or '')[:MAX_TENDER_TEXT_CHARS]}"
    )

    user_content = (
        "=== COMPANY CAPABILITY PROFILE ===\n"
        + _build_profile_summary(capability)
        + "\n\n=== TENDER ===\n"
        + tender_block
    )

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=EVAL_MODEL,
        messages=[
            {"role": "system", "content": EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=2200,
    )
    return json.loads(response.choices[0].message.content)


def _finalize_from_llm(llm: dict, bid_end_date) -> dict:
    """Apply deterministic hard eligibility caps on top of the LLM judgement."""
    flags = llm.get("flags", {}) or {}

    try:
        score = int(round(float(llm.get("overall_score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(10, score))

    caps_applied = []
    capped_reasons = []
    strengths = _clean_list(llm.get("strengths"))
    risks = _clean_list(llm.get("risks"))
    hard_failures = _clean_list(llm.get("hard_failures"))
    manual_checks = _clean_list(llm.get("manual_checks_needed"))

    if flags.get("product_related") is False:
        hard_failures.append("Tender product/category does not match the company profile.")
        return {
            "score": 0,
            "rating_label": "UNRELATED",
            "matched_brands": "",
            "eligibility_status": "REJECTED",
            "reason": _build_reason(
                "Product is unrelated to the company's business.",
                strengths=strengths,
                risks=risks,
                hard_failures=hard_failures,
                manual_checks=manual_checks,
            ),
            "evaluation_json": {
                "llm": llm,
                "caps_applied": ["unrelated_product_reject"],
                "method": "llm",
                "recommendation": "REJECT",
                "strengths": strengths,
                "risks": risks,
                "hard_failures": _clean_list(hard_failures),
                "manual_checks_needed": manual_checks,
            },
        }

    if flags.get("oem_authorization_required") and not flags.get("oem_authorization_available"):
        if score > 4:
            score = 4
        failure = "Mandatory OEM authorization is required but not held."
        caps_applied.append("oem_authorization_required_unavailable")
        capped_reasons.append(failure)
        hard_failures.append(failure)

    if flags.get("turnover_eligibility_met") is False:
        if score > 4:
            score = 4
        failure = "Stated turnover eligibility is not met."
        caps_applied.append("turnover_eligibility_failed")
        capped_reasons.append(failure)
        hard_failures.append(failure)

    if flags.get("experience_eligibility_met") is False:
        if score > 4:
            score = 4
        failure = "Stated experience eligibility is not met."
        caps_applied.append("experience_eligibility_failed")
        capped_reasons.append(failure)
        hard_failures.append(failure)

    bed = _parse_date(bid_end_date)
    if bed:
        days_remaining = (bed - date.today()).days
        if days_remaining < MIN_DEADLINE_DAYS:
            score = max(0, score - 2)
            warning = f"Bid deadline is very close ({days_remaining} day(s) remaining)."
            caps_applied.append("deadline_too_close")
            capped_reasons.append(warning)
            risks.append(warning)

    if score >= 8:
        rating_label = "STRONG_FIT"
    elif score >= 5:
        rating_label = "MODERATE_FIT"
    else:
        rating_label = "WEAK_FIT"

    matched = llm.get("matched_brands") or []
    matched_brands = ", ".join(str(b) for b in matched) if isinstance(matched, list) else str(matched)
    requested_recommendation = _clean_text(llm.get("recommendation")).upper()

    if hard_failures:
        recommendation = "REJECT"
    elif score >= AUTO_APPROVE_THRESHOLD and requested_recommendation != "REJECT":
        recommendation = "BID"
    else:
        recommendation = "REVIEW"

    eligibility_status = (
        "APPROVED" if recommendation == "BID"
        else "REJECTED" if recommendation == "REJECT"
        else "REVIEW"
    )

    reason = _build_reason(
        llm.get("summary") or "",
        strengths=strengths,
        risks=risks,
        hard_failures=hard_failures,
        manual_checks=manual_checks,
    )

    return {
        "score": score,
        "rating_label": rating_label,
        "matched_brands": matched_brands,
        "eligibility_status": eligibility_status,
        "reason": reason,
        "evaluation_json": {
            "llm": llm,
            "caps_applied": caps_applied,
            "method": "llm",
            "recommendation": recommendation,
            "strengths": strengths,
            "risks": risks,
            "hard_failures": _clean_list(hard_failures),
            "manual_checks_needed": manual_checks,
            "capped_reasons": capped_reasons,
        },
    }


def _check(checks, name):
    return next((c for c in checks if c.get("name") == name), None)


def _rule_based_eval(tender: dict, capability: dict, bid_end_date=None) -> dict:
    """Fallback evaluator when LLM usage is skipped or unavailable."""
    base = evaluate_tender_against_capability(tender, capability)
    checks = base.get("checks", [])

    product_check = _check(checks, "Product category")
    brand_check = _check(checks, "Brand")
    product_unrelated = bool(product_check) and product_check.get("status") == "missing" and \
        bool(brand_check) and brand_check.get("status") == "missing"

    if product_unrelated:
        return {
            "score": 0,
            "rating_label": "UNRELATED",
            "matched_brands": "",
            "eligibility_status": "REJECTED",
            "reason": _build_reason(
                "Product/brand unrelated to business profile.",
                hard_failures=["Tender product/category does not match the company profile."],
            ),
            "evaluation_json": {
                "base": base,
                "caps_applied": ["unrelated_product_reject"],
                "method": "rule_based",
                "recommendation": "REJECT",
                "strengths": [],
                "risks": _clean_list(base.get("risks"), limit=4),
                "hard_failures": ["Tender product/category does not match the company profile."],
                "manual_checks_needed": [],
            },
        }

    score = round(base.get("score", 0) / 10)
    score = max(0, min(10, score))
    caps_applied = []
    strengths = _clean_list(base.get("strengths"), limit=4)
    risks = _clean_list(base.get("risks"), limit=4)
    hard_failures = []
    manual_checks = []

    oem_check = _check(checks, "OEM support")
    if oem_check and oem_check.get("status") == "risk":
        if score > 4:
            score = 4
        caps_applied.append("oem_authorization_required_unavailable")
        hard_failures.append(_clean_text(oem_check.get("detail")))

    experience_check = _check(checks, "Experience")
    if experience_check and experience_check.get("status") == "risk":
        manual_checks.append(_clean_text(experience_check.get("detail")))

    bed = _parse_date(bid_end_date)
    if bed:
        days_remaining = (bed - date.today()).days
        if days_remaining < MIN_DEADLINE_DAYS:
            score = max(0, score - 2)
            risks.append(f"Bid deadline is very close ({days_remaining} day(s) remaining)")

    location_check = {
        "name": "Location/Supply feasibility",
        "status": "unknown",
        "impact": 0,
        "detail": "No service-area data available in capability profile",
    }
    checks_with_location = checks + [location_check]

    if score >= 8:
        rating_label = "STRONG_FIT"
    elif score >= 5:
        rating_label = "MODERATE_FIT"
    else:
        rating_label = "WEAK_FIT"

    matched_brands = ", ".join(base.get("strengths", [])[:1]) if brand_check and brand_check.get("status") == "match" else ""
    recommendation = "REJECT" if hard_failures else "BID" if score >= AUTO_APPROVE_THRESHOLD else "REVIEW"
    eligibility_status = (
        "APPROVED" if recommendation == "BID"
        else "REJECTED" if recommendation == "REJECT"
        else "REVIEW"
    )

    return {
        "score": score,
        "rating_label": rating_label,
        "matched_brands": matched_brands,
        "eligibility_status": eligibility_status,
        "reason": _build_reason(
            base.get("summary", ""),
            strengths=strengths,
            risks=risks,
            hard_failures=hard_failures,
            manual_checks=manual_checks,
        ),
        "evaluation_json": {
            "base": base,
            "checks": checks_with_location,
            "caps_applied": caps_applied,
            "method": "rule_based",
            "recommendation": recommendation,
            "strengths": strengths,
            "risks": risks,
            "hard_failures": hard_failures,
            "manual_checks_needed": manual_checks,
        },
    }


def evaluate_gem_candidate(tender: dict, capability: dict, bid_end_date=None, tender_text: str = None) -> dict:
    """Score a GeM candidate 0-10.

    The local rule-based evaluator is used as a cheap first-pass filter. If the
    tender looks unrelated or extremely weak, we skip the LLM to save cost.
    Otherwise the model reads the tender text and returns a richer judgement.
    """
    rule_based = _rule_based_eval(tender, capability, bid_end_date)

    if rule_based.get("rating_label") == "UNRELATED" or int(rule_based.get("score", 0) or 0) <= SKIP_LLM_IF_RULE_SCORE_AT_OR_BELOW:
        return rule_based

    if tender_text:
        try:
            llm = _llm_evaluate(tender, capability, tender_text)
            return _finalize_from_llm(llm, bid_end_date)
        except Exception as e:
            print(f"[gem_watcher] LLM evaluation failed ({type(e).__name__}: {e}) - falling back to rule-based.")
    return rule_based
