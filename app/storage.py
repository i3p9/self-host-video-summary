import json
import os
import sqlite3
import threading

from app.config import settings
from app.models import CachedTranscript, Job, JobStatus
from app.services.youtube import VideoMetadata, extract_video_id
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
            video_id TEXT DEFAULT '',
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcript_cache (
            video_id TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            channel TEXT,
            thumbnail TEXT,
            duration INTEGER,
            upload_date TEXT,
            whisper_model TEXT NOT NULL,
            transcript_text TEXT NOT NULL,
            transcript_segments TEXT NOT NULL,
            transcript_language TEXT,
            transcribe_time REAL DEFAULT 0,
            created_at REAL,
            PRIMARY KEY (video_id, whisper_model)
        )
    """)
    # Migrate existing databases that lack new columns
    existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    migrations = {
        "video_id": "TEXT DEFAULT ''",
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

    # Backfill video IDs for older rows so transcript/result reuse works for existing history.
    rows = conn.execute(
        "SELECT id, url FROM jobs WHERE COALESCE(video_id, '') = ''"
    ).fetchall()
    for row in rows:
        video_id = extract_video_id(row["url"]) or ""
        if video_id:
            conn.execute(
                "UPDATE jobs SET video_id = ? WHERE id = ?",
                (video_id, row["id"]),
            )

    # Seed transcript cache from completed jobs that already have transcripts.
    rows = conn.execute(
        """
        SELECT video_id, url, title, channel, thumbnail, duration, upload_date,
               whisper_model, transcript_text, transcript_segments, transcript_language,
               transcribe_time, created_at
        FROM jobs
        WHERE COALESCE(video_id, '') <> ''
          AND COALESCE(whisper_model, '') <> ''
          AND COALESCE(transcript_text, '') <> ''
        ORDER BY created_at DESC
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO transcript_cache
            (video_id, url, title, channel, thumbnail, duration, upload_date,
             whisper_model, transcript_text, transcript_segments, transcript_language,
             transcribe_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["video_id"],
                row["url"],
                row["title"],
                row["channel"],
                row["thumbnail"],
                row["duration"],
                row["upload_date"],
                row["whisper_model"],
                row["transcript_text"],
                row["transcript_segments"],
                row["transcript_language"],
                row["transcribe_time"] or 0,
                row["created_at"],
            ),
        )
    conn.commit()


def save_job(job: Job):
    conn = _get_conn()
    video_id = (
        job.metadata.video_id
        if job.metadata and job.metadata.video_id
        else (extract_video_id(job.url) or "")
    )
    segments_json = json.dumps(
        [{"start": s.start, "end": s.end, "text": s.text} for s in job.transcript_segments]
    )
    conn.execute(
        """INSERT OR REPLACE INTO jobs
           (id, video_id, url, title, channel, thumbnail, duration, upload_date,
            transcript_text, transcript_segments, transcript_language, summary, created_at,
            download_time, transcribe_time, summarize_time, whisper_model, summarizer_model,
            created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job.id,
            video_id,
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


def save_transcript_cache(job: Job):
    if not job.metadata or not job.metadata.video_id:
        raise ValueError("Cannot persist transcript cache without video metadata")
    if not job.whisper_model:
        raise ValueError("Cannot persist transcript cache without whisper model")
    if not job.transcript_text:
        raise ValueError("Cannot persist empty transcript cache")

    conn = _get_conn()
    segments_json = json.dumps(
        [{"start": s.start, "end": s.end, "text": s.text} for s in job.transcript_segments]
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO transcript_cache
        (video_id, url, title, channel, thumbnail, duration, upload_date,
         whisper_model, transcript_text, transcript_segments, transcript_language,
         transcribe_time, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.metadata.video_id,
            job.url,
            job.metadata.title,
            job.metadata.channel,
            job.metadata.thumbnail,
            job.metadata.duration,
            job.metadata.upload_date,
            job.whisper_model,
            job.transcript_text,
            segments_json,
            job.transcript_language,
            job.transcribe_time,
            job.created_at,
        ),
    )
    conn.commit()


def load_transcript_cache(video_id: str, whisper_model: str) -> CachedTranscript | None:
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT *
        FROM transcript_cache
        WHERE video_id = ? AND whisper_model = ?
        """,
        (video_id, whisper_model),
    ).fetchone()
    if not row:
        return None

    segments = [
        TranscriptSegment(start=s["start"], end=s["end"], text=s["text"])
        for s in json.loads(row["transcript_segments"])
    ]
    metadata = VideoMetadata(
        video_id=row["video_id"],
        title=row["title"],
        channel=row["channel"],
        thumbnail=row["thumbnail"],
        duration=row["duration"],
        upload_date=row["upload_date"],
    )
    return CachedTranscript(
        video_id=row["video_id"],
        url=row["url"],
        metadata=metadata,
        whisper_model=row["whisper_model"],
        transcript_text=row["transcript_text"],
        transcript_segments=segments,
        transcript_language=row["transcript_language"] or "",
        transcribe_time=row["transcribe_time"] or 0,
        created_at=row["created_at"],
    )


def find_reusable_job(
    video_id: str,
    whisper_model: str,
    summarizer_model: str,
) -> Job | None:
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT *
        FROM jobs
        WHERE video_id = ?
          AND whisper_model = ?
          AND summarizer_model = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (video_id, whisper_model, summarizer_model),
    ).fetchone()
    if not row:
        return None
    return _row_to_job(row)


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
    video_id = row["video_id"] or extract_video_id(row["url"]) or row["id"]
    metadata = None
    if row["title"]:
        metadata = VideoMetadata(
            video_id=video_id,
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
