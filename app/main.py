import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models import Job, JobStatus, jobs, create_job, get_job
from app.services import youtube
from app.pipeline import process_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Periodic cleanup of old jobs (>24h)
JOB_MAX_AGE = 86400
SSE_TIMEOUT = 7200  # 2 hours max for SSE connections


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="Video Summarize", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/metadata", response_class=HTMLResponse)
async def api_metadata(request: Request, url: str = Form(...)):
    try:
        metadata = await asyncio.to_thread(youtube.fetch_metadata, url)
    except ValueError as e:
        return HTMLResponse(
            f'<div class="text-red-400 text-sm mt-2">{e}</div>', status_code=400
        )
    except Exception:
        logger.exception("Metadata fetch failed")
        return HTMLResponse(
            '<div class="text-red-400 text-sm mt-2">Failed to fetch video info. Please check the URL and try again.</div>',
            status_code=500,
        )
    return templates.TemplateResponse(
        "_metadata.html", {"request": request, "meta": metadata, "url": url}
    )


@app.post("/api/jobs")
async def api_create_job(url: str = Form(...)):
    if not youtube.validate_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    job = create_job(url)

    # Fetch metadata for the job so we have the title for summarization
    try:
        job.status = JobStatus.FETCHING_METADATA
        metadata = await asyncio.to_thread(youtube.fetch_metadata, url)
        job.metadata = metadata
        job.status = JobStatus.CONFIRMED
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = f"Failed to fetch metadata: {e}"
        return RedirectResponse(url=f"/result/{job.id}", status_code=303)

    # Start processing in background
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
        # Always send current state immediately on connection
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
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse("result.html", {"request": request, "job": job})
