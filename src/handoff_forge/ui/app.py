"""Streamlit workbench for local-first handoff capture, composition, and continuation."""

from __future__ import annotations

import html
import os
import shlex
from collections.abc import Callable, Collection, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any, cast

import streamlit as st

from handoff_forge.application import (
    GenerationOutcome,
    HandoffApplication,
    MergeOutcome,
    build_application,
)
from handoff_forge.config import HandoffSettings
from handoff_forge.errors import HandoffForgeError
from handoff_forge.handoffs.catalog import HANDOFF_SECTION_SPECS
from handoff_forge.models import (
    BlockKind,
    HandoffMode,
    JobStatus,
    ModelRoute,
    ParsedDocument,
    ProjectRecord,
    SourceArtifact,
    TemplateProfile,
)
from handoff_forge.providers.base import ProviderStatus
from handoff_forge.security import redact_secrets
from handoff_forge.storage import StoredOutput
from handoff_forge.ui.presentation import (
    apply_theme,
    empty_state,
    format_bytes,
    journey_steps,
    page_header,
    short_hash,
    status_badge,
    summary_strip,
    workspace_context,
)
from handoff_forge.ui.state import (
    clear_project_state,
    get_project_state,
    initialize_state,
    pop_flash,
    pop_project_state,
    set_flash,
    set_project_state,
)
from handoff_forge.ui.workflow import (
    PRIMARY_WORKSPACE_VIEWS,
    SECONDARY_WORKSPACE_VIEWS,
    recommend_next_action,
    resolve_view_key,
    view_label,
)

VIEWS = tuple(view.key for view in PRIMARY_WORKSPACE_VIEWS)
_MODEL_DEFAULTS = {
    "offline": "extractive-v1",
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "google": "gemini-2.5-flash",
    "xai": "grok-3-mini",
}
_PROFILE_LABELS = {
    TemplateProfile.CODEX_PRECOMPACT_V1: "Save progress",
    TemplateProfile.CODEX_POST_CHAT_V1: "Finish and hand off",
    TemplateProfile.GOAL_V1: "Combined continuation plan",
}
_BLOCK_PREVIEW_LIMIT = 8_000
_HANDOFF_VALIDATION_PROFILES = (
    TemplateProfile.CODEX_PRECOMPACT_V1,
    TemplateProfile.CODEX_POST_CHAT_V1,
    TemplateProfile.GOAL_V1,
)
_VIEW_ANCHORS = {
    "Home": "home",
    "Files": "files",
    "Create handoff": "create-handoff",
    "Combine handoffs": "combine-handoffs",
    "Start session": "start-session",
    "Settings": "settings",
}


@st.cache_resource(show_spinner=False)
def _cached_application(
    data_root: str,
    offline: bool,
    allow_network: bool,
    enabled_extensions: tuple[str, ...],
) -> HandoffApplication:
    settings = HandoffSettings(
        data_root=Path(data_root),
        offline=offline,
        allow_network=allow_network,
    )
    return build_application(settings, enabled_extensions=enabled_extensions)


def get_application() -> HandoffApplication:
    """Return the durable application service, with a test-only state override."""

    override = st.session_state.get("_application_override")
    if override is not None:
        return cast(HandoffApplication, override)
    settings = HandoffSettings()
    enabled_extensions = tuple(
        name.strip()
        for name in os.environ.get("HANDOFF_FORGE_ENABLED_EXTENSIONS", "").split(",")
        if name.strip()
    )
    return _cached_application(
        str(settings.data_root),
        settings.offline,
        settings.allow_network,
        enabled_extensions,
    )


def build_route_matrix(
    *,
    global_provider: str,
    global_model: str,
    allow_cloud_upload: bool,
    global_include_visual_evidence: bool = False,
    overrides: Mapping[int, tuple[str, str, bool]] | None = None,
    remote_providers: Collection[str] | None = None,
) -> dict[int, ModelRoute]:
    """Build an exact 12-section route map from global and optional overrides."""

    route_matrix: dict[int, ModelRoute] = {}
    for section_id in range(1, 13):
        provider, model, include_visual_evidence = (overrides or {}).get(
            section_id,
            (global_provider, global_model, global_include_visual_evidence),
        )
        provider_is_remote = (
            provider != "offline" if remote_providers is None else provider in remote_providers
        )
        route_matrix[section_id] = ModelRoute(
            provider=provider,
            model=model,
            allow_cloud_upload=allow_cloud_upload and provider_is_remote,
            include_visual_evidence=include_visual_evidence,
        )
    return route_matrix


def launch_preview_matches_selection(
    preview_state: object,
    *,
    output_id: str,
    harness: str,
    model: str | None,
) -> bool:
    """Return whether a cached launch preview still matches every user-controlled input."""

    if not isinstance(preview_state, Mapping):
        return False
    return (
        preview_state.get("output_id") == output_id
        and preview_state.get("harness") == harness
        and preview_state.get("model") == model
        and isinstance(preview_state.get("argv"), list)
        and isinstance(preview_state.get("cwd"), str)
    )


def _file_addition_message(added: int, duplicates: int, warnings: int) -> str:
    """Describe one upload result without implying duplicate files were added again."""

    if min(added, duplicates, warnings) < 0:
        raise ValueError("file result counts cannot be negative")
    parts: list[str] = []
    if added:
        parts.append(f"Added {added} {'file' if added == 1 else 'files'}.")
    if duplicates:
        if added:
            noun = "file was" if duplicates == 1 else "files were"
            parts.append(f"{duplicates} {noun} already in this workspace.")
        else:
            parts.append(
                "That file was already in this workspace."
                if duplicates == 1
                else "Those files were already in this workspace."
            )
    if warnings:
        parts.append(f"{warnings} {'item needs' if warnings == 1 else 'items need'} review.")
    return " ".join(parts)


def _render_view_heading(title: str, description: str, *, eyebrow: str | None = None) -> None:
    """Render a compact, stable page heading without a repeated marketing hero."""

    anchor = _VIEW_ANCHORS[title]
    if eyebrow:
        st.markdown(
            f'<div class="hf-page-eyebrow">{html.escape(eyebrow)}</div>',
            unsafe_allow_html=True,
        )
    st.title(title, anchor=anchor)
    st.caption(description)


