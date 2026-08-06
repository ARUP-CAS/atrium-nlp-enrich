"""
tests/test_service_requirements.py
==================================
The service's runtime manifest declares everything the service is launched with
(atrium-project#10, G3).

``service/requirements.txt`` carried six test/contract deps and **no ASGI server** for
twelve days while three separate launch paths — ``Dockerfile``'s ``api`` ENTRYPOINT,
``docker-compose.yaml``'s ``nlp-api`` profile, and ``setup_api_service.sh`` — all invoke
``uvicorn``. Nothing in CI could see it, and the reason is structural rather than
accidental:

* the API contract tests drive the app **in-process** through ``TestClient``, which never
  starts a server, so a missing server is invisible to them by construction;
* ``docker-build-smoke`` only **builds** the image and never runs it, so a broken
  ENTRYPOINT is invisible to it too.

Both blind spots are about *running* the thing. This file takes the other route and checks
the manifest against the launch commands themselves — cheap, hermetic, and it fails the
moment someone prunes a requirements file again. It deliberately reads the real
Dockerfile/compose/setup-script text rather than hardcoding "uvicorn", so switching the
server (hypercorn, granian, …) keeps the gate honest instead of making it a lie.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVICE_REQS = _REPO_ROOT / "service" / "requirements.txt"
_ROOT_REQS = _REPO_ROOT / "requirements.txt"
_TEST_REQS = _REPO_ROOT / "requirements-test.txt"

#: Distributions that are only ever imported by tests. A served image has no business
#: carrying them, and the API layer used to consist of nothing else.
_TEST_ONLY = ("pytest", "pytest-cov", "openapi-spec-validator")

#: Known ASGI servers, so the assertion is about "a server is declared", not about uvicorn
#: specifically — see the module docstring.
_ASGI_SERVERS = ("uvicorn", "hypercorn", "daphne", "granian")


def _declared(path: Path) -> set[str]:
    """Distribution names declared in a requirements file, lower-cased.

    Strips extras (``uvicorn[standard]`` → ``uvicorn``), version specifiers, comments and
    ``-r`` includes — the includes are followed by pip, not by this parser, so a file that
    delegates is reported as delegating rather than as declaring nothing.
    """
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[\[<>=!;~ ]", line, maxsplit=1)[0]
        if name:
            names.add(name.lower())
    return names


def _includes(path: Path) -> set[str]:
    """``-r other.txt`` targets, resolved relative to *path*'s directory as pip does."""
    out: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        m = re.match(r"^-r\s+(\S+)$", line)
        if m:
            out.add(str((path.parent / m.group(1)).resolve()))
    return out


def _launch_commands() -> dict[str, str]:
    """Every place the repo says to start the service, as raw text to search."""
    return {
        "Dockerfile (api target ENTRYPOINT)": (_REPO_ROOT / "Dockerfile").read_text(
            encoding="utf-8"
        ),
        "docker-compose.yaml": (_REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8"),
        "setup_api_service.sh": (_REPO_ROOT / "setup_api_service.sh").read_text(encoding="utf-8"),
        "service/README.md": (_REPO_ROOT / "service" / "README.md").read_text(encoding="utf-8"),
    }


def test_an_asgi_server_is_declared():
    """The single defect G3 names: no server in the manifest, three launch paths using one."""
    declared = _declared(_SERVICE_REQS)
    assert declared & set(_ASGI_SERVERS), (
        f"service/requirements.txt declares no ASGI server ({declared}) — "
        f"the api image's ENTRYPOINT and setup_api_service.sh both exec one"
    )


@pytest.mark.parametrize("server", _ASGI_SERVERS)
def test_every_launched_server_is_declared(server):
    """Whichever server the repo actually invokes must be the one it installs. Catches the
    inverse drift too: swapping the ENTRYPOINT without updating the manifest."""
    declared = _declared(_SERVICE_REQS) | _declared(_ROOT_REQS)
    for where, text in _launch_commands().items():
        if re.search(rf"\b{server}\b", text) and server not in declared:
            pytest.fail(
                f"{where} invokes {server!r}, but neither service/requirements.txt nor "
                f"requirements.txt declares it"
            )


def test_serving_deps_the_endpoints_need_are_declared():
    """``fastapi`` for the app and ``python-multipart`` for it: every upload endpoint
    (`/enrich`, `/rescale`, `/jobs`, and the `document_json` part added for J3) is
    multipart, and FastAPI raises at request time — not import time — without it."""
    declared = _declared(_SERVICE_REQS) | _declared(_ROOT_REQS)
    for dep in ("fastapi", "python-multipart"):
        assert dep in declared, f"{dep} is not declared for the service runtime"


def test_runtime_manifest_carries_no_test_only_deps():
    """The other half of G3: the file was *only* test deps. Keeping them out is what makes
    the missing server obvious next time instead of hiding it in a plausible-looking list."""
    leaked = _declared(_SERVICE_REQS) & set(_TEST_ONLY)
    assert not leaked, (
        f"{sorted(leaked)} are test-only and belong in requirements-test.txt, "
        f"not in the served image's requirements"
    )


def test_test_only_deps_are_still_declared_somewhere():
    """…and they must not simply have been deleted: the §32 meta-contract lane needs them."""
    declared = _declared(_TEST_REQS)
    for dep in (*_TEST_ONLY, "fastapi", "httpx"):
        assert dep in declared, f"{dep} vanished from requirements-test.txt"


def test_no_include_escapes_the_docker_build_context():
    """``-r ../requirements.txt`` reads correctly from a checkout and breaks the image:
    Dockerfile's api stage copies this file to ``/app/service_requirements.txt``, so ``../``
    resolves outside /app. Pin the constraint rather than the comment explaining it."""
    for target in _includes(_SERVICE_REQS):
        assert Path(target).is_relative_to(_REPO_ROOT), (
            f"{target} escapes the repo root; see the comment in service/requirements.txt"
        )
        assert "COPY service/requirements.txt ./service_requirements.txt" not in (
            _REPO_ROOT / "Dockerfile"
        ).read_text(encoding="utf-8"), (
            "service/requirements.txt uses -r, but the Dockerfile flattens the COPY — "
            "the relative include will not resolve inside the image"
        )
