from __future__ import annotations

import json
from pathlib import Path

from reportlab.pdfgen import canvas
from typer.testing import CliRunner, Result

from handoff_forge.cli import app
from handoff_forge.handoffs.parser import parse_confidence_lines, parse_handoff
from handoff_forge.models import ConfidenceLevel, TemplateProfile

runner = CliRunner()
PROJECT_ROOT = Path(__file__).parents[2]


def _invoke(root: Path, *arguments: str) -> Result:
    return runner.invoke(app, ["--data-root", str(root), *arguments])


def _pdf(path: Path) -> None:
    document = canvas.Canvas(str(path))
    document.drawString(72, 720, "Architecture validation passed on localhost.")
    document.drawString(72, 700, "Next step: preserve the rollback snapshot.")
    document.save()


def test_offline_cli_restart_workflow_with_markdown_mdc_and_pdf(tmp_path: Path) -> None:
    root = tmp_path / "handoff-data"
    markdown = tmp_path / "context.md"
    markdown.write_text(
        "# Project purpose\n\nShip a deterministic local-first handoff.\n",
        encoding="utf-8",
    )
    mdc = tmp_path / "current.mdc"
    mdc.write_text(
        "---\nalwaysApply: false\n---\n# Current state\n\nTests pass. No network calls.\n",
        encoding="utf-8",
    )
    pdf = tmp_path / "proof.pdf"
    _pdf(pdf)

    created = _invoke(root, "project", "create", "Release Handoff", "--json")
    assert created.exit_code == 0, created.stdout
    project_id = json.loads(created.stdout)["id"]

    ingested = _invoke(
        root,
        "ingest",
        str(markdown),
        str(mdc),
        str(pdf),
        "--project",
        project_id,
        "--json",
    )
    assert ingested.exit_code == 0, ingested.stdout
    assert len(json.loads(ingested.stdout)) == 3

    searched = _invoke(
        root,
        "search",
        "validation rollback",
        "--project",
        project_id,
        "--json",
    )
    assert searched.exit_code == 0, searched.stdout
    assert json.loads(searched.stdout)

    rebuilt = _invoke(root, "rebuild", "--project", project_id, "--json")
    assert rebuilt.exit_code == 0, rebuilt.stdout
    assert json.loads(rebuilt.stdout)["indexed_nodes"] > 0

    generated = _invoke(
        root,
        "generate",
        "--project",
        project_id,
        "--mode",
        "pre-compact",
        "--json",
    )
    assert generated.exit_code == 0, generated.stdout
    generation = json.loads(generated.stdout)
    output_id = generation["output"]["id"]
    assert generation["output"]["stored_path"].endswith(".mdc")

    validated = _invoke(
        root,
        "validate",
        output_id,
        "--project",
        project_id,
        "--profile",
        "codex-precompact-v1",
        "--json",
    )
    assert validated.exit_code == 0, validated.stdout
    assert json.loads(validated.stdout)["valid"] is True

    preview = _invoke(
        root,
        "copy-path",
        output_id,
        "--project",
        project_id,
        "--json",
    )
    assert preview.exit_code == 0, preview.stdout
    assert json.loads(preview.stdout)["executed"] is False

    deleted = _invoke(root, "project", "delete", project_id, "--yes", "--json")
    assert deleted.exit_code == 0, deleted.stdout
    assert json.loads(deleted.stdout)["deleted"] is True
    assert json.loads(_invoke(root, "project", "list", "--json").stdout) == []


