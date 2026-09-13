"""Regression tests for CASEDD development and installation shell scripts."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_start_fb_restarts_running_daemon_before_saving_preference() -> None:
    """Framebuffer startup must replace an existing no-framebuffer daemon."""
    script = (_REPO_ROOT / "dev.sh").read_text(encoding="utf-8")
    function = script.split("cmd_start_fb() {", maxsplit=1)[1].split(
        "\n}\n\ncmd_stop()", maxsplit=1
    )[0]

    running_check = function.index("if _is_running; then")
    stop = function.index("cmd_stop", running_check)
    preference = function.index("_save_fb_pref")

    assert running_check < stop < preference


def test_installer_grants_framebuffer_group_before_service_start() -> None:
    """The installed service user must have framebuffer access before launch."""
    script = (_REPO_ROOT / "deploy/install/install.sh").read_text(encoding="utf-8")
    install_function = script.split("install_service() {", maxsplit=1)[1].split(
        "\n}\n\nuninstall_service()", maxsplit=1
    )[0]

    grant = install_function.index('ensure_framebuffer_access "${service_user}"')
    start = install_function.index('install_unit "${service_user}"')

    assert grant < start


def test_installer_uses_default_python_interpreter() -> None:
    """Fresh installs must work when Ubuntu provides Python newer than 3.12."""
    script = (_REPO_ROOT / "deploy/install/install.sh").read_text(encoding="utf-8")

    assert 'run_cmd python3 -m venv "${VENV_DIR}"' in script
    assert "python3.12 -m venv" not in script


def test_development_ruff_version_is_reproducible() -> None:
    """Lint behavior must not change merely because a new Ruff is released."""
    requirements = (_REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    assert "ruff==0.15.12" in requirements.splitlines()
