from contextlib import contextmanager
import os
import re
from dataclasses import dataclass
import logging
import shutil
import subprocess
import tempfile

import yt_dlp
from yt_dlp.utils import DownloadError

from app.config import settings

logger = logging.getLogger(__name__)


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


def _build_ydlp_base_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
    }


@contextmanager
def _ydlp_opts(extra: dict | None = None):
    opts = _build_ydlp_base_opts()
    runtime_cookie_file = None
    cookie_file = settings.youtube_cookie_file.strip()
    try:
        if cookie_file:
            if not os.path.isfile(cookie_file):
                raise ValueError(
                    f"YOUTUBE_COOKIE_FILE does not exist inside the container: {cookie_file}"
                )
            fd, runtime_cookie_file = tempfile.mkstemp(prefix="yt-cookies-", suffix=".txt")
            os.close(fd)
            shutil.copyfile(cookie_file, runtime_cookie_file)
            os.chmod(runtime_cookie_file, 0o600)
            opts["cookiefile"] = runtime_cookie_file

        if extra:
            opts.update(extra)
        yield opts
    finally:
        if runtime_cookie_file and os.path.exists(runtime_cookie_file):
            try:
                os.remove(runtime_cookie_file)
            except OSError:
                logger.warning("Failed to delete temporary cookie file: %s", runtime_cookie_file)


def _normalize_yt_error(exc: Exception) -> Exception:
    message = str(exc)
    lower = message.lower()
    needs_cookies = (
        "sign in to confirm you" in lower
        or "--cookies-from-browser or --cookies" in lower
    )
    if needs_cookies:
        if settings.youtube_cookie_file.strip():
            return ValueError(
                "YouTube rejected the configured cookie file. Export a fresh "
                "cookies.txt, copy it to the VM, and restart the app."
            )
        return ValueError(
            "YouTube requested a logged-in session. Copy a Netscape-format "
            "cookies.txt file to the server and set YOUTUBE_COOKIE_FILE "
            "(for example /app/secrets/youtube-cookies.txt)."
        )
    return exc


def _is_youtube_auth_challenge(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "sign in to confirm you" in message
        or "--cookies-from-browser or --cookies" in message
    )


def fetch_metadata(url: str) -> VideoMetadata:
    if not validate_url(url):
        raise ValueError("Invalid YouTube URL")

    try:
        with _ydlp_opts({
            "skip_download": True,
            "ignore_no_formats_error": True,
        }) as opts:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise _normalize_yt_error(exc) from exc

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

    try:
        with _ydlp_opts({
            "format": "bestaudio/best",
            "outtmpl": output_template,
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
        }) as opts:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_id = info["id"]
    except DownloadError as exc:
        if _is_youtube_auth_challenge(exc):
            logger.warning(
                "yt-dlp Python API hit YouTube auth challenge; falling back to CLI"
            )
            return _download_audio_with_cli(url, output_dir)
        raise _normalize_yt_error(exc) from exc

    wav_path = os.path.join(output_dir, f"{video_id}.wav")
    if not os.path.exists(wav_path):
        raise RuntimeError(f"Audio download failed: {wav_path} not found")
    return wav_path


def _download_audio_with_cli(url: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    raw_template = os.path.join(output_dir, "source.%(ext)s")
    wav_path = os.path.join(output_dir, "audio.wav")

    with _ydlp_opts() as opts:
        cmd = [
            "python",
            "-m",
            "yt_dlp",
            "--no-warnings",
            "--no-progress",
            "-f",
            "bestaudio/best",
            "-o",
            raw_template,
        ]
        cookie_file = opts.get("cookiefile")
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
        cmd.append(url)

        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise _normalize_yt_error(RuntimeError(message))

    source_candidates = [
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if os.path.isfile(os.path.join(output_dir, name))
        and name != os.path.basename(wav_path)
        and not name.endswith(".part")
    ]
    if not source_candidates:
        raise RuntimeError("yt-dlp CLI download completed but no audio file was found")

    source_path = max(source_candidates, key=os.path.getmtime)
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        source_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        wav_path,
    ]
    ffmpeg_result = subprocess.run(
        ffmpeg_cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if ffmpeg_result.returncode != 0:
        message = (ffmpeg_result.stderr or ffmpeg_result.stdout).strip()
        raise RuntimeError(f"ffmpeg audio conversion failed: {message}")

    try:
        os.remove(source_path)
    except OSError:
        logger.warning("Failed to delete temporary source audio: %s", source_path)

    if not os.path.exists(wav_path):
        raise RuntimeError("ffmpeg completed but WAV output was not created")
    return wav_path
