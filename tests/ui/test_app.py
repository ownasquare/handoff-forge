"""Deterministic Streamlit AppTest coverage for the project-first workbench."""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

from handoff_forge.application import HandoffApplication
from handoff_forge.config import HandoffSettings
from handoff_forge.extensions import ExtensionInfo
from handoff_forge.harnesses.launcher import HarnessLauncher
from handoff_forge.models import JobStatus, ModelRoute, ProviderCapabilities
from handoff_forge.providers.base import ProviderStatus
from handoff_forge.providers.registry import ProviderRegistry
from handoff_forge.ui.app import (
    _MODEL_DEFAULTS,
    VIEWS,
    _file_addition_message,
    _output_label,
    _render_view_heading,
    build_route_matrix,
    launch_preview_matches_selection,
)
from handoff_forge.ui.state import project_state_key

ROOT = Path(__file__).parents[2]
APP = ROOT / "src" / "handoff_forge" / "ui" / "app.py"


def test_remote_model_defaults_use_current_supported_ids() -> None:
    assert _MODEL_DEFAULTS["anthropic"] == "claude-sonnet-4-6"
    assert _MODEL_DEFAULTS["xai"] == "grok-4.5"


def _app_test(monkeypatch, tmp_path: Path) -> AppTest:
    monkeypatch.setenv("HANDOFF_FORGE_DATA_ROOT", str(tmp_path / "handoff-data"))
    monkeypatch.setenv("HANDOFF_FORGE_OFFLINE", "true")
    monkeypatch.setenv("HANDOFF_FORGE_ALLOW_NETWORK", "false")
    return AppTest.from_file(str(APP), default_timeout=20)


def _element_by_label(elements, label: str):
    return next(element for element in elements if element.label == label)


def test_empty_workbench_guides_the_first_action(monkeypatch, tmp_path: Path) -> None:
    app = _app_test(monkeypatch, tmp_path).run()

    assert not app.exception
    workspace_names = [item for item in app.text_input if item.label == "Workspace name"]
    assert len(workspace_names) == 1
    assert any(button.label == "Create workspace" for button in app.button)
    assert any(button.label == "Explore sample workspace" for button in app.button)
    assert not app.file_uploader
    visible_copy = " ".join(str(item.value) for item in app.markdown)
    assert "A handoff is a checked Markdown file" in visible_copy
    assert all(step in visible_copy for step in ("Add files", "Create handoff", "Start session"))


def test_existing_workspace_defaults_to_home_with_stable_navigation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    HandoffApplication(settings=settings).create_project("Release continuity")

    app = _app_test(monkeypatch, tmp_path).run()

    assert not app.exception
    workspace = _element_by_label(app.radio, "Workspace navigation")
    assert VIEWS == ("home", "sources", "create", "continue")
    assert tuple(workspace.options) == (
        "Home",
        "Files",
        "Create handoff",
        "Start session",
    )
    more = _element_by_label(app.expander, "More")
    assert more.proto.expanded is False
    assert any(button.label == "Combine handoffs" for button in app.button)
    assert any(button.label == "Settings" for button in app.button)
    assert workspace.value == "home"
    assert any(title.value == "Home" for title in app.title)


def test_workspace_creation_opens_sources_as_the_primary_file_flow(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.text_input, "Workspace name").set_value("Release continuity")
    _element_by_label(app.text_area, "What are you working on? (optional)").set_value(
        "Preserve validated state."
    )
    _element_by_label(app.button, "Create workspace").click()
    app.run()

    assert not app.exception
    workspace = _element_by_label(app.radio, "Workspace navigation")
    assert workspace.value == "sources"
    assert any(title.value == "Files" for title in app.title)
    assert any(
        uploader.label == "Project files (.md, .mdc, .pdf)" for uploader in app.file_uploader
    )
    assert any("Release continuity is ready" in str(item.value) for item in app.success)


def test_creating_another_project_selects_it_without_stale_widget_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "handoff-data"
    settings = HandoffSettings(data_root=data_root, offline=True, allow_network=False)
    application = HandoffApplication(settings=settings)
    existing = application.create_project("Existing project")

    app = _app_test(monkeypatch, tmp_path).run()
    selector = _element_by_label(app.selectbox, "Workspace")
    assert selector.value == existing.id

    _element_by_label(app.text_input, "Workspace name").set_value("Newest project")
    _element_by_label(app.button, "Create workspace").click()
    app.run()

    newest = next(
        project for project in application.list_projects() if project.name == "Newest project"
    )
    selector = _element_by_label(app.selectbox, "Workspace")
    assert selector.value == newest.id
    assert any("Newest project is ready" in str(item.value) for item in app.success)


