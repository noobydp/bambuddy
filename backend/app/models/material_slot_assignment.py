from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class MaterialSlotAssignment(Base):
    """Read-only inventory assignment for a provider-neutral material slot."""

    __tablename__ = "material_slot_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), index=True)
    material_system_id: Mapped[str] = mapped_column(String(64))
    slot_id: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(20))
    spool_id: Mapped[int | None] = mapped_column(
        ForeignKey("spool.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    spoolman_spool_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    printer: Mapped["Printer"] = relationship()
    spool: Mapped["Spool | None"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "printer_id",
            "material_system_id",
            "slot_id",
            name="uq_material_slot_assignment",
        ),
        CheckConstraint(
            "(source = 'internal' AND spool_id IS NOT NULL AND spoolman_spool_id IS NULL) OR "
            "(source = 'spoolman' AND spool_id IS NULL AND spoolman_spool_id IS NOT NULL)",
            name="ck_material_slot_assignment_source",
        ),
    )


from backend.app.models.printer import Printer  # noqa: E402
from backend.app.models.spool import Spool  # noqa: E402
