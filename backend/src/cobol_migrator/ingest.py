from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

import httpx

from cobol_migrator.errors import IngestionError

logger = logging.getLogger(__name__)

MAX_BYTES = 1_000_000  # 1 MB


def load_snippet(text: str) -> str:
    """
    Load COBOL from a raw text snippet.
    
    Validates size limit and basic COBOL structure.
    """
    if len(text.encode("utf-8", errors="replace")) > MAX_BYTES:
        raise IngestionError("Snippet too large (max 1MB)")

    if not _looks_like_cobol(text):
        logger.warning("Snippet doesn't look like COBOL, proceeding anyway")

    return text


def load_url(url: str) -> str:
    """
    Load COBOL from a public URL.
    
    Validates URL scheme, size limit, and content type.
    """
    if not url.startswith(("http://", "https://")):
        raise IngestionError("URL must start with http:// or https://")

    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            response = client.get(url)
            response.raise_for_status()

            if len(response.content) > MAX_BYTES:
                raise IngestionError("Remote file too large (max 1MB)")

            body = response.text

    except httpx.HTTPStatusError as e:
        raise IngestionError(f"HTTP error fetching URL: {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise IngestionError(f"Request error fetching URL: {e}") from e

    if not _looks_like_cobol(body):
        raise IngestionError("URL content does not look like COBOL")

    return body


def load_repo(git_url: str) -> str:
    """
    Load COBOL from a public GitHub repository.
    
    Clones shallow, finds entry-point .cbl/.cob file, extracts content.
    Only accepts GitHub URLs for security.
    """
    if not git_url.startswith("https://github.com/"):
        raise IngestionError("Only GitHub repositories are supported (https://github.com/...)")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", git_url, tmpdir],
                timeout=30,
                check=True,
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/usr/local/bin", "HOME": "/tmp", "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired:
            raise IngestionError("Repository clone timed out")
        except subprocess.CalledProcessError as e:
            raise IngestionError(f"Failed to clone repository: {e.stderr}") from e

        candidate = _pick_entrypoint(Path(tmpdir))
        if candidate is None:
            raise IngestionError("No .cbl or .cob file found in repository")

        body = candidate.read_text(errors="replace")
        if len(body.encode("utf-8", errors="replace")) > MAX_BYTES:
            raise IngestionError("Entry-point file too large (max 1MB)")

        return body


def _looks_like_cobol(text: str) -> bool:
    """Check if text appears to be COBOL source code."""
    upper = text.upper()
    return "IDENTIFICATION DIVISION" in upper or "PROGRAM-ID" in upper


def _pick_entrypoint(root: Path) -> Path | None:
    """
    Pick the most likely entry-point COBOL file from a directory.
    
    Prefers files containing PROGRAM-ID, else the largest .cbl/.cob file.
    """
    candidates = [
        p for p in root.rglob("*")
        if p.suffix.lower() in {".cbl", ".cob"} and p.is_file()
    ]

    if not candidates:
        return None

    with_prog_id = []
    for path in candidates:
        try:
            content = path.read_text(errors="replace")
            if "PROGRAM-ID" in content.upper():
                with_prog_id.append(path)
        except Exception:
            pass

    pool = with_prog_id or candidates
    return max(pool, key=lambda p: p.stat().st_size)


def load_source(
    source_type: str,
    source_ref: str,
) -> str:
    """
    Load COBOL source from the specified source type.
    
    Args:
        source_type: One of "snippet", "url", "repo"
        source_ref: The source content or reference
    
    Returns:
        The COBOL source code as a string
    """
    if source_type == "snippet":
        return load_snippet(source_ref)
    elif source_type == "url":
        return load_url(source_ref)
    elif source_type == "repo":
        return load_repo(source_ref)
    else:
        raise IngestionError(f"Unknown source type: {source_type}")