def test_compose_is_explicitly_offline_and_provider_safe(monkeypatch, tmp_path: Path) -> None:
    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.text_input, "Workspace name").set_value("Offline project")
    _element_by_label(app.button, "Create workspace").click()
    app.run()
    _element_by_label(app.radio, "Workspace navigation").set_value("create")
    app.run()

    assert not app.exception
    advanced = _element_by_label(app.expander, "Advanced processing")
    assert advanced.proto.expanded is False
    visible_text = " ".join(
        str(item.value)
        for collection in (app.markdown, app.info, app.success, app.warning, app.caption)
        for item in collection
    )
    assert "API key" not in visible_text
    provider = _element_by_label(app.selectbox, "Processing provider")
    assert provider.value == "offline"
    model = _element_by_label(app.text_input, "Model identifier")
    assert model.value == "extractive-v1"
    generate = _element_by_label(app.button, "Create handoff")
    assert generate.disabled
    assert any("Add at least one source file" in str(item.value) for item in app.info)
    assert any("zero remote provider calls" in str(item.value) for item in app.caption)
    assert any("No files added" in str(item.value) for item in app.markdown)
    visual_default = _element_by_label(app.checkbox, "Include preserved PDF pages and images")
    assert visual_default.value is False


def test_view_headings_use_unique_explicit_anchors(monkeypatch) -> None:
    rendered: list[tuple[str, str]] = []
    captions: list[str] = []
    monkeypatch.setattr(
        "handoff_forge.ui.app.st.title",
        lambda title, *, anchor: rendered.append((title, anchor)),
    )
    monkeypatch.setattr("handoff_forge.ui.app.st.caption", captions.append)

    headings = (
        ("Home", "home"),
        ("Files", "files"),
        ("Create handoff", "create-handoff"),
        ("Combine handoffs", "combine-handoffs"),
        ("Start session", "start-session"),
        ("Settings", "settings"),
    )
    for title, _anchor in headings:
        _render_view_heading(title, f"{title} description")

    assert rendered == list(headings)
    assert len({anchor for _title, anchor in rendered}) == len(headings)
    assert captions == [f"{title} description" for title, _anchor in headings]


def test_compose_exposes_independent_visual_attestation_for_every_section(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.text_input, "Workspace name").set_value("Visual routing")
    _element_by_label(app.button, "Create workspace").click()
    app.run()
    _element_by_label(app.radio, "Workspace navigation").set_value("create")
    app.run()

    def section_visual_controls():
        return [
            checkbox
            for checkbox in app.checkbox
            if checkbox.label.startswith("Section ")
            and checkbox.label.endswith(" preserved visuals")
        ]

    assert section_visual_controls() == []
    _element_by_label(
        app.checkbox,
        "Use different processing for individual sections",
    ).set_value(True)
    app.run()

    controls = section_visual_controls()
    assert len(controls) == 12
    assert all(checkbox.value is False for checkbox in controls)


def test_uploaded_handoffs_can_merge_without_generation(monkeypatch, tmp_path: Path) -> None:
    data_root = tmp_path / "handoff-data"
    settings = HandoffSettings(data_root=data_root, offline=True, allow_network=False)
    application = HandoffApplication(settings=settings)
    project = application.create_project("Uploaded merge")
    ingested = application.ingest_paths(
        project.id,
        [
            ROOT / "examples" / "handoffs" / "project-alpha.mdc",
            ROOT / "examples" / "handoffs" / "project-beta.mdc",
        ],
    )
    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.button, "Combine handoffs").click()
    app.run()

    selector = _element_by_label(app.multiselect, "Handoffs to combine")
    selector.set_value([item.artifact.id for item in ingested])
    app.run()

    assert any(title.value == "Combine handoffs" for title in app.title)
    _element_by_label(app.button, "Create combined plan").click()
    app.run()

    assert not app.exception
    assert any("Combined plan is ready" in str(item.value) for item in app.success)
    assert any("Unified execution plan" in str(item.value) for item in app.markdown)


