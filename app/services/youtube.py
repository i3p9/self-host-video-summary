import os
import re
from dataclasses import dataclass

import yt_dlp


@dataclass
class VideoMetadata:
    video_id: str
    title: str
    thumbnail: str
    duration: int  # seconds
    channel: str
    upload_date: str

    @property
    def duration_str(self) -> str:
        minutes, seconds = divmod(self.duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


_URL_PATTERN = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+"
)


def validate_url(url: str) -> bool:
    return bool(_URL_PATTERN.match(url))


def fetch_metadata(url: str) -> VideoMetadata:
    if not validate_url(url):
        raise ValueError("Invalid YouTube URL")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    return VideoMetadata(
        video_id=info["id"],
        title=info["title"],
        thumbnail=info.get("thumbnail", ""),
        duration=info.get("duration", 0),
        channel=info.get("channel", info.get("uploader", "Unknown")),
        upload_date=info.get("upload_date", ""),
    )


def download_audio(url: str, output_dir: str) -> str:
    """Download audio as 16kHz mono WAV. Returns the output file path."""
    if not validate_url(url):
        raise ValueError("Invalid YouTube URL")

    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        ],
        "postprocessor_args": [
            "-ar", "16000",
            "-ac", "1",
        ],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info["id"]

    wav_path = os.path.join(output_dir, f"{video_id}.wav")
    if not os.path.exists(wav_path):
        raise RuntimeError(f"Audio download failed: {wav_path} not found")
    return wav_path
