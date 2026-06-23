"""
Marker Studio - local backend.

Serves the UI and runs document -> markdown/json/html conversions by calling
the `marker-pdf` library. Models are loaded once at startup and reused; files
are converted one at a time on a background worker so we never fight over VRAM.

marker is imported lazily so this module can be imported (and the web server
booted) before the heavy ML stack is ready.
"""

import os
import sys
import json
import time
import queue
import shutil
import threading
import tempfile
import traceback
import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

# Keep torch / surya calm inside a single long-running process.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("IN_STREAMLIT", "true")  # stops surya spawning its own pool

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
STAGING_DIR = Path(tempfile.gettempdir()) / "marker-studio-uploads"
STAGING_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTS = {
    "pdf", "png", "jpg", "jpeg", "gif", "webp", "tiff", "bmp",
    "pptx", "docx", "xlsx", "html", "htm", "epub",
}

app = FastAPI(title="Marker Studio")


# --------------------------------------------------------------------------- #
# Model state                                                                  #
# --------------------------------------------------------------------------- #
class ModelState:
    def __init__(self) -> None:
        self.models: Optional[dict] = None
        self.loading = False
        self.error: Optional[str] = None
        self.device = "unknown"
        self.marker_version = "unknown"

    @property
    def ready(self) -> bool:
        return self.models is not None


MODELS = ModelState()


def _detect_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _load_models() -> None:
    """Load marker's model dict once. Runs on a background thread."""
    MODELS.loading = True
    MODELS.error = None
    try:
        from marker.models import create_model_dict  # heavy import
        try:
            import marker
            MODELS.marker_version = getattr(marker, "__version__", "installed")
        except Exception:
            MODELS.marker_version = "installed"
        MODELS.device = _detect_device()
        MODELS.models = create_model_dict()
    except Exception as exc:  # pragma: no cover - depends on ML stack
        MODELS.error = f"{exc}\n{traceback.format_exc()}"
    finally:
        MODELS.loading = False


# --------------------------------------------------------------------------- #
# Job queue                                                                    #
# --------------------------------------------------------------------------- #
class FileJob(BaseModel):
    id: str
    name: str
    path: str
    status: str = "queued"          # queued | converting | done | error | skipped
    message: str = ""
    output_dir: str = ""
    output_file: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class Batch:
    def __init__(self, batch_id: str, jobs: List[FileJob], settings: Dict[str, Any], output_dir: str):
        self.id = batch_id
        self.jobs = jobs
        self.settings = settings
        self.output_dir = output_dir
        self.created_at = time.time()


BATCHES: Dict[str, Batch] = {}
WORK_QUEUE: "queue.Queue[tuple[str, FileJob]]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, daemon=True).start()
        _worker_started = True


def _worker_loop() -> None:
    while True:
        batch_id, job = WORK_QUEUE.get()
        batch = BATCHES.get(batch_id)
        if batch is None:
            WORK_QUEUE.task_done()
            continue

        # Wait for models if they're still warming up.
        while not MODELS.ready:
            if MODELS.error:
                job.status = "error"
                job.message = "Models failed to load. See the terminal window."
                WORK_QUEUE.task_done()
                break
            time.sleep(0.4)
        if job.status == "error":
            continue

        job.status = "converting"
        job.started_at = time.time()
        try:
            out_dir, out_file = _convert_one(job.path, batch.settings, batch.output_dir)
            job.output_dir = out_dir
            job.output_file = out_file
            job.status = "done"
            job.message = "Converted"
        except Exception as exc:
            job.status = "error"
            job.message = str(exc)
            traceback.print_exc()
        finally:
            job.finished_at = time.time()
            try:
                import gc
                gc.collect()
            except Exception:
                pass
            WORK_QUEUE.task_done()


# --------------------------------------------------------------------------- #
# Conversion (mirrors marker.scripts.convert_single)                           #
# --------------------------------------------------------------------------- #
def _build_config(settings: Dict[str, Any], output_dir: str) -> Dict[str, Any]:
    """Translate UI settings into marker CLI-style options."""
    opts: Dict[str, Any] = {
        "output_dir": output_dir,
        "output_format": settings.get("output_format", "markdown"),
    }

    if settings.get("page_range"):
        opts["page_range"] = settings["page_range"]
    if settings.get("force_ocr"):
        opts["force_ocr"] = True
    if settings.get("strip_existing_ocr"):
        opts["strip_existing_ocr"] = True
    if settings.get("disable_image_extraction"):
        opts["disable_image_extraction"] = True
    if settings.get("disable_ocr_math"):
        opts["disable_ocr_math"] = True
    if settings.get("paginate_output"):
        opts["paginate_output"] = True

    if settings.get("use_llm"):
        opts["use_llm"] = True
        service = settings.get("llm_service", "gemini")
        service_map = {
            "gemini": "marker.services.gemini.GoogleGeminiService",
            "openai": "marker.services.openai.OpenAIService",
            "claude": "marker.services.claude.ClaudeService",
            "ollama": "marker.services.ollama.OllamaService",
        }
        opts["llm_service"] = service_map.get(service, service_map["gemini"])
        if settings.get("redo_inline_math"):
            opts["redo_inline_math"] = True

        key = (settings.get("llm_api_key") or "").strip()
        if service == "gemini" and key:
            opts["gemini_api_key"] = key
        elif service == "openai" and key:
            opts["openai_api_key"] = key
            if settings.get("openai_base_url"):
                opts["openai_base_url"] = settings["openai_base_url"]
            if settings.get("llm_model"):
                opts["openai_model"] = settings["llm_model"]
        elif service == "claude" and key:
            opts["claude_api_key"] = key
            if settings.get("llm_model"):
                opts["claude_model_name"] = settings["llm_model"]
        elif service == "ollama":
            if settings.get("ollama_base_url"):
                opts["ollama_base_url"] = settings["ollama_base_url"]
            if settings.get("llm_model"):
                opts["ollama_model"] = settings["llm_model"]
        if service == "gemini" and settings.get("llm_model"):
            opts["gemini_model_name"] = settings["llm_model"]

    return opts


def _convert_one(fpath: str, settings: Dict[str, Any], output_dir: str):
    from marker.config.parser import ConfigParser
    from marker.output import save_output

    opts = _build_config(settings, output_dir)
    config_parser = ConfigParser(opts)

    converter_cls = config_parser.get_converter_cls()  # PdfConverter handles all types
    config_dict = config_parser.generate_config_dict()
    config_dict["pdftext_workers"] = 1

    converter = converter_cls(
        config=config_dict,
        artifact_dict=MODELS.models,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )
    rendered = converter(fpath)

    out_folder = config_parser.get_output_folder(fpath)
    base = config_parser.get_base_filename(fpath)
    save_output(rendered, out_folder, base)

    # Work out the primary output file for the "open" button.
    ext_map = {"markdown": "md", "json": "json", "html": "html", "chunks": "json"}
    ext = ext_map.get(settings.get("output_format", "markdown"), "md")
    out_file = os.path.join(out_folder, f"{base}.{ext}")
    return out_folder, out_file


# --------------------------------------------------------------------------- #
# Native folder picker (runs in its own process to keep Tk off our threads)    #
# --------------------------------------------------------------------------- #
_FOLDER_PICKER_SNIPPET = (
    "import tkinter as tk;"
    "from tkinter import filedialog;"
    "r=tk.Tk();r.withdraw();r.attributes('-topmost', True);"
    "p=filedialog.askdirectory(title='Choose output folder');"
    "print(p or '')"
)


@app.post("/api/pick-folder")
def pick_folder():
    try:
        result = subprocess.run(
            [sys.executable, "-c", _FOLDER_PICKER_SNIPPET],
            capture_output=True, text=True, timeout=300,
        )
        path = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if path and os.path.isdir(path):
            return {"path": path}
        return {"path": ""}
    except Exception as exc:
        return JSONResponse(
            {"path": "", "error": f"Folder picker unavailable: {exc}. Paste a path instead."},
            status_code=200,
        )


# --------------------------------------------------------------------------- #
# API                                                                          #
# --------------------------------------------------------------------------- #
@app.get("/api/status")
def status():
    return {
        "models_ready": MODELS.ready,
        "loading": MODELS.loading,
        "error": MODELS.error,
        "device": MODELS.device if MODELS.ready else _detect_device(),
        "marker_version": MODELS.marker_version,
        "default_output": str(Path.home() / "Marker Studio Output"),
    }


@app.post("/api/upload")
async def upload(files: List[UploadFile] = File(...)):
    batch_dir = STAGING_DIR / f"in_{int(time.time()*1000)}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for f in files:
        ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
        if ext not in SUPPORTED_EXTS:
            continue
        dest = batch_dir / f.filename
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        staged.append({"name": f.filename, "path": str(dest), "ext": ext})
    if not staged:
        raise HTTPException(status_code=400, detail="No supported files in upload.")
    return {"files": staged}


class ConvertRequest(BaseModel):
    files: List[Dict[str, str]]
    output_dir: str
    settings: Dict[str, Any]


@app.post("/api/convert")
def convert(req: ConvertRequest):
    if not req.output_dir:
        raise HTTPException(status_code=400, detail="Choose an output folder first.")
    try:
        os.makedirs(req.output_dir, exist_ok=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Can't write to that folder: {exc}")

    _ensure_worker()
    batch_id = f"batch_{int(time.time()*1000)}"
    jobs: List[FileJob] = []
    for i, f in enumerate(req.files):
        jobs.append(FileJob(id=f"{batch_id}_{i}", name=f["name"], path=f["path"]))
    batch = Batch(batch_id, jobs, req.settings, req.output_dir)
    BATCHES[batch_id] = batch
    for job in jobs:
        WORK_QUEUE.put((batch_id, job))
    return {"batch_id": batch_id, "jobs": [j.model_dump() for j in jobs]}


@app.get("/api/jobs/{batch_id}")
def jobs(batch_id: str):
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Unknown batch.")
    return {
        "batch_id": batch_id,
        "output_dir": batch.output_dir,
        "jobs": [j.model_dump() for j in batch.jobs],
    }


class OpenRequest(BaseModel):
    path: str


@app.post("/api/open")
def open_path(req: OpenRequest):
    p = req.path
    if not p or not os.path.exists(p):
        raise HTTPException(status_code=404, detail="Path not found.")
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", p])
        elif sys.platform.startswith("win"):
            os.startfile(p)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", p])
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# --------------------------------------------------------------------------- #
# Static UI                                                                    #
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.on_event("startup")
def _startup():
    threading.Thread(target=_load_models, daemon=True).start()


def main():
    import uvicorn
    host = os.environ.get("MARKER_STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("MARKER_STUDIO_PORT", "8765"))
    url = f"http://{host}:{port}"

    if os.environ.get("MARKER_STUDIO_NO_BROWSER") != "1":
        def _open():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print("\n  Marker Studio is starting.")
    print(f"  Open this in your browser if it doesn't appear:  {url}")
    print("  First launch downloads the AI models (a few GB) the first time you convert.\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