def main() -> None:
    st.set_page_config(
        page_title="Handoff Forge",
        page_icon="⚒️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    initialize_state(_state())
    _initialize_ui_state()
    apply_theme()
    application = get_application()

    try:
        projects = application.list_projects()
    except Exception as error:
        st.markdown(
            page_header(
                "Handoff Forge",
                "Your private workspace could not be opened.",
            ),
            unsafe_allow_html=True,
        )
        _render_error("The local project library could not be opened.", error)
        st.stop()

    projects = sorted(projects, key=lambda project: (project.updated_at, project.id), reverse=True)
    active_project = _render_sidebar(application, projects)
    _render_flash()

    if active_project is None:
        _render_empty_workbench(application)
        return

    active_view = resolve_view_key(st.session_state.get("active_view"))
    st.session_state["active_view"] = active_view
    st.markdown(
        workspace_context(active_project.name, view_label(active_view)),
        unsafe_allow_html=True,
    )
    if active_view == "home":
        _render_home(application, active_project)
    elif active_view == "sources":
        _render_library(application, active_project)
    elif active_view == "create":
        _render_compose(application, active_project)
    elif active_view == "combine":
        _render_merge(application, active_project)
    elif active_view == "continue":
        _render_continue(application, active_project)
    else:
        _render_diagnostics(application, active_project)


def _initialize_ui_state() -> None:
    pending_view = st.session_state.pop("_pending_view", None)
    if pending_view is not None:
        resolved = resolve_view_key(pending_view)
        st.session_state["active_view"] = resolved
        if resolved in VIEWS:
            st.session_state["workspace-view"] = resolved
        else:
            st.session_state["workspace-view"] = None
    pending_project_id = st.session_state.pop("_pending_project_id", None)
    if pending_project_id is not None:
        st.session_state["active_project_id"] = pending_project_id
        st.session_state.pop("project-selector", None)


def _render_sidebar(
    application: HandoffApplication,
    projects: Sequence[ProjectRecord],
) -> ProjectRecord | None:
    with st.sidebar:
        st.markdown(
            '<div class="hf-sidebar-brand"><strong>Handoff Forge</strong>'
            "<span>Local continuity workspace</span></div>",
            unsafe_allow_html=True,
        )

        active_project: ProjectRecord | None = None
        if projects:
            project_by_id = {project.id: project for project in projects}
            preferred_id = st.session_state.get("active_project_id")
            if preferred_id not in project_by_id:
                preferred_id = projects[0].id
            if st.session_state.get("project-selector") not in project_by_id:
                st.session_state.pop("project-selector", None)
            project_options = list(project_by_id)
            selected_id: str | None = st.selectbox(
                "Workspace",
                options=project_options,
                index=project_options.index(preferred_id),
                format_func=lambda project_id: project_by_id[project_id].name,
                key="project-selector",
            )
            if selected_id not in project_by_id:
                selected_id = preferred_id
            st.session_state["active_project_id"] = selected_id
            active_project = project_by_id[selected_id]

            current_view = resolve_view_key(st.session_state.get("active_view"))
            workspace_view = st.session_state.get("workspace-view")
            if workspace_view is not None and workspace_view not in VIEWS:
                st.session_state.pop("workspace-view", None)
            navigation_index = (
                VIEWS.index(current_view)
                if "workspace-view" not in st.session_state and current_view in VIEWS
                else None
            )
            selected_view: str | None = st.radio(
                "Workspace navigation",
                options=VIEWS,
                index=navigation_index,
                format_func=view_label,
                key="workspace-view",
            )
            if selected_view is not None:
                st.session_state["active_view"] = selected_view
            effective_view = selected_view or current_view

            with st.expander(
                "More",
                expanded=effective_view in {view.key for view in SECONDARY_WORKSPACE_VIEWS},
            ):
                for view in SECONDARY_WORKSPACE_VIEWS:
                    if st.button(
                        view.label,
                        key=f"secondary-view-{view.key}",
                        width="stretch",
                    ):
                        st.session_state["_pending_view"] = view.key
                        st.rerun()

            with st.expander("New workspace"):
                _render_create_workspace_form(
                    application,
                    form_key="sidebar-create-workspace",
                    button_label="Create workspace",
                    next_view="sources",
                    compact=True,
                )

        st.divider()
        settings = application.settings
        if settings.network_enabled:
            st.warning("Network-capable. Every remote upload still requires your consent.")
        else:
            st.markdown(
                status_badge(
                    "Local-only",
                    tone="success",
                    description=(
                        "Files stay on this device. Remote processing stays off unless you "
                        "choose and approve it."
                    ),
                ),
                unsafe_allow_html=True,
            )
        return active_project


def _render_empty_workbench(application: HandoffApplication) -> None:
    st.markdown(
        page_header(
            "Carry your work into a new session",
            "Add project notes or PDFs, create a checked handoff, then use it in your coding app.",
        ),
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="hf-definition"><strong>A handoff is a checked Markdown file</strong> '
        "with the current state, decisions, proof, risks, and next steps.</p>",
        unsafe_allow_html=True,
    )
    st.markdown(
        journey_steps(("Add files", "Create handoff", "Start session")),
        unsafe_allow_html=True,
    )
    primary, sample = st.columns((1.4, 0.8), gap="large")
    with primary:
        st.markdown("### Create your workspace")
        _render_create_workspace_form(
            application,
            form_key="first-workspace",
            button_label="Create workspace",
            next_view="sources",
        )
    with sample:
        st.markdown("### Prefer to look around first?")
        st.caption("Open a ready-made local workspace and follow the same three steps.")
        sample_clicked = st.button(
            "Explore sample workspace",
            key="materialize-demo",
            width="stretch",
            help="Uses no account, credentials, provider, or network connection.",
        )
    if sample_clicked:
        try:
            with st.spinner("Preparing the sample workspace…"):
                outcome = application.materialize_demo()
        except Exception as error:
            _render_error("The sample workspace could not be prepared.", error)
        else:
            st.session_state["active_project_id"] = outcome.project.id
            st.session_state["_pending_view"] = "home"
            set_flash(_state(), "Sample workspace is ready.", tone="success")
            st.rerun()


def _render_create_workspace_form(
    application: HandoffApplication,
    *,
    form_key: str,
    button_label: str,
    next_view: str,
    compact: bool = False,
) -> None:
    with st.form(form_key, clear_on_submit=compact):
        name = st.text_input(
            "Workspace name",
            value="" if compact else "My workspace",
            placeholder="Release continuity",
            help="Shown only in your local workspace list.",
            key=f"{form_key}-name",
        )
        description = st.text_area(
            "What are you working on? (optional)",
            placeholder="Preserve the work and continue it safely.",
            height=80 if compact else 96,
            key=f"{form_key}-description",
        )
        submitted = st.form_submit_button(button_label, type="primary", width="stretch")
    if not submitted:
        return
    workspace_name = name.strip() or "My workspace"
    try:
        project = application.create_project(workspace_name, description)
    except Exception as error:
        _render_error("Workspace creation failed.", error)
        return
    st.session_state["active_project_id"] = project.id
    st.session_state["_pending_project_id"] = project.id
    st.session_state["_pending_view"] = next_view
    set_flash(
        _state(), f"{project.name} is ready. Add your files when you are ready.", tone="success"
    )
    st.rerun()


def _render_home(application: HandoffApplication, project: ProjectRecord) -> None:
    _render_view_heading(
        "Home",
        "Pick up where you left off, or take the next useful step.",
    )
    try:
        inspection = application.inspect_project(project.id)
    except Exception as error:
        _render_error("Workspace summary could not be loaded.", error)
        return

    next_action = recommend_next_action(inspection.artifact_count, inspection.output_count)
    st.markdown(
        summary_strip(
            (
                ("Files", str(inspection.artifact_count)),
                ("Handoffs", str(inspection.output_count)),
                ("Needs review", str(len(inspection.warnings))),
                ("Updated", project.updated_at.astimezone().strftime("%b %d")),
            )
        ),
        unsafe_allow_html=True,
    )

    st.markdown("### Next step")
    st.write(next_action.description)
    if st.button(next_action.title, type="primary", key="recommended-next-action"):
        st.session_state["_pending_view"] = next_action.view_key
        st.rerun()

    recent_files, recent_handoffs = st.columns(2, gap="large")
    with recent_files:
        st.markdown("### Recent files")
        artifacts = sorted(inspection.artifacts, key=lambda item: item.created_at, reverse=True)
        if not artifacts:
            st.caption("No files yet.")
        for artifact in artifacts[:4]:
            st.markdown(
                f"<strong>{html.escape(artifact.display_name)}</strong>",
                unsafe_allow_html=True,
            )
            st.caption(f"{artifact.kind.value.upper()} · {format_bytes(artifact.size_bytes)}")
        if artifacts and st.button("View all files", key="home-view-sources"):
            st.session_state["_pending_view"] = "sources"
            st.rerun()
    with recent_handoffs:
        st.markdown("### Recent handoffs")
        outputs = sorted(inspection.outputs, key=lambda item: item.created_at, reverse=True)
        if not outputs:
            st.caption("No handoffs yet.")
        for output in outputs[:4]:
            st.markdown(
                f"<strong>{html.escape(_output_label(output))}</strong>",
                unsafe_allow_html=True,
            )
        if outputs and st.button("Open latest handoff", key="home-view-continue"):
            st.session_state["_pending_view"] = "continue"
            st.rerun()


def _render_library(application: HandoffApplication, project: ProjectRecord) -> None:
    _render_view_heading(
        "Files",
        "Add the files that explain the work. Your originals stay unchanged.",
        eyebrow="Step 1 of 3",
    )
    try:
        inspection = application.inspect_project(project.id)
    except Exception as error:
        _render_error("We could not open this workspace.", error)
        return

    st.markdown("### Add files")
    upload_generation = int(get_project_state(_state(), project.id, "upload_generation", 0) or 0)
    uploads = st.file_uploader(
        "Project files (.md, .mdc, .pdf)",
        type=["md", "mdc", "pdf"],
        accept_multiple_files=True,
        help=(
            "MDC is Markdown saved with the .mdc extension. Files can be up to 50 MB each; "
            "originals are not changed."
        ),
        key=f"project-files-{project.id}-{upload_generation}",
    )
    if st.button(
        "Add files",
        type="primary",
        disabled=not uploads,
        help="Remote links inside Markdown are recorded but never fetched.",
    ):
        added = 0
        duplicates = 0
        processed = 0
        seen_artifact_ids = {artifact.id for artifact in inspection.artifacts}
        all_warnings: list[str] = []
        for upload in uploads or []:
            try:
                result = application.ingest_bytes(project.id, upload.name, upload.getvalue())
            except Exception as error:
                _render_error(f"Could not preserve {upload.name}.", error)
                continue
            processed += 1
            if result.artifact.id in seen_artifact_ids:
                duplicates += 1
            else:
                added += 1
                seen_artifact_ids.add(result.artifact.id)
            all_warnings.extend(warning.message for warning in result.warnings)
        if processed:
            set_project_state(
                _state(),
                project.id,
                "upload_generation",
                upload_generation + 1,
            )
            message = _file_addition_message(added, duplicates, len(all_warnings))
            set_flash(_state(), message, tone="success" if added else "info")
            st.rerun()

    if not inspection.artifacts:
        st.markdown(
            empty_state(
                "No files yet",
                "Choose one or more Markdown, MDC, or PDF files above to get started.",
            ),
            unsafe_allow_html=True,
        )
        return

    status_text = f"{inspection.artifact_count} file"
    if inspection.artifact_count != 1:
        status_text += "s"
    status_text += " ready"
    status_tone = "warning" if inspection.warnings else "success"
    status_description = (
        f"{len(inspection.warnings)} extraction warning(s) need review."
        if inspection.warnings
        else "Files were preserved and are ready to use."
    )
    st.markdown(
        status_badge(status_text, tone=status_tone, description=status_description),
        unsafe_allow_html=True,
    )
    st.caption("Next, turn these files into a checked handoff.")
    if st.button("Create a handoff", type="primary", key="files-go-create"):
        st.session_state["_pending_view"] = "create"
        st.rerun()

    review_toggle_key = f"review-files-toggle-{project.id}"
    review_open = st.toggle(
        "Review files",
        value=bool(get_project_state(_state(), project.id, "review_files_open", False)),
        key=review_toggle_key,
        help="Show local search, parsed content, images, and technical file details.",
        on_change=_remember_file_review_state,
        args=(project.id, review_toggle_key),
    )
    if review_open:
        _render_file_review(application, project, inspection.artifacts)


def _remember_file_review_state(project_id: str, widget_key: str) -> None:
    """Persist a reversible review disclosure after Streamlit removes hidden widgets."""

    set_project_state(
        _state(),
        project_id,
        "review_files_open",
        bool(st.session_state.get(widget_key, False)),
    )


def _render_file_review(
    application: HandoffApplication,
    project: ProjectRecord,
    artifacts: Sequence[SourceArtifact],
) -> None:
    """Render optional search and parsed evidence behind one disclosure."""

    st.markdown("#### Search")
    query = st.text_input(
        "Search files",
        placeholder="validation blocker",
        help="Search stays inside this workspace and runs locally.",
        label_visibility="collapsed",
        key=f"review-files-search-{project.id}",
    )
    if st.button(
        "Search",
        disabled=not query.strip(),
        key=f"search-sources-{project.id}",
    ):
        try:
            hits = application.search(project.id, query, limit=8)
        except Exception as error:
            _render_error("Search failed.", error)
        else:
            if not hits:
                st.info("No matching content was found.")
            for hit in hits:
                with st.container(border=True):
                    st.markdown(f"**{hit.metadata.get('block_kind', 'Content').title()} match**")
                    st.text(hit.text[:_BLOCK_PREVIEW_LIMIT])

    st.markdown("#### Browse")
    artifact_by_id = {artifact.id: artifact for artifact in artifacts}
    selected_artifact_id = st.selectbox(
        "Source file",
        options=list(artifact_by_id),
        format_func=lambda artifact_id: artifact_by_id[artifact_id].display_name,
        key=f"review-source-file-{project.id}",
    )
    artifact = artifact_by_id[selected_artifact_id]
    st.session_state["selected_artifact_id"] = artifact.id
    st.caption(
        f"{artifact.kind.value.upper()} · {format_bytes(artifact.size_bytes)} · private local file"
    )

    try:
        document = application.inspect_artifact(project.id, artifact.id)
    except Exception as error:
        _render_error("We could not read this file.", error)
        return
    _render_document(document, artifact_sha256=artifact.sha256)


def _render_document(document: ParsedDocument, *, artifact_sha256: str | None = None) -> None:
    content_tab, assets_tab, details_tab = st.tabs(("Content", "Images & pages", "Details"))
    with content_tab:
        if document.warnings:
            for warning in document.warnings:
                location = f" Page {warning.page_number}." if warning.page_number else ""
                st.warning(f"{warning.message}{location}")
        else:
            st.markdown(status_badge("Ready", tone="success"), unsafe_allow_html=True)
        for block in document.blocks:
            location = _block_location(block)
            st.markdown(f"**{block.order + 1}. {block.kind.value.title()} · {location}**")
            if block.kind in {BlockKind.CODE, BlockKind.TABLE}:
                st.code(block.text[:_BLOCK_PREVIEW_LIMIT], language="markdown")
            else:
                st.text(block.text[:_BLOCK_PREVIEW_LIMIT])
    with assets_tab:
        st.caption(
            "Page renders and images stay local. Image-aware processing is available only when "
            "you explicitly choose and confirm a compatible route."
        )
        visuals = [
            block
            for block in document.blocks
            if block.kind in {BlockKind.IMAGE, BlockKind.CHART, BlockKind.PAGE_RENDER}
            and block.artifact_path is not None
        ]
        if not visuals:
            st.info("This file has no preserved page image or image crop.")
        for visual in visuals:
            path = Path(visual.artifact_path or "")
            if path.is_file():
                st.image(
                    str(path),
                    caption=f"{visual.kind.value} · {_block_location(visual)}",
                    width="stretch",
                )
    with details_tab:
        if artifact_sha256:
            st.caption(f"SHA-256 · {artifact_sha256}")
        st.caption(f"Parser profile · {document.parser_profile}")
        if document.frontmatter:
            st.markdown("**Document metadata**")
            st.json(document.frontmatter)
        if document.references:
            st.markdown("**References**")
            st.dataframe(
                [
                    {
                        "Reference": reference.reference,
                        "Kind": reference.kind,
                        "Preserved": str(reference.resolved_path or "—"),
                    }
                    for reference in document.references
                ],
                width="stretch",
                hide_index=True,
            )
        st.markdown("**Extraction details**")
        st.dataframe(
            [
                {
                    "Item": block.order + 1,
                    "Type": block.kind.value,
                    "Location": _block_location(block),
                    "Method": block.extraction_method,
                    "Confidence": f"{block.confidence:.0%}",
                    "Block": short_hash(block.id),
                }
                for block in document.blocks
            ],
            hide_index=True,
            width="stretch",
        )


def _render_compose(application: HandoffApplication, project: ProjectRecord) -> None:
    _render_view_heading(
        "Create handoff",
        "Choose the moment you are preparing for. The safe local route is ready by default.",
        eyebrow="Step 2 of 3",
    )
    try:
        inspection = application.inspect_project(project.id)
        statuses = tuple(application.providers.statuses())
    except Exception as error:
        _render_error("We could not check handoff readiness.", error)
        return

    status_by_name = {status.name: status for status in statuses}
    if inspection.artifact_count:
        file_label = (
            f"{inspection.artifact_count} "
            f"{'file' if inspection.artifact_count == 1 else 'files'} · local processing"
        )
        file_tone = "success"
        file_description = "The normal workflow runs on this device and makes no remote calls."
    else:
        file_label = "No files added"
        file_tone = "neutral"
        file_description = "Add a Markdown, MDC, or PDF file before creating a handoff."
    st.markdown(
        status_badge(
            file_label,
            tone=file_tone,
            description=file_description,
        ),
        unsafe_allow_html=True,
    )

    profile = st.radio(
        "When will you use this?",
        options=(
            TemplateProfile.CODEX_PRECOMPACT_V1,
            TemplateProfile.CODEX_POST_CHAT_V1,
        ),
        format_func=lambda item: _PROFILE_LABELS[item],
        horizontal=True,
        help=(
            "Save progress before context is shortened; finish and hand off when the task is done."
        ),
    )
    if profile is TemplateProfile.CODEX_PRECOMPACT_V1:
        st.caption(
            "Save the current state before the conversation is shortened or context is lost."
        )
    else:
        st.caption("Package completed work, proof, open risks, and the exact next steps.")
    mode = (
        HandoffMode.PRE_COMPACT
        if profile is TemplateProfile.CODEX_PRECOMPACT_V1
        else HandoffMode.POST_TASK
    )
    provider_names = tuple(status_by_name) or ("offline",)
    overrides: dict[int, tuple[str, str, bool]] = {}
    consent = False
    with st.expander("Advanced processing", expanded=False):
        st.caption(
            "The normal workflow needs no changes here. Remote routes always require explicit "
            "consent for this run."
        )
        selected_global_provider = st.selectbox(
            "Processing provider",
            options=provider_names,
            index=provider_names.index("offline") if "offline" in provider_names else 0,
            format_func=lambda name: _provider_label(status_by_name.get(name), name),
        )
        global_provider = selected_global_provider or "offline"
        default_model = _MODEL_DEFAULTS.get(global_provider, "extractive-v1")
        global_model = st.text_input(
            "Model identifier",
            value=default_model,
            help="The exact identifier is preserved in the handoff route manifest.",
        )
        global_include_visual_evidence = st.checkbox(
            "Include preserved PDF pages and images",
            value=False,
            help=(
                "Send preserved page images and image crops only when the exact selected "
                "model/version accepts image input. This is not live capability discovery."
            ),
        )
        customize = st.checkbox("Use different processing for individual sections")
        if customize:
            st.markdown("#### Section processing")
            st.caption("Each section remains independently reroutable after a failed run.")
            for spec in HANDOFF_SECTION_SPECS:
                provider_column, model_column, visual_column = st.columns((0.3, 0.45, 0.25))
                with provider_column:
                    provider_choice: str | None = st.selectbox(
                        f"Section {spec.id} provider",
                        options=provider_names,
                        index=provider_names.index(global_provider),
                        format_func=lambda name: _provider_label(status_by_name.get(name), name),
                        key=f"route-provider-{project.id}-{spec.id}",
                    )
                    provider = provider_choice or global_provider
                with model_column:
                    model = st.text_input(
                        f"Section {spec.id}: {spec.title}",
                        value=(
                            global_model
                            if provider == global_provider
                            else _MODEL_DEFAULTS.get(provider, global_model)
                        ),
                        key=f"route-model-{project.id}-{spec.id}",
                    )
                with visual_column:
                    include_visual_evidence = st.checkbox(
                        f"Section {spec.id} preserved visuals",
                        value=global_include_visual_evidence,
                        help=(
                            "Send preserved page images and image crops for this section. "
                            "Extracted visual text remains available when disabled."
                        ),
                        key=f"route-visual-{project.id}-{spec.id}",
                    )
                overrides[spec.id] = (provider, model, include_visual_evidence)
        selected_providers = (
            {provider for provider, _model, _visual in overrides.values()}
            if overrides
            else {global_provider}
        )
        visual_attestation_selected = (
            any(include_visual for _provider, _model, include_visual in overrides.values())
            if overrides
            else global_include_visual_evidence
        )
        if visual_attestation_selected:
            st.info(
                "Visual file inclusion is your confirmation for each exact model/version, not a "
                "live capability check."
            )
        selected_remote_providers = {
            name for name in selected_providers if application.providers.is_remote(name)
        }
        remote_selected = bool(selected_remote_providers)
        if remote_selected:
            st.warning(
                "A remote route can upload only the content selected for its section. Complete "
                "source files are never silently uploaded."
            )
            consent = st.checkbox(
                "I consent to the selected content being sent to the chosen remote providers "
                "for this run"
            )
        else:
            st.caption("This setup makes zero remote provider calls.")
        _render_provider_statuses(statuses)

    route_matrix = build_route_matrix(
        global_provider=global_provider,
        global_model=global_model,
        allow_cloud_upload=consent,
        global_include_visual_evidence=global_include_visual_evidence,
        overrides=overrides,
        remote_providers=selected_remote_providers,
    )
    unavailable = [
        name
        for name in selected_providers
        if name not in status_by_name or not status_by_name[name].enabled
    ]
    if unavailable:
        st.error("Unavailable route selected: " + ", ".join(sorted(unavailable)))
    unsupported_visual_routes = [
        section_id
        for section_id, route in route_matrix.items()
        if route.include_visual_evidence
        and route.provider in status_by_name
        and not status_by_name[route.provider].capabilities.image_input
    ]
    if unsupported_visual_routes:
        st.error(
            "Visual files were enabled for section(s) "
            + ", ".join(str(section_id) for section_id in unsupported_visual_routes)
            + ", but those provider adapters do not support image input."
        )
    if not inspection.artifacts:
        st.info("Add at least one source file before creating a handoff.")
        if st.button("Go to Files", key="create-go-sources"):
            st.session_state["_pending_view"] = "sources"
            st.rerun()

    generate = st.button(
        "Create handoff",
        type="primary",
        disabled=(
            bool(unavailable)
            or bool(unsupported_visual_routes)
            or not inspection.artifacts
            or (remote_selected and not consent)
        ),
    )
    outcome: GenerationOutcome | None = None
    if generate:
        try:
            with st.spinner("Creating and checking your handoff…"):
                outcome = application.generate_handoff(
                    project.id,
                    mode=mode,
                    profile=profile,
                    routes=route_matrix,
                )
        except Exception as error:
            _render_error("Handoff generation failed.", error)
        else:
            _remember_generation(project.id, outcome)

    if outcome is not None:
        _render_generation_outcome(application, project, outcome)
    else:
        _render_remembered_job(application, project)


def _render_provider_statuses(statuses: Sequence[ProviderStatus]) -> None:
    st.markdown("#### Processing options")
    st.dataframe(
        [
            {
                "Provider": status.name,
                "State": status.state,
                "Enabled": status.enabled,
                "Text": status.capabilities.text,
                "Images": status.capabilities.image_input,
                "Boundary": status.reason or "Ready for explicitly selected content",
            }
            for status in statuses
        ],
        hide_index=True,
        width="stretch",
    )


def _render_generation_outcome(
    application: HandoffApplication,
    project: ProjectRecord,
    outcome: GenerationOutcome,
) -> None:
    completed = len(outcome.job.completed_section_ids)
    st.progress(completed / 12, text=f"{completed} of 12 sections complete")
    if outcome.job.status is JobStatus.COMPLETE and outcome.output and outcome.validation:
        st.success("Handoff ready and checked.")
        if st.button("Start a session", type="primary", key="open-generated-output"):
            st.session_state["_pending_view"] = "continue"
            st.rerun()
        with st.expander("File details"):
            st.caption(str(outcome.output.stored_path))
            st.caption(f"SHA-256 · {outcome.output.sha256}")
        return
    if outcome.job.status is JobStatus.FAILED:
        st.error(outcome.job.error or "Generation stopped at a section boundary.")
        if st.button("Retry incomplete sections", key=f"resume-{outcome.job.id}"):
            try:
                resumed = application.resume_job(project.id, outcome.job.id)
            except Exception as error:
                _render_error("The generation job could not be resumed.", error)
            else:
                _remember_generation(project.id, resumed)
                st.rerun()
    elif outcome.job.status in {JobStatus.PENDING, JobStatus.RUNNING}:
        st.info("Generation is checkpointed between sections.")
        if st.button("Request cancellation", key=f"cancel-{outcome.job.id}"):
            try:
                application.cancel_job(project.id, outcome.job.id)
            except Exception as error:
                _render_error("Cancellation could not be requested.", error)
            else:
                set_flash(_state(), "Cancellation requested at the next section boundary.")
                st.rerun()


def _render_remembered_job(application: HandoffApplication, project: ProjectRecord) -> None:
    job_id = get_project_state(_state(), project.id, "last_job_id")
    status = get_project_state(_state(), project.id, "last_job_status")
    if not job_id or not status:
        return
    if status == JobStatus.COMPLETE.value:
        output_id = get_project_state(_state(), project.id, "last_generated_output_id")
        output = next(
            (item for item in application.list_outputs(project.id) if item.id == output_id),
            None,
        )
        if output is None:
            pop_project_state(_state(), project.id, "last_job_id")
            pop_project_state(_state(), project.id, "last_job_status")
            pop_project_state(_state(), project.id, "last_generated_output_id")
            return
        st.success("Handoff ready and checked.")
        if st.button("Start a session", type="primary", key="open-generated-output"):
            st.session_state["_pending_view"] = "continue"
            st.rerun()
        with st.expander("File details"):
            st.caption(str(output.stored_path))
            st.caption(f"SHA-256 · {output.sha256}")
        return
    st.caption(f"Most recent job: {job_id} · {status}")
    if status == JobStatus.FAILED.value and st.button("Resume most recent job"):
        try:
            outcome = application.resume_job(project.id, str(job_id))
        except Exception as error:
            _render_error("The generation job could not be resumed.", error)
        else:
            _remember_generation(project.id, outcome)
            st.rerun()


def _remember_generation(project_id: str, outcome: GenerationOutcome) -> None:
    set_project_state(_state(), project_id, "last_job_id", outcome.job.id)
    set_project_state(_state(), project_id, "last_job_status", outcome.job.status.value)
    if outcome.output:
        set_project_state(
            _state(),
            project_id,
            "last_generated_output_id",
            outcome.output.id,
        )


def _is_structural_handoff(
    application: HandoffApplication,
    project: ProjectRecord,
    handoff: SourceArtifact | StoredOutput,
) -> bool:
    """Return whether a managed Markdown/MDC file satisfies a handoff profile."""

    for profile in _HANDOFF_VALIDATION_PROFILES:
        try:
            if isinstance(handoff, StoredOutput):
                application.validate_output(project.id, handoff.id, profile)
            else:
                application.validate_path(handoff.stored_path, profile)
        except (HandoffForgeError, OSError, UnicodeError):
            continue
        return True
    return False


def _render_merge(application: HandoffApplication, project: ProjectRecord) -> None:
    _render_view_heading(
        "Combine handoffs",
        "Bring two or more handoffs into one continuation plan without losing conflicts or rules.",
    )
    try:
        outputs = application.list_outputs(project.id)
        artifacts = application.list_artifacts(project.id)
    except Exception as error:
        _render_error("We could not list the handoffs in this workspace.", error)
        return
    markdown_candidates: list[SourceArtifact | StoredOutput] = [
        *[
            artifact
            for artifact in artifacts
            if artifact.stored_path.suffix.casefold() in {".md", ".mdc"}
        ],
        *[output for output in outputs if output.stored_path.suffix.casefold() in {".md", ".mdc"}],
    ]
    handoffs = [
        handoff
        for handoff in markdown_candidates
        if _is_structural_handoff(application, project, handoff)
    ]
    excluded_count = len(markdown_candidates) - len(handoffs)
    if excluded_count:
        noun = "file was" if excluded_count == 1 else "files were"
        st.caption(
            f"{excluded_count} Markdown/MDC {noun} hidden because they are not valid handoffs."
        )
    if len(handoffs) < 2:
        st.markdown(
            empty_state(
                "Two handoffs are needed",
                "Add or create another Markdown/MDC handoff, then return to combine them.",
            ),
            unsafe_allow_html=True,
        )
        return
    handoff_by_id = {handoff.id: handoff for handoff in handoffs}
    selected = st.multiselect(
        "Handoffs to combine",
        options=list(handoff_by_id),
        format_func=lambda handoff_id: _handoff_label(handoff_by_id[handoff_id]),
        help=(
            "Select at least two unique handoffs. Input order never changes the "
            "deterministic result."
        ),
    )
    target_profile = TemplateProfile.GOAL_V1
    if len(selected) == 1:
        st.warning("Select one more unique handoff to enable merge.")
    outcome: MergeOutcome | None = None
    if st.button(
        "Create combined plan",
        type="primary",
        disabled=len(selected) < 2,
    ):
        try:
            with st.spinner("Reconciling sections, constraints, conflicts, and next steps…"):
                outcome = application.merge_handoffs(
                    project.id,
                    selected,
                    target_profile=target_profile,
                )
        except Exception as error:
            _render_error("The selected handoffs could not be merged.", error)
        else:
            set_project_state(_state(), project.id, "last_merge_output_id", outcome.output.id)
            set_project_state(
                _state(),
                project.id,
                "last_merge_summary",
                {
                    "conflicts": len(outcome.plan.conflicts),
                    "tasks": len(outcome.plan.tasks),
                    "constraints": len(outcome.plan.preserved_constraints),
                },
            )
    if outcome is not None:
        _render_merge_outcome(outcome)
    elif summary := get_project_state(_state(), project.id, "last_merge_summary"):
        st.caption(
            f"Most recent merge: {summary['conflicts']} conflict(s), {summary['tasks']} task(s), "
            f"{summary['constraints']} preserved constraint(s)."
        )


def _render_merge_outcome(outcome: MergeOutcome) -> None:
    st.success("Combined plan is ready.")
    st.markdown(
        summary_strip(
            (
                ("Conflicts", str(len(outcome.plan.conflicts))),
                ("Next steps", str(len(outcome.plan.tasks))),
                ("Preserved rules", str(len(outcome.plan.preserved_constraints))),
            )
        ),
        unsafe_allow_html=True,
    )
    with st.expander("File details"):
        st.caption(str(outcome.output.stored_path))
    if outcome.plan.conflicts:
        st.markdown("### Conflict review")
        for conflict in outcome.plan.conflicts:
            with st.expander(f"Section {conflict.section_id} · {conflict.status}"):
                st.text(conflict.summary)
                for variant in conflict.variants:
                    st.code(variant, language=None)
                st.caption("Sources: " + ", ".join(conflict.source_refs))
                st.write(f"Resolution: {conflict.resolution}")
    if outcome.plan.tasks:
        st.markdown("### Unified execution plan")
        st.dataframe(
            [
                {
                    "Priority": task.priority,
                    "Task": task.title,
                    "Status": task.status,
                    "Dependencies": ", ".join(task.dependencies) or "—",
                }
                for task in outcome.plan.tasks
            ],
            hide_index=True,
            width="stretch",
        )


def _render_continue(application: HandoffApplication, project: ProjectRecord) -> None:
    _render_view_heading(
        "Start session",
        "Download a checked handoff or prepare it for an installed coding app.",
        eyebrow="Step 3 of 3",
    )
    try:
        outputs = application.list_outputs(project.id)
    except Exception as error:
        _render_error("We could not list your handoffs.", error)
        return
    if not outputs:
        st.markdown(
            empty_state(
                "No handoffs yet",
                "Create a handoff first, then return here to continue in a new session.",
            ),
            unsafe_allow_html=True,
        )
        if st.button("Create a handoff", type="primary", key="continue-go-create"):
            st.session_state["_pending_view"] = "create"
            st.rerun()
        return
    ordered_outputs = sorted(outputs, key=lambda item: item.created_at, reverse=True)
    output_by_id = {output.id: output for output in ordered_outputs}
    preferred = get_project_state(
        _state(), project.id, "last_merge_output_id"
    ) or get_project_state(_state(), project.id, "last_generated_output_id")
    options = [output.id for output in ordered_outputs]
    index = options.index(preferred) if preferred in output_by_id else 0
    selected_id = st.selectbox(
        "Handoff",
        options=options,
        index=index,
        format_func=lambda output_id: _output_label(output_by_id[output_id]),
        help=(
            "A handoff is a checked Markdown file with current state, decisions, proof, risks, "
            "and next steps."
        ),
    )
    output = output_by_id[selected_id]
    _render_output_summary(output)

    metadata_profile = str(output.metadata.get("profile") or TemplateProfile.GOAL_V1.value)
    try:
        default_profile = TemplateProfile(metadata_profile)
    except ValueError:
        default_profile = TemplateProfile.GOAL_V1
    validation_passed = False
    try:
        report = application.validate_output(project.id, output.id, default_profile)
    except Exception as error:
        st.warning(
            "This handoff cannot start a session because its structure check did not pass. "
            "You can still download or inspect it."
        )
        with st.expander("Validation details"):
            _render_error("Validation did not pass.", error)
    else:
        validation_passed = True
        st.markdown(
            status_badge(
                "Checked",
                tone="success",
                description="All required sections are present and passed the structure check.",
            ),
            unsafe_allow_html=True,
        )
        for warning in report.warnings:
            st.warning(warning)

    try:
        payload = output.stored_path.read_bytes()
    except OSError as error:
        payload = None
        _render_error("The handoff file could not be read.", error)

    st.markdown("### Open in an app")
    available_harnesses = application.available_harnesses() if validation_passed else ()
    harness: str | None = None
    normalized_model: str | None = None
    preview_state = get_project_state(_state(), project.id, "last_launch_preview")
    preview_button = False
    if not validation_passed:
        pop_project_state(_state(), project.id, "last_launch_preview")
        preview_state = None
        st.caption("Repair or recreate this handoff before preparing a launch command.")
        st.download_button(
            "Download handoff",
            data=payload or b"",
            file_name=output.stored_path.name,
            mime="text/markdown",
            disabled=payload is None,
            width="stretch",
        )
    elif available_harnesses:
        selected_harness = st.selectbox(
            "App",
            options=available_harnesses,
            format_func=lambda value: value.title(),
            help="Only installed, supported command-line coding apps are listed.",
        )
        harness = selected_harness or available_harnesses[0]
        with st.expander("App options"):
            model = st.text_input("Model (optional)", placeholder="Use the app default")
        normalized_model = model.strip() or None
        if not launch_preview_matches_selection(
            preview_state,
            output_id=output.id,
            harness=harness,
            model=normalized_model,
        ):
            pop_project_state(_state(), project.id, "last_launch_preview")
            preview_state = None
        st.caption(
            "Handoff Forge will show a launch command. You run this command in Terminal; "
            "the browser does not open the app itself."
        )
        action_column, download_column = st.columns(2)
        with action_column:
            preview_button = st.button(
                "Show launch command",
                type="primary",
                width="stretch",
            )
        with download_column:
            st.download_button(
                "Download handoff",
                data=payload or b"",
                file_name=output.stored_path.name,
                mime="text/markdown",
                disabled=payload is None,
                width="stretch",
            )
    else:
        pop_project_state(_state(), project.id, "last_launch_preview")
        preview_state = None
        st.warning(
            "No supported command-line app was found. Install Codex, Claude, Gemini, or Grok "
            "to create a launch command. You can still download the handoff."
        )
        st.download_button(
            "Download handoff",
            data=payload or b"",
            file_name=output.stored_path.name,
            mime="text/markdown",
            disabled=payload is None,
            width="stretch",
        )
    if preview_button and harness is not None:
        try:
            preview = application.launch_output(
                project.id,
                output.id,
                harness=harness,
                model=normalized_model,
                execute=False,
            )
        except Exception as error:
            _render_error("A new-session command could not be prepared for this app.", error)
        else:
            preview_state = {
                "output_id": output.id,
                "argv": list(preview.argv),
                "cwd": str(preview.cwd),
                "harness": preview.harness,
                "model": normalized_model,
            }
            set_project_state(_state(), project.id, "last_launch_preview", preview_state)
    if preview_state:
        st.markdown("#### Run in Terminal")
        st.code(
            " ".join(shlex.quote(argument) for argument in preview_state["argv"]),
            language="shell",
        )
        st.caption("Use the copy control, paste the command into Terminal, and press Return.")

    with st.expander("Preview handoff"):
        if payload is not None:
            decoded = payload.decode("utf-8", errors="replace")
            st.text_area(
                "Handoff content",
                value=decoded[:40_000],
                height=420,
                disabled=True,
            )

    with st.expander("Technical details"):
        profile_options = list(TemplateProfile)
        profile = st.selectbox(
            "Validation profile",
            options=profile_options,
            index=profile_options.index(default_profile),
            format_func=lambda item: _PROFILE_LABELS[item],
        )
        if st.button("Run validation", key="validate-selected-handoff"):
            try:
                manual_report = application.validate_output(project.id, output.id, profile)
            except Exception as error:
                _render_error("The handoff does not satisfy the selected profile.", error)
            else:
                st.success(
                    f"Valid profile · Sections {', '.join(map(str, manual_report.section_ids))}"
                )
                for warning in manual_report.warnings:
                    st.warning(warning)
        st.text_input("Raw output path", value=str(output.stored_path), disabled=True)
        st.text_input("File URL", value=output.file_uri, disabled=True)
        copy_path_column, copy_uri_column, reveal_column = st.columns(3)
        with copy_path_column:
            if st.button("Copy raw path", width="stretch"):
                _run_action(
                    lambda: application.copy_output(project.id, output.id, execute=True),
                    "Path copy",
                )
        with copy_uri_column:
            if st.button("Copy file URL", width="stretch"):
                _run_action(
                    lambda: application.copy_output(
                        project.id,
                        output.id,
                        as_uri=True,
                        execute=True,
                    ),
                    "File URL copy",
                )
        with reveal_column:
            if st.button("Open folder", width="stretch"):
                _run_action(
                    lambda: application.open_output(project.id, output.id, execute=True),
                    "Folder open",
                )
        if preview_state and harness is not None:
            st.caption(f"Working directory · {preview_state['cwd']} · shell=False")
            cli_argv = [
                "handoff-forge",
                "--data-root",
                str(application.settings.data_root),
                "launch",
                output.id,
                "--project",
                project.id,
                "--harness",
                harness,
            ]
            if normalized_model is not None:
                cli_argv.extend(("--model", normalized_model))
            cli_argv.append("--execute")
            st.markdown("**Interactive terminal command**")
            st.code(" ".join(shlex.quote(argument) for argument in cli_argv), language="shell")


def _render_output_summary(output: StoredOutput) -> None:
    st.markdown(
        summary_strip(
            (
                ("Handoff", _output_title(output)),
                ("Size", format_bytes(output.size_bytes)),
                ("Created", output.created_at.astimezone().strftime("%b %d, %I:%M %p")),
            )
        ),
        unsafe_allow_html=True,
    )


def _render_diagnostics(application: HandoffApplication, project: ProjectRecord) -> None:
    _render_view_heading(
        "Settings",
        "Review local privacy, system readiness, and workspace controls.",
    )
    try:
        report = application.doctor()
    except Exception as error:
        _render_error("System status could not be loaded.", error)
        return
    system_state = "Ready" if report["ready"] else "Needs attention"
    st.markdown(
        summary_strip(
            (
                ("Mode", "Offline" if report["offline"] else "Network-capable"),
                ("Remote access", "Off" if not report["network_enabled"] else "Available"),
                ("System", system_state),
            )
        ),
        unsafe_allow_html=True,
    )
    st.markdown("### Privacy")
    st.write(
        "Source files and generated handoffs are stored locally. Remote processing is disabled "
        "by default and always requires consent for the current run."
    )
    with st.expander("System check"):
        if report["ready"]:
            st.success("Required local runtime checks are ready.")
        else:
            st.warning("One or more local runtime checks need attention.")
        st.dataframe(report["checks"], hide_index=True, width="stretch")
        providers = []
        for provider in report["providers"]:
            capabilities = provider.get("capabilities", {})
            providers.append(
                {
                    "Provider": provider.get("name"),
                    "State": provider.get("state"),
                    "Enabled": provider.get("enabled"),
                    "Text": capabilities.get("text"),
                    "Images": capabilities.get("image_input"),
                    "Native PDF": capabilities.get("native_pdf"),
                    "Reason": provider.get("reason") or "Ready",
                }
            )
        st.dataframe(providers, hide_index=True, width="stretch")
    with st.expander("Storage details"):
        st.text_input("Private data root", value=str(report["data_root"]), disabled=True)

    st.markdown("### Extensions")
    try:
        extensions = application.list_extensions()
    except Exception as error:
        _render_error("Extension details could not be loaded.", error)
    else:
        if not extensions:
            st.caption("No add-on extensions are installed.")
        else:
            with st.expander(f"Extensions ({len(extensions)})"):
                st.dataframe(
                    [
                        {
                            "Name": extension.name,
                            "Type": (
                                "Processing provider"
                                if extension.kind == "provider"
                                else "Coding app"
                            ),
                            "Enabled": "On" if extension.enabled else "Off",
                            "Status": extension.status.title(),
                            "Details": extension.reason or "Ready",
                        }
                        for extension in extensions
                    ],
                    hide_index=True,
                    width="stretch",
                )

    st.markdown("### Workspace controls")
    st.caption("Deleting a workspace removes its source files, handoffs, and local search index.")
    confirm = st.checkbox(
        f"I understand deleting {project.name} cannot be undone",
        key=f"delete-confirm-{project.id}",
    )
    if st.button(
        "Delete workspace",
        disabled=not confirm,
        key=f"delete-project-{project.id}",
    ):
        try:
            application.delete_project(project.id)
        except Exception as error:
            _render_error("Workspace deletion failed.", error)
        else:
            clear_project_state(_state(), project.id)
            st.session_state["active_project_id"] = None
            st.session_state["_pending_view"] = "home"
            set_flash(_state(), "Workspace and local index deleted.", tone="success")
            st.rerun()


def _render_flash() -> None:
    flash = pop_flash(_state())
    if flash is None:
        return
    message, tone = flash
    renderer = {
        "success": st.success,
        "warning": st.warning,
        "error": st.error,
    }.get(tone, st.info)
    renderer(message)


def _render_error(summary: str, error: Exception) -> None:
    if isinstance(error, HandoffForgeError):
        detail = redact_secrets(str(error))
    else:
        detail = f"{type(error).__name__}: {redact_secrets(str(error))}"
    st.error(f"{summary} {detail}".strip())


def _run_action(action: Callable[[], Any], label: str) -> None:
    try:
        result = action()
    except Exception as error:
        _render_error(f"{label} failed.", error)
        return
    if result.executed:
        st.success(result.message)
    else:
        st.info(result.message)
    st.code(result.payload, language=None)


def _provider_label(status: ProviderStatus | None, name: str) -> str:
    friendly = "Offline extractive" if name == "offline" else name.title()
    state = status.state if status is not None else "unavailable"
    return f"{friendly} · {state}"


def _output_label(output: StoredOutput) -> str:
    created = output.created_at.astimezone().strftime("%b %d, %I:%M %p")
    return f"{_output_title(output)} · {created}"


def _output_title(output: StoredOutput) -> str:
    profile = str(output.metadata.get("profile") or "")
    labels = {
        TemplateProfile.CODEX_PRECOMPACT_V1.value: "Saved progress",
        TemplateProfile.CODEX_POST_CHAT_V1.value: "Finished handoff",
        TemplateProfile.GOAL_V1.value: "Combined plan",
    }
    return labels.get(profile, "Handoff")


def _handoff_label(handoff: SourceArtifact | StoredOutput) -> str:
    if isinstance(handoff, StoredOutput):
        return _output_label(handoff)
    return f"{handoff.display_name} · uploaded"


def _block_location(block: Any) -> str:
    if block.page_number is not None:
        return f"page {block.page_number}"
    if block.line_start is not None:
        ending = block.line_end or block.line_start
        return f"lines {block.line_start}-{ending}"
    return "source location unavailable"


def _state() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], st.session_state)


if __name__ == "__main__":
    main()
