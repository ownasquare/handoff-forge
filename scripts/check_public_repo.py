"""Fail when public project files contain common secret or private-path material."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    ".git",
    ".venv",
    ".data",
    "dist",
    "build",
    "tmp",
    "test-results",
    "playwright-report",
}
PRIVATE_DOC_ROOTS = {
    Path("docs/ai-harness-handoff-system"),
    Path("docs/handoff-forge"),
    Path("docs/handoffs"),
    Path("docs/superpowers"),
}
TEXT_SUFFIXES = {
    ".bash",
    ".cfg",
    ".conf",
    ".css",
    ".csv",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".md",
    ".mdc",
    ".properties",
    ".py",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
TEXT_FILENAMES = {
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "LICENSE",
    "Makefile",
}
SECRET_PATTERNS = (
    (
        "credential assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
            r"private[_-]?token|refresh[_-]?token)\b\s*[:=]\s*[\"']?"
            r"[A-Za-z0-9][A-Za-z0-9+/_=.-]{11,}"
        ),
    ),
    (
        "provider token",
        re.compile(
            r"\b(?:"
            r"(?:AKIA|ASIA)[0-9A-Z]{16}|"
            r"AIza[0-9A-Za-z_-]{30,}|"
            r"gh[pousr]_[A-Za-z0-9]{30,}|"
            r"github_pat_[A-Za-z0-9_]{30,}|"
            r"hf_[A-Za-z0-9]{30,}|"
            r"sk-[A-Za-z0-9_-]{20,}|"
            r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}|"
            r"xai-[A-Za-z0-9_-]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{20,}"
            r")\b"
        ),
    ),
    (
        "JSON web token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
PRIVATE_PATHS = (
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"/home/[^/\s]+/"),
    re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+\\"),
)


def _is_text_file(path: Path) -> bool:
    name = path.name.casefold()
    return (
        path.suffix.casefold() in TEXT_SUFFIXES
        or path.name in TEXT_FILENAMES
        or name == ".env"
        or name.startswith(".env.")
    )


def _public_text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        relative = path.relative_to(ROOT)
        if any(relative.is_relative_to(root) for root in PRIVATE_DOC_ROOTS):
            continue
        if _is_text_file(path):
            files.append(path)
    return sorted(files)


def _tracked_private_files() -> list[Path]:
    """Return ignored continuation documents that nevertheless entered the Git index."""

    result = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--",
            *(root.as_posix() for root in sorted(PRIVATE_DOC_ROOTS)),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError("git could not verify tracked private documentation roots")
    return sorted(
        Path(value.decode("utf-8", errors="replace"))
        for value in result.stdout.split(b"\0")
        if value
    )


def main() -> int:
    failures: list[str] = []
    try:
        tracked_private_files = _tracked_private_files()
    except (OSError, RuntimeError):
        failures.append("could not verify whether private documentation roots are tracked")
    else:
        failures.extend(
            f"{path}: private documentation must not be tracked" for path in tracked_private_files
        )

    public_files = _public_text_files()
    for path in public_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(ROOT)
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(f"{relative}: possible {label}")
        is_scanner_source = relative == Path("scripts/check_public_repo.py")
        if not is_scanner_source and any(pattern.search(text) for pattern in PRIVATE_PATHS):
            failures.append(f"{relative}: machine-local home path")
    if failures:
        print("Public repository check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"Public repository check passed for {len(public_files)} text files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
