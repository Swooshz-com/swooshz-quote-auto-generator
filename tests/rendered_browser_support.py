"""Fresh synthetic browser evidence directories, retained on success or failure."""
from contextlib import contextmanager
from pathlib import Path
import tempfile


@contextmanager
def owned_browser_evidence_directory():
    parent = Path(__file__).resolve().parents[1] / "_logs" / "browser"
    parent.mkdir(parents=True, exist_ok=True)
    # Retain every attempt; a later run never reuses or removes earlier evidence.
    yield tempfile.mkdtemp(prefix="r-", dir=parent)
