# Retail data pipeline

This package contains the utilities used to convert legacy Retail PDFs into the
structured JSON schema consumed by the application and Milvus chunking pipeline.

- `schema.py` defines and validates the canonical Retail document structure.
- `chunking.py` converts structured content blocks into readable Milvus text.
- `extract_retail_pdf.py` converts one PDF to its adjacent `.structured.json` file.
- `extract_all_retail_pdfs.py` refreshes structured JSON for every PDF under
  `Data/retail` while preserving approved extraction data when available.

Run from the project root:

```powershell
.venv\Scripts\python.exe -m retail_pipeline.extract_all_retail_pdfs
```

For one PDF:

```powershell
.venv\Scripts\python.exe -m retail_pipeline.extract_retail_pdf "Data\retail\cards.pdf"
```

The application reads `.structured.json` first. PDFs are retained only as legacy
source references and extraction inputs.
