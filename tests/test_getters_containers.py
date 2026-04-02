"""Tests for container runtime getter and parser helpers."""

from __future__ import annotations

from unittest.mock import patch

from casedd.data_store import DataStore
from casedd.getters.containers import (
    ContainersGetter,
    _parse_containerd_rows,
    _parse_docker_like_rows,
)


def test_docker_getter_builds_summary_payload() -> None:
    """Docker backend should populate counts and rows."""
    docker_rows = (
        "web|Up 3 hours (healthy)|nginx:latest\n"
        "db|Exited (0) 2 days ago|postgres:16\n"
        "cache|Paused|redis:7\n"
    )

    with (
        patch(
            "casedd.getters.containers.shutil.which",
            side_effect=["/usr/bin/docker", None, None],
        ),
        patch("casedd.getters.containers.subprocess.run") as run_mock,
    ):
        run_mock.return_value.stdout = docker_rows
        getter = ContainersGetter(DataStore(), runtime="docker", max_items=5)
        payload = getter._sample()

    assert payload["containers.available"] == 1.0
    assert payload["containers.runtime"] == "docker"
    assert payload["containers.count_total"] == 3.0
    assert payload["containers.count_running"] == 1.0
    assert payload["containers.count_exited"] == 1.0
    assert payload["containers.count_paused"] == 1.0
    assert payload["containers.logo_path"] == "assets/docker/docker-official-logo.png"
    assert payload["containers.1.name"] == "web"
    assert payload["containers.1.status_icon"] == "started"
    assert payload["containers.1.health_icon"] == "healthy"
    assert payload["containers.2.status"] == "Exited"
    first_row = str(payload["containers.rows"]).splitlines()[0]
    assert first_row.startswith("web|started|healthy|")


def test_getter_falls_back_to_unavailable() -> None:
    """When no runtime command exists, getter should emit unavailable status."""
    with patch("casedd.getters.containers.shutil.which", side_effect=[None, None, None]):
        payload = ContainersGetter(DataStore())._sample()

    assert payload["containers.available"] == 0.0
    assert payload["containers.runtime"] == "unavailable"
    assert payload["containers.logo_path"] == "assets/casedd-logo.png"
    assert "No runtime available" in str(payload["containers.rows"])


def test_auto_runtime_prefers_podman_when_docker_missing() -> None:
    """Auto mode should pick podman if docker is unavailable."""
    with (
        patch(
            "casedd.getters.containers.shutil.which",
            side_effect=[None, "/usr/bin/podman", None],
        ),
        patch("casedd.getters.containers.subprocess.run") as run_mock,
    ):
        run_mock.return_value.stdout = "svc|Up 10 minutes|ghcr.io/demo/svc:latest\n"
        payload = ContainersGetter(DataStore(), runtime="auto")._sample()

    assert payload["containers.runtime"] == "podman"
    assert payload["containers.logo_path"] == "assets/docker/podman-official-logo.webp"
    assert payload["containers.count_running"] == 1.0


def test_parse_docker_like_rows_normalizes_status() -> None:
    """Parser should normalize docker-like status strings."""
    rows = _parse_docker_like_rows(
        "api|Up 15 minutes (healthy)|ghcr.io/demo/api:latest\n"
        "worker|Exited (137) 3 hours ago|ghcr.io/demo/worker:latest"
    )
    assert rows[0].status == "Running"
    assert rows[0].health == "healthy"
    assert rows[1].status == "Exited"


def test_parse_containerd_rows_merges_tasks_status() -> None:
    """containerd parser should infer status from task table."""
    containers_text = (
        "CONTAINER    IMAGE                              RUNTIME\n"
        "alpha        docker.io/library/nginx:latest    io.containerd.runc.v2\n"
        "beta         docker.io/library/redis:7         io.containerd.runc.v2\n"
    )
    tasks_text = (
        "TASK         PID      STATUS\n"
        "alpha        1234     RUNNING\n"
    )
    rows = _parse_containerd_rows(containers_text, tasks_text)
    assert rows[0].name == "alpha"
    assert rows[0].status == "Running"
    assert rows[1].status == "Exited"
