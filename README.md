# MarkItDown Web

A web interface for converting documents to Markdown. PDF OCR runs on full-page raster images locally; separate image files use the configured SumoPod vision route.

## PDF pipeline

1. Validate the PDF and enforce the 30-page limit.
2. Render **each entire page** to a JPEG with Poppler (`pdftoppm`, longest side 2800 px). Embedded images, text, stamps, and tables are flattened together in that page image. The **OCR diperbesar** button rerenders the selected page at 3200 px and replaces its OCR text when successful.
3. Run Tesseract (`ind+eng`) once on the rendered image for both text and word coordinates. The original PDF bytes are never sent to an OCR model. The selectable PDF text layer does not form the Markdown output.
4. Display the page image and OCR text. The browser processes all pages in order and assembles the Markdown download only when every page has been processed. Blank/unreadable pages are reported for review.

This change removes the SumoPod vision probe from the PDF path. It remains in use for separately uploaded PNG/JPEG/WebP images. Tesseract can misread small print, handwriting, stamps, and tables; inspect the Markdown against the page images before relying on sensitive details.

## Run

```bash
pip install -r requirements.txt
# Install poppler-utils, tesseract-ocr, tesseract-ocr-eng, tesseract-ocr-ind
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The Dockerfile installs the OCR dependencies. `SUMOPOD_API_KEY` is required only for separate image uploads. Do not commit keys. The server processes uploads in memory and does not persist them. Other supported inputs are DOCX, PPTX, XLSX, TXT, CSV, and JSON.

## API

`POST /api/convert` validates a PDF and returns an empty initial Markdown document. The browser calls `POST /api/pdf-preview?page=N` for each page to receive the rendered JPEG, OCR `page_text`, word `regions`, and page count. For PDF, `page_source` is `tesseract` when text is recognized. `GET /health` reports server availability.