def test_multi_source_examples_keep_one_authoritative_confidence_set_and_resume(
    tmp_path: Path,
) -> None:
    root = tmp_path / "multi-source-data"
    created = _invoke(root, "project", "create", "Manual Proof", "--json")
    assert created.exit_code == 0, created.stdout
    project_id = json.loads(created.stdout)["id"]

    ingested = _invoke(
        root,
        "ingest",
        str(PROJECT_ROOT / "examples" / "handoffs" / "project-alpha.mdc"),
        str(PROJECT_ROOT / "examples" / "handoffs" / "project-beta.mdc"),
        str(PROJECT_ROOT / "examples" / "northstar-continuity-review.pdf"),
        "--project",
        project_id,
        "--json",
    )
    assert ingested.exit_code == 0, ingested.stdout
    assert len(json.loads(ingested.stdout)) == 3

    modes = (
        ("pre-compact", TemplateProfile.CODEX_PRECOMPACT_V1),
        ("post-task", TemplateProfile.CODEX_POST_CHAT_V1),
    )
    for mode, profile in modes:
        generated = _invoke(
            root,
            "generate",
            "--project",
            project_id,
            "--mode",
            mode,
            "--json",
        )
        assert generated.exit_code == 0, generated.stdout
        payload = json.loads(generated.stdout)
        assert payload["job"]["status"] == "complete"
        assert payload["job"]["error"] is None
        output_path = Path(payload["output"]["stored_path"])
        rendered = output_path.read_text(encoding="utf-8")

        authoritative_start = rendered.rfind("### Section assessments")
        assert authoritative_start >= 0
        assert "- Section 2 — High" in rendered[:authoritative_start]
        assessments = parse_confidence_lines(parse_handoff(rendered))
        assert [item.section_id for item in assessments] == list(range(1, 12))
        assert all(item.confidence is ConfidenceLevel.LOW for item in assessments)

        resumed = _invoke(
            root,
            "resume",
            payload["job"]["id"],
            "--project",
            project_id,
            "--json",
        )
        assert resumed.exit_code == 0, resumed.stdout
        resumed_payload = json.loads(resumed.stdout)
        assert resumed_payload["job"]["status"] == "complete"
        assert resumed_payload["job"]["error"] is None
        assert resumed_payload["output"]["stored_path"] == str(output_path)
        assert resumed_payload["validation"]["valid"] is True

        validated = _invoke(
            root,
            "validate",
            payload["output"]["id"],
            "--project",
            project_id,
            "--profile",
            profile.value,
            "--json",
        )
        assert validated.exit_code == 0, validated.stdout
        assert json.loads(validated.stdout)["valid"] is True


def test_path_ingestion_preserves_safe_relative_markdown_assets(tmp_path: Path) -> None:
    root = tmp_path / "handoff-data"
    source = tmp_path / "source"
    assets = source / "assets"
    assets.mkdir(parents=True)
    diagram = assets / "architecture.png"
    diagram.write_bytes(b"\x89PNG\r\n\x1a\nlocal-fixture")
    markdown = source / "context.md"
    markdown.write_text(
        "# Architecture\n\n![System diagram](assets/architecture.png)\n",
        encoding="utf-8",
    )

    created = _invoke(root, "project", "create", "Asset Handoff", "--json")
    project_id = json.loads(created.stdout)["id"]
    ingested = _invoke(
        root,
        "ingest",
        str(markdown),
        "--project",
        project_id,
        "--json",
    )
    assert ingested.exit_code == 0, ingested.stdout
    artifact_id = json.loads(ingested.stdout)[0]["artifact"]["id"]

    inspected = _invoke(
        root,
        "inspect",
        "--project",
        project_id,
        "--artifact",
        artifact_id,
        "--json",
    )
    assert inspected.exit_code == 0, inspected.stdout
    document = json.loads(inspected.stdout)
    reference = next(item for item in document["references"] if item["kind"] == "local")

    assert Path(reference["resolved_path"]).is_relative_to(root.resolve())
    assert not any(
        warning["code"] == "missing_or_unsafe_relative_asset" for warning in document["warnings"]
    )


def test_uploaded_mdc_artifacts_are_direct_merge_inputs(tmp_path: Path) -> None:
    root = tmp_path / "merge-data"
    created = _invoke(root, "project", "create", "Project Alpha", "--json")
    project_id = json.loads(created.stdout)["id"]
    ingested = _invoke(
        root,
        "ingest",
        str(PROJECT_ROOT / "examples" / "handoffs" / "project-alpha.mdc"),
        str(PROJECT_ROOT / "examples" / "handoffs" / "project-beta.mdc"),
        "--project",
        project_id,
        "--json",
    )
    assert ingested.exit_code == 0, ingested.stdout
    artifact_ids = [item["artifact"]["id"] for item in json.loads(ingested.stdout)]

    merged = _invoke(
        root,
        "merge",
        *artifact_ids,
        "--project",
        project_id,
        "--profile",
        "goal-v1",
        "--json",
    )

    assert merged.exit_code == 0, merged.stdout
    payload = json.loads(merged.stdout)
    assert payload["output"]["stored_path"].endswith(".mdc")
    assert len(payload["plan"]["source_hashes"]) == 2
