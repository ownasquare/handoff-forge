"""Parser selection by validated canonical artifact type."""

from __future__ import annotations

from pathlib import Path

from handoff_forge.config import HandoffSettings
from handoff_forge.errors import ParseError
from handoff_forge.models import ArtifactKind, ParsedDocument, SourceArtifact
from handoff_forge.parsing.base import DocumentParser
from handoff_forge.parsing.markdown import MarkdownParser
from handoff_forge.parsing.pdf import PDFParser


class ParserRegistry:
    """Small explicit parser registry; it never performs network discovery."""

    def __init__(
        self,
        settings: HandoffSettings | None = None,
        *,
        artifact_dir: Path | None = None,
    ) -> None:
        self.settings = settings or HandoffSettings()
        self._parsers: dict[ArtifactKind, DocumentParser] = {
            ArtifactKind.MARKDOWN: MarkdownParser(self.settings, artifact_dir=artifact_dir),
            ArtifactKind.MDC: MarkdownParser(self.settings, artifact_dir=artifact_dir),
            ArtifactKind.PDF: PDFParser(self.settings, artifact_dir=artifact_dir),
        }

    def register(self, kind: ArtifactKind, parser: DocumentParser) -> None:
        self._parsers[kind] = parser

    def parser_for(self, artifact: SourceArtifact) -> DocumentParser:
        try:
            return self._parsers[artifact.kind]
        except KeyError as exc:
            raise ParseError(
                f"no parser is registered for artifact kind {artifact.kind.value}"
            ) from exc

    def parse(self, artifact: SourceArtifact) -> ParsedDocument:
        return self.parser_for(artifact).parse(artifact)

    def parse_many(self, artifacts: list[SourceArtifact]) -> list[ParsedDocument]:
        return [self.parse(artifact) for artifact in artifacts]
