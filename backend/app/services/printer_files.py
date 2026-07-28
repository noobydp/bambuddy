"""Provider-aware printer file operations.

This is the protocol boundary for the file manager and queue.  Callers pass a
printer row and never need to know whether the transport is Bambu FTPS,
FlashForge HTTP, or Moonraker's file manager.
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import Any

from backend.app.models.printer import Printer
from backend.app.services.bambu_ftp import (
    DeleteResult,
    delete_file_async,
    download_file_bytes_async,
    get_storage_info_async,
    list_files_async,
    upload_file_async,
)
from backend.app.services.moonraker import DEFAULT_MOONRAKER_PORT, MoonrakerClient
from backend.app.services.printer_manager import printer_manager
from backend.app.services.printer_providers import PROVIDER_KLIPPER, provider_for_printer


def _moonraker_client(printer: Printer) -> MoonrakerClient:
    active = printer_manager.get_client(printer.id)
    if isinstance(active, MoonrakerClient):
        return active
    return MoonrakerClient(
        ip_address=printer.ip_address,
        port=printer.connection_port or DEFAULT_MOONRAKER_PORT,
        api_key=printer.access_code,
        model=printer.model,
    )


async def list_printer_files(printer: Printer, path: str = "/") -> list[dict[str, Any]]:
    if provider_for_printer(printer) == PROVIDER_KLIPPER:
        return await asyncio.to_thread(_moonraker_client(printer).list_files, path)
    return await list_files_async(
        printer.ip_address,
        printer.access_code,
        path,
        printer_model=printer.model,
        serial_number=printer.serial_number,
    )


async def download_printer_file(printer: Printer, path: str) -> bytes | None:
    if provider_for_printer(printer) == PROVIDER_KLIPPER:
        return await asyncio.to_thread(_moonraker_client(printer).download_file, path)
    return await download_file_bytes_async(
        printer.ip_address,
        printer.access_code,
        path,
        printer_model=printer.model,
        serial_number=printer.serial_number,
    )


async def delete_printer_file(printer: Printer, path: str) -> DeleteResult:
    if provider_for_printer(printer) == PROVIDER_KLIPPER:
        client = _moonraker_client(printer)
        metadata = await asyncio.to_thread(client.file_metadata, path)
        if metadata is None:
            return DeleteResult.NOT_FOUND
        deleted = await asyncio.to_thread(client.delete_file, path)
        return DeleteResult.DELETED if deleted else DeleteResult.FAILED
    return await delete_file_async(
        printer.ip_address,
        printer.access_code,
        path,
        printer_model=printer.model,
        serial_number=printer.serial_number,
    )


async def upload_printer_file(
    printer: Printer,
    local_path: Path,
    remote_path: str,
    *,
    path: str = "",
    on_progress=None,
) -> bool:
    if provider_for_printer(printer) == PROVIDER_KLIPPER:
        result = await asyncio.to_thread(
            _moonraker_client(printer).upload_file,
            PurePosixPath(remote_path).name,
            local_path,
            path=path,
            on_progress=on_progress,
        )
        return result is not None
    return bool(
        await upload_file_async(
            printer.ip_address,
            printer.access_code,
            local_path,
            remote_path,
            printer_model=printer.model,
            serial_number=printer.serial_number,
            progress_callback=on_progress,
        )
    )


async def get_printer_storage(printer: Printer) -> dict[str, int | None]:
    if provider_for_printer(printer) == PROVIDER_KLIPPER:
        client = _moonraker_client(printer)
        result = await asyncio.to_thread(client._http_get, "/server/files/roots")
        roots = result if isinstance(result, list) else (result or {}).get("roots") or []
        root = next((item for item in roots if item.get("name") == "gcodes"), None)
        if not root:
            return {"used_bytes": None, "free_bytes": None}
        total = root.get("total_space")
        free = root.get("free_space")
        return {
            "used_bytes": total - free if isinstance(total, int) and isinstance(free, int) else None,
            "free_bytes": free if isinstance(free, int) else None,
        }
    return await get_storage_info_async(
        printer.ip_address,
        printer.access_code,
        printer_model=printer.model,
        serial_number=printer.serial_number,
    ) or {"used_bytes": None, "free_bytes": None}


async def get_printer_thumbnail(printer: Printer, path: str) -> tuple[bytes, str] | None:
    """Return the best Moonraker-managed thumbnail for a G-code file."""
    if provider_for_printer(printer) != PROVIDER_KLIPPER:
        return None
    client = _moonraker_client(printer)
    metadata = await asyncio.to_thread(client.file_metadata, path)
    thumbnails = list((metadata or {}).get("thumbnails") or [])
    if not thumbnails:
        return None
    best = max(thumbnails, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
    relative = str(best.get("relative_path") or "").replace("\\", "/").strip("/")
    if not relative:
        return None
    parent = PurePosixPath(str(path).replace("\\", "/").strip("/")).parent
    thumbnail_path = (parent / relative).as_posix() if str(parent) != "." else relative
    data = await asyncio.to_thread(client.download_file, thumbnail_path)
    if not data:
        return None
    media_type = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    return data, media_type
