"""Tests for :mod:`casedd.getters.plex` (issue #65)."""

from __future__ import annotations

from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

import pytest

from casedd.data_store import DataStore
from casedd.getters.plex import PlexGetter, _parse_sessions


class _FakeResponse:
    """Minimal context-managed HTTP response for urlopen monkeypatching."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _PlexUrlOpenOK:
    """Return fixture XML payloads by request URL."""

    def __call__(self, req, timeout: float, context=None):  # noqa: PLR0911 -- URL fixture dispatcher
        url = req.full_url
        if url.endswith("/"):
            return _FakeResponse(
                '<MediaContainer friendlyName="My Plex" version="1.2.3" platform="Linux" />'
            )
        if "/status/sessions" in url:
            return _FakeResponse(
                """
                <MediaContainer size="2">
                                    <Video
                                        title="Movie One"
                                        duration="1000"
                                        viewOffset="500"
                                        bitrate="8000"
                                        librarySectionTitle="Movies"
                                    >
                    <User title="alice" />
                    <Media>
                      <Part>
                        <Stream decision="copy" />
                      </Part>
                    </Media>
                  </Video>
                                    <Video
                                        grandparentTitle="Show Name"
                                        duration="2000"
                                        viewOffset="1000"
                                        bitrate="6000"
                                        librarySectionTitle="Shows"
                                    >
                    <User title="bob" />
                    <TranscodeSession />
                  </Video>
                </MediaContainer>
                """
            )
        if "/library/sections/1/all" in url:
            return _FakeResponse('<MediaContainer totalSize="10" />')
        if "/library/sections/2/all" in url:
            return _FakeResponse('<MediaContainer totalSize="5" />')
        if "/library/sections/3/all" in url:
            return _FakeResponse('<MediaContainer totalSize="7" />')
        if "/library/sections" in url:
            return _FakeResponse(
                """
                <MediaContainer>
                  <Directory key="1" type="movie" />
                  <Directory key="2" type="show" />
                  <Directory key="3" type="artist" />
                </MediaContainer>
                """
            )
        if "/library/recentlyAdded" in url:
            return _FakeResponse(
                """
                <MediaContainer>
                                    <Video type="movie" title="Hidden Movie"
                                                 librarySectionTitle="Kids" addedAt="100" />
                                    <Directory type="album" title="New Album"
                                             librarySectionTitle="Music"
                                             addedAt="101" />
                </MediaContainer>
                """
            )
        raise AssertionError(f"Unhandled URL in fixture: {url}")


class _PlexUrlOpenNoSessions:
    """Return fixture XML with no active sessions and no recent entries."""

    def __call__(self, req, timeout: float, context=None):
        url = req.full_url
        if url.endswith("/"):
            return _FakeResponse(
                '<MediaContainer friendlyName="My Plex" version="1.2.3" platform="Linux" />'
            )
        if "/status/sessions" in url:
            return _FakeResponse('<MediaContainer size="0" />')
        if "/library/sections/1/all" in url:
            return _FakeResponse('<MediaContainer totalSize="1" />')
        if "/library/sections" in url:
            return _FakeResponse(
                '<MediaContainer><Directory key="1" type="movie" /></MediaContainer>'
            )
        if "/library/recentlyAdded" in url:
            return _FakeResponse('<MediaContainer size="0" />')
        raise AssertionError(f"Unhandled URL in fixture: {url}")


async def test_plex_getter_happy_path(monkeypatch) -> None:
    """Plex getter emits expected flattened keys for active sessions."""
    monkeypatch.setattr("casedd.getters.plex.urlopen", _PlexUrlOpenOK())

    getter = PlexGetter(
        DataStore(),
        base_url="http://plex.local:32400",
        token="abc",
        max_sessions=3,
        max_recent=3,
        privacy_filter_regex="(hidden|kids)",
    )

    payload = await getter.fetch()

    assert payload["plex.server.name"] == "My Plex"
    assert payload["plex.server.reachable"] == 1
    assert payload["plex.sessions.active_count"] == 2.0
    assert payload["plex.sessions.transcoding_count"] == 1.0
    assert payload["plex.sessions.direct_stream_count"] == 1.0
    assert payload["plex.sessions.direct_play_count"] == 0.0
    assert payload["plex.library.movies_count"] == 10.0
    assert payload["plex.library.shows_count"] == 5.0
    assert payload["plex.library.music_albums_count"] == 7.0
    assert payload["plex.session_1.user"] == "alice"
    assert payload["plex.session_1.title"] == "Movie One"
    assert payload["plex.session_2.transcode_decision"] == "transcode"
    assert payload["plex.recently_added_1.title"] == "[hidden]"
    assert payload["plex.recently_added_1.library"] == "[hidden]"
    assert payload["plex.recently_added.count"] == 2.0


async def test_plex_getter_no_sessions(monkeypatch) -> None:
    """No active sessions yields valid zero-state payload, not errors."""
    monkeypatch.setattr("casedd.getters.plex.urlopen", _PlexUrlOpenNoSessions())

    getter = PlexGetter(
        DataStore(),
        base_url="http://plex.local:32400",
        max_sessions=2,
        max_recent=2,
    )
    payload = await getter.fetch()

    assert payload["plex.server.reachable"] == 1
    assert payload["plex.sessions.active_count"] == 0.0
    assert payload["plex.sessions.transcoding_count"] == 0.0
    assert payload["plex.summary"] == "0 active / 0 transcode"
    assert payload["plex.session_1.title"] == ""
    assert payload["plex.recently_added_1.title"] == ""


async def test_plex_getter_library_name_filter(monkeypatch) -> None:
    """Configured library names should redact matching session and recent rows."""
    monkeypatch.setattr("casedd.getters.plex.urlopen", _PlexUrlOpenOK())

    getter = PlexGetter(
        DataStore(),
        base_url="http://plex.local:32400",
        token="abc",
        max_sessions=3,
        max_recent=3,
        privacy_filter_libraries=["Movies", "Kids"],
    )

    payload = await getter.fetch()

    assert payload["plex.session_1.title"] == "[hidden]"
    assert payload["plex.session_1.library"] == "[hidden]"
    assert payload["plex.session_2.title"] == "Show Name"
    assert payload["plex.session_2.library"] == "Shows"
    assert payload["plex.recently_added_1.title"] == "[hidden]"
    assert payload["plex.recently_added_1.library"] == "[hidden]"
    assert payload["plex.recently_added_2.title"] == "New Album"
    assert payload["plex.recently_added_2.library"] == "Music"


async def test_plex_getter_auth_failure(monkeypatch) -> None:
    """HTTP 401/403 should raise a clear auth failure runtime error."""

    def _raise_auth(req, timeout: float, context=None):
        raise HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr("casedd.getters.plex.urlopen", _raise_auth)

    getter = PlexGetter(DataStore(), token="bad-token")
    with pytest.raises(RuntimeError, match="auth failed"):
        await getter.fetch()


async def test_plex_getter_transport_failure(monkeypatch) -> None:
    """Network transport failures should bubble as runtime errors."""

    def _raise_transport(req, timeout: float, context=None):
        raise URLError("connect refused")

    monkeypatch.setattr("casedd.getters.plex.urlopen", _raise_transport)

    getter = PlexGetter(DataStore())
    with pytest.raises(RuntimeError, match="transport error"):
        await getter.fetch()


def test_parse_sessions_normalization_fixture() -> None:
    """Representative session XML normalizes play-mode decisions correctly."""
    xml = """
    <MediaContainer>
      <Video title="One" duration="1000" viewOffset="100" bitrate="1200">
        <User title="u1" />
        <Media><Part><Stream decision="copy" /></Part></Media>
      </Video>
      <Video title="Two" duration="2000" viewOffset="400" bitrate="900">
        <User title="u2" />
        <Media><Part><Stream decision="directplay" /></Part></Media>
      </Video>
      <Video title="Three" duration="3000" viewOffset="900" bitrate="1500">
        <User title="u3" />
        <TranscodeSession />
      </Video>
    </MediaContainer>
    """
    root = ET.fromstring(xml)
    rows = _parse_sessions(root)

    assert [row.transcode_decision for row in rows] == [
        "direct_stream",
        "direct_play",
        "transcode",
    ]
    assert rows[0].progress_percent == 10.0
