"""Delivery of the single-page app and the integrity of the vendored files."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from httpx import AsyncClient

from app.errors import ErrorCode
from app.main import API_PREFIX, STATIC_DIR

LOCALES_DIR = STATIC_DIR.parent / "locales"

VUE_FILE = STATIC_DIR / "vendor" / "vue.esm-browser.prod.js"
#: Checksum of the release published as vue@3.5.40 — see vendor/README.md.
VUE_SHA256 = "2e1777387ce6985aa839f465cfc688e31fe283124146b007a253eb0cb8f4a6a5"


def test_vendored_vue_is_the_verified_release() -> None:
    digest = hashlib.sha256(VUE_FILE.read_bytes()).hexdigest()

    assert digest == VUE_SHA256


def test_vendored_vue_keeps_its_licence_banner() -> None:
    header = VUE_FILE.read_text(encoding="utf-8")[:200]

    assert "vue v3.5.40" in header
    assert "@license MIT" in header


def test_no_inline_script_or_style_anywhere() -> None:
    """The strict CSP of AP29 forbids both — this must hold from the first frontend AP."""
    inline_script = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>[^<]*\S", re.IGNORECASE)
    inline_style_tag = re.compile(r"<style", re.IGNORECASE)
    style_attribute = re.compile(r"\bstyle\s*=\s*[\"']", re.IGNORECASE)

    for path in STATIC_DIR.rglob("*.html"):
        markup = path.read_text(encoding="utf-8")
        assert not inline_script.search(markup), path
        assert not inline_style_tag.search(markup), path
        assert not style_attribute.search(markup), path


def test_no_runtime_dependency_on_a_cdn() -> None:
    """Everything the browser loads has to come from this instance."""
    remote = re.compile(r"""["'(](https?:)?//""")

    served = [*STATIC_DIR.rglob("*.js"), *STATIC_DIR.rglob("*.html"), *STATIC_DIR.rglob("*.css")]
    for path in served:
        if path == VUE_FILE:
            continue
        assert not remote.search(path.read_text(encoding="utf-8")), path


async def test_root_serves_the_app_shell(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert '<div id="app">' in response.text


async def test_deep_link_falls_back_to_the_shell(client: AsyncClient) -> None:
    """A reload on /chores must not 404."""
    response = await client.get("/chores")

    assert response.status_code == 200
    assert '<div id="app">' in response.text


async def test_assets_are_served(client: AsyncClient) -> None:
    for path in ("/css/app.css", "/js/main.js", "/js/api.js", "/vendor/vue.esm-browser.prod.js"):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.content, path


async def test_manifest_makes_the_app_installable(client: AsyncClient) -> None:
    """ "Add to home screen" needs a manifest with a standalone display and both icons."""
    response = await client.get("/manifest.webmanifest")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["name"] == "Kehrwoche"
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])


async def test_icons_are_served_in_the_declared_size(client: AsyncClient) -> None:
    from io import BytesIO

    from PIL import Image

    expected = {
        "/icons/icon-192.png": 192,
        "/icons/icon-512.png": 512,
        "/icons/icon-maskable-512.png": 512,
        "/icons/apple-touch-icon.png": 180,
    }
    for path, size in expected.items():
        response = await client.get(path)
        assert response.status_code == 200, path
        assert Image.open(BytesIO(response.content)).size == (size, size), path


async def test_the_shell_links_manifest_and_touch_icon(client: AsyncClient) -> None:
    markup = (await client.get("/")).text

    assert 'rel="manifest"' in markup
    # iOS ignores the manifest for the home screen and uses this instead.
    assert 'rel="apple-touch-icon"' in markup
    assert 'name="theme-color"' in markup


async def test_assets_are_always_revalidated(client: AsyncClient) -> None:
    """A cached bundle from before an update must not keep running."""
    for path in ("/", "/css/app.css", "/js/main.js", "/manifest.webmanifest"):
        response = await client.get(path)
        assert response.headers.get("cache-control") == "no-cache", path


