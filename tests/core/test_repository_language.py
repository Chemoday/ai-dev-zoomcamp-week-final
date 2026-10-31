"""The repository is English only; the one exception is the notifications localization CSV."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {"src/modules/notifications/messages.csv"}
CYRILLIC = re.compile("[\u0400-\u04ff]")


def tracked_files() -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    output = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return output.splitlines()


def test_only_the_localization_file_contains_cyrillic() -> None:
    offenders = []
    for name in tracked_files():
        if name in ALLOWED:
            continue
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        if CYRILLIC.search(text):
            offenders.append(name)

    assert offenders == []
