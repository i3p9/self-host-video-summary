import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models import Job, JobStatus, jobs, create_job, get_job
from app.services import youtube
from app.pipeline import process_job
from app import storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

JOB_MAX_AGE = 86400
SSE_TIMEOUT = 7200

# Auth: cookie token is sha256(password + salt)
_AUTH_COOKIE = "vs_auth"
_USER_COOKIE = "vs_user"
_AUTH_SALT = "video-summarize-auth"


def _make_token(password: str) -> str:
    return hashlib.sha256(f"{password}:{_AUTH_SALT}".encode()).hexdigest()


# Rate limiter: per-IP request counts
_rate_buckets: dict[str, list[float]] = defaultdict(list)

# Login ban: permanent after 5 failed attempts
_MAX_LOGIN_ATTEMPTS = 5
_login_failures: dict[str, int] = defaultdict(int)
_banned_ips: set[str] = set()


def _is_banned(ip: str) -> bool:
    return ip in _banned_ips


def _record_failure(ip: str):
    _login_failures[ip] += 1
    if _login_failures[ip] >= _MAX_LOGIN_ATTEMPTS:
        _banned_ips.add(ip)
        logger.warning("IP %s permanently banned after %d failed attempts", ip, _login_failures[ip])


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    bucket = _rate_buckets[ip]
    # Prune old entries
    _rate_buckets[ip] = [t for t in bucket if now - t < 60]
    if len(_rate_buckets[ip]) >= settings.rate_limit:
        return False
    _rate_buckets[ip].append(now)
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="Video Summarize", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# Auth middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not settings.auth_password:
        return await call_next(request)

    path = request.url.path
    if path in ("/login",) or path.startswith("/static"):
        return await call_next(request)

    # Block banned IPs from everything
    ip = request.client.host
    if _is_banned(ip):
        return HTMLResponse("<h1>BANNED</h1><p>Too many failed login attempts. Try again later.</p>", status_code=403)

    token = request.cookies.get(_AUTH_COOKIE)
    if token != _make_token(settings.auth_password):
        return RedirectResponse(url="/login", status_code=303)

    return await call_next(request)


async def _cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        expired = [
            jid
            for jid, j in jobs.items()
            if now - j.created_at > JOB_MAX_AGE
            and j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
        ]
        for jid in expired:
            del jobs[jid]
        if expired:
            logger.info("Cleaned up %d expired jobs", len(expired))


# --- Auth routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = request.client.host
    if _is_banned(ip):
        return HTMLResponse("<h1>BANNED</h1><p>Too many failed login attempts. Try again later.</p>", status_code=403)
    if password != settings.auth_password:
        _record_failure(ip)
        remaining = _MAX_LOGIN_ATTEMPTS - _login_failures[ip]
        logger.warning("Failed login from %s (%d attempts left)", ip, max(remaining, 0))
        return RedirectResponse(url="/login?error=1", status_code=303)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        _AUTH_COOKIE,
        _make_token(password),
        httponly=True,
        samesite="strict",
        max_age=86400 * 30,
    )
    response.set_cookie(
        _USER_COOKIE,
        username.strip(),
        samesite="strict",
        max_age=86400 * 30,
    )
    return response


# --- App routes ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    history = await asyncio.to_thread(storage.list_jobs)
    # Find any active (in-progress) jobs
    active = [
        j for j in jobs.values()
        if j.status not in (JobStatus.COMPLETED, JobStatus.FAILED)
    ]
    return templates.TemplateResponse("index.html", {"request": request, "history": history, "active": active})


@app.get("/api/status")
async def api_status():
    if settings.summarizer != "ollama":
        return JSONResponse({"ok": True, "summarizer": settings.summarizer})

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.ollama_base_url}/api/tags", timeout=5
            )
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]

        model = settings.ollama_model
        model_ready = any(
            m == model or m == f"{model}:latest" or m.startswith(f"{model}:")
            for m in models
        )
        if model_ready:
            return JSONResponse({"ok": True, "model": model})
        return JSONResponse(
            {"ok": False, "error": f"Model '{model}' not pulled. Run: docker compose exec ollama ollama pull {model}"},
            status_code=503,
        )
    except httpx.ConnectError:
        return JSONResponse(
            {"ok": False, "error": "Ollama is not reachable. Is it running?"},
            status_code=503,
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"Status check failed: {e}"},
            status_code=503,
        )


@app.post("/api/metadata", response_class=HTMLResponse)
async def api_metadata(request: Request, url: str = Form(...)):
    ip = request.client.host
    if not _check_rate_limit(ip):
        return HTMLResponse(
            '<div class="text-hot text-sm mt-2 font-bold">!! RATE LIMITED. WAIT A MINUTE.</div>',
            status_code=429,
        )

    try:
        metadata = await asyncio.to_thread(youtube.fetch_metadata, url)
    except ValueError as e:
        return HTMLResponse(
            f'<div class="text-hot text-sm mt-2 font-bold">!! {e}</div>', status_code=400
        )
    except Exception:
        logger.exception("Metadata fetch failed")
        return HTMLResponse(
            '<div class="text-hot text-sm mt-2 font-bold">!! FAILED TO FETCH VIDEO INFO.</div>',
            status_code=500,
        )
    return templates.TemplateResponse(
        "_metadata.html", {"request": request, "meta": metadata, "url": url}
    )


@app.post("/api/jobs")
async def api_create_job(request: Request, url: str = Form(...)):
    ip = request.client.host
    if not _check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Rate limited")

    if not youtube.validate_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    job = create_job(url)
    job.created_by = request.cookies.get(_USER_COOKIE, "")

    try:
        job.status = JobStatus.FETCHING_METADATA
        metadata = await asyncio.to_thread(youtube.fetch_metadata, url)
        job.metadata = metadata
        job.status = JobStatus.CONFIRMED
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = f"Failed to fetch metadata: {e}"
        return RedirectResponse(url=f"/result/{job.id}", status_code=303)

    asyncio.create_task(process_job(job))

    return RedirectResponse(url=f"/processing/{job.id}", status_code=303)


@app.get("/processing/{job_id}", response_class=HTMLResponse)
async def processing_page(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == JobStatus.COMPLETED:
        return RedirectResponse(url=f"/result/{job_id}", status_code=303)
    if job.status == JobStatus.FAILED:
        return RedirectResponse(url=f"/result/{job_id}", status_code=303)
    return templates.TemplateResponse(
        "processing.html", {"request": request, "job": job}
    )


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        started = time.time()
        last_status = None
        last_progress = -1
        while True:
            if time.time() - started > SSE_TIMEOUT:
                return
            if job.status != last_status or job.progress != last_progress:
                last_status = job.status
                last_progress = job.progress
                data = json.dumps(
                    {
                        "status": job.status.value,
                        "progress": job.progress,
                        "stage_detail": job.stage_detail,
                        "error": job.error,
                    }
                )
                yield f"data: {data}\n\n"

                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/result/{job_id}", response_class=HTMLResponse)
async def result_page(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        job = await asyncio.to_thread(storage.load_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse("result.html", {"request": request, "job": job})
