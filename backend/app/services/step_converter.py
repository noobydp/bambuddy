"""STEP/STP tessellation for headless slicer sidecars.

OrcaSlicer and Bambu Studio can import STEP in their desktop interfaces, but
their headless CLI entrypoints only accept mesh formats.  The slicer API
sidecars nevertheless advertise STEP uploads, so passing a CAD solid through
unchanged reaches the CLI and fails with "Unknown file format".

This module keeps the original CAD file in Bambuddy and creates an in-memory
binary STL only for the sidecar request.  OpenCascade is used directly so the
same conversion works for both slicers and on every Bambuddy deployment.
"""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger(__name__)

# Fine enough for normal FDM slicing without producing needlessly huge meshes.
# OpenCascade interprets the linear value in the STEP model's millimetre units.
LINEAR_DEFLECTION_MM = 0.05
ANGULAR_DEFLECTION_RADIANS = 0.2


class StepConversionError(ValueError):
    """The supplied STEP payload could not be converted into a usable mesh."""


def is_step_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in {".step", ".stp"}


def convert_step_to_stl(model_bytes: bytes, model_filename: str) -> tuple[bytes, str]:
    """Tessellate STEP bytes and return ``(binary_stl_bytes, stl_filename)``.

    The conversion uses fixed filenames inside a private temporary directory;
    the user-controlled filename is used only to derive the multipart display
    name returned to the caller.
    """
    if not is_step_filename(model_filename):
        raise StepConversionError("Source file is not STEP or STP")
    if not model_bytes:
        raise StepConversionError("STEP file is empty")

    try:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader
        from OCP.StlAPI import StlAPI_Writer
    except ImportError as exc:
        raise StepConversionError(
            "STEP conversion support is unavailable because OpenCascade is not installed"
        ) from exc

    stl_filename = f"{Path(model_filename).stem}.stl"

    try:
        with TemporaryDirectory(prefix="bambuddy-step-") as temp_dir:
            temp_root = Path(temp_dir)
            step_path = temp_root / "source.step"
            stl_path = temp_root / "converted.stl"
            step_path.write_bytes(model_bytes)

            reader = STEPControl_Reader()
            if reader.ReadFile(str(step_path)) != IFSelect_RetDone:
                raise StepConversionError("OpenCascade could not read the STEP structure")
            if reader.TransferRoots() <= 0:
                raise StepConversionError("STEP file contains no transferable solids")

            shape = reader.OneShape()
            if shape.IsNull():
                raise StepConversionError("STEP file contains no usable geometry")

            mesh = BRepMesh_IncrementalMesh(
                shape,
                LINEAR_DEFLECTION_MM,
                False,
                ANGULAR_DEFLECTION_RADIANS,
                True,
            )
            mesh.Perform()
            if not mesh.IsDone():
                raise StepConversionError("OpenCascade could not tessellate the STEP geometry")

            writer = StlAPI_Writer()
            writer.ASCIIMode = False
            if not writer.Write(shape, str(stl_path)):
                raise StepConversionError("OpenCascade could not write the converted STL")

            stl_bytes = stl_path.read_bytes()
    except StepConversionError:
        raise
    except Exception as exc:
        raise StepConversionError(f"OpenCascade conversion failed: {exc}") from exc

    # Binary STL: 80-byte header + uint32 triangle count + 50 bytes/triangle.
    if len(stl_bytes) < 84:
        raise StepConversionError("Converted STL is empty")
    triangle_count = int.from_bytes(stl_bytes[80:84], "little")
    expected_size = 84 + triangle_count * 50
    if triangle_count <= 0 or len(stl_bytes) != expected_size:
        raise StepConversionError("Converted STL does not contain a valid triangle mesh")

    logger.info(
        "Converted STEP model %s to %s (%d triangles, %d bytes)",
        Path(model_filename).name,
        stl_filename,
        triangle_count,
        len(stl_bytes),
    )
    return stl_bytes, stl_filename
