import os, sys, shutil, subprocess, tempfile, time, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st
import psutil

# --- Optional imports ---
try:
    import pikepdf
except ImportError:
    pikepdf = None

# ---------- Helpers ----------
def human_size(b):
    for u in ["B","KB","MB","GB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def has_ghostscript():
    try:
        cmd = ["gswin64c" if os.name == "nt" else "gs", "-v"]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception:
        return False

def ghostscript_compress(src: Path, dst: Path, preset: str, dpi: int = 150, jpegq: int = 60):
    """
    Use Ghostscript for best image compression.
    Presets: lossless / balanced / aggressive / custom (uses dpi/jpegq)
    """
    gs = "gswin64c" if os.name == "nt" else "gs"
    # Map preset -> quality parameters
    if preset == "lossless":
        # Note: ghostscript "lossless" не гарантирует 0-изменений, но максимально бережный профиль.
        cmd = [
            gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
            "-dPDFSETTINGS=/default",
            "-dDetectDuplicateImages=true",
            "-dColorImageDownsampleType=/Average",
            "-dColorImageResolution=300",
            "-dGrayImageDownsampleType=/Average",
            "-dGrayImageResolution=300",
            "-dMonoImageDownsampleType=/Subsample",
            "-dMonoImageResolution=600",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={str(dst)}", str(src)
        ]
    elif preset == "balanced":
        cmd = [
            gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
            "-dPDFSETTINGS=/printer",
            "-dDetectDuplicateImages=true",
            "-dColorImageDownsampleType=/Average",
            "-dColorImageResolution=200",
            "-dGrayImageDownsampleType=/Average",
            "-dGrayImageResolution=200",
            "-dMonoImageDownsampleType=/Subsample",
            "-dMonoImageResolution=600",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={str(dst)}", str(src)
        ]
    elif preset == "aggressive":
        cmd = [
            gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
            "-dPDFSETTINGS=/screen",
            "-dDetectDuplicateImages=true",
            "-dColorImageDownsampleType=/Average",
            "-dColorImageResolution=120",
            "-dGrayImageDownsampleType=/Average",
            "-dGrayImageResolution=120",
            "-dMonoImageDownsampleType=/Subsample",
            "-dMonoImageResolution=400",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={str(dst)}", str(src)
        ]
    else:
        # custom
        cmd = [
            gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
            "-dDetectDuplicateImages=true",
            "-dColorImageDownsampleType=/Average",
            f"-dColorImageResolution={dpi}",
            "-dGrayImageDownsampleType=/Average",
            f"-dGrayImageResolution={dpi}",
            "-dMonoImageDownsampleType=/Subsample",
            "-dMonoImageResolution=400",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={str(dst)}", str(src)
        ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

def pikepdf_optimize(src: Path, dst: Path):
    if not pikepdf:
        raise RuntimeError("pikepdf not installed")
    with pikepdf.open(str(src)) as pdf:
        # базовая оптимизация + линейризация
        pdf.save(str(dst), optimize_streams=True, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate, linearize=True)

def try_ocrmypdf(src: Path, dst: Path):
    # optional OCR route if installed
    try:
        subprocess.run(["ocrmypdf", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception:
        return False, "ocrmypdf not installed"
    cmd = ["ocrmypdf", "--optimize", "3", "--skip-text", "--quiet", str(src), str(dst)]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return (res.returncode == 0), (res.stderr.decode("utf-8", errors="ignore") or "ok")

def compress_one(pdf_path: Path, out_dir: Path, mode: str, policy_overwrite: bool, custom_dpi: int, custom_jpegq: int, use_ocr: bool):
    out_dir = out_dir if out_dir else pdf_path.parent
    out_path = (pdf_path if policy_overwrite else out_dir / (pdf_path.stem + "_compressed.pdf"))

    # backup if overwrite
    backup_path = None
    if policy_overwrite:
        ensure_dir(Path("backups"))
        backup_path = Path("backups") / f"{pdf_path.stem}_{int(time.time())}.pdf"
        shutil.copy2(pdf_path, backup_path)

    tmp = Path(tempfile.gettempdir()) / f"tmp_{pdf_path.stem}_{time.time()}.pdf"
    size_before = pdf_path.stat().st_size
    status, note = "OK", ""

    try:
        # 1) Try Ghostscript if available
        if has_ghostscript():
            ghostscript_compress(pdf_path, tmp, preset=mode, dpi=custom_dpi, jpegq=custom_jpegq)
        else:
            # 2) Fallback to pikepdf structural optimization (lossless-ish)
            pikepdf_optimize(pdf_path, tmp)

        # 3) If requested OCR for scans
        if use_ocr:
            tmp2 = tmp.parent / (tmp.stem + "_ocr.pdf")
            ok, msg = try_ocrmypdf(tmp, tmp2)
            if ok:
                tmp = tmp2
            else:
                note += f" OCR skipped: {msg}"

        # Move tmp -> final
        ensure_dir(out_dir)
        shutil.move(tmp, out_path)

        size_after = out_path.stat().st_size
        saved = max(0, size_before - size_after)
        ratio = (saved / size_before * 100) if size_before else 0.0
        return {
            "file": str(pdf_path),
            "output": str(out_path),
            "before": size_before,
            "after": size_after,
            "saved_bytes": saved,
            "saved_pct": round(ratio, 1),
            "status": status,
            "note": note,
            "backup": str(backup_path) if backup_path else ""
        }
    except Exception as e:
        # rollback if overwrite
        if policy_overwrite and backup_path and pdf_path.exists():
            # keep backup; user may откатить вручную
            pass
        return {
            "file": str(pdf_path),
            "output": "",
            "before": size_before,
            "after": size_before,
            "saved_bytes": 0,
            "saved_pct": 0.0,
            "status": "ERROR",
            "note": str(e),
            "backup": str(backup_path) if backup_path else ""
        }
    finally:
        if tmp.exists():
            try: tmp.unlink()
            except: pass

# ---------- UI ----------
st.set_page_config(page_title="PDF Compressor", layout="wide")
st.title("PDF Folder Compressor")

colA, colB, colC = st.columns([2,2,1])

mode = colA.selectbox(
    "Preset",
    ["balanced", "lossless", "aggressive", "custom"],
    index=0
)

policy_overwrite = colB.radio("Write policy", ["suffix (_compressed.pdf)", "overwrite with backup"], index=0) == "overwrite with backup"
use_ocr = colC.checkbox("OCR for scans (if available)", value=False)

custom_dpi = 150
custom_jpegq = 60
if mode == "custom":
    custom_dpi = st.slider("Image DPI (color/gray)", min_value=72, max_value=300, value=150, step=10)
    custom_jpegq = st.slider("JPEG Quality (hint)", min_value=30, max_value=90, value=60, step=5)

st.divider()

source_mode = st.radio("Source type", ["Folder(s)", "Upload files"], horizontal=True)
paths = []
uploaded_files = []

if source_mode == "Folder(s)":
    st.info("Укажите путь(и) к папкам (Windows: C:\\path\\to\\dir). Можно несколько, через запятую.")
    folders_raw = st.text_input("Folders", value="")
    recursive = st.checkbox("Recurse subfolders", value=True)
    out_dir = st.text_input("Output directory (optional)", value=str(Path("output").resolve()))
    if st.button("Scan folders"):
        for p in [s.strip() for s in folders_raw.split(",") if s.strip()]:
            pth = Path(p)
            if pth.is_dir():
                if recursive:
                    paths += list(pth.rglob("*.pdf"))
                else:
                    paths += list(pth.glob("*.pdf"))
        st.success(f"Discovered {len(paths)} PDF files.")
else:
    uploaded_files = st.file_uploader("Upload PDFs", type=["pdf"], accept_multiple_files=True)
    out_dir = st.text_input("Output directory (optional)", value=str(Path("output").resolve()))

st.divider()

max_workers = st.slider("Max workers", 1, max(2, psutil.cpu_count(logical=True)), value=min(4, max(2, psutil.cpu_count(True))))
start_btn = st.button("Start compression")

results = []
if start_btn:
    ensure_dir(Path(out_dir))
    if source_mode == "Folder(s)":
        files = paths
    else:
        # Save uploaded to temp files first
        tmp_upload_dir = Path(tempfile.gettempdir()) / f"pdf_upload_{int(time.time())}"
        ensure_dir(tmp_upload_dir)
        files = []
        for uf in uploaded_files:
            p = tmp_upload_dir / uf.name
            with open(p, "wb") as f:
                f.write(uf.getbuffer())
            files.append(p)

    progress = st.progress(0)
    table_placeholder = st.empty()
    done = 0

    rows = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = []
        for f in files:
            futs.append(ex.submit(
                compress_one,
                Path(f),
                Path(out_dir) if out_dir else None,
                mode,
                policy_overwrite,
                custom_dpi,
                custom_jpegq,
                use_ocr
            ))
        for fut in as_completed(futs):
            res = fut.result()
            rows.append(res)
            done += 1
            progress.progress(done / max(1, len(files)))
            # quick table
            st.experimental_rerun() if False else None  # noop to avoid flicker

    # Final table
    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        df_display = df.copy()
        df_display["before"] = df_display["before"].apply(human_size)
        df_display["after"] = df_display["after"].apply(human_size)
        st.subheader("Results")
        st.dataframe(df_display, use_container_width=True)

        # Report save
        ensure_dir(Path("reports"))
        report_path = Path("reports") / f"report_{int(time.time())}.csv"
        df.to_csv(report_path, index=False)
        st.success(f"Report saved: {report_path}")

print("This is the placeholder for the Streamlit PDF Compressor app. Please paste the full code here.")
