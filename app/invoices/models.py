from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, Numeric, DateTime, Text, ForeignKey, UniqueConstraint, func

from app.core.database.base import Base


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        # Business key: same invoice number from the same seller = same invoice
        # Nullable columns allow multiple NULLs in PostgreSQL (partial records without QR)
        UniqueConstraint("nif_seller", "invoice_number", name="uq_invoices_seller_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), index=True)

    # Seller (emitente)
    nif_seller: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    seller_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Buyer (adquirente)
    nif_buyer: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Invoice identifiers
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    atcud: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Amounts
    taxable_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    vat_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Multibanco payment data
    mb_entidade: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mb_referencia: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mb_valor: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    mb_data_limite: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Document type (from QR field D): FT, FR, FS, ND, NC, RG, etc.
    document_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Human-readable label derived from document_type: "Fatura", "Recibo", etc.
    document_type_description: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Raw QR string and extraction metadata
    raw_qr: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
