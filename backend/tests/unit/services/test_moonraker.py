from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import backend.app.services.moonraker as moonraker
from backend.app.services.moonraker import MoonrakerClient, probe_moonraker_connection, stable_moonraker_device_id

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "moonraker"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _client_from_fixture(name: str, **kwargs: Any) -> MoonrakerClient:
    fixture = _fixture(name)
    client = MoonrakerClient(ip_address="192.0.2.30", **kwargs)
    client._server_info = fixture["server_info"]
    client._printer_info = fixture["printer_info"]
    client._objects = set(fixture["objects"])
    client._webcams = fixture["webcams"]
    client._apply_status(fixture["status"])
    return client


def test_tinyt_maps_tools_sensors_motion_and_capabilities() -> None:
    client = _client_from_fixture("tinyt")

    snapshot = client.state.raw_data["provider_snapshot"]
    assert client.state.connected is True
    assert client.state.state == "IDLE"
    assert [tool["id"] for tool in snapshot["tools"]] == ["T0", "T1"]
    assert snapshot["tools"][0]["active"] is True
    assert snapshot["motion"]["homed_axes"] == ["x", "y", "z"]
    assert snapshot["motion"]["leveling_method"] == "z_tilt"
    assert snapshot["device_info"]["build_volume"] == "169.2 × 172.5 × 125 mm"
    assert snapshot["device_info"]["mcu_count"] == 3
    assert {sensor["label"] for sensor in snapshot["sensors"]} >= {
        "Toolhead T0",
        "Toolhead T1",
        "Raspberry Pi",
    }
    assert client.get_macros() == ["PARK", "PRINT_START"]
    assert client.get_webcams()[0]["snapshot_url"] == "/webcam/snapshot"


def test_trident_maps_qgl_single_tool_and_unhomed_state() -> None:
    client = _client_from_fixture("trident")

    snapshot = client.state.raw_data["provider_snapshot"]
    assert [tool["id"] for tool in snapshot["tools"]] == ["extruder"]
    assert snapshot["motion"]["homed_axes"] == []
    assert snapshot["motion"]["leveling_method"] == "quad_gantry_level"
    assert snapshot["motion"]["speed_factor_percent"] == 90
    assert snapshot["motion"]["flow_factor_percent"] == 105
    assert snapshot["device_info"]["build_volume"] == "424 × 416 × 360 mm"


def test_partial_websocket_updates_merge_and_emit_print_transitions() -> None:
    started: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    client = _client_from_fixture("tinyt", on_print_start=started.append, on_print_complete=completed.append)

    client._handle_websocket_payload(
        {
            "method": "notify_status_update",
            "params": [
                {
                    "print_stats": {
                        "state": "printing",
                        "filename": "Bambuddy Tests/tinyt-validation.gcode",
                        "print_duration": 60,
                        "info": {"current_layer": 2, "total_layer": 20},
                    },
                    "virtual_sdcard": {"progress": 0.1},
                }
            ],
        }
    )
    assert client.state.state == "RUNNING"
    assert client.state.progress == 10
    assert client.state.layer_num == 2
    assert client.state.temperatures["nozzle"] == pytest.approx(24.1)
    assert started[0]["filename"] == "Bambuddy Tests/tinyt-validation.gcode"

    client._handle_websocket_payload(
        {
            "method": "notify_status_update",
            "params": [{"print_stats": {"state": "complete"}}],
        }
    )
    assert client.state.state == "FINISH"
    assert completed[0]["status"] == "completed"


def test_dynamic_subscription_excludes_unknown_and_private_objects() -> None:
    client = _client_from_fixture("tinyt")
    client._objects.add("dangerous_custom_object")

    subscribed = client._subscription_objects()

    assert "print_stats" in subscribed
    assert "tool T0" in subscribed
    assert "filament_switch_sensor toolhead_T0" in subscribed
    assert "dangerous_custom_object" not in subscribed
    assert "gcode_macro _INTERNAL_HELPER" not in subscribed