def test_files_page_has_visible_next_action_and_reversible_review(
    monkeypatch, tmp_path: Path
) -> None:
    data_root = tmp_path / "handoff-data"
    settings = HandoffSettings(data_root=data_root, offline=True, allow_network=False)
    application = HandoffApplication(settings=settings)
    project = application.create_project("Release continuity")
    application.ingest_paths(project.id, [ROOT / "examples" / "handoffs" / "project-alpha.mdc"])

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.radio, "Workspace navigation").set_value("sources")
    app.run()

    review = _element_by_label(app.toggle, "Review files")
    assert review.value is False
    assert not any(item.label == "Source file" for item in app.selectbox)
    review.set_value(True)
    app.run()
    assert _element_by_label(app.toggle, "Review files").value is True
    assert any(item.label == "Source file" for item in app.selectbox)
    _element_by_label(app.toggle, "Review files").set_value(False)
    app.run()
    assert not any(item.label == "Source file" for item in app.selectbox)
    next_action = _element_by_label(app.button, "Create a handoff")
    assert not next_action.disabled

    next_action.click()
    app.run()

    assert any(title.value == "Create handoff" for title in app.title)
    assert _element_by_label(app.radio, "Workspace navigation").value == "create"


def test_file_review_open_state_is_scoped_to_each_workspace(monkeypatch, tmp_path: Path) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    application = HandoffApplication(settings=settings)
    first = application.create_project("First workspace")
    second = application.create_project("Second workspace")
    for project in (first, second):
        application.ingest_paths(
            project.id,
            [ROOT / "examples" / "handoffs" / "project-alpha.mdc"],
        )

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.selectbox, "Workspace").set_value(first.id)
    app.run()
    _element_by_label(app.radio, "Workspace navigation").set_value("sources")
    app.run()
    _element_by_label(app.toggle, "Review files").set_value(True)
    app.run()
    assert _element_by_label(app.toggle, "Review files").value is True

    _element_by_label(app.selectbox, "Workspace").set_value(second.id)
    app.run()
    assert _element_by_label(app.toggle, "Review files").value is False

    _element_by_label(app.selectbox, "Workspace").set_value(first.id)
    app.run()
    assert _element_by_label(app.toggle, "Review files").value is True


def test_more_closes_immediately_when_returning_to_primary_navigation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    HandoffApplication(settings=settings).create_project("Navigation")

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.button, "Settings").click()
    app.run()
    assert _element_by_label(app.expander, "More").proto.expanded is True

    _element_by_label(app.radio, "Workspace navigation").set_value("home")
    app.run()

    assert _element_by_label(app.expander, "More").proto.expanded is False
    assert any(title.value == "Home" for title in app.title)


def test_settings_lists_extension_metadata_without_loading_disabled_code(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    HandoffApplication(settings=settings).create_project("Extensions")
    monkeypatch.setattr(
        HandoffApplication,
        "list_extensions",
        lambda self: (
            ExtensionInfo(
                name="local-notes",
                kind="provider",
                value="example:factory",
                enabled=True,
                status="enabled",
            ),
            ExtensionInfo(
                name="team-harness",
                kind="harness",
                value="example:harness",
                enabled=False,
                status="available",
                reason="Not enabled for this run.",
            ),
        ),
    )

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.button, "Settings").click()
    app.run()

    extensions = _element_by_label(app.expander, "Extensions (2)")
    assert extensions.proto.expanded is False
    rendered = " ".join(str(item.value) for item in app.dataframe)
    assert "local-notes" in rendered
    assert "Processing provider" in rendered
    assert "team-harness" in rendered
    assert "Coding app" in rendered


def test_transient_generation_success_does_not_cross_workspaces(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    application = HandoffApplication(settings=settings)
    generated = application.materialize_demo()
    other = application.create_project("Other workspace")
    application.ingest_paths(
        other.id,
        [ROOT / "examples" / "handoffs" / "project-alpha.mdc"],
    )

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.selectbox, "Workspace").set_value(generated.project.id)
    app.run()
    _element_by_label(app.radio, "Workspace navigation").set_value("create")
    app.run()
    app.session_state[project_state_key(generated.project.id, "last_job_id")] = "job-a"
    app.session_state[project_state_key(generated.project.id, "last_job_status")] = (
        JobStatus.COMPLETE.value
    )
    app.session_state[project_state_key(generated.project.id, "last_generated_output_id")] = (
        generated.generation.output.id
    )
    app.run()
    assert any("Handoff ready and checked" in str(item.value) for item in app.success)

    _element_by_label(app.selectbox, "Workspace").set_value(other.id)
    app.run()

    assert not any("Handoff ready and checked" in str(item.value) for item in app.success)
    assert not any(button.label == "Start a session" for button in app.button)


