"""Add document_type_routing table with default folder mappings

Revision ID: 019
Revises: 018
"""
from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

# Default folder routing per document type code.
# folder_external = seller is an outside supplier
# folder_internal = seller NIF matches one of the buyer's own companies
_SEED = [
    # code   description                  folder_external   folder_internal
    ("FT",  "Fatura",                    "Faturas",        "Faturas"),
    ("FR",  "Fatura-Recibo",             "Pagamentos",     "Pagamentos"),
    ("FS",  "Fatura Simplificada",       "Faturas",        "Faturas"),
    ("ND",  "Nota de Débito",            "Faturas",        "Faturas"),
    ("NC",  "Nota de Crédito",           "Faturas",        "Faturas"),
    ("GR",  "Guia de Remessa",           None,             None),
    ("GT",  "Guia de Transporte",        None,             None),
    ("GD",  "Guia ou Nota de Devolução", None,             None),
    ("RG",  "Recibo",                    "Pagamentos",     "Pagamentos"),
    ("RC",  "Recibo IVA de Caixa",       "Pagamentos",     "Pagamentos"),
    ("CM",  "Consulta de Mesa",          None,             None),
    ("PF",  "Fatura Pró-Forma",          None,             None),
    ("OR",  "Orçamento",                 None,             None),
    ("NE",  "Nota de Encomenda",         None,             None),
]


def upgrade():
    routing = op.create_table(
        "document_type_routing",
        sa.Column("id",              sa.Integer(),                   primary_key=True),
        sa.Column("code",            sa.String(10),  nullable=False),
        sa.Column("description",     sa.String(100), nullable=False),
        sa.Column("folder_external", sa.String(100), nullable=True),
        sa.Column("folder_internal", sa.String(100), nullable=True),
        sa.Column("active",          sa.Boolean(),   nullable=False, server_default="true"),
        sa.Column("updated_at",      sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_document_type_routing_code", "document_type_routing", ["code"], unique=True)

    op.bulk_insert(routing, [
        {
            "code": code,
            "description": desc,
            "folder_external": f_ext,
            "folder_internal": f_int,
            "active": True,
        }
        for code, desc, f_ext, f_int in _SEED
    ])


def downgrade():
    op.drop_index("ix_document_type_routing_code", table_name="document_type_routing")
    op.drop_table("document_type_routing")
