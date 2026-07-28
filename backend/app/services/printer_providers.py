"""Printer provider registry and provider-neutral helpers.

New code uses this module to choose a protocol explicitly instead of
repeatedly inferring it from a display-model string. Model inference remains
as a compatibility path for rows created before ``printers.provider`` existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

PROVIDER_BAMBU: Final = "bambu"
PROVIDER_FLASHFORGE: Final = "flashforge"
SUPPORTED_PROVIDERS: Final = frozenset({PROVIDER_BAMBU, PROVIDER_FLASHFORGE})


@dataclass(frozen=True)
class PrinterProviderDescriptor:
    key: str
    name: str
    credential_label: str
    serial_label: str
    models: tuple[str, ...]
    discovery_ports: tuple[int, ...]


PROVIDER_DESCRIPTORS: Final = (
    PrinterProviderDescriptor(
        key=PROVIDER_BAMBU,
        name="Bambu Lab",
        credential_label="Access Code",
        serial_label="Serial Number",
        models=(
            "X1",
            "X1C",
            "X1E",
            "X2D",
            "P1P",
            "P1S",
            "P2S",
            "A1",
            "A1 Mini",
            "A2L",
            "H2C",
            "H2D",
            "H2D Pro",
            "H2S",
        ),
        discovery_ports=(8883, 990),
    ),
    PrinterProviderDescriptor(
        key=PROVIDER_FLASHFORGE,
        name="FlashForge",
        credential_label="Device Key",
        serial_label="Serial Number",
        models=("FlashForge Creator 5 Pro",),
        discovery_ports=(8898, 8080),
    ),
)


def infer_printer_provider(model: str | None) -> str:
    """Infer a provider for a legacy printer row."""
    if not isinstance(model, str):
        return PROVIDER_BAMBU
    normalized = "".join(str(model or "").strip().upper().replace("-", " ").split())
    if "CREATOR5PRO" in normalized:
        return PROVIDER_FLASHFORGE
    return PROVIDER_BAMBU


def normalize_printer_provider(provider: str | None, model: str | None = None) -> str:
    """Return a supported provider key, falling back to model inference."""
    inferred = infer_printer_provider(model)
    # Creator 5 Pro rows existed before the provider column and some code paths
    # still construct ORM objects directly. Never let the generic DB default
    # silently route that confirmed model into the Bambu MQTT client.
    if inferred == PROVIDER_FLASHFORGE:
        return PROVIDER_FLASHFORGE
    if not isinstance(provider, str) or not provider.strip():
        return inferred
    normalized = str(provider).strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported printer provider: {provider}")
    return normalized


def provider_for_printer(printer) -> str:
    """Resolve the provider for an ORM row or printer-like test object."""
    return normalize_printer_provider(
        getattr(printer, "provider", None),
        getattr(printer, "model", None),
    )


def is_flashforge_provider(provider: str | None, model: str | None = None) -> bool:
    return normalize_printer_provider(provider, model) == PROVIDER_FLASHFORGE


def provider_descriptors() -> list[dict]:
    """Return setup metadata safe to expose through the public API."""
    return [
        {
            "key": descriptor.key,
            "name": descriptor.name,
            "credential_label": descriptor.credential_label,
            "serial_label": descriptor.serial_label,
            "models": list(descriptor.models),
            "discovery_ports": list(descriptor.discovery_ports),
        }
        for descriptor in PROVIDER_DESCRIPTORS
    ]