async def test_unknown_api_path_stays_a_json_error(client: AsyncClient) -> None:
    response = await client.get(f"{API_PREFIX}/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND


async def test_the_shell_references_only_local_assets(client: AsyncClient) -> None:
    markup = (await client.get("/")).text

    assert '<link rel="stylesheet" href="/css/app.css" />' in markup
    assert '<script type="module" src="/js/main.js"></script>' in markup


def test_javascript_modules_import_only_relative_paths() -> None:
    imports = re.compile(r"""^\s*import\s.*?from\s+["']([^"']+)["']""", re.MULTILINE)

    for path in (STATIC_DIR / "js").rglob("*.js"):
        for target in imports.findall(path.read_text(encoding="utf-8")):
            assert target.startswith("."), f"{path}: {target}"
            assert (Path(path).parent / target).resolve().exists(), f"{path}: {target}"


def test_every_feed_event_type_has_a_text_in_both_languages() -> None:
    """A type nobody wrote a sentence for would show up as a raw key on the pinboard."""
    from app.models import FeedEventType

    catalogues = {
        code: json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("de", "en")
    }
    for event_type in FeedEventType:
        if event_type is FeedEventType.USER_POST:
            continue  # a post carries its own text
        for code, catalogue in catalogues.items():
            assert f"feed.event.{event_type.value}" in catalogue, f"{event_type} in {code}.json"


def test_every_feed_event_type_is_actually_emitted() -> None:
    """The specification names the events; this proves the code still writes them."""
    from app.models import FeedEventType

    services = Path("app/services")
    written = "\n".join(path.read_text(encoding="utf-8") for path in services.glob("*.py"))
    for event_type in FeedEventType:
        assert f"FeedEventType.{event_type.name}" in written, event_type


def test_every_notification_type_has_a_text_in_both_languages() -> None:
    """Notifications travel as keys — a missing text would reach the bell as a key."""
    from app.models import NotificationType

    catalogues = {
        code: json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("de", "en")
    }
    for notification_type in NotificationType:
        for code, catalogue in catalogues.items():
            for part in ("title", "body"):
                key = f"notification.{notification_type.value}.{part}"
                assert key in catalogue, f"{key} missing in {code}.json"


def test_render_functions_declare_the_state_they_use() -> None:
    """`state` lives in setup(); a render function has to fetch it from `this` first.

    Forgetting that line does not fail loudly: the component throws while rendering and
    the view stays blank, which is exactly how it slipped through once. The bracket check
    cannot see it, a unit test would not reach it — so it is checked here.
    """
    uses_state = re.compile(r"(?<!\.)\bstate\.")

    for path in STATIC_DIR.rglob("*.js"):
        if path == VUE_FILE:
            continue
        source = path.read_text(encoding="utf-8")
        # A module-level `const state = …` is in scope everywhere; nothing to declare.
        if re.search(r"^const state\b", source, re.M):
            continue

        for block in re.findall(r"\n  (?:render|render\(\))\s*\(\)\s*\{(.*?)\n  \},", source, re.S):
            if uses_state.search(block):
                assert "const { state } = this" in block, (
                    f"{path.name}: render() uses `state.` without taking it from `this`"
                )


def test_no_view_puts_a_server_message_on_screen() -> None:
    """The server's `message` is English; only `errorText()` may decide what is shown.

    This is the guard for a fault that hid in plain sight for eleven work packages:
    every failure appeared in English no matter which language was chosen, because the
    views passed the developer wording straight through.
    """
    allowed = {"api.js", "error-text.js"}

    for path in (STATIC_DIR / "js").rglob("*.js"):
        if path.name in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        assert "error.message" not in source, f"{path.name}: shows the server's English message"


def test_every_error_code_has_a_general_text_in_both_languages() -> None:
    """A code without a text would reach the screen as `error.some_code`."""
    catalogues = {
        code: json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("de", "en")
    }
    for error_code in ErrorCode:
        for code, catalogue in catalogues.items():
            assert f"error.{error_code.value}" in catalogue, f"{error_code} missing in {code}.json"


def test_every_message_key_the_server_sends_exists_in_both_languages() -> None:
    """The specific wordings travel as keys, exactly like notifications do."""
    keys = re.compile(r"""message_key=["']([^"']+)["']""")
    catalogues = {
        code: json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("de", "en")
    }

    used = set()
    for path in Path("app").rglob("*.py"):
        used |= set(keys.findall(path.read_text(encoding="utf-8")))

    assert used, "no message keys found — has the error format changed?"
    for key in sorted(used):
        for code, catalogue in catalogues.items():
            assert key in catalogue, f"{key} missing in {code}.json"


def test_the_network_reasons_have_texts_in_both_languages() -> None:
    """`NetworkError` is raised with three reasons; each is shown as its own sentence."""
    # One reason is chosen by a ternary, so take every literal up to the closing bracket.
    raised = re.compile(r"new NetworkError\(([^)]*)\)")
    literal = re.compile(r"""["']([a-z]+)["']""")
    source = (STATIC_DIR / "js" / "api.js").read_text(encoding="utf-8")
    catalogues = {
        code: json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("de", "en")
    }

    found = {reason for call in raised.findall(source) for reason in literal.findall(call)}
    assert found >= {"offline", "timeout", "unreachable"}, found
    for reason in sorted(found):
        for code, catalogue in catalogues.items():
            assert f"error.{reason}" in catalogue, f"error.{reason} missing in {code}.json"
