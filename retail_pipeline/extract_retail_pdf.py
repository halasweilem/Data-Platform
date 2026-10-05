"""Extract one Retail PDF into the canonical JSON schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app import extract_retail_pdf
from .schema import validate_document


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract one Retail PDF into canonical JSON")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args()
    source = args.pdf.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        parser.error(f"PDF not found: {source}")
    document = extract_retail_pdf(source)
    errors = validate_document(document)
    if errors:
        raise SystemExit("Invalid extraction: " + "; ".join(errors[:10]))
    destination = (args.output or source.with_suffix(".structured.json")).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Extracted {len(document['sections'])} sections to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
