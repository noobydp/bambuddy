"""Unit tests for supported-printer subnet discovery."""

from unittest.mock import AsyncMock, patch

from backend.app.services.discovery import SubnetScanner


class TestSubnetScanner:
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

    async def test_ignores_unrelated_host(self):
        scanner = SubnetScanner()

        with patch.object(scanner, "_check_port", new=AsyncMock(return_value=False)):
            await scanner._probe_host("192.168.1.99", 0.1)

        assert scanner.discovered_printers == []
