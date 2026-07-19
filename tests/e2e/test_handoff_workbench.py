"""Opt-in rendered workspace proof through Playwright.

The root validation run starts Streamlit and sets ``HANDOFF_FORGE_RUN_E2E=1``.
Normal unit/integration runs collect this module without opening a browser.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect

ROOT = Path(__file__).parents[2]
E2E_URL = os.environ.get("HANDOFF_FORGE_E2E_URL", "http://127.0.0.1:8517")
WORKSPACE_VIEW_LABELS = (
    "Home",
    "Files",
    "Create handoff",
    "Start session",
)
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("HANDOFF_FORGE_RUN_E2E") != "1",
        reason="set HANDOFF_FORGE_RUN_E2E=1 after starting the isolated Streamlit server",
    ),
]


def _proof_path(filename: str) -> Path:
    configured = os.environ.get("HANDOFF_FORGE_E2E_ARTIFACT_DIR")
    directory = Path(configured).expanduser().resolve() if configured else ROOT / "docs" / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def _watch_page(page: Page) -> tuple[list[str], list[str], list[str]]:
    console_errors: list[str] = []
    page_errors: list[str] = []
    external_requests: list[str] = []
    target_host = urlparse(E2E_URL).hostname
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def record_external(request) -> None:
        parsed = urlparse(request.url)
        if parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname != target_host:
            external_requests.append(request.url)

    page.on("request", record_external)
    return console_errors, page_errors, external_requests


def _assert_product_chrome_absent(page: Page) -> None:
    """Keep Streamlit's deploy/developer controls outside the product surface."""
    expect(page.locator('[data-testid="stDeployButton"]')).not_to_be_visible()
    expect(page.locator('[data-testid="stStatusWidget"]')).not_to_be_visible()
    expect(page.get_by_role("button", name="Deploy", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Manage app", exact=True)).to_have_count(0)


def _assert_workspace_context(page: Page, workspace: str | None, view: str) -> None:
    context = page.get_by_label("Current workspace")
    expect(context).to_be_visible()
    if workspace is not None:
        expect(context).to_contain_text(workspace)
    expect(context).to_contain_text(view)


def _select_workspace_view(page: Page, view: str) -> None:
    assert view in WORKSPACE_VIEW_LABELS
    workspace_views = page.get_by_role("radiogroup", name="Workspace navigation")
    expect(workspace_views).to_be_visible()
    for visible_label in WORKSPACE_VIEW_LABELS:
        expect(workspace_views.get_by_text(visible_label, exact=True)).to_be_visible()
    workspace_views.get_by_text(view, exact=True).click()
    expect(page.get_by_role("heading", name=view, exact=True, level=1)).to_be_visible()


def _select_adjacent_workspace_view_with_keyboard(
    page: Page,
    *,
    current: str,
    expected: str,
    key: str,
) -> None:
    """Prove Streamlit's real radio inputs retain native keyboard navigation."""

    workspace_views = page.get_by_role("radiogroup", name="Workspace navigation")
    radios = workspace_views.get_by_role("radio")
    expect(radios).to_have_count(len(WORKSPACE_VIEW_LABELS))
    current_radio = workspace_views.get_by_role("radio", name=current, exact=True)
    current_radio.focus()
    expect(current_radio).to_be_focused()
    page.keyboard.press(key)
    expect(page.get_by_role("heading", name=expected, exact=True, level=1)).to_be_visible()
    expect(
        page.get_by_role("radiogroup", name="Workspace navigation").get_by_role(
            "radio", name=expected, exact=True
        )
    ).to_be_checked()


def _select_secondary_workspace_view(page: Page, view: str) -> None:
    assert view in {"Combine handoffs", "Settings"}
    more = page.get_by_test_id("stExpander").filter(has_text="More")
    more.locator("summary").click()
    more.get_by_role("button", name=view, exact=True).click()
    expect(page.get_by_role("heading", name=view, exact=True, level=1)).to_be_visible()


def _assert_expander_collapsed(page: Page, label: str) -> None:
    expander = page.get_by_test_id("stExpander").filter(has_text=label)
    expect(expander).to_have_count(1)
    details = expander.locator("details")
    expect(details).to_have_count(1)
    assert details.evaluate("(element) => element.open") is False


def _assert_no_overflow(page: Page) -> None:
    overflow = page.evaluate(
        """() => ({
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: window.innerWidth,
          bodyWidth: document.body.scrollWidth,
        })"""
    )
    assert overflow["documentWidth"] <= overflow["viewportWidth"] + 1
    assert overflow["bodyWidth"] <= overflow["viewportWidth"] + 1


def _reset_main_scroll(page: Page) -> None:
    page.get_by_test_id("stMain").evaluate(
        """(element) => {
          element.scrollTop = 0;
          element.scrollLeft = 0;
        }"""
    )
    page.evaluate(
        """() => {
          window.scrollTo(0, 0);
          document.documentElement.scrollTop = 0;
          document.body.scrollTop = 0;
          if (document.activeElement instanceof HTMLElement) {
            document.activeElement.blur();
          }
          const sidebar = document.querySelector('[data-testid="stSidebarContent"]');
          if (sidebar) {
            sidebar.scrollTop = 0;
            sidebar.scrollLeft = 0;
          }
        }"""
    )


def test_first_time_flow_files_create_start_and_optional_combine(page: Page) -> None:
    console_errors, page_errors, external_requests = _watch_page(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(E2E_URL, wait_until="networkidle")

    expect(page).to_have_title("Handoff Forge")
    expect(
        page.get_by_test_id("stMain").get_by_role(
            "heading", name="Carry your work into a new session", exact=True, level=1
        )
    ).to_be_visible()
    expect(page.get_by_text("A handoff is a checked Markdown file", exact=False)).to_be_visible()
    for step in ("Add files", "Create handoff", "Start session"):
        expect(page.get_by_text(step, exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Create workspace", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Explore sample workspace", exact=True)).to_be_visible()
    _assert_product_chrome_absent(page)
    page.screenshot(path=_proof_path("handoff-workbench-first-run.png"), full_page=False)

    page.get_by_role("textbox", name="Workspace name", exact=True).fill("E2E Continuity Workspace")
    page.get_by_role("textbox", name="What are you working on? (optional)", exact=True).fill(
        "Prove the complete local continuation workflow."
    )
    page.get_by_role("button", name="Create workspace", exact=True).click()
    expect(page.get_by_role("heading", name="Files", exact=True, level=1)).to_be_visible()
    _assert_workspace_context(page, "E2E Continuity Workspace", "Files")
    local_only = page.get_by_label(re.compile(r"^Local-only:"))
    expect(local_only).to_be_visible()
    expect(local_only).to_have_attribute("tabindex", "0")
    expect(local_only).to_have_attribute("data-tooltip", re.compile(r"\S+"))
    local_only.focus()
    expect(local_only).to_be_focused()
    expect(page.get_by_role("heading", name="Add files", exact=True)).to_be_visible()

    uploader = page.locator('input[type="file"]')
    uploader.set_input_files(
        [
            ROOT / "examples" / "handoffs" / "project-alpha.mdc",
            ROOT / "examples" / "handoffs" / "project-beta.mdc",
            ROOT / "examples" / "northstar-continuity-review.pdf",
        ]
    )
    add_files_action = page.locator('[data-testid="stBaseButton-primary"]:visible').filter(
        has_text="Add files"
    )
    add_files_action.click()
    expect(page.get_by_text("Added 3 files.", exact=False)).to_be_visible(timeout=30_000)
    expect(page.get_by_text("3 files ready", exact=False)).to_be_visible()
    expect(add_files_action).to_be_disabled()
    assert page.locator('input[type="file"]').evaluate("(element) => element.files.length") == 0

    uploader = page.locator('input[type="file"]')
    uploader.set_input_files(ROOT / "examples" / "handoffs" / "project-alpha.mdc")
    expect(add_files_action).to_be_enabled()
    add_files_action.click()
    expect(page.get_by_text("That file was already in this workspace.", exact=True)).to_be_visible(
        timeout=30_000
    )
    # The flash message is rendered before Streamlit finishes replacing the uploader.
    # Waiting for the action to disable proves the new empty widget has reached the browser.
    expect(add_files_action).to_be_disabled()
    assert page.locator('input[type="file"]').evaluate("(element) => element.files.length") == 0

    review_files = page.get_by_role("switch", name="Review files", exact=True)
    expect(review_files).not_to_be_checked()
    review_files.focus()
    page.keyboard.press("Space")
    expect(review_files).to_be_checked()

    source_select = page.get_by_role("combobox", name="Source file", exact=True)
    source_group = page.get_by_role("group").filter(has=source_select)
    source_group.get_by_role("button", name="Open", exact=True).click()
    source_select.fill("northstar-continuity-review.pdf")
    source_select.press("Enter")
    expect(page.get_by_text(re.compile(r"^PDF ·"))).to_be_visible()

    images_tab = page.get_by_role("tab", name="Images & pages", exact=True)
    expect(images_tab).to_have_count(1)
    images_tab.click()
    expect(page.get_by_role("img").first).to_be_visible()

    content_tab = page.get_by_role("tab", name="Content", exact=True)
    expect(content_tab).to_have_count(1)
    content_tab.click()
    expect(page.get_by_text(re.compile(r"table · page 1", re.IGNORECASE))).to_be_visible()

    details_tab = page.get_by_role("tab", name="Details", exact=True)
    expect(details_tab).to_have_count(1)
    details_tab.click()
    expect(page.get_by_text("Parser profile ·", exact=False)).to_be_visible()
    expect(page.get_by_text("Extraction details", exact=True)).to_be_visible()

    page.get_by_role("button", name="Create a handoff", exact=True).click()
    expect(page.get_by_role("heading", name="Create handoff", exact=True, level=1)).to_be_visible()
    _assert_workspace_context(page, "E2E Continuity Workspace", "Create handoff")
    expect(page.get_by_text("local processing", exact=False)).to_be_visible()
    _assert_expander_collapsed(page, "Advanced processing")
    expect(page.get_by_role("combobox", name="Processing provider", exact=True)).not_to_be_visible()
    expect(page.get_by_role("textbox", name="Model identifier", exact=True)).not_to_be_visible()

    lifecycle = page.get_by_role("radiogroup", name="When will you use this?")
    lifecycle.get_by_text("Save progress", exact=True).click()
    page.get_by_test_id("stMain").get_by_role("button", name="Create handoff", exact=True).click()
    expect(page.get_by_text("Handoff ready and checked.", exact=True).first).to_be_visible(
        timeout=30_000
    )
    page.get_by_role("button", name="Start a session", exact=True).click()
    expect(page.get_by_role("heading", name="Start session", exact=True, level=1)).to_be_visible()
    expect(page.get_by_role("button", name="Download handoff", exact=True)).to_be_visible()

    _select_workspace_view(page, "Create handoff")
    lifecycle = page.get_by_role("radiogroup", name="When will you use this?")
    lifecycle.get_by_text("Finish and hand off", exact=True).click()
    page.get_by_test_id("stMain").get_by_role("button", name="Create handoff", exact=True).click()
    expect(page.get_by_text("Handoff ready and checked.", exact=True).first).to_be_visible(
        timeout=30_000
    )

    _select_workspace_view(page, "Home")
    start_session = page.get_by_test_id("stMain").get_by_role(
        "button", name="Start a session", exact=True
    )
    # Streamlit can briefly retain the previous view while replacing the main tree.
    # Wait for the Home view to settle before applying a strict visibility check.
    expect(start_session).to_have_count(1)
    expect(start_session).to_be_visible()
    expect(page.get_by_text("Quick actions", exact=True)).to_have_count(0)
    _reset_main_scroll(page)
    page.wait_for_timeout(500)
    page.screenshot(path=_proof_path("handoff-workbench-home.png"), full_page=False)

    _select_secondary_workspace_view(page, "Combine handoffs")
    _assert_workspace_context(page, "E2E Continuity Workspace", "Combine handoffs")
    handoff_select = page.get_by_role("combobox", name="Handoffs to combine", exact=True)
    handoff_select.click()
    handoff_select.fill("project-alpha.mdc")
    handoff_select.press("Enter")
    expect(page.get_by_role("button", name="project-alpha.mdc", exact=False)).to_be_visible()
    handoff_select = page.get_by_role("combobox", name="Handoffs to combine", exact=False)
    handoff_select.click()
    handoff_select.fill("project-beta.mdc")
    handoff_select.press("Enter")
    expect(page.get_by_role("button", name="project-beta.mdc", exact=False)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("option", name="project-beta.mdc", exact=False)).not_to_be_visible()
    combine_button = page.get_by_role("button", name="Create combined plan", exact=True)
    expect(combine_button).to_be_enabled()
    combine_button.click()
    expect(page.get_by_text("Combined plan is ready.", exact=True)).to_be_visible(timeout=30_000)

    _select_workspace_view(page, "Start session")
    _assert_workspace_context(page, "E2E Continuity Workspace", "Start session")
    expect(page.get_by_label(re.compile(r"^Checked:"))).to_be_visible()
    expect(page.get_by_role("button", name="Download handoff", exact=True)).to_be_visible()
    launch_button = page.get_by_role("button", name="Show launch command", exact=True)
    if launch_button.count():
        expect(page.get_by_text("run this command in Terminal", exact=False)).to_be_visible()
        launch_button.click()
        expect(page.get_by_role("heading", name="Run in Terminal", exact=True)).to_be_visible()
        expect(page.get_by_text("paste the command into Terminal", exact=False)).to_be_visible()
    else:
        expect(
            page.get_by_text("No supported command-line app was found", exact=False)
        ).to_be_visible()

    page.get_by_text("Technical details", exact=True).click()
    expect(page.get_by_role("textbox", name="Raw output path", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="File URL", exact=True)).to_be_visible()
    page.get_by_role("button", name="Run validation", exact=True).click()
    expect(page.get_by_text("Valid profile · Sections", exact=False)).to_be_visible()
    if launch_button.count():
        expect(page.locator("code").filter(has_text="--execute")).to_be_visible()

    _assert_product_chrome_absent(page)
    _assert_no_overflow(page)
    _reset_main_scroll(page)
    page.screenshot(path=_proof_path("handoff-workbench-desktop.png"), full_page=True)
    assert console_errors == []
    assert page_errors == []
    assert external_requests == []


@pytest.mark.parametrize(
    ("width", "height", "proof_name"),
    [
        (1440, 900, "handoff-workbench-desktop-shell.png"),
        (834, 1112, "handoff-workbench-tablet.png"),
        (390, 844, "handoff-workbench-mobile.png"),
    ],
)
def test_responsive_files_shell_keyboard_and_overflow(
    page: Page,
    width: int,
    height: int,
    proof_name: str,
) -> None:
    console_errors, page_errors, external_requests = _watch_page(page)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(E2E_URL, wait_until="networkidle")

    expect(page).to_have_title("Handoff Forge")
    if page.get_by_role("radiogroup", name="Workspace navigation").count() == 0:
        page.get_by_role("button", name="Explore sample workspace", exact=True).click()
        expect(page.get_by_role("heading", name="Home", exact=True, level=1)).to_be_visible()
    _select_workspace_view(page, "Files")
    _assert_workspace_context(page, None, "Files")
    _assert_product_chrome_absent(page)

    add_files_heading = page.get_by_role("heading", name="Add files", exact=True)
    add_files_action = page.locator('[data-testid="stBaseButton-primary"]:visible').filter(
        has_text="Add files"
    )
    expect(add_files_heading).to_be_visible()
    expect(add_files_action).to_be_visible()
    action_bounds = add_files_action.bounding_box()
    assert action_bounds is not None
    assert 0 <= action_bounds["y"] < height
    expect(page.get_by_role("button", name="Create a handoff", exact=True)).to_be_visible()
    review_files = page.get_by_role("switch", name="Review files", exact=True)
    expect(review_files).not_to_be_checked()

    _select_adjacent_workspace_view_with_keyboard(
        page,
        current="Files",
        expected="Home",
        key="ArrowUp",
    )
    _select_adjacent_workspace_view_with_keyboard(
        page,
        current="Home",
        expected="Files",
        key="ArrowDown",
    )

    if width == 390:
        close_drawer = page.get_by_role("button", name="keyboard_double_arrow_left", exact=True)
        expect(close_drawer).to_be_visible()
        close_drawer.click()
        expect(
            page.get_by_role("button", name="keyboard_double_arrow_right", exact=True)
        ).to_be_visible()
        page.wait_for_function(
            """() => {
              const sidebar = document.querySelector('[data-testid="stSidebar"]');
              return sidebar && sidebar.getBoundingClientRect().right <= 0;
            }"""
        )
        expect(page.get_by_label("Current workspace")).to_be_visible()
        expect(page.get_by_label("Current workspace")).to_contain_text("Files")

    assert page.evaluate("() => getComputedStyle(document.documentElement).colorScheme") == "light"
    _assert_no_overflow(page)
    _reset_main_scroll(page)
    page.wait_for_timeout(500)
    page.screenshot(path=_proof_path(proof_name), full_page=width != 390)
    assert console_errors == []
    assert page_errors == []
    assert external_requests == []
