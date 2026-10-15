"""Upload validation: extension allowlist, magic bytes, size, zip-bomb and macro checks."""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile

ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".txt": "txt",
    ".md": "md",
    ".csv": "csv",
}
DOWNLOAD_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",  # never served as renderable HTML
    "csv": "text/csv; charset=utf-8",
}
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class UploadRejected(ValueError):
    """Raised with a message that is safe to show to the uploader."""


def clean_display_filename(raw: str | None) -> str:
    """Validate and normalise a client-supplied filename for *display only*.
    The on-disk name is always server-generated."""
    if not raw or not raw.strip():
        raise UploadRejected("A filename is required")
    name = unicodedata.normalize("NFC", raw.strip())
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise UploadRejected("Filename must not contain path separators or '..'")
    name = _CONTROL_RE.sub("", name)
    if not name or name.startswith("."):
        raise UploadRejected("Invalid filename")
    if len(name) > 200:
        raise UploadRejected("Filename is too long (max 200 characters)")
    return name


def extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def _check_zip(data: bytes, required_member: str, max_uncompressed: int, forbid_macros: bool) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise UploadRejected("File is corrupt or not a valid Office document") from exc
    if len(infos) > 10_000:
        raise UploadRejected("Archive contains too many entries")
    total = 0
    names = set()
    for info in infos:
        if info.filename.startswith("/") or ".." in info.filename.split("/"):
            raise UploadRejected("Archive contains unsafe paths")
        total += info.file_size
        names.add(info.filename)
    if total > max_uncompressed:
        raise UploadRejected("Document expands to an unreasonable size")
    if required_member not in names:
        raise UploadRejected("File content does not match its extension")
    if forbid_macros and any(n.lower().endswith("vbaproject.bin") for n in names):
        raise UploadRejected("Macro-enabled documents are not accepted")


def decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise UploadRejected("File content does not look like text")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UploadRejected("Text encoding is not supported (use UTF-8)")


def validate_upload(filename: str, data: bytes, max_bytes: int, max_zip_uncompressed_mb: int) -> tuple[str, str]:
    """Return (clean_display_name, file_type) or raise UploadRejected."""
    name = clean_display_filename(filename)
    ext = extension_of(name)
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejected(
            "Unsupported file type. Allowed: " + ", ".join(sorted(e.lstrip(".") for e in ALLOWED_EXTENSIONS))
        )
    if len(data) == 0:
        raise UploadRejected("The file is empty")
    if len(data) > max_bytes:
        raise UploadRejected(f"File exceeds the maximum size of {max_bytes // (1024 * 1024)} MB")
    ftype = ALLOWED_EXTENSIONS[ext]

    if ftype == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise UploadRejected("File content does not match its extension")
    elif ftype == "docx":
        if not data.startswith(b"PK\x03\x04"):
            raise UploadRejected("File content does not match its extension")
        _check_zip(data, "word/document.xml", max_zip_uncompressed_mb * 1024 * 1024, forbid_macros=True)
    elif ftype == "xlsx":
        if not data.startswith(b"PK\x03\x04"):
            raise UploadRejected("File content does not match its extension")
        _check_zip(data, "xl/workbook.xml", max_zip_uncompressed_mb * 1024 * 1024, forbid_macros=True)
    elif ftype == "xls":
        if not data.startswith(OLE_MAGIC):
            raise UploadRejected("File content does not match its extension")
    else:  # txt, md, csv
        decode_text(data)
    return name, ftype
