import threading
from dataclasses import dataclass

from faster_whisper import WhisperModel

from app.config import settings


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

    @property
    def start_str(self) -> str:
        return _format_time(self.start)

    @property
    def end_str(self) -> str:
        return _format_time(self.end)


@dataclass
class TranscriptResult:
    text: str
    segments: list[TranscriptSegment]
    language: str


def _format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


_model = None
_model_lock = threading.Lock()


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = WhisperModel(
                    settings.whisper_model,
                    device="cpu",
                    compute_type=settings.whisper_compute_type,
                )
    return _model


def transcribe(audio_path: str, progress_callback=None) -> TranscriptResult:
    """Transcribe audio file. progress_callback(segments_done, total_estimate) is called per segment."""
    model = _get_model()
    raw_segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        vad_filter=True,
    )

    segments = []
    text_parts = []
    duration = info.duration
    # Estimate total segments based on duration (~1 segment per 3-5 seconds of speech)
    estimated_total = max(int(duration / 4), 1)

    for seg in raw_segments:
        segment = TranscriptSegment(start=seg.start, end=seg.end, text=seg.text.strip())
        segments.append(segment)
        text_parts.append(segment.text)
        if progress_callback:
            progress_callback(len(segments), estimated_total)

    return TranscriptResult(
        text=" ".join(text_parts),
        segments=segments,
        language=info.language,
    )