def test_combine_hides_markdown_files_that_are_not_structural_handoffs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    application = HandoffApplication(settings=settings)
    project = application.create_project("Eligible merge")
    note = tmp_path / "ordinary-notes.md"
    note.write_text("# Meeting notes\n\nA useful note, but not a handoff.\n", encoding="utf-8")
    application.ingest_paths(
        project.id,
        [
            ROOT / "examples" / "handoffs" / "project-alpha.mdc",
            ROOT / "examples" / "handoffs" / "project-beta.mdc",
            note,
        ],
    )

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.button, "Combine handoffs").click()
    app.run()

    selector = _element_by_label(app.multiselect, "Handoffs to combine")
    assert set(selector.options) == {
        "project-alpha.mdc · uploaded",
        "project-beta.mdc · uploaded",
    }
    assert all("ordinary-notes.md" not in option for option in selector.options)
    assert any("not valid handoffs" in str(item.value) for item in app.caption)


def test_home_keeps_one_recommended_action_without_quick_action_duplicates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    HandoffApplication(settings=settings).materialize_demo()

    app = _app_test(monkeypatch, tmp_path).run()

    visible_copy = " ".join(str(item.value) for item in app.markdown)
    assert "Quick actions" not in visible_copy
    assert sum(button.label == "Start a session" for button in app.button) == 1


def test_primary_handoff_labels_are_friendly_and_omit_hashes(tmp_path: Path) -> None:
    application = HandoffApplication(
        settings=HandoffSettings(
            data_root=tmp_path / "handoff-data",
            offline=True,
            allow_network=False,
        )
    )
    outcome = application.materialize_demo()
    output = application.list_outputs(outcome.project.id)[0]

    label = _output_label(output)

    assert output.sha256[:12] not in label
    assert " · " in label
    assert any(kind in label for kind in ("Saved progress", "Finished handoff", "Combined plan"))


def test_start_session_discloses_terminal_boundary_and_only_lists_installed_apps(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    HandoffApplication(settings=settings).materialize_demo()
    monkeypatch.setattr(HandoffApplication, "available_harnesses", lambda self: ("codex",))

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.radio, "Workspace navigation").set_value("continue")
    app.run()

    destination = _element_by_label(app.selectbox, "App")
    assert tuple(destination.options) == ("Codex",)
    assert any(button.label == "Show launch command" for button in app.button)
    assert not any(button.label == "Prepare new session" for button in app.button)
    assert any("run this command in Terminal" in str(item.value) for item in app.caption)


def test_start_session_keeps_download_when_no_supported_cli_is_installed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    HandoffApplication(settings=settings).materialize_demo()
    monkeypatch.setattr(HandoffApplication, "available_harnesses", lambda self: ())

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.radio, "Workspace navigation").set_value("continue")
    app.run()

    assert not any(selectbox.label == "App" for selectbox in app.selectbox)
    assert not any(button.label == "Show launch command" for button in app.button)
    assert any(
        "No supported command-line app was found" in str(item.value)
        and "still download the handoff" in str(item.value)
        for item in app.warning
    )


def test_start_session_cannot_prepare_launch_after_automatic_validation_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "handoff-data",
        offline=True,
        allow_network=False,
    )
    HandoffApplication(settings=settings).materialize_demo()
    monkeypatch.setattr(HandoffApplication, "available_harnesses", lambda self: ("codex",))

    def fail_validation(self, project_reference, output_reference, profile):
        raise ValueError("required handoff sections are missing")

    monkeypatch.setattr(HandoffApplication, "validate_output", fail_validation)

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.radio, "Workspace navigation").set_value("continue")
    app.run()

    assert not any(button.label == "Show launch command" for button in app.button)
    assert any(button.label == "Download handoff" for button in app.get("download_button"))
    assert any("cannot start a session" in str(item.value) for item in app.warning)
    assert any(expander.label == "Preview handoff" for expander in app.expander)
    assert any(expander.label == "Technical details" for expander in app.expander)


