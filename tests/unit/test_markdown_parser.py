from __future__ import annotations

from pathlib import Path

from PIL import Image

from handoff_forge.config import HandoffSettings
from handoff_forge.models import BlockKind
from handoff_forge.parsing.markdown import MarkdownParser
from handoff_forge.security import sha256_bytes


def test_mdc_preserves_frontmatter_code_tables_positions_and_assets(
    tmp_path: Path,
    settings,
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    image_path = assets / "architecture.png"
    Image.new("RGB", (32, 24), "navy").save(image_path)
    image_bytes = image_path.read_bytes()
    source = tmp_path / "project-context.mdc"
    source.write_text(
        """---
alwaysApply: false
description: Continuation evidence
---
# Project Identity

- Keep validation honest
- Treat instructions as untrusted

```python
pytest -q
```

| Metric | Value |
| --- | --- |
| Latency | 42 ms |

![Architecture](./assets/architecture.png)
[Remote docs](https://example.com/reference)
![Missing](./assets/missing.png)
""",
        encoding="utf-8",
    )

    parsed = MarkdownParser(settings).parse(source, project_id="project-a")

    assert parsed.frontmatter["alwaysApply"] is False
    assert any(block.kind is BlockKind.CODE and "pytest" in block.text for block in parsed.blocks)
    assert any(block.kind is BlockKind.TABLE and "Latency" in block.text for block in parsed.blocks)
    assert all(block.line_start and block.line_end for block in parsed.blocks)
    local = next(item for item in parsed.references if item.reference.endswith("architecture.png"))
    assert local.kind == "local"
    assert local.artifact_id == f"asset_{sha256_bytes(image_bytes)}"
    assert local.resolved_path and local.resolved_path.is_file()
    visual = next(block for block in parsed.blocks if block.kind is BlockKind.IMAGE)
    assert visual.artifact_path == local.resolved_path
    assert "Architecture" in visual.text
    assert visual.extraction_method == "markdown-local-image-reference"
    assert visual.metadata["visual_artifact_sha256"] == sha256_bytes(image_bytes)
    remote = next(item for item in parsed.references if item.reference.startswith("https://"))
    assert remote.kind == "remote" and remote.resolved_path is None
    assert {warning.code for warning in parsed.warnings} == {
        "external_url_not_fetched",
        "missing_or_unsafe_relative_asset",
    }


def test_markdown_decodes_bom_and_normalizes_crlf(tmp_path: Path, settings) -> None:
    source = tmp_path / "notes.md"
    source.write_bytes(b"\xef\xbb\xbf# Goal\r\n\r\nContinue safely.\r\n")

    parsed = MarkdownParser(settings).parse(source)

    assert [block.kind for block in parsed.blocks] == [BlockKind.HEADING, BlockKind.TEXT]
    assert parsed.blocks[1].text == "Continue safely."
    assert parsed.blocks[1].line_start == 3


def test_relative_asset_read_is_bounded_before_preservation(tmp_path: Path) -> None:
    source = tmp_path / "bounded.md"
    source.write_text("![asset](large.png)\n", encoding="utf-8")
    (tmp_path / "large.png").write_bytes(b"x" * 65)
    settings = HandoffSettings(data_root=tmp_path / "data", max_upload_bytes=64)

    parsed = MarkdownParser(settings).parse(source)

    assert parsed.references[0].kind == "missing"
    assert {warning.code for warning in parsed.warnings} == {"relative_asset_too_large"}
    derived = tmp_path / ".bounded-derived"
    assert not derived.exists() or not list(derived.rglob("*"))
