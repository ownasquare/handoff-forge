"""Offline-first Typer command line interface for Handoff Forge."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer
from pydantic import BaseModel

from handoff_forge import __version__
from handoff_forge.application import (
    DemoOutcome,
    GenerationOutcome,
    HandoffApplication,
    build_application,
)
from handoff_forge.config import HandoffSettings
from handoff_forge.errors import CapabilityError, HandoffForgeError
from handoff_forge.models import HandoffMode, JobStatus, ModelRoute, TemplateProfile
from handoff_forge.security import redact_secrets

app = typer.Typer(
    name="handoff-forge",
    help="Create, inspect, validate, merge, and continue local-first AI handoffs.",
    no_args_is_help=True,
    add_completion=False,
)
project_app = typer.Typer(help="Create, list, and delete local projects.")
extensions_app = typer.Typer(help="Inspect trusted provider and harness extensions.")
app.add_typer(project_app, name="project")
app.add_typer(extensions_app, name="extensions")


class _CLIState:
    def __init__(
        self,
        settings: HandoffSettings,
        enabled_extensions: tuple[str, ...] = (),
    ) -> None:
        self.settings = settings
        self.enabled_extensions = enabled_extensions
        self.service: HandoffApplication | None = None


def _version_callback(value: bool) -> bool:
    if value:
        typer.echo(f"handoff-forge {__version__}")
        raise typer.Exit()
    return value


@app.callback()
def main(
    context: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed Handoff Forge version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    data_root: Annotated[
        Path | None,
        typer.Option(
            "--data-root",
            help="Private local data directory. Defaults to the platform application directory.",
        ),
    ] = None,
    allow_network: Annotated[
        bool,
        typer.Option(
            "--allow-network",
            help="Enable configured cloud adapters; each generation still requires upload consent.",
        ),
    ] = False,
    enable_extension: Annotated[
        list[str] | None,
        typer.Option(
            "--enable-extension",
            help=(
                "Load one trusted installed provider or harness extension by entry-point name. "
                "Repeat for multiple extensions."
            ),
        ),
    ] = None,
) -> None:
    del version
    values: dict[str, Any] = {}
    if data_root is not None:
        values["data_root"] = data_root
    if allow_network:
        values.update({"offline": False, "allow_network": True})
    context.obj = _CLIState(
        HandoffSettings(**values),
        tuple(enable_extension or ()),
    )


@app.command("doctor")
def doctor_command(
    context: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(context, json_output, lambda service: service.doctor(), _doctor_text)


@extensions_app.command("list")
def extensions_list_command(
    context: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """List installed extension metadata without importing disabled extensions."""

    _perform(
        context,
        json_output,
        lambda service: service.list_extensions(),
        _extensions_text,
    )


@project_app.command("create")
def project_create(
    context: typer.Context,
    name: Annotated[str, typer.Argument(help="Human-readable project name.")],
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.create_project(name, description),
        lambda project: f"Created project {project.name} ({project.id}).",
    )


@project_app.command("list")
def project_list(
    context: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.list_projects(),
        lambda projects: (
            "No projects found."
            if not projects
            else "\n".join(f"{project.id}\t{project.name}" for project in projects)
        ),
    )


@project_app.command("delete")
def project_delete(
    context: typer.Context,
    project: Annotated[str, typer.Argument(help="Project ID, exact name, or unique slug.")],
    confirmed: Annotated[
        bool,
        typer.Option("--yes", help="Confirm recursive deletion after vector cleanup."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    def operation(service: HandoffApplication) -> dict[str, Any]:
        resolved = service.resolve_project(project)
        if not confirmed:
            return {
                "project_id": resolved.id,
                "project_name": resolved.name,
                "deleted": False,
                "preview": "Pass --yes to delete canonical and derived project state.",
            }
        service.delete_project(resolved.id)
        return {"project_id": resolved.id, "project_name": resolved.name, "deleted": True}

    _perform(
        context,
        json_output,
        operation,
        lambda result: (
            f"Deleted project {result['project_name']}."
            if result["deleted"]
            else str(result["preview"])
        ),
    )


@app.command("ingest")
def ingest_command(
    context: typer.Context,
    paths: Annotated[list[Path], typer.Argument(help="Markdown, MDC, or PDF paths.")],
    project: Annotated[str, typer.Option("--project", "-p")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.ingest_paths(project, paths),
        lambda results: "\n".join(
            f"Ingested {item.artifact.display_name}: {item.block_count} blocks, "
            f"{item.indexed_nodes} nodes."
            for item in results
        ),
    )


@app.command("inspect")
def inspect_command(
    context: typer.Context,
    project: Annotated[str, typer.Option("--project", "-p")],
    artifact: Annotated[
        str | None,
        typer.Option("--artifact", "-a", help="Artifact ID or display name."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    def operation(service: HandoffApplication) -> object:
        if artifact is None:
            return service.inspect_project(project)
        return service.inspect_artifact(project, artifact)

    _perform(context, json_output, operation, _model_text)


@app.command("search")
def search_command(
    context: typer.Context,
    query: Annotated[str, typer.Argument(help="Local retrieval query.")],
    project: Annotated[str, typer.Option("--project", "-p")],
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 5,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.search(project, query, limit=limit),
        lambda hits: (
            "No matching evidence."
            if not hits
            else "\n".join(
                f"{hit.score:.3f}\t{hit.source_id or 'unknown'}\t{hit.text}" for hit in hits
            )
        ),
    )


@app.command("rebuild")
def rebuild_command(
    context: typer.Context,
    project: Annotated[str, typer.Option("--project", "-p")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: {"indexed_nodes": service.rebuild_index(project)},
        lambda result: f"Rebuilt {result['indexed_nodes']} indexed nodes.",
    )


@app.command("outputs")
def outputs_command(
    context: typer.Context,
    project: Annotated[str, typer.Option("--project", "-p")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.list_outputs(project),
        lambda outputs: (
            "No generated outputs."
            if not outputs
            else "\n".join(f"{output.id}\t{output.stored_path}" for output in outputs)
        ),
    )


@app.command("generate")
def generate_command(
    context: typer.Context,
    project: Annotated[str, typer.Option("--project", "-p")],
    mode: Annotated[HandoffMode, typer.Option("--mode")] = HandoffMode.PRE_COMPACT,
    profile: Annotated[
        TemplateProfile | None,
        typer.Option("--profile", help="Override the mode's default output profile."),
    ] = None,
    provider: Annotated[str, typer.Option("--provider")] = "offline",
    model: Annotated[str, typer.Option("--model")] = "extractive-v1",
    allow_cloud_upload: Annotated[
        bool,
        typer.Option(
            "--allow-cloud-upload",
            help="Consent to upload only the selected evidence for this run.",
        ),
    ] = False,
    include_visual_evidence: Annotated[
        bool,
        typer.Option(
            "--include-visual-evidence",
            help=(
                "Include selected visual files after confirming the exact model/version "
                "accepts image input."
            ),
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    route_matrix = {
        section_id: ModelRoute(
            provider=provider,
            model=model,
            allow_cloud_upload=allow_cloud_upload,
            include_visual_evidence=include_visual_evidence,
        )
        for section_id in range(1, 13)
    }

    def operation(service: HandoffApplication) -> GenerationOutcome:
        result = service.generate_handoff(
            project,
            mode=mode,
            profile=profile,
            routes=route_matrix,
        )
        if result.job.status is not JobStatus.COMPLETE:
            detail = result.job.error or "generation did not complete"
            raise CapabilityError(f"generation job {result.job.id} stopped: {detail}")
        return result

    _perform(
        context,
        json_output,
        operation,
        lambda result: f"Generated {_generation_output_path(result)}",
    )


@app.command("resume")
def resume_command(
    context: typer.Context,
    job_id: Annotated[str, typer.Argument(help="Generation job identifier.")],
    project: Annotated[str, typer.Option("--project", "-p")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    def operation(service: HandoffApplication) -> object:
        result = service.resume_job(project, job_id)
        if result.job.status is JobStatus.FAILED:
            raise CapabilityError(result.job.error or "generation resume failed")
        return result

    _perform(context, json_output, operation, _model_text)


@app.command("cancel")
def cancel_command(
    context: typer.Context,
    job_id: Annotated[str, typer.Argument(help="Generation job identifier.")],
    project: Annotated[str, typer.Option("--project", "-p")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.cancel_job(project, job_id),
        lambda job: f"Job {job.id} status: {job.status.value}",
    )


@app.command("merge")
def merge_command(
    context: typer.Context,
    outputs: Annotated[
        list[str],
        typer.Argument(help="Two or more managed source artifact or generated output references."),
    ],
    project: Annotated[str, typer.Option("--project", "-p")],
    profile: Annotated[TemplateProfile, typer.Option("--profile")] = TemplateProfile.GOAL_V1,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.merge_handoffs(project, outputs, target_profile=profile),
        lambda result: f"Merged handoff: {result.output.stored_path}",
    )


@app.command("validate")
def validate_command(
    context: typer.Context,
    target: Annotated[str, typer.Argument(help="Managed output ID or local handoff path.")],
    profile: Annotated[TemplateProfile, typer.Option("--profile")],
    project: Annotated[
        str | None,
        typer.Option("--project", "-p", help="Required when target is a managed output ID."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    def operation(service: HandoffApplication) -> object:
        if project is None:
            return service.validate_path(Path(target), profile)
        return service.validate_output(project, target, profile)

    _perform(context, json_output, operation, lambda report: "Handoff is valid.")


@app.command("launch")
def launch_command(
    context: typer.Context,
    target: Annotated[str, typer.Argument(help="Managed output ID.")],
    project: Annotated[str, typer.Option("--project", "-p")],
    harness: Annotated[str, typer.Option("--harness")] = "codex",
    model: Annotated[str | None, typer.Option("--model")] = None,
    working_directory: Annotated[Path | None, typer.Option("--working-directory")] = None,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually start the selected harness."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Explicitly preview argv without starting a harness."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    if execute and dry_run:
        raise typer.BadParameter("--execute and --dry-run are mutually exclusive")
    _perform(
        context,
        json_output,
        lambda service: service.launch_output(
            project,
            target,
            harness=harness,
            model=model,
            working_directory=working_directory,
            execute=execute and not dry_run,
        ),
        lambda result: (
            f"Harness session completed with status {result.returncode}."
            if result.executed
            else f"Preview argv: {json.dumps(result.argv)}"
        ),
    )


@app.command("copy-path")
def copy_path_command(
    context: typer.Context,
    target: Annotated[str, typer.Argument(help="Managed output ID.")],
    project: Annotated[str, typer.Option("--project", "-p")],
    uri: Annotated[bool, typer.Option("--uri", help="Copy the percent-encoded file URI.")] = False,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually write to the platform clipboard."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.copy_output(
            project,
            target,
            as_uri=uri,
            execute=execute,
        ),
        lambda result: result.message,
    )


@app.command("open")
def open_command(
    context: typer.Context,
    target: Annotated[str, typer.Argument(help="Managed output ID.")],
    project: Annotated[str, typer.Option("--project", "-p")],
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually reveal the file in the platform file manager."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.open_output(project, target, execute=execute),
        lambda result: result.message,
    )


@app.command("demo")
def demo_command(
    context: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.materialize_demo(),
        _demo_text,
    )


@app.command("ui")
def ui_command(
    context: typer.Context,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65_535)] = 8501,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview Streamlit argv instead of starting the server."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    _perform(
        context,
        json_output,
        lambda service: service.launch_ui(host=host, port=port, execute=not dry_run),
        lambda result: (
            f"Streamlit session ended with status {result.returncode}."
            if result.executed
            else f"Preview argv: {json.dumps(result.argv)}"
        ),
    )


T = TypeVar("T")


def _service(context: typer.Context) -> HandoffApplication:
    state = context.find_root().obj
    if not isinstance(state, _CLIState):
        raise RuntimeError("CLI application state was not initialized")
    if state.service is None:
        state.service = build_application(
            state.settings,
            enabled_extensions=state.enabled_extensions,
        )
    return state.service


def _perform(
    context: typer.Context,
    json_output: bool,
    operation: Callable[[HandoffApplication], T],
    human: Callable[[T], str],
) -> None:
    try:
        result = operation(_service(context))
    except (HandoffForgeError, OSError, UnicodeError, ValueError) as error:
        message = redact_secrets(str(error))[:1_000]
        if json_output:
            typer.echo(json.dumps({"error": message}, ensure_ascii=False), err=True)
        else:
            typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=2) from None
    if json_output:
        typer.echo(json.dumps(_jsonable(result), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        typer.echo(human(result))


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value


def _doctor_text(report: dict[str, Any]) -> str:
    checks = report["checks"]
    lines = [
        f"Handoff Forge ready: {report['ready']}",
        f"Network enabled: {report['network_enabled']}",
    ]
    lines.extend(f"{check['status']}: {check['name']} — {check['detail']}" for check in checks)
    return "\n".join(lines)


def _extensions_text(extensions: Sequence[object]) -> str:
    if not extensions:
        return "No provider or harness extensions are installed."
    lines = ["NAME\tKIND\tSTATUS\tENTRY POINT"]
    for extension in extensions:
        name = getattr(extension, "name", "unknown")
        kind = getattr(extension, "kind", "unknown")
        status = getattr(extension, "status", "unknown")
        value = getattr(extension, "value", "unknown")
        lines.append(f"{name}\t{kind}\t{status}\t{value}")
        reason = getattr(extension, "reason", None)
        if reason:
            lines.append(f"  {reason}")
    return "\n".join(lines)


def _model_text(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)


def _generation_output_path(result: GenerationOutcome) -> Path:
    if result.output is None:
        raise CapabilityError("generation completed without a readable output")
    return result.output.stored_path


def _demo_text(result: DemoOutcome) -> str:
    return f"Demo handoff: {_generation_output_path(result.generation)}"


if __name__ == "__main__":  # pragma: no cover
    app()
