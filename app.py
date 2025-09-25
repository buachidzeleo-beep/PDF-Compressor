
import os, shutil, subprocess, tempfile, time, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st

# ---------------- Utilities ----------------
def human_size(b):
    try: b = float(b)
    except: return "0 B"
    for u in ["B","KB","MB","GB","TB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def normalize_paths(raw: str):
    # Split by comma, strip quotes/spaces, expand user, resolve
    items = []
    for s in [x.strip() for x in raw.split(",") if x.strip()]:
        s = s.strip('"').strip("'")
        p = Path(os.path.expanduser(s))
        items.append(p)
    return items

def scan_pdfs(paths, recursive=True):
    found = []
    errors = []
    for p in paths:
        try:
            if p.is_file() and p.suffix.lower() == ".pdf":
                found.append(p.resolve())
            elif p.is_dir():
                if recursive:
                    for root, _, files in os.walk(p):
                        for fn in files:
                            if fn.lower().endswith(".pdf"):
                                found.append(Path(root) / fn)
                else:
                    for fn in os.listdir(p):
                        if fn.lower().endswith(".pdf"):
                            found.append(p / fn)
            else:
                errors.append(f"Path not found or not accessible: {p}")
        except Exception as e:
            errors.append(f"{p}: {e}")
    # Deduplicate
    uniq = []
    seen = set()
    for f in found:
        rp = str(Path(f).resolve())
        if rp not in seen:
            uniq.append(Path(rp))
            seen.add(rp)
    return uniq, errors

def has_ghostscript():
    try:
        cmd = ["gswin64c" if os.name == "nt" else "gs", "-v"]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception:
        return False

# Compression backends
try:
    import pikepdf
except Exception:
    pikepdf = None

def ghostscript_compress(src: Path, dst: Path, preset: str, dpi: int = 150):
    gs = "gswin64c" if os.name == "nt" else "gs"
    if preset == "lossless":
        cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
               "-dPDFSETTINGS=/default", "-dDetectDuplicateImages=true",
               "-dNOPAUSE","-dQUIET","-dBATCH", f"-sOutputFile={str(dst)}", str(src)]
    elif preset == "balanced":
        cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
               "-dPDFSETTINGS=/printer", "-dDetectDuplicateImages=true",
               "-dNOPAUSE","-dQUIET","-dBATCH", f"-sOutputFile={str(dst)}", str(src)]
    elif preset == "aggressive":
        cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
               "-dPDFSETTINGS=/screen", "-dDetectDuplicateImages=true",
               "-dNOPAUSE","-dQUIET","-dBATCH", f"-sOutputFile={str(dst)}", str(src)]
    else:
        cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
               "-dDetectDuplicateImages=true",
               f"-dColorImageResolution={dpi}", f"-dGrayImageResolution={dpi}",
               "-dNOPAUSE","-dQUIET","-dBATCH", f"-sOutputFile={str(dst)}", str(src)]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

def pikepdf_optimize(src: Path, dst: Path):
    if not pikepdf: raise RuntimeError("pikepdf not installed")
    with pikepdf.open(str(src)) as pdf:
        pdf.save(str(dst), optimize_streams=True, compress_streams=True, linearize=True)

def compress_one(pdf_path: Path, out_dir: Path, mode: str, overwrite: bool, custom_dpi: int):
    out_dir = out_dir if out_dir else pdf_path.parent
    out_path = (pdf_path if overwrite else out_dir / (pdf_path.stem + "_compressed.pdf"))
    backup_path = None
    if overwrite:
        ensure_dir(Path("backups"))
        backup_path = Path("backups") / f"{pdf_path.stem}_{int(time.time())}.pdf"
        shutil.copy2(pdf_path, backup_path)
    tmp = Path(tempfile.gettempdir()) / f"tmp_{pdf_path.stem}_{time.time()}.pdf"
    size_before = pdf_path.stat().st_size
    status, note = "OK", ""
    try:
        if has_ghostscript():
            ghostscript_compress(pdf_path, tmp, preset=mode, dpi=custom_dpi)
        else:
            pikepdf_optimize(pdf_path, tmp)
        ensure_dir(out_dir); shutil.move(tmp, out_path)
        size_after = out_path.stat().st_size
        ratio = (size_before - size_after) / size_before * 100 if size_before else 0
        return {"file": str(pdf_path), "before": size_before,"after": size_after,
                "saved_pct": round(ratio,1),"status":status,"note":note,
                "output": str(out_path),
                "backup": str(backup_path) if backup_path else ""}
    except Exception as e:
        return {"file": str(pdf_path),"before": size_before,"after": size_before,
                "saved_pct":0.0,"status":"ERROR","note":str(e),
                "output": "",
                "backup": str(backup_path) if backup_path else ""}
    finally:
        if tmp.exists(): tmp.unlink()

