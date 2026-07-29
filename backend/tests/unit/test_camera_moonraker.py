import asyncio
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


def test_moonraker_native_mjpeg_detection_is_conservative():
    assert camera._moonraker_supports_native_mjpeg(
        {
            "service": "mjpegstreamer-adaptive",
            "stream_url": "/webcam/stream",
            "snapshot_url": "/webcam/snapshot",
        }
    )
    assert not camera._moonraker_supports_native_mjpeg(
        {
            "service": "mjpegstreamer-adaptive",
            "stream_url": "/webcam/snapshot",
            "snapshot_url": "/webcam/snapshot",
        }
    )
    assert not camera._moonraker_supports_native_mjpeg(
        {
            "service": "webrtc-camerastreamer",
            "stream_url": "/webcam/webrtc",
            "snapshot_url": "/webcam/snapshot",
        }
    )


@pytest.mark.asyncio
async def test_persistent_mjpeg_reuses_one_http_connection_for_multiple_frames(monkeypatch):
    requested = []
    first = b"\xff\xd8first\xff\xd9"
    second = b"\xff\xd8second\xff\xd9"

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"multipart headers\r\n" + first[:5]
            yield first[5:] + b"\r\n--boundary\r\n"
            await asyncio.sleep(0.1)
            yield second

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, url):
            requested.append(url)
            return Response()

    monkeypatch.setattr(camera.httpx, "AsyncClient", lambda **_kwargs: Client())
    disconnect = asyncio.Event()
    stream = camera._generate_persistent_mjpeg_stream(
        7,
        ["http://192.0.2.30/webcam/stream"],
        30,
        disconnect,
    )
    frame_one = await anext(stream)
    frame_two = await anext(stream)
    disconnect.set()
    await stream.aclose()

    assert requested == ["http://192.0.2.30/webcam/stream"]
    assert first in frame_one
    assert second in frame_two
    assert camera._last_frames[7] == second
    camera._last_frames.pop(7, None)
    camera._last_frame_times.pop(7, None)


@pytest.mark.asyncio
async def test_moonraker_native_stream_falls_back_to_shared_snapshot_loop(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        if False:
            yield b""
        raise camera._MjpegStreamUnavailable

    async def polling(*_args, **_kwargs):
        yield b"snapshot-frame"

    monkeypatch.setattr(camera, "_generate_persistent_mjpeg_stream", unavailable)
    monkeypatch.setattr(camera, "_generate_moonraker_polling_stream", polling)
    webcam = {
        "service": "mjpegstreamer-adaptive",
        "stream_url": "/webcam/stream",
        "snapshot_url": "/webcam/snapshot",
    }
    stream = camera._generate_moonraker_camera_stream(
        7,
        _printer(),
        webcam,
        ["http://192.0.2.30/webcam/stream"],
        ["http://192.0.2.30/webcam/snapshot"],
        10,
        asyncio.Event(),
    )

    assert await anext(stream) == b"snapshot-frame"
    await stream.aclose()


@pytest.mark.asyncio
async def test_snapshot_reuses_provider_neutral_active_stream(monkeypatch):
    frame = b"\xff\xd8shared-frame\xff\xd9"
    camera._last_frames[77] = frame
    monkeypatch.setattr(camera, "is_stream_active", lambda _printer_id: True)

    response = await camera._snapshot_from_active_stream(77)

    assert response is not None
    assert response.body == frame
    camera._last_frames.pop(77, None)


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
