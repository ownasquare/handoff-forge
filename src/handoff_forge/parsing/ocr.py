"""Bounded, optional Tesseract OCR support."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


class OCRUnavailable(RuntimeError):
    """Tesseract is not installed or cannot be executed."""


class OCRTimeout(RuntimeError):
    """OCR exceeded its configured per-page deadline."""


class OCRFailure(RuntimeError):
    """OCR failed for a reason that can be surfaced as a parser warning."""


@dataclass(frozen=True, slots=True)
class OCRResult:
    text: str
    confidence: float


def extract_ocr(
    image: Image.Image,
    *,
    language: str,
    timeout_seconds: int,
) -> OCRResult:
    """Extract OCR text and mean word confidence from one rendered page."""

    try:
        import pytesseract
        from pytesseract import Output, TesseractNotFoundError
    except ImportError as exc:  # pragma: no cover - core dependency contract
        raise OCRUnavailable("pytesseract is not installed") from exc

    try:
        data = pytesseract.image_to_data(
            image,
            lang=language,
            config="--psm 6",
            output_type=Output.DICT,
            timeout=timeout_seconds,
        )
    except TesseractNotFoundError as exc:
        raise OCRUnavailable("Tesseract executable is unavailable") from exc
    except RuntimeError as exc:
        if "timeout" in str(exc).casefold():
            raise OCRTimeout(f"Tesseract exceeded the {timeout_seconds}-second timeout") from exc
        raise OCRFailure("Tesseract could not process the rendered page") from exc
    except Exception as exc:
        raise OCRFailure("Tesseract could not process the rendered page") from exc

    words: list[str] = []
    confidences: list[float] = []
    for text, raw_confidence in zip(data.get("text", []), data.get("conf", []), strict=False):
        normalized = str(text).strip()
        if not normalized:
            continue
        words.append(normalized)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if confidence >= 0:
            confidences.append(confidence / 100.0)
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return OCRResult(text=" ".join(words).strip(), confidence=max(0.0, min(1.0, mean_confidence)))
