"""Build canonical Retail JSON for every existing PDF without overwriting approvals."""
from __future__ import annotations

import json
from pathlib import Path

from app import RETAIL_ROOT, extracted_path, extract_retail_pdf, normalize_retail_data
from .schema import validate_document


def main() -> int:
    pdfs = sorted(RETAIL_ROOT.rglob("*.pdf"))
    failures = 0
    for pdf in pdfs:
        relative = pdf.relative_to(RETAIL_ROOT).as_posix()
        approved = extracted_path(relative)
        # Preserve any business-approved edits already stored for this PDF. PDFs that
        # have never been reviewed are extracted directly into the same schema.
        if approved.is_file():
            document = normalize_retail_data(json.loads(approved.read_text(encoding="utf-8-sig")))
            document["source"] = {"type": "pdf", "filename": pdf.name, "migration": "approved_extraction"}
            document = normalize_retail_data(document)
            origin = "approved extraction"
        else:
            document = extract_retail_pdf(pdf)
            origin = "PDF"
        errors = validate_document(document)
        if errors:
            failures += 1
            print(f"FAILED {relative}: {'; '.join(errors[:5])}")
            continue
        sidecar = pdf.with_suffix(".structured.json")
        sidecar.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"OK {relative}: {len(document['sections'])} sections from {origin} -> {sidecar}")
    print(f"Completed {len(pdfs) - failures}/{len(pdfs)} Retail products")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
