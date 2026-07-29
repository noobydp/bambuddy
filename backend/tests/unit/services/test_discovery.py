"""Unit tests for supported-printer subnet discovery."""

from unittest.mock import AsyncMock, patch

from backend.app.services.discovery import SubnetScanner


class TestSubnetScanner:
    async def test_discovers_verified_moonraker_endpoint(self):
        scanner = SubnetScanner()

        async def check_port(_ip: str, port: int, _timeout: float) -> bool:
            return port == scanner.MOONRAKER_PORT

        probe_result = {
            "success": True,
            "hostname": "test-klipper",
            "serial_number": "KLIPPER-TESTDEVICE",
        }
        with (
            patch.object(scanner, "_check_port", new=AsyncMock(side_effect=check_port)),
            patch(
                "backend.app.services.discovery.probe_moonraker_connection",
                new=AsyncMock(return_value=probe_result),
            ) as probe,
        ):
            await scanner._probe_host("192.0.2.30", 0.1)

        probe.assert_awaited_once_with(
            "192.0.2.30",
            port=scanner.MOONRAKER_PORT,
            timeout=1.0,
        )
        discovered = scanner.discovered_printers
        assert len(discovered) == 1
        assert discovered[0].serial == "KLIPPER-TESTDEVICE"
        assert discovered[0].name == "test-klipper"
        assert discovered[0].model == "Klipper"
        assert discovered[0].provider == "klipper"
        assert discovered[0].connection_port == 7125

    async def test_open_7125_is_not_enough_without_moonraker_response(self):
        scanner = SubnetScanner()

        async def check_port(_ip: str, port: int, _timeout: float) -> bool:
            return port == scanner.MOONRAKER_PORT

        with (
            patch.object(scanner, "_check_port", new=AsyncMock(side_effect=check_port)),
            patch(
                "backend.app.services.discovery.probe_moonraker_connection",
                new=AsyncMock(return_value={"success": False}),
            ),
        ):
            await scanner._probe_host("192.0.2.31", 0.1)

        assert scanner.discovered_printers == []

    async def test_discovers_flashforge_by_local_api_port(self):
        scanner = SubnetScanner()

        async def check_port(_ip: str, port: int, _timeout: float) -> bool:
            return port == scanner.FLASHFORGE_API_PORT

        with patch.object(scanner, "_check_port", new=AsyncMock(side_effect=check_port)):
            await scanner._probe_host("192.168.1.42", 0.1)

        discovered = scanner.discovered_printers
        assert len(discovered) == 1
        assert discovered[0].ip_address == "192.168.1.42"
        assert discovered[0].serial == "unknown-192-168-1-42"
        assert discovered[0].name == "FlashForge at 192.168.1.42"
        assert discovered[0].model == "FlashForge Creator 5 Pro"
        assert discovered[0].provider == "flashforge"
        assert discovered[0].connection_port == 8898

    async def test_prefers_flashforge_protocol_over_moonraker_compatibility(self):
        scanner = SubnetScanner()

        async def check_port(_ip: str, port: int, _timeout: float) -> bool:
            return port in {scanner.FLASHFORGE_API_PORT, scanner.MOONRAKER_PORT}

        with (
            patch.object(scanner, "_check_port", new=AsyncMock(side_effect=check_port)),
            patch(
                "backend.app.services.discovery.probe_moonraker_connection",
                new=AsyncMock(),
            ) as moonraker_probe,
        ):
            await scanner._probe_host("192.0.2.44", 0.1)

        moonraker_probe.assert_not_awaited()
        discovered = scanner.discovered_printers
        assert len(discovered) == 1
        assert discovered[0].provider == "flashforge"
        assert discovered[0].connection_port == 8898

    async def test_ignores_unrelated_host(self):
        scanner = SubnetScanner()

        with patch.object(scanner, "_check_port", new=AsyncMock(return_value=False)):
            await scanner._probe_host("192.168.1.99", 0.1)

        assert scanner.discovered_printers == []
