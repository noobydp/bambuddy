from __future__ import annotations

import pytest

import backend.app.services.orca_profiles as orca_profiles
from backend.app.services.orca_profiles import resolve_bundle_preset


@pytest.mark.asyncio
async def test_resolves_complete_custom_printer_chain_before_stock_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_stock_profile(*_args, **_kwargs):
        return None

    monkeypatch.setattr(orca_profiles, "fetch_and_cache_base_profile", no_stock_profile)
    bundle = {
        "MyToolChanger": {
            "name": "MyToolChanger",
            "printer_model": "Custom Toolchanger",
            "bed_shape": ["0x0", "170x0", "170x170", "0x170"],
            "machine_max_speed_x": ["500"],
        },
        "TinyT": {
            "name": "TinyT",
            "inherits": "MyToolChanger",
            "bed_shape": ["0x0", "165x0", "165x165", "0x165"],
            "extruder_colour": ["#FF0000", "#0000FF"],
        },
    }

    resolved = await resolve_bundle_preset(bundle["TinyT"], "printer", bundle, None)

    assert resolved["name"] == "TinyT"
    assert resolved["machine_max_speed_x"] == ["500"]
    assert resolved["bed_shape"][1] == "165x0"
    assert len(resolved["extruder_colour"]) == 2


@pytest.mark.asyncio
async def test_rejects_incomplete_custom_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_stock_profile(*_args, **_kwargs):
        return None

    monkeypatch.setattr(orca_profiles, "fetch_and_cache_base_profile", no_stock_profile)
    preset = {"name": "Trident", "inherits": "MyToolChanger", "printer_model": "Trident"}

    with pytest.raises(ValueError, match="Incomplete inheritance chain"):
        await resolve_bundle_preset(preset, "printer", {"Trident": preset}, None)


@pytest.mark.asyncio
async def test_rejects_cyclic_custom_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_stock_profile(*_args, **_kwargs):
        return None

    monkeypatch.setattr(orca_profiles, "fetch_and_cache_base_profile", no_stock_profile)
    bundle = {
        "TinyT": {"name": "TinyT", "inherits": "MyToolChanger"},
        "MyToolChanger": {"name": "MyToolChanger", "inherits": "TinyT"},
    }

    with pytest.raises(ValueError, match="Cyclic inheritance chain"):
        await resolve_bundle_preset(bundle["TinyT"], "printer", bundle, None)
