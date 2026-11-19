"""Zbir Clipper: automatic TikTok clips from Zbir's YouTube lives."""
from pathlib import Path as _P
try:
    __version__ = (_P(__file__).resolve().parent.parent / "version.txt").read_text(encoding="utf-8").strip() or "0.0.0"
except OSError:
    __version__ = "0.0.0"