def test_local_extension_provider_does_not_trigger_remote_warning_or_consent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    original_statuses = ProviderRegistry.statuses
    original_is_remote = ProviderRegistry.is_remote

    def statuses_with_local_extension(self):
        return (
            *original_statuses(self),
            ProviderStatus(
                name="local-notes",
                installed=True,
                configured=True,
                enabled=True,
                state="ready",
                capabilities=ProviderCapabilities(stability="local"),
                reason="Runs entirely on this device.",
            ),
        )

    def classify_extension(self, name: str) -> bool:
        if name == "local-notes":
            return False
        return original_is_remote(self, name)

    monkeypatch.setattr(ProviderRegistry, "statuses", statuses_with_local_extension)
    monkeypatch.setattr(ProviderRegistry, "is_remote", classify_extension)

    app = _app_test(monkeypatch, tmp_path).run()
    _element_by_label(app.text_input, "Workspace name").set_value("Local extension")
    _element_by_label(app.button, "Create workspace").click()
    app.run()
    _element_by_label(app.radio, "Workspace navigation").set_value("create")
    app.run()
    _element_by_label(app.selectbox, "Processing provider").set_value("local-notes")
    app.run()

    assert not any(checkbox.label.startswith("I consent") for checkbox in app.checkbox)
    assert not any("A remote route can upload" in str(item.value) for item in app.warning)
    assert any("zero remote provider calls" in str(item.value) for item in app.caption)


def test_route_matrix_is_exact_and_cloud_consent_is_section_scoped() -> None:
    routes = build_route_matrix(
        global_provider="offline",
        global_model="extractive-v1",
        allow_cloud_upload=True,
        global_include_visual_evidence=False,
        overrides={2: ("openai", "gpt-4.1-mini", True)},
        remote_providers={"openai"},
    )

    assert set(routes) == set(range(1, 13))
    assert routes[1] == ModelRoute(provider="offline", model="extractive-v1")
    assert routes[2].provider == "openai"
    assert routes[2].model == "gpt-4.1-mini"
    assert routes[2].allow_cloud_upload is True
    assert routes[2].include_visual_evidence is True
    assert routes[3].allow_cloud_upload is False
    assert routes[1].include_visual_evidence is False
    assert routes[3].include_visual_evidence is False

    local_routes = build_route_matrix(
        global_provider="local-notes",
        global_model="deterministic-v1",
        allow_cloud_upload=True,
        remote_providers={"openai"},
    )
    assert all(not route.allow_cloud_upload for route in local_routes.values())


def test_file_addition_message_distinguishes_new_and_duplicate_files() -> None:
    assert _file_addition_message(2, 0, 0) == "Added 2 files."
    assert _file_addition_message(1, 2, 0) == (
        "Added 1 file. 2 files were already in this workspace."
    )
    assert _file_addition_message(0, 1, 0) == "That file was already in this workspace."
    assert _file_addition_message(0, 2, 1) == (
        "Those files were already in this workspace. 1 item needs review."
    )


def test_launch_preview_matching_binds_every_reviewed_input() -> None:
    preview = {
        "output_id": "output-1",
        "harness": "codex",
        "model": "gpt-5",
        "argv": ["codex", "--model", "gpt-5", "handoff.mdc"],
        "cwd": "/workspace",
    }

    assert launch_preview_matches_selection(
        preview,
        output_id="output-1",
        harness="codex",
        model="gpt-5",
    )
    assert not launch_preview_matches_selection(
        preview,
        output_id="output-2",
        harness="codex",
        model="gpt-5",
    )
    assert not launch_preview_matches_selection(
        preview,
        output_id="output-1",
        harness="claude",
        model="gpt-5",
    )
    assert not launch_preview_matches_selection(
        preview,
        output_id="output-1",
        harness="codex",
        model="gpt-5.1",
    )


def test_launch_preview_is_invalidated_when_model_changes(monkeypatch, tmp_path: Path) -> None:
    data_root = tmp_path / "handoff-data"
    settings = HandoffSettings(
        data_root=data_root,
        offline=True,
        allow_network=False,
    )
    data_root.mkdir(parents=True)
    application = HandoffApplication(
        settings=settings,
        launcher=HarnessLauncher(
            managed_root=data_root,
            executable_resolver=lambda candidate: sys.executable if candidate == "codex" else None,
        ),
    )
    application.materialize_demo()
    app = _app_test(monkeypatch, tmp_path)
    app.session_state["_application_override"] = application
    app.run()
    _element_by_label(app.radio, "Workspace navigation").set_value("continue")
    app.run()

    _element_by_label(app.button, "Show launch command").click()
    app.run()

    assert len(app.code) >= 2
    assert any("Run in Terminal" in str(item.value) for item in app.markdown)

    _element_by_label(app.text_input, "Model (optional)").set_value("gpt-5")
    app.run()

    assert not app.code
