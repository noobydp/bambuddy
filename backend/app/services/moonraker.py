"""Klipper/Moonraker provider client.

Moonraker exposes a regular HTTP API for commands and files plus a JSON-RPC
WebSocket for low-latency status updates.  Bambuddy's printer manager is
synchronous at the provider boundary, so this client owns a small background
asyncio loop for the persistent WebSocket and keeps the familiar
``PrinterState`` object up to date.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import aiohttp
import httpx

from backend.app.services.bambu_mqtt import PrinterState

logger = logging.getLogger(__name__)

DEFAULT_MOONRAKER_PORT = 7125
MOONRAKER_STALE_AFTER_SECONDS = 45.0
MOONRAKER_POLL_INTERVAL_SECONDS = 15.0
MOONRAKER_COMMAND_TIMEOUT_SECONDS = 12.0
MOONRAKER_CONSOLE_HISTORY_LIMIT = 500
_RPC_FAILED = object()

_ACTIVE_STATES = {"RUNNING", "PAUSE", "PREPARE"}
_MACRO_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_OBJECT_PREFIXES = (
    "extruder",
    "heater_bed",
    "heater_generic ",
    "temperature_sensor ",
    "temperature_host ",
    "temperature_fan ",
    "fan",
    "fan_generic ",
    "heater_fan ",
    "controller_fan ",
    "filament_switch_sensor ",
    "filament_motion_sensor ",
    "tool ",
    "mcu",
)
_CORE_OBJECTS = {
    "webhooks",
    "print_stats",
    "virtual_sdcard",
    "display_status",
    "toolhead",
    "gcode_move",
    "configfile",
    "bed_mesh",
    "toolchanger",
    "system_stats",
}


class MoonrakerError(RuntimeError):
    """A Moonraker request returned an error response."""


class _ProgressReader:
    """File-like upload wrapper that reports bytes consumed by httpx."""

    def __init__(
        self,
        source,
        total: int,
        callback: Callable[[int, int], None] | None,
    ) -> None:
        self._source = source
        self._total = total
        self._callback = callback

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        if self._callback:
            self._callback(self._source.tell(), self._total)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._source.seek(offset, whence)

    def tell(self) -> int:
        return self._source.tell()

    def fileno(self) -> int:
        return self._source.fileno()


def stable_moonraker_device_id(hostname: str | None, endpoint: str, port: int) -> str:
    """Create a stable, non-secret Bambuddy identity for a Moonraker endpoint."""
    normalized_hostname = (hostname or endpoint).strip().lower()
    normalized_endpoint = endpoint.strip().lower()
    digest = hashlib.sha256(f"{normalized_hostname}@{normalized_endpoint}:{port}".encode()).hexdigest()[:16]
    return f"KLIPPER-{digest.upper()}"


def _headers(api_key: str | None) -> dict[str, str]:
    return {"X-Api-Key": api_key.strip()} if api_key and api_key.strip() else {}


def _result(payload: dict[str, Any]) -> Any:
    if not isinstance(payload, dict):
        raise MoonrakerError("Moonraker returned an invalid response")
    if payload.get("error"):
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise MoonrakerError(message or "Moonraker command failed")
    return payload.get("result")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _label(name: str) -> str:
    if " " in name:
        value = name.split(" ", 1)[1]
    else:
        value = name
    return value.replace("_", " ").strip().title()


def _safe_gcode_path(path: str | None, *, allow_root: bool = True) -> str:
    raw = str(path or "").replace("\\", "/").strip("/")
    if not raw:
        if allow_root:
            return ""
        raise ValueError("A G-code file path is required")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("Invalid G-code path")
    return pure.as_posix()


def _deep_merge_status(target: dict[str, dict[str, Any]], update: dict[str, Any]) -> None:
    for object_name, values in update.items():
        if values is None:
            target.pop(object_name, None)
        elif isinstance(values, dict):
            target.setdefault(object_name, {}).update(values)


async def probe_moonraker_connection(
    ip_address: str,
    port: int = DEFAULT_MOONRAKER_PORT,
    api_key: str | None = None,
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Probe a Moonraker endpoint without persisting credentials or config."""
    base_url = f"http://{ip_address}:{port}"
    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            headers=_headers(api_key),
            timeout=timeout,
        ) as client:
            server_response, printer_response, objects_response = await asyncio.gather(
                client.get("/server/info"),
                client.get("/printer/info"),
                client.get("/printer/objects/list"),
            )
            server_response.raise_for_status()
            printer_response.raise_for_status()
            objects_response.raise_for_status()
            server_info = _result(server_response.json()) or {}
            printer_info = _result(printer_response.json()) or {}
            objects_info = _result(objects_response.json()) or {}
    except (httpx.HTTPError, ValueError, MoonrakerError) as exc:
        return {
            "success": False,
            "code": "moonraker_connection_failed",
            "message": str(exc),
        }

    hostname = str(server_info.get("hostname") or printer_info.get("hostname") or ip_address)
    objects = sorted(str(value) for value in objects_info.get("objects") or [])
    return {
        "success": True,
        "state": str(printer_info.get("state") or "unknown"),
        "model": "Klipper",
        "hostname": hostname,
        "serial_number": stable_moonraker_device_id(hostname, ip_address, port),
        "connection_port": port,
        "moonraker_version": server_info.get("moonraker_version"),
        "klipper_version": printer_info.get("software_version"),
        "objects": objects,
    }


