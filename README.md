# PDFCompressor

Streamlit application for compressing PDF files in bulk using Ghostscript, PikePDF, and optional OCRmyPDF.

## Features
- Batch compress PDF files from folders or uploaded files
- Multiple compression presets (lossless, balanced, aggressive, custom)
- Optional OCR for scanned PDFs
- Parallel processing with progress bar
- Reports with size savings

## Installation
```
pip install -r requirements.txt
```

## Run
```
python -m streamlit run app.py
```

## Notes
- Ghostscript is recommended for best compression results.
- OCR requires `ocrmypdf` and Tesseract installed.
