import asyncio
import logging
import os

from app.config import settings
from app.models import Job, JobStatus
from app.services import youtube, transcriber
from app.services.summarizer import get_summarizer

logger = logging.getLogger(__name__)


async def process_job(job: Job) -> None:
    """Run the full pipeline: download → transcribe → summarize."""
    audio_path = None
    try:
        # Stage 1: Download audio
        job.status = JobStatus.DOWNLOADING
        job.progress = 0
        job.stage_detail = "Downloading audio..."

        output_dir = os.path.join(settings.data_dir, job.id)
        audio_path = await asyncio.to_thread(
            youtube.download_audio, job.url, output_dir
        )
        job.progress = 100
        job.stage_detail = "Download complete"

        # Stage 2: Transcribe
        job.status = JobStatus.TRANSCRIBING
        job.progress = 0
        job.stage_detail = "Loading transcription model..."

        def on_progress(done: int, total: int) -> None:
            job.progress = min(int(done / total * 100), 99)
            job.stage_detail = f"Transcribing... ({done} segments)"

        result = await asyncio.to_thread(
            transcriber.transcribe, audio_path, on_progress
        )
        job.transcript_text = result.text
        job.transcript_segments = result.segments
        job.transcript_language = result.language
        job.progress = 100
        job.stage_detail = "Transcription complete"

        # Clean up audio file
        try:
            os.remove(audio_path)
            audio_path = None
            # Remove the job directory if empty
            job_dir = os.path.join(settings.data_dir, job.id)
            if os.path.isdir(job_dir) and not os.listdir(job_dir):
                os.rmdir(job_dir)
        except OSError:
            pass

        # Stage 3: Summarize
        job.status = JobStatus.SUMMARIZING
        job.progress = 50
        job.stage_detail = "Generating summary..."

        summarizer = get_summarizer()
        title = job.metadata.title if job.metadata else "Unknown"
        job.summary = await asyncio.to_thread(
            summarizer.summarize, job.transcript_text, title
        )
        job.progress = 100
        job.stage_detail = "Summary complete"

        # Done
        job.status = JobStatus.COMPLETED

    except Exception as e:
        logger.exception("Job %s failed", job.id)
        job.status = JobStatus.FAILED
        job.error = str(e)
        # Clean up on failure
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass
