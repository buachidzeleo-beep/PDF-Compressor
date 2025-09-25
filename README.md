# PDFCompressor — scan fix build

Fixes:
- Persist scanned file list via `st.session_state` (no loss between clicks).
- Robust recursive scan using `os.walk` (case-insensitive `.pdf`).
- Diagnostics panel for path/access debugging.
