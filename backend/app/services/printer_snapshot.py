"""Build the provider-neutral printer component snapshot."""

from __future__ import annotations

from typing import Any


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _legacy_tools(state) -> list[dict]:
    temperatures = getattr(state, "temperatures", None) or {}
    tools: list[dict] = []
    keys = ["nozzle"]
    if "nozzle_2" in temperatures:
        keys.append("nozzle_2")
    for index, key in enumerate(keys):
        temperature = _float_or_none(temperatures.get(key))
        target = _float_or_none(temperatures.get(f"{key}_target"))
        nozzle_info = (getattr(state, "nozzles", None) or [])
        info = nozzle_info[index] if index < len(nozzle_info) else None
        tools.append(
            {
                "id": key,
                "index": index,
                "label": "Nozzle" if index == 0 and len(keys) == 1 else f"Nozzle {index + 1}",
                "temperature": temperature,
                "target_temperature": target,
                "heating": bool(temperatures.get(f"{key}_heating")),
                "active": index == getattr(state, "active_extruder", 0),
                "nozzle_diameter": getattr(info, "nozzle_diameter", None) or None,
                "filament_type": None,
            }
        )
    return tools


def _legacy_heaters(state, *, chamber_controllable: bool) -> list[dict]:
    temperatures = getattr(state, "temperatures", None) or {}
    heaters = []
    for key, label, controllable in (
        ("bed", "Bed", True),
        ("chamber", "Chamber", chamber_controllable),
    ):
        if key not in temperatures:
            continue
        heaters.append(
            {
                "id": key,
                "label": label,
                "temperature": _float_or_none(temperatures.get(key)),
                "target_temperature": _float_or_none(temperatures.get(f"{key}_target")),
                "heating": bool(temperatures.get(f"{key}_heating")),
                "controllable": controllable,
            }
        )
    return heaters


def _legacy_fans(state) -> list[dict]:
    fans = []
    for key, label, attr in (
        ("part", "Part cooling", "cooling_fan_speed"),
        ("aux", "Auxiliary", "big_fan1_speed"),
        ("chamber", "Chamber", "big_fan2_speed"),
        ("heatbreak", "Heatbreak", "heatbreak_fan_speed"),
    ):
        value = _int_or_none(getattr(state, attr, None))
        if value is None:
            continue
        fans.append(
            {
                "id": key,
                "label": label,
                "speed_percent": value,
                "active": value > 0,
                "controllable": True,
            }
        )
    return fans


def _legacy_material_systems(state) -> list[dict]:
    raw_data = getattr(state, "raw_data", None) or {}
    systems = []
    tray_now = getattr(state, "tray_now", 255)
    for unit in raw_data.get("ams") or []:
        if not isinstance(unit, dict):
            continue
        unit_id = _int_or_none(unit.get("id")) or 0
        module_type = str(unit.get("module_type") or "ams")
        slots = []
        for index, tray in enumerate(unit.get("tray") or []):
            if not isinstance(tray, dict):
                continue
            tray_id = _int_or_none(tray.get("id"))
            if tray_id is None:
                tray_id = index
            global_id = unit_id * 4 + tray_id
            exists = tray.get("exists")
            if exists is None:
                exists = bool(tray.get("tray_type")) or tray.get("state") in (10, 11)
            remaining = _int_or_none(tray.get("remain"))
            if remaining is not None and remaining < 0:
                remaining = None
            slots.append(
                {
                    "id": tray_id,
                    "label": f"Slot {index + 1}",
                    "occupied": exists,
                    "active": tray_now == global_id,
                    "material_type": tray.get("tray_type") or None,
                    "color": tray.get("tray_color") or None,
                    "remaining_percent": remaining,
                }
            )
        systems.append(
            {
                "id": f"{module_type}_{unit_id}",
                "name": "AMS",
                "kind": module_type,
                "slots": slots,
            }
        )
    return systems


def build_printer_snapshot(
    state,
    *,
    provider: str,
    model: str | None,
    can_set_temperature: bool,
    supports_chamber_heater: bool,
) -> dict:
    """Return versioned component data, preferring the provider's native map."""
    raw_data = getattr(state, "raw_data", None) or {}
    native = raw_data.get("provider_snapshot")
    if isinstance(native, dict):
        return {
            "snapshot_version": int(native.get("version") or 1),
            "tools": native.get("tools") or [],
            "heaters": native.get("heaters") or [],
            "fans": native.get("fans") or [],
            "material_systems": native.get("material_systems") or [],
            "device_info": native.get("device_info"),
        }

    return {
        "snapshot_version": 1,
        "tools": _legacy_tools(state),
        "heaters": _legacy_heaters(
            state,
            chamber_controllable=can_set_temperature or supports_chamber_heater,
        ),
        "fans": _legacy_fans(state),
        "material_systems": _legacy_material_systems(state),
        "device_info": {
            "vendor": "Bambu Lab" if provider == "bambu" else provider.title(),
            "model": model,
            "build_volume": None,
            "firmware_version": getattr(state, "firmware_version", None),
            "cumulative_print_time": None,
            "cumulative_filament": None,
            "tvoc": None,
            "lidar": None,
            "auto_shutdown": None,
            "auto_shutdown_minutes": None,
            "remaining_disk_gb": None,
        },
    }
