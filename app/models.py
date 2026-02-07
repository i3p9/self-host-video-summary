import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from app.services.youtube import VideoMetadata
from app.services.transcriber import TranscriptSegment


class JobStatus(str, Enum):
    PENDING = "pending"
    FETCHING_METADATA = "fetching_metadata"
    CONFIRMED = "confirmed"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    url: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: JobStatus = JobStatus.PENDING
    progress: int = 0  # 0-100
    stage_detail: str = ""
    metadata: VideoMetadata | None = None
    transcript_text: str = ""
    transcript_segments: list[TranscriptSegment] = field(default_factory=list)
    transcript_language: str = ""
    summary: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    created_by: str = ""
    # Timing (seconds)
    download_time: float = 0.0
    transcribe_time: float = 0.0
    summarize_time: float = 0.0
    # Model info
    whisper_model: str = ""
    summarizer_model: str = ""

    @property
    def total_time(self) -> float:
        return self.download_time + self.transcribe_time + self.summarize_time

    @property
    def word_count(self) -> int:
        return len(self.transcript_text.split()) if self.transcript_text else 0


# In-memory job store
jobs: dict[str, Job] = {}


def create_job(url: str) -> Job:
    job = Job(url=url)
    jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    return jobs.get(job_id)
