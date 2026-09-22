import struct

import pytest

from app.artifacts import (
    PCM_SAMPLE_RATE,
    append_pcm_samples,
    convert_pcm_to_mp3,
    temp_pcm_path,
    validate_artifact,
)
from tests.conftest import isolate_env


def test_append_pcm_samples_appends_binary(tmp_path) -> None:
    path = temp_pcm_path(str(tmp_path), "task-1")
    append_pcm_samples(path, [0, 1000, -1000])
    append_pcm_samples(path, [42, -42])
    data = path.read_bytes()
    assert data == struct.pack("<5h", 0, 1000, -1000, 42, -42)


def test_convert_pcm_to_mp3(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch)
    pcm_path = temp_pcm_path(str(tmp_path), "task-2")
    frame_count = PCM_SAMPLE_RATE // 2
    samples = [int(8000 * (1 if index % 100 < 50 else -1)) for index in range(frame_count)]
    append_pcm_samples(pcm_path, samples)
    mp3_path = tmp_path / "artifacts" / "task-2.mp3"
    convert_pcm_to_mp3(pcm_path, mp3_path)
    size, duration = validate_artifact(mp3_path)
    assert size >= 128
    assert duration > 0.4
