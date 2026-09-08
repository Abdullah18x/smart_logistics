"""Product master: the physical characteristics that drive logistics."""

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.constants.formats import DEFAULT_CURRENCY
from app.models.base import (
    CATALOG_SCHEMA,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Sku(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A stock-keeping unit.

    Deliberately thin — SmartLogistics moves goods, it does not merchandise
    them. Only attributes that affect packing, courier capacity or handling are
    modelled; pricing and marketing data belong to the upstream commerce system.
    """

    __tablename__ = "skus"
    __table_args__ = (
        Index("ix_skus_category_active", "category", "is_active"),
        # Live rows only (ADR-016).
        Index(
            "ix_skus_active_category",
            "category",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("weight_g > 0", name="weight_positive"),
        CheckConstraint(
            "length_mm > 0 AND width_mm > 0 AND height_mm > 0", name="dimensions_positive"
        ),
        {"schema": CATALOG_SCHEMA},
    )

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    category: Mapped[str | None] = mapped_column(String(100))

    # Integer units throughout: grams and millimetres avoid floating-point drift
    # when summing hundreds of items into a shipment weight.
    weight_g: Mapped[int] = mapped_column(Integer, nullable=False)
    length_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    height_mm: Mapped[int] = mapped_column(Integer, nullable=False)

    # Handling flags. These constrain which courier and zone can take the item.
    is_fragile: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_hazmat: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    requires_cold_chain: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Declared value per unit, used for insurance and customs paperwork.
    unit_value: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=DEFAULT_CURRENCY
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    @property
    def volume_mm3(self) -> int:
        return self.length_mm * self.width_mm * self.height_mm

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Sku {self.code}>"
