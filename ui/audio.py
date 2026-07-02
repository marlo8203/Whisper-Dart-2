"""Audio file storage and probing helpers."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ui.constants import RECORDING_STORAGE
from ui.logging_setup import get_logger

log = get_logger(__name__)

RECORDING_STORAGE.mkdir(exist_ok=True)

# Container extensions we treat as video; audio is extracted from these before
# it reaches the transcription pipeline / audio player.
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".mpg", ".mpeg", ".wmv", ".flv"}


def is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def extract_audio(video_path: str) -> str:
    """Extract the audio track of a video file into a temp .m4a and return its
    path. Tries a fast stream-copy first (works when the track is already AAC,
    as in most MP4s); falls back to re-encoding to AAC otherwise.

    Written to the system temp dir, which Gradio serves without extra config.
    Raises RuntimeError if ffmpeg can't produce audio (e.g. no audio track).
    """
    src = Path(video_path)
    out_dir = Path(tempfile.mkdtemp(prefix="whisper-dart-"))
    dest = out_dir / f"{src.stem}.m4a"

    copy_cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-acodec", "copy", str(dest)]
    encode_cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-acodec", "aac", "-b:a", "192k", str(dest)]

    for strategy, cmd in (("stream-copy", copy_cmd), ("re-encode", encode_cmd)):
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            stderr = getattr(exc, "stderr", "") or ""
            log.warning("ffmpeg %s failed for %s: %s", strategy, src.name, stderr.strip()[-500:])
            continue
        if dest.exists() and dest.stat().st_size > 0:
            log.debug("Extracted audio via %s: %s", strategy, dest)
            return str(dest)

    raise RuntimeError(f"Could not extract audio from {src.name} (no audio track?)")


def store(entry_id: str, source_path: str) -> str:
    """Transcode `source_path` into RECORDING_STORAGE as `{entry_id}.flac`.

    All stored audio is normalised to lossless FLAC — uniform, compact, and
    (unlike WAV) roughly half the size with no quality loss. Returns the
    absolute path to the stored copy.

    If the source is already inside RECORDING_STORAGE (e.g. on a load → change
    cycle), it's returned unchanged so the original is not re-transcoded. If
    ffmpeg is unavailable or fails, falls back to a plain copy so a recording
    is never lost.
    """
    src = Path(source_path)
    if not src.exists():
        return ""
    if src.parent.resolve() == RECORDING_STORAGE.resolve():
        return str(src)

    dest = RECORDING_STORAGE / f"{entry_id}.flac"
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "flac", str(dest)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
        if dest.exists() and dest.stat().st_size > 0:
            log.info("Stored recording as FLAC: %s (from %s)", dest.name, src.name)
            return str(dest)
        log.warning("ffmpeg produced no FLAC for %s — falling back to copy", src.name)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        stderr = getattr(exc, "stderr", "") or str(exc)
        log.warning("FLAC transcode failed for %s (%s) — falling back to copy",
                    src.name, stderr.strip()[-300:])

    # Fallback: keep the original bytes rather than lose the recording.
    fallback = RECORDING_STORAGE / f"{entry_id}{src.suffix}"
    shutil.copyfile(src, fallback)
    return str(fallback)


def delete_stored(audio_path: str) -> None:
    """Delete a stored audio file. Silently no-ops if the path is empty,
    outside RECORDING_STORAGE, or already gone."""
    if not audio_path:
        return
    p = Path(audio_path)
    try:
        if p.resolve().parent == RECORDING_STORAGE.resolve() and p.exists():
            p.unlink()
    except OSError:
        pass


def duration_seconds(path: str) -> float | None:
    """Return audio duration in seconds via ffprobe, or None on failure.

    Used to compute the live transcription progress percentage. ffprobe
    ships with the ffmpeg dependency Whisper already requires.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        d = float(out.stdout.strip())
        return d if d > 0 else None
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return None