def test_tool_selection_requires_idle_homed_ready_toolchanger(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_from_fixture("tinyt")
    commands: list[str] = []
    monkeypatch.setattr(client, "run_gcode", lambda script, **_kwargs: commands.append(script) or True)

    assert client.select_tool("T1") is True
    assert commands == ["T1"]

    client.state.raw_data["provider_snapshot"]["motion"]["homed_axes"] = ["x", "y"]
    assert client.select_tool("T0") is False
    client.state.raw_data["provider_snapshot"]["motion"]["homed_axes"] = ["x", "y", "z"]
    client.state.state = "RUNNING"
    assert client.select_tool("T0") is False


def test_files_are_directory_aware_and_paths_are_confined(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_from_fixture("trident")
    monkeypatch.setattr(
        client,
        "_http_get",
        lambda *_args, **_kwargs: [
            {"path": "root.gcode", "size": 12, "modified": 1},
            {"path": "jobs/one.gcode", "size": 23, "modified": 2},
            {"path": "jobs/nested/two.gcode", "size": 34, "modified": 3},
        ],
    )

    root = client.list_files()
    assert [(item["name"], item["is_directory"]) for item in root] == [
        ("jobs", True),
        ("root.gcode", False),
    ]
    jobs = client.list_files("jobs")
    assert [(item["name"], item["is_directory"]) for item in jobs] == [
        ("nested", True),
        ("one.gcode", False),
    ]
    with pytest.raises(ValueError):
        client.list_files("../config")
    with pytest.raises(ValueError):
        client.download_file("../../printer.cfg")


def test_history_is_bounded_and_mapped_without_raw_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_from_fixture("trident")
    monkeypatch.setattr(
        client,
        "_http_get",
        lambda *_args, **_kwargs: {
            "jobs": [
                {
                    "job_id": "test-job",
                    "filename": "cube.gcode",
                    "status": "completed",
                    "start_time": 1,
                    "end_time": 2,
                    "print_duration": 59.5,
                    "total_duration": 65,
                    "metadata": {"filament_total": 123.4, "private": "not exposed"},
                }
            ]
        },
    )

    assert client.get_print_history(500) == [
        {
            "job_id": "test-job",
            "filename": "cube.gcode",
            "status": "completed",
            "start_time": 1,
            "end_time": 2,
            "print_duration": 59.5,
            "total_duration": 65,
            "filament_used": 123.4,
        }
    ]


def test_null_json_rpc_result_is_still_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_from_fixture("trident")
    monkeypatch.setattr(client, "_rpc", lambda *_args, **_kwargs: None)
    assert client.pause_print() is True
    assert client.resume_print() is True
    assert client.stop_print() is True


def test_command_timeout_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_from_fixture("trident")

    class TimeoutClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> TimeoutClient:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def post(self, *_args: Any, **_kwargs: Any) -> None:
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(moonraker.httpx, "Client", TimeoutClient)
    assert client.pause_print() is False


def test_temperature_fan_uses_temperature_target_command(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_from_fixture("tinyt")
    client._objects.add("temperature_fan enclosure")
    commands: list[str] = []
    monkeypatch.setattr(client, "run_gcode", lambda script, **_kwargs: commands.append(script) or True)

    assert client.set_heater_temperature("temperature_fan enclosure", 38) is True
    assert commands == ["SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN=enclosure TARGET=38"]
    assert client.set_fan_percent("temperature_fan enclosure", 50) is False


def test_stable_id_is_repeatable_and_endpoint_specific() -> None:
    first = stable_moonraker_device_id("tinyt", "192.0.2.30", 7125)
    assert first == stable_moonraker_device_id("TinyT", "192.0.2.30", 7125)
    assert first.startswith("KLIPPER-")
    assert first != stable_moonraker_device_id("tinyt", "192.0.2.31", 7125)


@pytest.mark.asyncio
async def test_probe_sends_optional_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_headers: dict[str, str] = {}

    class FakeResponse:
        def __init__(self, result: dict[str, Any]) -> None:
            self._result = result

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"result": self._result}

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            seen_headers.update(kwargs.get("headers") or {})

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, path: str) -> FakeResponse:
            if path == "/server/info":
                return FakeResponse({"hostname": "tinyt", "moonraker_version": "v0.10"})
            if path == "/printer/info":
                return FakeResponse({"state": "ready", "software_version": "v0.13"})
            return FakeResponse({"objects": ["print_stats", "toolhead"]})

    monkeypatch.setattr(moonraker.httpx, "AsyncClient", FakeAsyncClient)
    result = await probe_moonraker_connection("192.0.2.30", api_key="example-test-key")

    assert result["success"] is True
    assert result["hostname"] == "tinyt"
    assert result["serial_number"].startswith("KLIPPER-")
    assert seen_headers == {"X-Api-Key": "example-test-key"}


@pytest.mark.asyncio
async def test_http_polling_fallback_applies_full_status() -> None:
    fixture = _fixture("trident")
    client = MoonrakerClient(ip_address="192.0.2.31")
    client._server_info = fixture["server_info"]
    client._printer_info = fixture["printer_info"]
    client._objects = set(fixture["objects"])

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self) -> None:
            return None

        async def json(self) -> dict[str, Any]:
            return {"result": {"status": fixture["status"]}}

    class FakeSession:
        def post(self, *_args, **_kwargs):
            return FakeResponse()

    await client._poll_status_http(FakeSession())

    assert client.state.connected is True
    assert client.state.state == "IDLE"
    assert client.state.raw_data["provider_snapshot"]["motion"]["leveling_method"] == "quad_gantry_level"
