from types import SimpleNamespace

import pytest

from backend.app.services.printer_providers import (
    PROVIDER_BAMBU,
    PROVIDER_FLASHFORGE,
    PROVIDER_KLIPPER,
    infer_printer_provider,
    normalize_printer_provider,
    provider_descriptors,
    provider_for_printer,
)


@pytest.mark.parametrize(
    "model",
    [
        "Creator 5 Pro",
        "FlashForge Creator 5 Pro",
        "creator5pro",
    ],
)
def test_infer_printer_provider_recognizes_creator_5_pro(model):
    assert infer_printer_provider(model) == PROVIDER_FLASHFORGE


def test_provider_for_printer_keeps_legacy_creator_rows_on_flashforge():
    printer = SimpleNamespace(provider=PROVIDER_BAMBU, model="FlashForge Creator 5 Pro")

    assert provider_for_printer(printer) == PROVIDER_FLASHFORGE


def test_normalize_printer_provider_rejects_unknown_providers():
    with pytest.raises(ValueError, match="Unsupported printer provider"):
        normalize_printer_provider("unknown-provider")


def test_provider_descriptors_expose_setup_metadata():
    descriptors = {descriptor["key"]: descriptor for descriptor in provider_descriptors()}

    assert descriptors[PROVIDER_BAMBU]["credential_label"] == "Access Code"
    assert descriptors[PROVIDER_FLASHFORGE]["credential_label"] == "Device Key"
    assert descriptors[PROVIDER_FLASHFORGE]["models"] == ["FlashForge Creator 5 Pro"]
    assert descriptors[PROVIDER_KLIPPER]["credential_required"] is False
    assert descriptors[PROVIDER_KLIPPER]["serial_required"] is False
    assert descriptors[PROVIDER_KLIPPER]["default_port"] == 7125
