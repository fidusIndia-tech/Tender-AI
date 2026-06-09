"""
pipeline.py — end-to-end: PDF in, ready-to-upload folder out.

Steps
  1. parse the tender PDF -> structured fields + required-doc list
  2. for each required doc_key, look it up in the document database manifest
       - static   -> copy file into the output folder
       - template -> fill placeholders from company profile + tender, save
       - manual   -> can't auto-produce; add to the "still needed" checklist
       - no match -> add to "unmatched" checklist
  3. write tender_summary.json + checklist.md into the output folder
"""

import os
import re
import json
import shutil

import extract
import fill_forms

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "doc_database")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _slug(bid_number):
    return re.sub(r"[^A-Za-z0-9]+", "_", bid_number or "tender").strip("_")


def run(pdf_path, profile_path=None, out_root=None):
    profile = _load(profile_path or os.path.join(HERE, "company_profile.json"))
    manifest = _load(os.path.join(DB, "manifest.json"))

    tender = extract.parse_tender(pdf_path)
    context = fill_forms.build_context(profile, tender)

    out_root = out_root or os.path.join(HERE, "output")
    out_dir = os.path.join(out_root, _slug(tender.get("bid_number")))
    os.makedirs(out_dir, exist_ok=True)

    produced, still_needed, unmatched = [], [], []
    # dedupe by doc_key first, then by resolved output to avoid duplicates
    # when multiple keys alias to the same manifest entry.
    seen_keys = set()
    seen_outputs = set()
    required = [d for d in tender["required_documents"]
                if not (d["doc_key"] in seen_keys or seen_keys.add(d["doc_key"]))]

    for doc in required:
        key = doc["doc_key"]
        entry = manifest.get(key)
        if not entry:
            unmatched.append(doc["label"])
            continue

        # conditional docs (e.g. MSE only if eligible)
        if entry.get("only_if") == "msme_eligible" and not (
                profile.get("udyam_no") and profile.get("is_manufacturer")):
            still_needed.append(f"{doc['label']} (skipped: not an MSE manufacturer)")
            continue

        if entry["type"] == "static":
            output_name = entry["output_name"]
            if output_name in seen_outputs:
                continue
            seen_outputs.add(output_name)
            src = os.path.join(DB, entry["file"])
            dst = os.path.join(out_dir, output_name)
            shutil.copy(src, dst)
            produced.append(output_name)

        elif entry["type"] == "template":
            output_name = entry["output_name"]
            if output_name in seen_outputs:
                continue
            seen_outputs.add(output_name)
            src = os.path.join(DB, entry["file"])
            dst = os.path.join(out_dir, output_name)
            fill_forms.fill_template(src, dst, context)
            produced.append(output_name + "  (auto-filled)")

        elif entry["type"] == "manual":
            note = entry.get("note", "")
            if note in seen_outputs:
                continue
            seen_outputs.add(note)
            still_needed.append(f"{doc['label']} — {note}")

    # summary + checklist
    with open(os.path.join(out_dir, "tender_summary.json"), "w", encoding="utf-8") as f:
        json.dump(tender, f, indent=2, ensure_ascii=False)

    produced_lines = [f"- [x] {p}" for p in produced] or ["- (none)"]
    needed_lines = [f"- [ ] {s}" for s in still_needed] or ["- (none)"]
    checklist = [
        f"# Submission checklist — {tender.get('bid_number')}",
        f"\n**{tender.get('boq_title')}** | {tender.get('organisation')} | {tender.get('office')}",
        f"Bid end: {tender.get('bid_end_datetime')}  |  Opening: {tender.get('bid_opening_datetime')}",
        f"Total qty: {tender.get('total_quantity')}  |  EMD per schedule: {tender.get('emd')}",
        "\n## Auto-produced (in this folder)",
        *produced_lines,
        "\n## Still needed from you",
        *needed_lines,
    ]
    if unmatched:
        checklist += ["\n## Required but NOT in your database (add a template/file)",
                      *[f"- [ ] {u}" for u in unmatched]]
    with open(os.path.join(out_dir, "checklist.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(checklist))

    return out_dir, produced, still_needed, unmatched


if __name__ == "__main__":
    import sys
    pdf = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/GeM-Bidding-9318524_IFM.pdf"
    out_dir, produced, needed, unmatched = run(pdf)
    print("OUTPUT FOLDER:", out_dir)
    print("\nproduced:", *produced, sep="\n  ")
    print("\nstill needed:", *needed, sep="\n  ")
    print("\nunmatched:", *unmatched, sep="\n  ")
