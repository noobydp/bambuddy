"""Provider-boundary tests for printer-side K-profile routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

PROFILE = {
    "slot_id": 0,
    "extruder_id": 0,
    "nozzle_id": "HS00-0.4",
    "nozzle_diameter": "0.4",
    "filament_id": "GFA00",
    "name": "PLA Basic",
    "k_value": "0.020000",
}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("provider", ["klipper", "flashforge"])
async def test_get_kprofiles_rejects_non_bambu_provider(
    async_client: AsyncClient,
    printer_factory,
    provider: str,
):
    printer = await printer_factory(provider=provider, model="Klipper" if provider == "klipper" else "Creator 5 Pro")

    response = await async_client.get(f"/api/v1/printers/{printer.id}/kprofiles/")

    assert response.status_code == 501
    assert response.json()["detail"] == "Printer-side K-profiles are only supported for Bambu Lab printers"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "", PROFILE),
        ("POST", "batch", [PROFILE]),
        ("DELETE", "", PROFILE),
    ],
)
async def test_kprofile_mutations_reject_klipper_provider(
    async_client: AsyncClient,
    printer_factory,
    method: str,
    path: str,
    payload,
):
    printer = await printer_factory(provider="klipper", model="Klipper")
    suffix = f"/{path}" if path else "/"

    response = await async_client.request(
        method,
        f"/api/v1/printers/{printer.id}/kprofiles{suffix}",
        json=payload,
    )

    assert response.status_code == 501
    assert response.json()["detail"] == "Printer-side K-profiles are only supported for Bambu Lab printers"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_kprofiles_still_uses_connected_bambu_client(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(provider="bambu", model="X1C")
    client = MagicMock()
    client.state = SimpleNamespace(connected=True)
    client.get_kprofiles = AsyncMock(return_value=[])

    with patch("backend.app.api.routes.kprofiles.printer_manager.get_client", return_value=client):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/kprofiles/?nozzle_diameter=0.6")

    assert response.status_code == 200
    assert response.json() == {"profiles": [], "nozzle_diameter": "0.6"}
    client.get_kprofiles.assert_awaited_once_with(nozzle_diameter="0.6")
