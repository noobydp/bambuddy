"""Klipper queue dispatch contracts.

These tests keep Moonraker dispatch on the provider file boundary and prove
that selected-plate extraction, exact-profile validation, temporary-file
cleanup, and the existing cancel race guard all remain intact.
"""

import json
import zipfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata
import backend.app.services.print_scheduler as scheduler_module
from backend.app.core.database import Base
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import PrintScheduler


@pytest.fixture
async def klipper_queue_factory(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    counter = 0

    async def make_case(*, preset="TinyT", embedded_preset="TinyT", raw=False):
        nonlocal counter
        counter += 1
        base_dir = tmp_path / f"case-{counter}"
        archive_dir = base_dir / "archives"
        archive_dir.mkdir(parents=True)
        filename = f"moonraker-{counter}.gcode" if raw else f"moonraker-{counter}.gcode.3mf"
        source = archive_dir / filename
        if raw:
            source.write_text("; raw Klipper fixture\nM115\n", encoding="utf-8")
        else:
            with zipfile.ZipFile(source, "w") as bundle:
                bundle.writestr(
                    "Metadata/project_settings.config",
                    json.dumps({"printer_settings_id": embedded_preset}),
                )
                bundle.writestr("Metadata/plate_1.gcode", "; plate one\nM115\n")
                bundle.writestr("Metadata/plate_2.gcode", "; selected plate two\nM115\n")

        async with session_maker() as db:
            printer = Printer(
                name=f"Klipper {counter}",
                serial_number=f"KLIPPER-TEST-{counter}",
                ip_address="192.0.2.30",
                access_code="",
                model="Klipper",
                provider="klipper",
                connection_port=7125,
                slicer_preset_source="orca_cloud",
                slicer_preset_id=preset,
            )
            db.add(printer)
            await db.flush()
            archive = PrintArchive(
                printer_id=printer.id,
                filename=filename,
                file_path=str(Path("archives") / filename),
                file_size=source.stat().st_size,
                content_hash=None,
                thumbnail_path=None,
                timelapse_path=None,
                print_time_seconds=60,
                status="completed",
            )
            db.add(archive)
            await db.flush()
            item = PrintQueueItem(
                printer_id=printer.id,
                archive_id=archive.id,
                status="pending",
                plate_id=2 if not raw else None,
                klipper_compatibility_acknowledged=raw,
            )
            db.add(item)
            await db.commit()
            return SimpleNamespace(
                session_maker=session_maker,
                base_dir=base_dir,
                source=source,
                item_id=item.id,
                printer_id=printer.id,
                upload=AsyncMock(return_value=True),
                delete=AsyncMock(return_value=True),
                start=MagicMock(return_value=True),
            )

    try:
        yield make_case
    finally:
        await engine.dispose()


async def _dispatch(ctx, *, upload_side_effect=None):
    scheduler = PrintScheduler()
    if upload_side_effect is not None:
        ctx.upload.side_effect = upload_side_effect

    preheat = AsyncMock()
    bambu_upload = AsyncMock()
    patches = [
        patch.object(scheduler_module.settings, "base_dir", ctx.base_dir),
        patch("backend.app.services.print_scheduler.printer_manager.is_connected", MagicMock(return_value=True)),
        patch("backend.app.services.print_scheduler.printer_manager.get_status", MagicMock(return_value=None)),
        patch("backend.app.services.print_scheduler.printer_manager.start_print", ctx.start),
        patch("backend.app.services.print_scheduler.printer_manager.set_awaiting_plate_clear", MagicMock()),
        patch(
            "backend.app.services.print_scheduler.get_ftp_retry_settings",
            AsyncMock(return_value=(False, 0, 0, 1.0)),
        ),
        patch("backend.app.services.print_scheduler.provider_delete_printer_file", ctx.delete),
        patch("backend.app.services.print_scheduler.provider_upload_printer_file", ctx.upload),
        patch("backend.app.services.print_scheduler.upload_file_async", bambu_upload),
        patch("backend.app.services.print_scheduler.cache_3mf_download", MagicMock()),
        patch("backend.app.services.print_scheduler.spawn_background_task", MagicMock()),
        patch("backend.app.main.register_expected_print", MagicMock()),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_started",
            AsyncMock(),
        ),
        patch(
            "backend.app.services.notification_service.notification_service.on_queue_job_failed",
            AsyncMock(),
        ),
        patch("backend.app.services.mqtt_relay.mqtt_relay.on_queue_job_started", AsyncMock()),
        patch.object(scheduler, "_propagate_owner_to_printer_manager", AsyncMock()),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_preheat_and_soak", preheat),
    ]
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        async with ctx.session_maker() as db:
            item = await db.get(PrintQueueItem, ctx.item_id)
            await scheduler._start_print(db, item)

    preheat.assert_not_awaited()
    bambu_upload.assert_not_awaited()


async def _item(ctx):
    async with ctx.session_maker() as db:
        return await db.get(PrintQueueItem, ctx.item_id)


@pytest.mark.asyncio
async def test_selected_plate_is_extracted_uploaded_and_cleaned(klipper_queue_factory):
    ctx = await klipper_queue_factory()
    uploaded = {}

    async def inspect_upload(printer, local_path, remote_path, *, on_progress):
        uploaded["bytes"] = local_path.read_bytes()
        uploaded["path"] = local_path
        uploaded["remote_path"] = remote_path
        on_progress(0, local_path.stat().st_size)
        on_progress(local_path.stat().st_size, local_path.stat().st_size)
        return True

    await _dispatch(ctx, upload_side_effect=inspect_upload)

    item = await _item(ctx)
    assert item.status == "printing"
    assert uploaded["bytes"] == b"; selected plate two\nM115\n"
    assert uploaded["remote_path"].endswith(".gcode")
    assert not uploaded["path"].exists()
    ctx.start.assert_called_once()


@pytest.mark.asyncio
async def test_profile_mismatch_fails_before_upload(klipper_queue_factory):
    ctx = await klipper_queue_factory(embedded_preset="Trident")

    await _dispatch(ctx)

    item = await _item(ctx)
    assert item.status == "failed"
    assert "does not exactly match TinyT" in item.error_message
    ctx.upload.assert_not_awaited()
    ctx.start.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_during_moonraker_upload_never_starts_print(klipper_queue_factory):
    ctx = await klipper_queue_factory(raw=True)

    async def cancel_during_upload(*_args, **_kwargs):
        async with ctx.session_maker() as other_db:
            item = await other_db.get(PrintQueueItem, ctx.item_id)
            item.status = "cancelled"
            await other_db.commit()
        return True

    await _dispatch(ctx, upload_side_effect=cancel_during_upload)

    item = await _item(ctx)
    assert item.status == "cancelled"
    ctx.start.assert_not_called()
    assert ctx.delete.await_count == 2