class MoonrakerClient:
    """Persistent Moonraker status client with synchronous command methods."""

    def __init__(
        self,
        *,
        ip_address: str,
        port: int = DEFAULT_MOONRAKER_PORT,
        api_key: str | None = None,
        model: str | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
    ) -> None:
        self.ip_address = ip_address
        self.port = port or DEFAULT_MOONRAKER_PORT
        self.api_key = (api_key or "").strip()
        self.model = model or "Klipper"
        self.state = PrinterState()
        self.state.nozzles = []
        self._on_state_change = on_state_change
        self._on_print_start = on_print_start
        self._on_print_complete = on_print_complete
        self._objects: set[str] = set()
        self._status: dict[str, dict[str, Any]] = {}
        self._server_info: dict[str, Any] = {}
        self._printer_info: dict[str, Any] = {}
        self._webcams: list[dict[str, Any]] = []
        self._disk_usage: dict[str, Any] = {}
        self._last_update = 0.0
        self._previous_state = "unknown"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._console_history: deque[dict[str, Any]] = deque(maxlen=MOONRAKER_CONSOLE_HISTORY_LIMIT)
        self.logging_enabled = False

    @property
    def base_url(self) -> str:
        return f"http://{self.ip_address}:{self.port}"

    @property
    def websocket_url(self) -> str:
        return f"ws://{self.ip_address}:{self.port}/websocket"

    def connect(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"moonraker-{self.ip_address}",
            daemon=True,
        )
        self._thread.start()

    def disconnect(self, timeout: float = 0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and timeout:
            thread.join(timeout)
        self.state.connected = False

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._connection_loop())
        except Exception:
            logger.exception("Moonraker connection loop failed for %s", self.ip_address)
        finally:
            self.state.connected = False

    async def _connection_loop(self) -> None:
        backoff = 1.0
        headers = _headers(self.api_key)
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=60)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            while not self._stop_event.is_set():
                fallback_connected = False
                try:
                    await self._discover(session)
                    await self._websocket_session(session)
                    backoff = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if not self._stop_event.is_set():
                        logger.warning("Moonraker disconnected from %s: %s", self.ip_address, exc)
                        # Some reverse proxies disable WebSockets while leaving
                        # Moonraker's HTTP API healthy. Preserve live status via
                        # JSON-RPC polling between bounded reconnect attempts.
                        try:
                            await self._poll_status_http(session)
                            fallback_connected = True
                        except Exception as poll_exc:
                            logger.debug("Moonraker HTTP fallback failed for %s: %s", self.ip_address, poll_exc)
                self.state.connected = fallback_connected
                if not self._last_update or time.monotonic() - self._last_update > MOONRAKER_STALE_AFTER_SECONDS:
                    self._emit_state()
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(min(backoff, MOONRAKER_POLL_INTERVAL_SECONDS))
                backoff = min(backoff * 2, 30.0)

    async def _get_json(self, session: aiohttp.ClientSession, path: str) -> Any:
        async with session.get(f"{self.base_url}{path}") as response:
            response.raise_for_status()
            return _result(await response.json())

    async def _discover(self, session: aiohttp.ClientSession) -> None:
        server_info, printer_info, object_info = await asyncio.gather(
            self._get_json(session, "/server/info"),
            self._get_json(session, "/printer/info"),
            self._get_json(session, "/printer/objects/list"),
        )
        self._server_info = server_info or {}
        self._printer_info = printer_info or {}
        self._objects = set((object_info or {}).get("objects") or [])
        try:
            webcam_info = await self._get_json(session, "/server/webcams/list")
            self._webcams = list((webcam_info or {}).get("webcams") or [])
        except Exception:
            self._webcams = []
        try:
            directory_info = await self._get_json(session, "/server/files/directory?path=gcodes")
            self._disk_usage = dict((directory_info or {}).get("disk_usage") or {})
        except Exception:
            self._disk_usage = {}

    async def _poll_status_http(self, session: aiohttp.ClientSession) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "printer.objects.query",
            "params": {"objects": self._subscription_objects()},
            "id": int(time.time() * 1000),
        }
        async with session.post(f"{self.base_url}/server/jsonrpc", json=payload) as response:
            response.raise_for_status()
            result = _result(await response.json())
        if isinstance(result, dict) and isinstance(result.get("status"), dict):
            self._apply_status(result["status"])

    def _subscription_objects(self) -> dict[str, None]:
        selected = {
            name
            for name in self._objects
            if name in _CORE_OBJECTS
            or any(name == prefix or name.startswith(prefix) for prefix in _SAFE_OBJECT_PREFIXES)
        }
        return dict.fromkeys(sorted(selected))

    async def _websocket_session(self, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(self.websocket_url, heartbeat=20) as ws:
            request_id = 1
            identify = {
                "jsonrpc": "2.0",
                "method": "server.connection.identify",
                "params": {
                    "client_name": "Bambuddy",
                    "version": "1",
                    "type": "agent",
                    "url": "https://bambuddy.cool",
                },
                "id": request_id,
            }
            if self.api_key:
                identify["params"]["api_key"] = self.api_key
            await ws.send_json(identify)
            request_id += 1
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "method": "printer.objects.subscribe",
                    "params": {"objects": self._subscription_objects()},
                    "id": request_id,
                }
            )

            last_poll = 0.0
            while not self._stop_event.is_set():
                try:
                    message = await asyncio.wait_for(ws.receive(), timeout=5.0)
                except TimeoutError:
                    message = None
                now = time.monotonic()
                if message is not None:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self._handle_websocket_payload(json.loads(message.data))
                    elif message.type in {
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    }:
                        break
                if now - last_poll >= MOONRAKER_POLL_INTERVAL_SECONDS:
                    await ws.send_json(
                        {
                            "jsonrpc": "2.0",
                            "method": "printer.objects.query",
                            "params": {"objects": self._subscription_objects()},
                            "id": request_id + 1,
                        }
                    )
                    request_id += 1
                    last_poll = now

    def _handle_websocket_payload(self, payload: dict[str, Any]) -> None:
        method = payload.get("method")
        if method == "notify_status_update":
            params = payload.get("params") or []
            self._apply_status(params[0] if params else {})
            return
        if method == "notify_gcode_response":
            params = payload.get("params") or []
            if params:
                self._record_console("response", str(params[0]))
            return
        if method in {"notify_klippy_shutdown", "notify_klippy_disconnected"}:
            self.state.connected = False
            self._emit_state()
            return

        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("status"), dict):
            self._apply_status(result["status"])

    def _apply_status(self, update: dict[str, Any]) -> None:
        if not isinstance(update, dict):
            return
        with self._lock:
            _deep_merge_status(self._status, update)
            self._last_update = time.monotonic()
            self.state.connected = True
            self._map_state()
        self._emit_state()

    def _map_state(self) -> None:
        status = self._status
        print_stats = status.get("print_stats", {})
        virtual_sd = status.get("virtual_sdcard", {})
        display = status.get("display_status", {})
        toolhead = status.get("toolhead", {})
        gcode_move = status.get("gcode_move", {})
        config = status.get("configfile", {}).get("settings") or {}

        raw_print_state = str(print_stats.get("state") or "standby").lower()
        mapped = {
            "printing": "RUNNING",
            "paused": "PAUSE",
            "complete": "FINISH",
            "cancelled": "FAILED",
            "error": "FAILED",
            "standby": "IDLE",
        }.get(raw_print_state, raw_print_state.upper() if raw_print_state else "unknown")
        filename = str(print_stats.get("filename") or virtual_sd.get("file_path") or "") or None
        self.state.state = mapped
        self.state.current_print = filename
        self.state.subtask_name = PurePosixPath(filename).name if filename else None
        self.state.gcode_file = filename
        self.state.subtask_id = filename
        progress = virtual_sd.get("progress", display.get("progress", 0))
        self.state.progress = max(0.0, min(100.0, _float(progress) * (100 if _float(progress) <= 1 else 1)))
        info = print_stats.get("info") or {}
        self.state.layer_num = _int(info.get("current_layer"))
        self.state.total_layers = _int(info.get("total_layer"))
        print_duration = _float(print_stats.get("print_duration"))
        if self.state.progress > 0 and mapped in _ACTIVE_STATES:
            estimated_total = print_duration / (self.state.progress / 100)
            self.state.remaining_time = max(0, int(math.ceil((estimated_total - print_duration) / 60)))
        else:
            self.state.remaining_time = 0

        temperatures: dict[str, float | bool] = {}
        tools: list[dict[str, Any]] = []
        heaters: list[dict[str, Any]] = []
        fans: list[dict[str, Any]] = []
        sensors: list[dict[str, Any]] = []

        extruders = sorted(
            (name for name in status if name == "extruder" or name.startswith("extruder")),
            key=lambda name: (name != "extruder", name),
        )
        active_extruder = str(toolhead.get("extruder") or "extruder")
        for index, name in enumerate(extruders):
            values = status.get(name, {})
            key = "nozzle" if index == 0 else f"nozzle_{index + 1}"
            current = _float(values.get("temperature"))
            target = _float(values.get("target"))
            temperatures[key] = current
            temperatures[f"{key}_target"] = target
            temperatures[f"{key}_heating"] = target > current + 1
            tools.append(
                {
                    "id": name,
                    "index": index,
                    "label": _label(name),
                    "temperature": current,
                    "target_temperature": target,
                    "heating": target > current + 1,
                    "active": name == active_extruder,
                    "nozzle_diameter": None,
                    "filament_type": None,
                }
            )
        self.state.active_extruder = extruders.index(active_extruder) if active_extruder in extruders else 0

        heater_names = [
            name
            for name in status
            if name == "heater_bed" or name.startswith("heater_generic ") or name.startswith("temperature_fan ")
        ]
        for name in sorted(heater_names):
            values = status.get(name, {})
            current = _float(values.get("temperature"))
            target = _float(values.get("target"))
            heater_id = "bed" if name == "heater_bed" else name
            if name == "heater_bed":
                temperatures["bed"] = current
                temperatures["bed_target"] = target
                temperatures["bed_heating"] = target > current + 1
            heaters.append(
                {
                    "id": heater_id,
                    "label": _label(name),
                    "temperature": current,
                    "target_temperature": target,
                    "heating": target > current + 1,
                    "controllable": True,
                }
            )

        fan_names = [
            name
            for name in status
            if name == "fan"
            or name.startswith("fan_generic ")
            or name.startswith("temperature_fan ")
            or name.startswith("controller_fan ")
            or name.startswith("heater_fan ")
        ]
        for name in sorted(fan_names):
            values = status.get(name, {})
            speed = max(0, min(100, round(_float(values.get("speed")) * 100)))
            # Temperature fans are controlled by a temperature target, not a
            # direct fan-speed target. They are exposed in both lists for
            # visibility, but only the heater control is actionable.
            controllable = name == "fan" or name.startswith("fan_generic ")
            fans.append(
                {
                    "id": name,
                    "label": _label(name),
                    "speed_percent": speed,
                    "active": speed > 0,
                    "controllable": controllable,
                }
            )
        default_fan = status.get("fan", {})
        self.state.cooling_fan_speed = (
            max(0, min(100, round(_float(default_fan.get("speed")) * 100))) if default_fan else None
        )

        for name in sorted(status):
            values = status.get(name, {})
            if name.startswith(("filament_switch_sensor ", "filament_motion_sensor ")):
                detected = bool(values.get("filament_detected"))
                sensors.append(
                    {
                        "id": name,
                        "label": _label(name),
                        "kind": "filament",
                        "value": detected,
                        "unit": None,
                        "triggered": detected,
                    }
                )
            elif name.startswith(("temperature_sensor ", "temperature_host ")):
                value = _float(values.get("temperature"))
                sensors.append(
                    {
                        "id": name,
                        "label": _label(name),
                        "kind": "temperature",
                        "value": value,
                        "unit": "°C",
                        "triggered": None,
                    }
                )

        kinematics = str((config.get("printer") or {}).get("kinematics") or "") or None
        leveling_method = (
            "quad_gantry_level"
            if "quad_gantry_level" in self._objects
            else ("z_tilt" if "z_tilt" in self._objects else None)
        )
        position = toolhead.get("position") or gcode_move.get("gcode_position") or []
        position_map = {
            axis: _float(position[index]) for index, axis in enumerate(("x", "y", "z", "e")) if index < len(position)
        }
        homed_axes = list(str(toolhead.get("homed_axes") or "").lower())
        speed_factor = round(_float(gcode_move.get("speed_factor"), 1.0) * 100)
        flow_factor = round(_float(gcode_move.get("extrude_factor"), 1.0) * 100)

        tool_objects = [name for name in sorted(status) if name.startswith("tool ")]
        if tool_objects:
            active_tools = {
                str(status.get("toolchanger", {}).get("tool") or ""),
                str(status.get("toolchanger", {}).get("active_tool") or ""),
            }
            tools = [
                {
                    "id": name.split(" ", 1)[1],
                    "index": index,
                    "label": name.split(" ", 1)[1],
                    "temperature": (tools[index].get("temperature") if index < len(tools) else None),
                    "target_temperature": (tools[index].get("target_temperature") if index < len(tools) else None),
                    "heating": tools[index].get("heating", False) if index < len(tools) else False,
                    "active": name in active_tools or name.split(" ", 1)[1] in active_tools,
                    "nozzle_diameter": None,
                    "filament_type": None,
                }
                for index, name in enumerate(tool_objects)
            ]

        macros = sorted(
            name.split(" ", 1)[1]
            for name in self._objects
            if name.startswith("gcode_macro ") and not name.split(" ", 1)[1].startswith("_")
        )
        build_volume = None
        printer_config = config.get("printer") or {}
        if printer_config:
            x = _float((config.get("stepper_x") or {}).get("position_max"))
            y = _float((config.get("stepper_y") or {}).get("position_max"))
            z = _float((config.get("stepper_z") or {}).get("position_max"))
            if x and y and z:
                build_volume = f"{x:g} × {y:g} × {z:g} mm"
        free_storage = self._disk_usage.get("free")
        remaining_disk_gb = (
            round(free_storage / (1024**3), 1) if isinstance(free_storage, (int, float)) and free_storage >= 0 else None
        )

        self.state.temperatures = temperatures
        self.state.firmware_version = self._printer_info.get("software_version")
        self.state.ipcam = bool(self._webcams)
        self.state.raw_data = {
            "moonraker": {
                "objects": sorted(self._objects),
                "macros": macros,
                "webcams": self._webcams,
                "toolchanger_ready": self._toolchanger_ready(),
            },
            "provider_snapshot": {
                "version": 2,
                "tools": tools,
                "heaters": heaters,
                "fans": fans,
                "sensors": sensors,
                "motion": {
                    "position": position_map,
                    "homed_axes": homed_axes,
                    "speed_factor_percent": speed_factor,
                    "flow_factor_percent": flow_factor,
                    "kinematics": kinematics,
                    "leveling_method": leveling_method,
                },
                "material_systems": [],
                "device_info": {
                    "vendor": "Klipper",
                    "model": self.model,
                    "build_volume": build_volume,
                    "firmware_version": self._printer_info.get("software_version"),
                    "remaining_disk_gb": remaining_disk_gb,
                    "hostname": self._server_info.get("hostname") or self._printer_info.get("hostname"),
                    "moonraker_version": self._server_info.get("moonraker_version"),
                    "klipper_version": self._printer_info.get("software_version"),
                    "kinematics": kinematics,
                    "mcu_count": sum(1 for name in self._objects if name == "mcu" or name.startswith("mcu ")),
                    "toolchanger_ready": self._toolchanger_ready() if tool_objects else None,
                },
            },
        }

        previous = self._previous_state
        if mapped == "RUNNING" and previous not in _ACTIVE_STATES and self._on_print_start:
            self._on_print_start({"filename": filename, "subtask_name": self.state.subtask_name})
        if mapped in {"FINISH", "FAILED"} and previous in _ACTIVE_STATES and self._on_print_complete:
            outcome = "completed" if mapped == "FINISH" else raw_print_state
            self._on_print_complete(
                {
                    "filename": filename,
                    "subtask_name": self.state.subtask_name,
                    "status": outcome,
                }
            )
        self._previous_state = mapped

    def _toolchanger_ready(self) -> bool:
        values = self._status.get("toolchanger", {})
        if not values:
            return False
        status = str(values.get("status") or values.get("state") or "").lower()
        return status in {"ready", "initialized", "idle"} or bool(values.get("initialized"))

    def _emit_state(self) -> None:
        if self._on_state_change:
            try:
                self._on_state_change(self.state)
            except Exception:
                logger.exception("Moonraker status callback failed")

    def check_staleness(self) -> bool:
        if self._last_update and time.monotonic() - self._last_update > MOONRAKER_STALE_AFTER_SECONDS:
            self.state.connected = False
        return self.state.connected

    def _rpc(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": int(time.time() * 1000)}
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=_headers(self.api_key),
                timeout=timeout or MOONRAKER_COMMAND_TIMEOUT_SECONDS,
            ) as client:
                response = client.post("/server/jsonrpc", json=payload)
                response.raise_for_status()
                return _result(response.json())
        except (httpx.HTTPError, ValueError, MoonrakerError) as exc:
            logger.warning("Moonraker command %s failed for %s: %s", method, self.ip_address, exc)
            return _RPC_FAILED

    def request_status_update(self) -> bool:
        result = self._rpc("printer.objects.query", {"objects": self._subscription_objects()})
        if isinstance(result, dict) and isinstance(result.get("status"), dict):
            self._apply_status(result["status"])
            return True
        return False

    def start_print(self, filename: str, *_args: Any, **_kwargs: Any) -> bool:
        path = _safe_gcode_path(filename, allow_root=False)
        return self._rpc("printer.print.start", {"filename": path}) is not _RPC_FAILED

    def stop_print(self) -> bool:
        return self._rpc("printer.print.cancel") is not _RPC_FAILED

    def pause_print(self) -> bool:
        return self._rpc("printer.print.pause") is not _RPC_FAILED

    def resume_print(self) -> bool:
        return self._rpc("printer.print.resume") is not _RPC_FAILED

    def run_gcode(self, script: str, *, source: str = "api") -> bool:
        command = str(script or "").strip()
        if not command or len(command) > 4096 or "\x00" in command:
            return False
        self._record_console("command", command, source=source)
        return self._rpc("printer.gcode.script", {"script": command}) is not _RPC_FAILED

    def run_macro(self, name: str, parameters: str = "") -> bool:
        macro = str(name or "").strip()
        if not _MACRO_NAME.fullmatch(macro) or macro not in self.get_macros():
            return False
        suffix = str(parameters or "").strip()
        if len(suffix) > 2048 or "\n" in suffix or "\r" in suffix:
            return False
        return self.run_gcode(f"{macro} {suffix}".strip(), source="macro")

    def emergency_stop(self) -> bool:
        return self._rpc("printer.emergency_stop") is not _RPC_FAILED

    def set_nozzle_temperature(self, target: int, nozzle: int = 0) -> bool:
        heater = "extruder" if nozzle == 0 else f"extruder{nozzle}"
        if heater not in self._objects:
            return False
        return self.run_gcode(f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET={int(target)}", source="control")

    def set_bed_temperature(self, target: int) -> bool:
        if "heater_bed" not in self._objects:
            return False
        return self.run_gcode(f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={int(target)}", source="control")

    def set_heater_temperature(self, heater: str, target: int) -> bool:
        heater_name = str(heater or "").strip()
        if heater_name not in self._objects or not (
            heater_name.startswith("heater_generic ") or heater_name.startswith("temperature_fan ")
        ):
            return False
        config_name = heater_name.split(" ", 1)[1]
        if heater_name.startswith("temperature_fan "):
            return self.run_gcode(
                f"SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN={config_name} TARGET={int(target)}",
                source="control",
            )
        return self.run_gcode(f"SET_HEATER_TEMPERATURE HEATER={config_name} TARGET={int(target)}", source="control")

    def set_fan_percent(self, fan: str, speed_percent: int) -> bool:
        name = str(fan or "").strip()
        speed = max(0, min(100, int(speed_percent))) / 100
        if name == "fan" and name in self._objects:
            return self.run_gcode(f"M106 S{round(speed * 255)}", source="control")
        if name.startswith("fan_generic ") and name in self._objects:
            return self.run_gcode(
                f"SET_FAN_SPEED FAN={name.split(' ', 1)[1]} SPEED={speed:.3f}",
                source="control",
            )
        return False

    def set_speed_factor(self, percent: int) -> bool:
        return 10 <= percent <= 300 and self.run_gcode(f"M220 S{percent}", source="control")

    def set_flow_factor(self, percent: int) -> bool:
        return 50 <= percent <= 200 and self.run_gcode(f"M221 S{percent}", source="control")

    def home_axes(self, axes: str = "XYZ") -> bool:
        normalized = "".join(axis for axis in str(axes).upper() if axis in "XYZ")
        suffix = " ".join(normalized) if normalized and normalized != "XYZ" else ""
        return self.run_gcode(f"G28 {suffix}".strip(), source="control")

    def jog(self, axis: str, distance: float, speed: int) -> bool:
        normalized = axis.strip().upper()
        if normalized not in {"X", "Y", "Z", "E"} or abs(distance) > 50 or not 1 <= speed <= 30000:
            return False
        return self.run_gcode(
            f"SAVE_GCODE_STATE NAME=BAMBUDDY_JOG\nG91\nG1 {normalized}{distance:.3f} F{speed}\nRESTORE_GCODE_STATE NAME=BAMBUDDY_JOG",
            source="control",
        )

    def level_gantry(self) -> bool:
        if "quad_gantry_level" in self._objects:
            return self.run_gcode("QUAD_GANTRY_LEVEL", source="control")
        if "z_tilt" in self._objects:
            return self.run_gcode("Z_TILT_ADJUST", source="control")
        return False

    def calibrate_bed_mesh(self) -> bool:
        return "bed_mesh" in self._objects and self.run_gcode("BED_MESH_CALIBRATE", source="control")

    def select_tool(self, tool_id: str) -> bool:
        tool = str(tool_id or "").strip().upper()
        if self.state.state != "IDLE":
            return False
        motion = (self.state.raw_data.get("provider_snapshot") or {}).get("motion") or {}
        if not {"x", "y", "z"}.issubset(set(motion.get("homed_axes") or [])):
            return False
        if not self._toolchanger_ready() or f"tool {tool}" not in self._objects:
            return False
        return self.run_gcode(tool, source="control")

    def get_macros(self) -> list[str]:
        moonraker = self.state.raw_data.get("moonraker") or {}
        return list(moonraker.get("macros") or [])

    def _record_console(self, direction: str, text: str, *, source: str = "moonraker") -> None:
        self._console_history.append(
            {
                "timestamp": time.time(),
                "direction": direction,
                "source": source,
                "text": str(text)[:4096],
            }
        )

    def get_console_history(self, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), MOONRAKER_CONSOLE_HISTORY_LIMIT))
        return list(self._console_history)[-bounded:]

    def get_print_history(self, limit: int = 25) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 100))
        result = self._http_get(
            "/server/history/list",
            params={"limit": bounded, "order": "desc"},
        )
        jobs = (result or {}).get("jobs") if isinstance(result, dict) else None
        if not isinstance(jobs, list):
            return []
        return [
            {
                "job_id": job.get("job_id"),
                "filename": job.get("filename"),
                "status": job.get("status"),
                "start_time": job.get("start_time"),
                "end_time": job.get("end_time"),
                "print_duration": job.get("print_duration"),
                "total_duration": job.get("total_duration"),
                "filament_used": (job.get("metadata") or {}).get("filament_total"),
            }
            for job in jobs[:bounded]
            if isinstance(job, dict)
        ]

    def enable_logging(self, enabled: bool = True) -> None:
        self.logging_enabled = enabled

    def get_logs(self) -> list:
        return []

    def clear_logs(self) -> None:
        self._console_history.clear()

    def get_webcams(self) -> list[dict[str, Any]]:
        return list(self._webcams)

    def get_storage_info(self) -> dict[str, int | None]:
        result = self._http_get(
            "/server/files/directory",
            params={"path": "gcodes"},
        )
        disk_usage = (result or {}).get("disk_usage") if isinstance(result, dict) else None
        if not isinstance(disk_usage, dict):
            return {"used_bytes": None, "free_bytes": None}
        self._disk_usage = dict(disk_usage)
        used = disk_usage.get("used")
        free = disk_usage.get("free")
        return {
            "used_bytes": int(used) if isinstance(used, (int, float)) else None,
            "free_bytes": int(free) if isinstance(free, (int, float)) else None,
        }

    def list_files(self, path: str = "") -> list[dict[str, Any]]:
        directory = _safe_gcode_path(path)
        result = self._http_get("/server/files/list", params={"root": "gcodes"})
        files = list(result or []) if isinstance(result, list) else list((result or {}).get("files") or [])
        prefix = f"{directory}/" if directory else ""
        output: list[dict[str, Any]] = []
        directories: set[str] = set()
        for item in files:
            filename = _safe_gcode_path(item.get("path"), allow_root=False)
            if not filename.startswith(prefix):
                continue
            relative = filename[len(prefix) :]
            if "/" in relative:
                directories.add(relative.split("/", 1)[0])
                continue
            output.append(
                {
                    "name": relative,
                    "path": filename,
                    "is_directory": False,
                    "size": item.get("size"),
                    "modified": item.get("modified"),
                }
            )
        output.extend(
            {"name": name, "path": f"{prefix}{name}", "is_directory": True, "size": None, "modified": None}
            for name in sorted(directories)
        )
        return sorted(output, key=lambda item: (not item["is_directory"], item["name"].lower()))

    def file_metadata(self, filename: str) -> dict[str, Any] | None:
        path = _safe_gcode_path(filename, allow_root=False)
        result = self._http_get("/server/files/metadata", params={"filename": path})
        return result if isinstance(result, dict) else None

    def download_file(self, filename: str) -> bytes | None:
        path = _safe_gcode_path(filename, allow_root=False)
        try:
            with httpx.Client(headers=_headers(self.api_key), timeout=60) as client:
                response = client.get(f"{self.base_url}/server/files/gcodes/{quote(path, safe='/')}")
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            logger.warning("Moonraker file download failed: %s", exc)
            return None

    def upload_file(
        self,
        filename: str,
        data: bytes | Path,
        *,
        path: str = "",
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any] | None:
        safe_name = PurePosixPath(_safe_gcode_path(filename, allow_root=False)).name
        directory = _safe_gcode_path(path)
        if isinstance(data, Path):
            total = data.stat().st_size
            source = data.open("rb")
        else:
            total = len(data)
            source = io.BytesIO(data)
        try:
            if on_progress:
                on_progress(0, total)
            progress_reader = _ProgressReader(source, total, on_progress)
            with httpx.Client(headers=_headers(self.api_key), timeout=120) as client:
                response = client.post(
                    f"{self.base_url}/server/files/upload",
                    data={"root": "gcodes", "path": directory},
                    files={"file": (safe_name, progress_reader, "application/octet-stream")},
                )
                response.raise_for_status()
                result = _result(response.json())
            if on_progress:
                on_progress(total, total)
            return result if isinstance(result, dict) else {}
        except (httpx.HTTPError, ValueError, MoonrakerError) as exc:
            logger.warning("Moonraker file upload failed: %s", exc)
            return None
        finally:
            source.close()

    def delete_file(self, filename: str) -> bool:
        path = _safe_gcode_path(filename, allow_root=False)
        try:
            with httpx.Client(headers=_headers(self.api_key), timeout=30) as client:
                response = client.delete(f"{self.base_url}/server/files/gcodes/{quote(path, safe='/')}")
                response.raise_for_status()
                _result(response.json())
            return True
        except (httpx.HTTPError, ValueError, MoonrakerError) as exc:
            logger.warning("Moonraker file delete failed: %s", exc)
            return False

    def _http_get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=_headers(self.api_key),
                timeout=30,
            ) as client:
                response = client.get(path, params=params)
                response.raise_for_status()
                return _result(response.json())
        except (httpx.HTTPError, ValueError, MoonrakerError) as exc:
            logger.warning("Moonraker request %s failed: %s", path, exc)
            return None