# ---------------- Streamlit UI ----------------
st.set_page_config(page_title="PDF Folder Compressor", layout="wide")
st.title("PDF Folder Compressor")

# Session state init
if "files_to_process" not in st.session_state:
    st.session_state["files_to_process"] = []
if "last_scan_errors" not in st.session_state:
    st.session_state["last_scan_errors"] = []
if "scan_paths" not in st.session_state:
    st.session_state["scan_paths"] = []

with st.sidebar:
    st.markdown("### Settings")
    preset = st.selectbox("Preset", ["balanced","lossless","aggressive","custom"], index=0)
    overwrite = st.radio("Write policy", ["suffix (_compressed.pdf)", "overwrite with backup"], index=0) == "overwrite with backup"
    custom_dpi = 150
    if preset=="custom":
        custom_dpi = st.slider("Custom DPI",72,300,150,10)
    max_workers = st.slider("Max workers",1,16,4,1)

st.markdown("#### Source")
source_mode = st.radio("Source type", ["Folder(s)","Upload"], horizontal=True)
out_dir_str = st.text_input("Output directory", value=str(Path("output").resolve()))
out_dir = Path(out_dir_str) if out_dir_str else None

if source_mode=="Folder(s)":
    st.info("Enter folder paths (comma-separated). Supports subfolders.")
    folders_raw = st.text_input("Folders", value="")
    recursive = st.checkbox("Include subfolders (recursive)", True)
    col1, col2 = st.columns([1,1])
    if col1.button("Scan folders", type="primary"):
        paths = normalize_paths(folders_raw)
        st.session_state["scan_paths"] = [str(p) for p in paths]
        files, errs = scan_pdfs(paths, recursive=recursive)
        st.session_state["files_to_process"] = [str(f.resolve()) for f in files]
        st.session_state["last_scan_errors"] = errs
        st.success(f"Found {len(files)} PDF(s).")
    if col2.button("Clear scan results"):
        st.session_state["files_to_process"] = []
        st.session_state["last_scan_errors"] = []
        st.session_state["scan_paths"] = []
else:
    uploaded = st.file_uploader("Upload PDFs",type=["pdf"],accept_multiple_files=True)
    if uploaded:
        tmpdir=Path(tempfile.gettempdir())/f"upload_{int(time.time())}"
        ensure_dir(tmpdir)
        files_saved = []
        for uf in uploaded:
            p=tmpdir/uf.name
            with open(p,"wb") as f: f.write(uf.getbuffer())
            files_saved.append(str(p))
        st.session_state["files_to_process"] = files_saved
        st.session_state["last_scan_errors"] = []
        st.session_state["scan_paths"] = [str(tmpdir)]

st.divider()

# Diagnostics
with st.expander("Diagnostics (paths & errors)"):
    st.write("Scan paths:", st.session_state.get("scan_paths", []))
    st.write("Files found:", len(st.session_state.get("files_to_process", [])))
    if st.session_state.get("files_to_process"):
        st.write(st.session_state["files_to_process"][:50])  # preview up to 50
    if st.session_state.get("last_scan_errors"):
        st.warning("Issues:")
        for e in st.session_state["last_scan_errors"]:
            st.write("- ", e)

start = st.button("Start compression", type="primary")
if start:
    files = [Path(p) for p in st.session_state.get("files_to_process", [])]
    if not files:
        st.warning("No files to process. Run **Scan folders** or upload PDFs.")
    else:
        # Ensure output directory exists
        ensure_dir(Path(out_dir_str))
        results=[]
        from concurrent.futures import ThreadPoolExecutor, as_completed
        total = len(files)
        progress = st.progress(0.0)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs=[ex.submit(compress_one,Path(f),out_dir,preset,overwrite,custom_dpi) for f in files]
            done = 0
            for fut in as_completed(futs):
                results.append(fut.result())
                done += 1
                progress.progress(done/total)
        import pandas as pd
        df=pd.DataFrame(results)
        if not df.empty:
            df_show = df.copy()
            df_show["before"]=df_show["before"].apply(human_size)
            df_show["after"]=df_show["after"].apply(human_size)
            st.subheader("Results")
            st.dataframe(df_show,use_container_width=True)
            ensure_dir(Path("reports"))
            report_path = Path("reports")/f"report_{int(time.time())}.csv"
            df.to_csv(report_path, index=False)
            st.success(f"Report saved: {report_path}")
        else:
            st.info("No results to show.")
else:
    st.caption("Scan or upload files first, then click **Start compression**.")
