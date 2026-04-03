import asyncio
from sqlalchemy import select

from .db import make_engine, make_session_factory
from .models import ApiCredential
from .crypto import encrypt_secret
from .config import get_settings

async def upsert_outlook_credential(
    tenant_id: int,
    azure_tenant_id: str,
    client_id: str,
    client_secret: str,
):
    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    secret_enc = encrypt_secret(settings.master_key, client_secret)

    async with session_factory() as session:
        res = await session.execute(
            select(ApiCredential).where(
                ApiCredential.tenant_id == tenant_id,
                ApiCredential.provider == "outlook",
            )
        )
        row = res.scalar_one_or_none()

        if row:
            row.azure_tenant_id = azure_tenant_id
            row.client_id = client_id
            row.client_secret_encrypted = secret_enc
            row.active = True
        else:
            session.add(ApiCredential(
                tenant_id=tenant_id,
                provider="outlook",
                azure_tenant_id=azure_tenant_id,
                client_id=client_id,
                client_secret_encrypted=secret_enc,
                active=True,
            ))

        await session.commit()

    await engine.dispose()


async def main():
    # EDIT THESE VALUES:
    await upsert_outlook_credential(
        tenant_id=1,
        azure_tenant_id="azure_tenant_id",
        client_id="client_id",
        client_secret="client_secret",
    )

if __name__ == "__main__":
    asyncio.run(main())