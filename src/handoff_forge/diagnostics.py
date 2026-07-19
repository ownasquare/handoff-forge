"""Sanitized, side-effect-light runtime diagnostics."""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from handoff_forge.config import HandoffSettings


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    name: str
    status: str
    detail: str
    required: bool = False


def _package_check(distribution: str, *, required: bool = True) -> DiagnosticCheck:
    try:
        installed = version(distribution)
    except PackageNotFoundError:
        return DiagnosticCheck(distribution, "missing", "not installed", required)
    return DiagnosticCheck(distribution, "ready", f"version {installed}", required)


def _executable_check(name: str, *, required: bool = False) -> DiagnosticCheck:
    path = shutil.which(name)
    if path is None:
        return DiagnosticCheck(name, "unavailable", "not on PATH", required)
    return DiagnosticCheck(name, "ready", str(Path(path).resolve()), required)


def run_diagnostics(settings: HandoffSettings) -> list[DiagnosticCheck]:
    """Return key-name-free diagnostic state without provider calls."""

    checks = [
        _package_check("handoff-forge"),
        _package_check("streamlit"),
        _package_check("llama-index-core"),
        _package_check("chromadb"),
        _package_check("pdfplumber"),
        _executable_check("tesseract"),
    ]
    for harness in ("codex", "claude", "gemini", "grok"):
        checks.append(_executable_check(harness))
    root = settings.data_root
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        writable = root.is_dir() and os.access(root, os.W_OK)
    except OSError as exc:
        checks.append(DiagnosticCheck("data-root", "error", type(exc).__name__, True))
    else:
        detail = f"writable local directory: {root}" if writable else "not writable"
        checks.append(DiagnosticCheck("data-root", "ready", detail, True))
    policy = "offline; outbound providers disabled"
    if settings.network_enabled:
        policy = "network enabled; each cloud upload still requires consent"
    checks.append(DiagnosticCheck("network-policy", "ready", policy, True))
    optional_modules = {
        "openai-adapter": "openai",
        "anthropic-adapter": "anthropic",
        "google-adapter": "google.genai",
        "xai-adapter": "xai_sdk",
        "voyage-adapter": "voyageai",
    }
    for label, module in optional_modules.items():
        installed = importlib.util.find_spec(module) is not None
        checks.append(
            DiagnosticCheck(
                label,
                "installed" if installed else "optional",
                "SDK installed" if installed else "install the providers extra to enable",
            )
        )
    return checks


def diagnostics_ready(checks: list[DiagnosticCheck]) -> bool:
    return all(check.status == "ready" for check in checks if check.required)
