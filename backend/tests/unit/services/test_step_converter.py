"""Unit tests for STEP-to-STL conversion used by slicer sidecars."""

from pathlib import Path

import pytest

from backend.app.services.step_converter import (
    StepConversionError,
    convert_step_to_stl,
    is_step_filename,
)


def _make_cube_step(tmp_path: Path) -> bytes:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    path = tmp_path / "cube.step"
    writer = STEPControl_Writer()
    assert writer.Transfer(BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), STEPControl_AsIs) == IFSelect_RetDone
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path.read_bytes()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("part.step", True),
        ("part.STP", True),
        ("part.stl", False),
        ("part.step.txt", False),
    ],
)
def test_is_step_filename(filename: str, expected: bool):
    assert is_step_filename(filename) is expected


def test_convert_step_to_binary_stl(tmp_path: Path):
    stl_bytes, stl_filename = convert_step_to_stl(_make_cube_step(tmp_path), "nested/Cube.step")

    assert stl_filename == "Cube.stl"
    assert len(stl_bytes) >= 84
    triangle_count = int.from_bytes(stl_bytes[80:84], "little")
    assert triangle_count == 12
    assert len(stl_bytes) == 84 + triangle_count * 50


@pytest.mark.parametrize(
    ("model_bytes", "filename", "message"),
    [
        (b"", "empty.step", "empty"),
        (b"not a STEP file", "broken.step", "could not read"),
        (b"data", "part.stl", "not STEP"),
    ],
)
def test_convert_step_rejects_invalid_input(model_bytes: bytes, filename: str, message: str):
    with pytest.raises(StepConversionError, match=message):
        convert_step_to_stl(model_bytes, filename)
