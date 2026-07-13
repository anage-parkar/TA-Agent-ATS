"""Attachment validation (Workstream E).

Validates uploaded resumes before they touch disk: extension allowlist, size
cap, content-type/magic-byte sniffing, and a pluggable malware-scan seam. Raises
AttachmentRejected on any failure. Filenames are never trusted — callers store
under a generated name (uuid), so path traversal is not possible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

MAX_BYTES = 10 * 1024 * 1024  # 10 MB

ALLOWED_EXT = {".pdf", ".doc", ".docx", ".txt", ".rtf"}

# ext -> acceptable content-type prefixes (browsers vary; prefix match)
_ALLOWED_TYPES = {
    ".pdf": ("application/pdf",),
    ".txt": ("text/plain",),
    ".rtf": ("application/rtf", "text/rtf"),
    ".doc": ("application/msword", "application/octet-stream"),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream", "application/zip",
    ),
}

# magic-byte signatures by extension (best-effort; empty = skip sniff)
_MAGIC = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),     # docx is a zip
    ".doc": (b"\xd0\xcf\x11\xe0",),  # OLE compound file
}


class AttachmentRejected(ValueError):
    """Raised when an uploaded file fails validation."""


# Pluggable scanner: bytes -> None (ok) or raise AttachmentRejected. Default no-op
# placeholder so a real AV/CDR scanner (ClamAV, cloud) can be wired in later.
_scanner: Optional[Callable[[bytes], None]] = None


def set_scanner(fn: Callable[[bytes], None] | None) -> None:
    global _scanner
    _scanner = fn


def validate_upload(filename: str, content: bytes, content_type: str | None = None) -> str:
    """Validate an upload. Returns the safe extension; raises AttachmentRejected."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise AttachmentRejected(f"unsupported resume type: {ext or '(none)'}")
    if not content:
        raise AttachmentRejected("empty file")
    if len(content) > MAX_BYTES:
        raise AttachmentRejected("resume exceeds 10 MB")

    if content_type:
        allowed = _ALLOWED_TYPES.get(ext, ())
        ct = content_type.split(";", 1)[0].strip().lower()
        if allowed and not any(ct.startswith(a) for a in allowed):
            raise AttachmentRejected(f"content-type {ct!r} does not match {ext}")

    magic = _MAGIC.get(ext)
    if magic and not any(content.startswith(sig) for sig in magic):
        raise AttachmentRejected(f"file contents do not look like a valid {ext}")

    if _scanner is not None:
        _scanner(content)  # raises AttachmentRejected if it finds something

    return ext
