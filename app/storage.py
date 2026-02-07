import json
import os
import sqlite3
import threading

from app.config import settings
from app.models import Job, JobStatus
from app.services.youtube import VideoMetadata
from app.services.transcriber import TranscriptSegment

_db_path = os.path.join(settings.data_dir, "jobs.db")
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        os.makedirs(settings.data_dir, exist_ok=True)
        _local.conn = sqlite3.connect(_db_path)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT,
            channel TEXT,
            thumbnail TEXT,
            duration INTEGER,
            upload_date TEXT,
            transcript_text TEXT,
            transcript_segments TEXT,
            transcript_language TEXT,
            summary TEXT,
            created_at REAL,
            download_time REAL DEFAULT 0,
            transcribe_time REAL DEFAULT 0,
            summarize_time REAL DEFAULT 0,
            whisper_model TEXT DEFAULT '',
            summarizer_model TEXT DEFAULT ''
        )
    """)
    # Migrate existing databases that lack new columns
    existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    migrations = {
        "download_time": "REAL DEFAULT 0",
        "transcribe_time": "REAL DEFAULT 0",
        "summarize_time": "REAL DEFAULT 0",
        "whisper_model": "TEXT DEFAULT ''",
        "summarizer_model": "TEXT DEFAULT ''",
        "created_by": "TEXT DEFAULT ''",
    }
    for col, typedef in migrations.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
    conn.commit()


def save_job(job: Job):
    conn = _get_conn()
    segments_json = json.dumps(
        [{"start": s.start, "end": s.end, "text": s.text} for s in job.transcript_segments]
    )
    conn.execute(
        """INSERT OR REPLACE INTO jobs
           (id, url, title, channel, thumbnail, duration, upload_date,
            transcript_text, transcript_segments, transcript_language, summary, created_at,
            download_time, transcribe_time, summarize_time, whisper_model, summarizer_model,
            created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job.id,
            job.url,
            job.metadata.title if job.metadata else "",
            job.metadata.channel if job.metadata else "",
            job.metadata.thumbnail if job.metadata else "",
            job.metadata.duration if job.metadata else 0,
            job.metadata.upload_date if job.metadata else "",
            job.transcript_text,
            segments_json,
            job.transcript_language,
            job.summary,
            job.created_at,
            job.download_time,
            job.transcribe_time,
            job.summarize_time,
            job.whisper_model,
            job.summarizer_model,
            job.created_by,
        ),
    )
    conn.commit()


def load_job(job_id: str) -> Job | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    return _row_to_job(row)


def list_jobs(limit: int = 20) -> list[dict]:
    """Return recent jobs as lightweight dicts for the history list."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, channel, thumbnail, duration, created_at, created_by FROM jobs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _row_to_job(row: sqlite3.Row) -> Job:
    segments = [
        TranscriptSegment(start=s["start"], end=s["end"], text=s["text"])
        for s in json.loads(row["transcript_segments"])
    ]
    metadata = None
    if row["title"]:
        metadata = VideoMetadata(
            video_id=row["id"],
            title=row["title"],
            channel=row["channel"],
            thumbnail=row["thumbnail"],
            duration=row["duration"],
            upload_date=row["upload_date"],
        )
    job = Job(
        url=row["url"],
        id=row["id"],
        status=JobStatus.COMPLETED,
        progress=100,
        metadata=metadata,
        transcript_text=row["transcript_text"],
        transcript_segments=segments,
        transcript_language=row["transcript_language"],
        summary=row["summary"],
        created_at=row["created_at"],
        download_time=row["download_time"] or 0,
        transcribe_time=row["transcribe_time"] or 0,
        summarize_time=row["summarize_time"] or 0,
        whisper_model=row["whisper_model"] or "",
        summarizer_model=row["summarizer_model"] or "",
        created_by=row["created_by"] or "",
    )
    return job
