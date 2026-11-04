"""Editor IA — servidor web.

Local:   APP_PASSWORD=minhasenha uvicorn app:app --reload   → http://localhost:8000
Nuvem:   veja o README (Dockerfile pronto).

Variáveis de ambiente:
  ANTHROPIC_API_KEY  chave da API do Claude (obrigatória)
  APP_PASSWORD       senha de acesso ao app (obrigatória quando publicado na internet)
  DATA_DIR           onde guardar projetos (padrão ./projetos; na nuvem use um volume persistente, ex. /data)
  MAX_PARALLEL_JOBS  quantos vídeos processar ao mesmo tempo (padrão 1)
  WHISPER_MODEL      modelo de transcrição: tiny/base/small/medium (padrão small)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from editor.pipeline import Project
from editor.plan import EditPlan

BASE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "projetos"))
DATA.mkdir(parents=True, exist_ok=True)
PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_FILE = DATA / ".secret"
if not SECRET_FILE.exists():
    SECRET_FILE.write_text(secrets.token_hex(32))
SECRET = os.environ.get("APP_SECRET") or SECRET_FILE.read_text().strip()
COOKIE = "editoria_sess"
SLOTS = threading.Semaphore(int(os.environ.get("MAX_PARALLEL_JOBS", "1")))
BUSY = ("na fila", "analisando", "planejando", "renderizando")

app = FastAPI(title="Editor IA")
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


# ------------------------------------------------------------------ autenticação

def _token() -> str:
    return hmac.new(SECRET.encode(), b"ok:" + PASSWORD.encode(), hashlib.sha256).hexdigest()


@app.middleware("http")
async def auth(request: Request, call_next):
    open_paths = ("/login", "/healthz", "/manifest.webmanifest", "/icon.svg")
    if PASSWORD and request.url.path not in open_paths:
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), _token()):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "não autenticado"}, status_code=401)
            return RedirectResponse("/login")
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return (BASE / "static" / "login.html").read_text(encoding="utf-8")


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    time.sleep(0.5)  # freia tentativas de adivinhar a senha
    if not PASSWORD or not hmac.compare_digest(password, PASSWORD):
        return RedirectResponse("/login?erro=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, _token(), max_age=60 * 60 * 24 * 60, httponly=True, samesite="lax",
                    secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login")
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------------------------------------------ estado dos jobs (persistido em disco)

def _status_path(pid: str) -> Path:
    return DATA / pid / "status.json"


def _set(pid: str, **kw):
    with LOCK:
        job = JOBS.setdefault(pid, {"status": "pronto", "log": [], "progress": 0.0, "error": None})
        job.update(kw)
        _status_path(pid).write_text(json.dumps(job, ensure_ascii=False))


def _job(pid: str) -> dict:
    if not (DATA / pid / "project.json").exists() or "/" in pid or ".." in pid:
        raise HTTPException(404, "Projeto não encontrado")
    if pid not in JOBS:
        p = _status_path(pid)
        job = json.loads(p.read_text()) if p.exists() else {"status": "pronto", "log": [], "progress": 1.0}
        if job.get("status") in BUSY:  # servidor reiniciou no meio do processamento
            job.update(status="erro", error="O servidor reiniciou durante o processamento. Tente de novo.")
        JOBS[pid] = job
    return JOBS[pid]


def _run(pid: str, steps: list[str], feedback: str | None = None):
    proj = Project(DATA / pid)

    def log(m):
        JOBS[pid]["log"].append(str(m))
        JOBS[pid]["log"] = JOBS[pid]["log"][-300:]
        _set(pid)

    with SLOTS:  # fila: limita quantos vídeos processam ao mesmo tempo
        try:
            if "analyze" in steps:
                _set(pid, status="analisando", progress=0.03)
                proj.analyze(log=log)
            if "plan" in steps:
                _set(pid, status="planejando", progress=0.15)
                proj.make_plan(feedback=feedback, log=log)
            if "render" in steps:
                _set(pid, status="renderizando", progress=0.2)
                proj.render(log=log, progress=lambda f: _set(pid, progress=0.2 + 0.8 * f))
                shutil.rmtree(DATA / pid / "_render", ignore_errors=True)
            _set(pid, status="pronto", progress=1.0, finished_at=time.time())
        except Exception as e:
            log(traceback.format_exc())
            _set(pid, status="erro", error=str(e))


def _start(pid: str, steps: list[str], feedback: str | None = None):
    job = _job(pid)
    if job["status"] in BUSY:
        raise HTTPException(409, "Este projeto já está processando")
    _set(pid, status="na fila", error=None, progress=0.0)
    threading.Thread(target=_run, args=(pid, steps, feedback), daemon=True).start()


# ------------------------------------------------------------------ páginas

@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "Editor IA", "short_name": "Editor IA", "start_url": "/", "display": "standalone",
        "background_color": "#0f1115", "theme_color": "#0f1115",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }, media_type="application/manifest+json")


@app.get("/icon.svg")
def icon():
    return FileResponse(BASE / "static" / "icon.svg", media_type="image/svg+xml")


# ------------------------------------------------------------------ API

@app.get("/api/projects")
def list_projects():
    items = []
    for d in DATA.iterdir():
        if not (d / "project.json").exists():
            continue
        meta = json.loads((d / "project.json").read_text())
        st = _job(d.name)
        plan = d / "plan.json"
        title = json.loads(plan.read_text()).get("title") if plan.exists() else None
        items.append({
            "id": d.name, "title": title or (meta.get("settings", {}).get("brief") or "Sem título")[:60],
            "status": st["status"], "aspect": meta.get("settings", {}).get("aspect"),
            "created": meta.get("created", d.stat().st_mtime),
            "has_video": (d / "video_final.mp4").exists(),
        })
    return sorted(items, key=lambda x: x["created"], reverse=True)


async def _save_upload(f: UploadFile, dst: Path):
    with dst.open("wb") as out:  # grava em blocos: vídeos de celular podem ter vários GB
        while chunk := await f.read(4 * 1024 * 1024):
            out.write(chunk)


@app.post("/api/projects")
async def create_project(
    clips: list[UploadFile] = File(...),
    music: UploadFile | None = File(None),
    brief: str = Form(""),
    aspect: str = Form("9:16"),
    target_seconds: float | None = Form(None),
    captions: bool = Form(True),
    language: str = Form("pt"),
):
    pid = uuid.uuid4().hex[:10]
    root = DATA / pid
    up = root / "uploads"
    up.mkdir(parents=True)
    paths = []
    for i, f in enumerate(clips):
        name = Path(f.filename or "clip.mp4").name.replace(" ", "_")
        dst = up / f"{i + 1:02d}_{name}"
        await _save_upload(f, dst)
        paths.append(dst)
    music_path = None
    if music is not None and music.filename:
        music_path = up / ("music" + Path(music.filename).suffix)
        await _save_upload(music, music_path)
    proj = Project(root)
    proj.add_inputs(paths, music_path, {
        "brief": brief, "aspect": aspect, "target_seconds": target_seconds or None,
        "captions": captions, "transcribe": True, "language": language,
    }, move=True)
    proj.meta["created"] = time.time()
    proj.save()
    shutil.rmtree(up, ignore_errors=True)
    _set(pid, status="pronto", log=[], progress=0.0)
    _start(pid, ["analyze", "plan", "render"])
    return {"id": pid}


@app.get("/api/projects/{pid}")
def status(pid: str):
    job = _job(pid)
    proj = Project(DATA / pid)
    plan = proj.plan
    return {
        **{k: v for k, v in job.items() if k != "log"},
        "log": job.get("log", [])[-40:],
        "settings": proj.meta.get("settings"),
        "clips": [{"id": c.id, "filename": c.filename, "duration": c.duration} for c in proj.clips],
        "plan": plan.model_dump() if plan else None,
        "has_video": proj.output.exists() and job["status"] == "pronto",
        "video_version": int(proj.output.stat().st_mtime) if proj.output.exists() else 0,
    }


class Revise(BaseModel):
    feedback: str


@app.post("/api/projects/{pid}/revise")
def revise(pid: str, body: Revise):
    _start(pid, ["plan", "render"], feedback=body.feedback)
    return {"ok": True}


@app.put("/api/projects/{pid}/plan")
def put_plan(pid: str, plan: EditPlan):
    _job(pid)
    Project(DATA / pid).apply_manual_plan(plan)
    _start(pid, ["render"])
    return {"ok": True}


@app.get("/api/projects/{pid}/video")
def video(pid: str, download: int = 0):
    _job(pid)
    out = Project(DATA / pid).output
    if not out.exists():
        raise HTTPException(404, "Vídeo ainda não renderizado")
    return FileResponse(out, media_type="video/mp4",
                        filename=f"editado_{pid}.mp4" if download else None)


@app.get("/api/projects/{pid}/thumb")
def thumb(pid: str):
    _job(pid)
    frames = sorted((DATA / pid / "analysis" / "frames").glob("C1_*.jpg"))
    if not frames:
        raise HTTPException(404)
    return FileResponse(frames[0], media_type="image/jpeg")


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    if _job(pid)["status"] in BUSY:
        raise HTTPException(409, "Aguarde terminar o processamento")
    shutil.rmtree(DATA / pid)
    JOBS.pop(pid, None)
    return {"ok": True}
