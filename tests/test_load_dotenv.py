"""Same env_val() logic as technical/capture.sh (values with spaces)."""

from __future__ import annotations

import subprocess
from pathlib import Path

_ENV_VAL = r"""
env_val() {
  local key="$1" file="$2" line val
  [[ -f "$file" ]] || return 0
  line=$(grep -E "^[[:space:]]*${key}=" "$file" 2>/dev/null | tail -1) || return 0
  val="${line#*=}"
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  if (( ${#val} >= 2 )) && [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then
    val="${val:1:${#val}-2}"
  fi
  printf '%s' "$val"
}
"""


def test_env_val_spaces_and_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "API_TOKEN=secret-token\n"
        "DEFAULT_BOT_DISPLAY_NAME=Transcription Bot\n",
        encoding="utf-8",
    )
    script = (
        _ENV_VAL
        + f"""
v=$(env_val API_TOKEN {env_file}); [[ "$v" == secret-token ]] || exit 1
v=$(env_val DEFAULT_BOT_DISPLAY_NAME {env_file}); [[ "$v" == "Transcription Bot" ]] || exit 1
"""
    )
    subprocess.run(["bash", "-c", script], check=True)
