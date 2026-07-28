from types import SimpleNamespace

import pytest

from backend.app.api.routes import camera


def _printer():
    return SimpleNamespace(
        id=7,
        ip_address="192.0.2.30",
        connection_port=7125,
        access_code="",
    )


def test_moonraker_camera_urls_are_same_host_and_include_web_proxy_fallback():
    printer = _printer()

    assert camera._moonraker_camera_urls(
        printer,
        {"snapshot_url": "/webcam/snapshot"},
        "snapshot_url",
    ) == [
        "http://192.0.2.30:7125/webcam/snapshot",
        "http://192.0.2.30/webcam/snapshot",
    ]
    assert (
        camera._moonraker_camera_urls(
            printer,
            {"snapshot_url": "http://untrusted.example/snapshot"},
            "snapshot_url",
        )
        == []
    )
    assert (
        camera._moonraker_camera_urls(
            printer,
            {"snapshot_url": "//untrusted.example/snapshot"},
            "snapshot_url",
        )
        == []
    )


@pytest.mark.asyncio
async def test_moonraker_stream_falls_back_to_same_host_port_80(monkeypatch):
    requested = []

    class Response:
        def __init__(self, status_code, content=b""):
            self.status_code = status_code
            self.content = content

        def raise_for_status(self):
            if self.status_code >= 400:
                raise camera.httpx.HTTPStatusError(
                    "not found",
                    request=camera.httpx.Request("GET", requested[-1]),
                    response=camera.httpx.Response(self.status_code),
                )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            requested.append(url)
            return Response(404) if ":7125/" in url else Response(200, b"\xff\xd8camera")

    monkeypatch.setattr(camera.httpx, "AsyncClient", lambda **_kwargs: Client())
    stream = camera._generate_moonraker_polling_stream(
        7,
        _printer(),
        [
            "http://192.0.2.30:7125/webcam/snapshot",
            "http://192.0.2.30/webcam/snapshot",
        ],
        5,
    )
    frame = await anext(stream)
    await stream.aclose()

    assert requested == [
        "http://192.0.2.30:7125/webcam/snapshot",
        "http://192.0.2.30/webcam/snapshot",
    ]
    assert b"\xff\xd8camera" in frame
