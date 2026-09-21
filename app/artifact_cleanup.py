"""Remove capture artifacts and temp files for a task."""

from __future__ import annotations

from pathlib import Path

from app.artifacts import artifact_part_path, artifact_path, temp_wav_path


def remove_task_artifacts(data_dir: str, task_id: str) -> None:
    mp3 = artifact_path(data_dir, task_id)
    for path in (mp3, artifact_part_path(mp3), temp_wav_path(data_dir, task_id)):
        if path.exists():
            path.unlink(missing_ok=True)
