"""Tiny helper to set KEY=value lines in backend/.env without ever printing the values."""

from pathlib import Path
from typing import Dict

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def set_values(values: Dict[str, str], path: Path = ENV_PATH) -> None:
    """Add or replace the given keys, keeping every other line (and comments) untouched."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining and out and out[-1].strip():
        out.append("")
    out += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def has_value(key: str, path: Path = ENV_PATH) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}=") and line.split("=", 1)[1].strip().strip("\"'"):
            return True
    return False
