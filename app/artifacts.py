"""Capture artifact paths, ffmpeg conversion, and validation."""

from __future__ import annotations

import json
import math
import struct
import subprocess
import wave
from pathlib import Path

from app.config import get_settings

ARTIFACT_EXT = "mp3"
CONTENT_TYPE = "audio/mpeg"
FILENAME = f"capture.{ARTIFACT_EXT}"
PCM_SAMPLE_RATE = 48000


def artifact_path(data_dir: str, task_id: str) -> Path:
    return Path(data_dir) / "artifacts" / f"{task_id}.{ARTIFACT_EXT}"


def temp_dir(data_dir: str) -> Path:
    return Path(data_dir) / "tmp"


def temp_wav_path(data_dir: str, task_id: str) -> Path:
    return temp_dir(data_dir) / f"{task_id}.pcm.wav"


def temp_pcm_path(data_dir: str, task_id: str) -> Path:
    return temp_dir(data_dir) / f"{task_id}.pcm"


def temp_mp3_part_path(data_dir: str, task_id: str) -> Path:
    return temp_dir(data_dir) / f"{task_id}.part.{ARTIFACT_EXT}"


def ensure_artifacts_dir(data_dir: str) -> Path:
    path = Path(data_dir) / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_tmp_dir(data_dir: str) -> Path:
    path = temp_dir(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_ffmpeg(args: list[str]) -> None:
    try:
        subprocess.run(
            ["ffmpeg", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("ffmpeg not found") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise ValueError(f"ffmpeg failed: {detail}") from exc


def append_pcm_samples(path: Path, pcm_samples: list[int]) -> None:
    if not pcm_samples:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(struct.pack(f"<{len(pcm_samples)}h", *pcm_samples))


def convert_pcm_to_mp3(pcm_path: Path, mp3_path: Path) -> None:
    settings = get_settings()
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = str(mp3_path.parent.parent)
    tmp_path = temp_mp3_part_path(data_dir, mp3_path.stem)
    ensure_tmp_dir(data_dir)
    if tmp_path.exists():
        tmp_path.unlink()
    try:
        _run_ffmpeg(
            [
                "-y",
                "-f",
                "s16le",
                "-ar",
                str(PCM_SAMPLE_RATE),
                "-ac",
                "1",
                "-i",
                str(pcm_path),
                "-c:a",
                "libmp3lame",
                "-q:a",
                str(settings.FFMPEG_MP3_VBR_QUALITY),
                "-ar",
                str(settings.ARTIFACT_SAMPLE_RATE),
                str(tmp_path),
            ]
        )
        tmp_path.replace(mp3_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    settings = get_settings()
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = str(mp3_path.parent.parent)
    tmp_path = temp_mp3_part_path(data_dir, mp3_path.stem)
    ensure_tmp_dir(data_dir)
    if tmp_path.exists():
        tmp_path.unlink()
    try:
        _run_ffmpeg(
            [
                "-y",
                "-i",
                str(wav_path),
                "-c:a",
                "libmp3lame",
                "-q:a",
                str(settings.FFMPEG_MP3_VBR_QUALITY),
                "-ar",
                str(settings.ARTIFACT_SAMPLE_RATE),
                str(tmp_path),
            ]
        )
        tmp_path.replace(mp3_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _ffprobe_json(path: Path) -> dict:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,sample_rate:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("ffprobe not found") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise ValueError(f"ffprobe failed: {detail}") from exc
    return json.loads(completed.stdout or "{}")


def validate_artifact(path: Path) -> tuple[int, float]:
    settings = get_settings()
    if not path.is_file():
        raise ValueError("artifact missing")
    size = path.stat().st_size
    if size < 128:
        raise ValueError("artifact too small")

    probe = _ffprobe_json(path)
    stream = (probe.get("streams") or [{}])[0]
    codec = str(stream.get("codec_name") or "")
    if codec != "mp3":
        raise ValueError("expected mp3")

    sample_rate = int(stream.get("sample_rate") or 0)
    if sample_rate != settings.ARTIFACT_SAMPLE_RATE:
        raise ValueError(f"expected {settings.ARTIFACT_SAMPLE_RATE // 1000} kHz")

    duration_raw = (probe.get("format") or {}).get("duration")
    duration = float(duration_raw) if duration_raw is not None else 0.0
    if duration <= 0:
        raise ValueError("zero duration")
    return size, duration


def write_stub_wav(path: Path, duration_sec: float, *, frequency_hz: float = 440.0) -> None:
    """Write a temporary mono PCM WAV before ffmpeg conversion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = max(int(duration_sec * PCM_SAMPLE_RATE), PCM_SAMPLE_RATE // 10)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(PCM_SAMPLE_RATE)
        for index in range(frame_count):
            sample = int(16000 * math.sin(2 * math.pi * frequency_hz * index / PCM_SAMPLE_RATE))
            wf.writeframes(struct.pack("<h", sample))


def write_stub_artifact(data_dir: str, task_id: str, duration_sec: float) -> Path:
    wav_path = temp_wav_path(data_dir, task_id)
    out_path = artifact_path(data_dir, task_id)
    write_stub_wav(wav_path, duration_sec)
    convert_wav_to_mp3(wav_path, out_path)
    wav_path.unlink(missing_ok=True)
    return out_path
