"""Remove capture artifacts and temp files for a task."""

from __future__ import annotations

from pathlib import Path

from app.artifacts import artifact_path, temp_wav_path


def remove_task_artifacts(data_dir: str, task_id: str) -> None:
    for path in (artifact_path(data_dir, task_id), temp_wav_path(data_dir, task_id)):
        if path.exists():
            path.unlink(missing_ok=True)
