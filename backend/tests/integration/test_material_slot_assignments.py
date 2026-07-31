from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool


def _toolhead_status() -> MagicMock:
    state = MagicMock()
    state.raw_data = {
        "provider_snapshot": {
            "material_systems": [
                {
                    "id": "toolheads",
                    "name": "Toolheads",
                    "kind": "toolheads",
                    "slots": [
                        {"id": "extruder", "label": "T0"},
                        {"id": "extruder1", "label": "T1"},
                    ],
                }
            ]
        }
    }
    return state


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_toolhead_assignment_is_manual_and_replaceable(
    async_client: AsyncClient,
    db_session: AsyncSession,
    printer_factory,
) -> None:
    printer = await printer_factory(
        name="TinyT",
        provider="klipper",
        model="Klipper",
        access_code="",
    )
    first = Spool(
        material="PLA",
        subtype="Basic",
        color_name="Red",
        rgba="FF0000FF",
        label_weight=1000,
        weight_used=125,
    )
    second = Spool(
        material="PETG",
        subtype="HF",
        color_name="Blue",
        rgba="0000FFFF",
        label_weight=1000,
        weight_used=250,
    )
    db_session.add_all([first, second])
    await db_session.commit()
    await db_session.refresh(first)
    await db_session.refresh(second)

    with patch("backend.app.services.printer_manager.printer_manager") as manager:
        manager.get_status.return_value = _toolhead_status()

        response = await async_client.post(
            "/api/v1/inventory/material-slot-assignments",
            json={
                "printer_id": printer.id,
                "material_system_id": "toolheads",
                "slot_id": "extruder",
                "source": "internal",
                "spool_id": first.id,
            },
        )
        assert response.status_code == 200
        assert response.json()["spool"]["material"] == "PLA"

        replacement = await async_client.post(
            "/api/v1/inventory/material-slot-assignments",
            json={
                "printer_id": printer.id,
                "material_system_id": "toolheads",
                "slot_id": "extruder",
                "source": "internal",
                "spool_id": second.id,
            },
        )
        assert replacement.status_code == 200
        assert replacement.json()["spool_id"] == second.id

        listed = await async_client.get(f"/api/v1/inventory/material-slot-assignments?printer_id={printer.id}")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["spool"]["material"] == "PETG"

        removed = await async_client.delete(
            f"/api/v1/inventory/material-slot-assignments/{printer.id}/toolheads/extruder"
        )
        assert removed.status_code == 200
        manager.get_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_assignment_rejects_a_slot_not_exposed_by_provider(
    async_client: AsyncClient,
    db_session: AsyncSession,
    printer_factory,
) -> None:
    printer = await printer_factory(provider="klipper", model="Klipper", access_code="")
    spool = Spool(material="PLA", label_weight=1000, weight_used=0)
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(spool)

    with patch("backend.app.services.printer_manager.printer_manager") as manager:
        manager.get_status.return_value = _toolhead_status()
        response = await async_client.post(
            "/api/v1/inventory/material-slot-assignments",
            json={
                "printer_id": printer.id,
                "material_system_id": "toolheads",
                "slot_id": "extruder99",
                "source": "internal",
                "spool_id": spool.id,
            },
        )

    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_spoolman_toolhead_assignment_stores_only_metadata(
    async_client: AsyncClient,
    printer_factory,
) -> None:
    printer = await printer_factory(provider="klipper", model="Klipper", access_code="")
    spoolman = MagicMock()
    spoolman.get_spool = AsyncMock(return_value={"id": 456})

    with (
        patch("backend.app.services.printer_manager.printer_manager") as manager,
        patch(
            "backend.app.api.routes.inventory.get_spoolman_client",
            AsyncMock(return_value=spoolman),
        ),
    ):
        manager.get_status.return_value = _toolhead_status()
        response = await async_client.post(
            "/api/v1/inventory/material-slot-assignments",
            json={
                "printer_id": printer.id,
                "material_system_id": "toolheads",
                "slot_id": "extruder1",
                "source": "spoolman",
                "spoolman_spool_id": 456,
            },
        )

    assert response.status_code == 200
    assert response.json()["source"] == "spoolman"
    assert response.json()["spoolman_spool_id"] == 456
    assert response.json()["spool"] is None
    manager.get_client.assert_not_called()
