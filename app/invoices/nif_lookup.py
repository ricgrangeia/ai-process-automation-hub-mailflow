"""
NIF Lookup — resolves invoice seller names via the ai-api tool server.

Flow:
  1. Check sellers table (cache) — return immediately if known
  2. Call GET {tool_server_url}/tools/nif/lookup?nif={nif}
  3. If found: upsert into sellers table + backfill invoices.seller_name
  4. Return name or None
"""

import asyncio
import logging

import httpx
from sqlalchemy import text

logger = logging.getLogger("nif-lookup")


async def resolve_seller_name(
    nif: str | None,
    db_url: str,
    tool_server_url: str,
    api_key: str = "",
) -> str | None:
    """
    Return the company name for a NIF, using the sellers table as cache.
    Fetches from ai-api /tools/nif/lookup on cache miss.
    Returns None if unknown or lookup fails.
    """
    if not nif or not nif.strip().isdigit() or len(nif.strip()) != 9:
        return None

    nif = nif.strip()

    import sqlalchemy

    def _db_lookup() -> str | None:
        """Check sellers cache. Returns None gracefully if table doesn't exist yet."""
        try:
            engine = sqlalchemy.create_engine(db_url)
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT name FROM sellers WHERE nif = :nif LIMIT 1"),
                    {"nif": nif},
                ).fetchone()
            engine.dispose()
            return row[0] if row else None
        except Exception:
            return None  # table may not exist yet — not a fatal error

    def _db_upsert(data: dict) -> None:
        """Save to sellers table. Skipped silently if table doesn't exist yet."""
        try:
            engine = sqlalchemy.create_engine(db_url)
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO sellers (nif, name, activity, cae, address, situation)
                        VALUES (:nif, :name, :activity, :cae, :address, :situation)
                        ON CONFLICT (nif) DO UPDATE SET
                            name      = EXCLUDED.name,
                            activity  = EXCLUDED.activity,
                            cae       = EXCLUDED.cae,
                            address   = EXCLUDED.address,
                            situation = EXCLUDED.situation,
                            updated_at = now()
                    """),
                    data,
                )
            engine.dispose()
        except Exception as e:
            logger.warning(f"Could not upsert seller {nif}: {e}")

    def _db_update_invoices(name: str) -> None:
        """Backfill seller_name on existing invoices. Always runs independently."""
        try:
            engine = sqlalchemy.create_engine(db_url)
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE invoices
                        SET seller_name = :name
                        WHERE nif_seller = :nif
                          AND (seller_name IS NULL OR seller_name = '')
                    """),
                    {"name": name, "nif": nif},
                )
            engine.dispose()
        except Exception as e:
            logger.warning(f"Could not backfill invoices seller_name for {nif}: {e}")

    # 1. Check sellers table cache
    cached = await asyncio.to_thread(_db_lookup)
    if cached:
        logger.debug(f"NIF {nif} cached as '{cached}'")
        return cached

    # 2. Call ai-api tool server
    if not tool_server_url:
        return None

    try:
        headers = {"x-api-key": api_key} if api_key else {}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{tool_server_url}/tools/nif/lookup",
                params={"nif": nif},
                headers=headers,
            )
        if resp.status_code != 200:
            logger.warning(f"NIF lookup HTTP {resp.status_code} for {nif}")
            return None
        data = resp.json()
    except Exception as e:
        logger.warning(f"NIF lookup request failed for {nif}: {e}")
        return None

    if not data.get("found") or not data.get("name"):
        logger.info(f"NIF {nif} not found in registry (found={data.get('found')}, name={data.get('name')})")
        return None

    name = data["name"]
    logger.info(f"NIF {nif} resolved → '{name}'")

    # 3. Each DB operation is independent — one failing doesn't block the others
    await asyncio.to_thread(_db_upsert, {
        "nif":       nif,
        "name":      name,
        "activity":  data.get("activity"),
        "cae":       data.get("cae"),
        "address":   data.get("address"),
        "situation": data.get("situation"),
    })
    await asyncio.to_thread(_db_update_invoices, name)

    return name
