import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

async def get_app_token(azure_tenant_id: str, client_id: str, client_secret: str) -> str:
    url = f"https://login.microsoftonline.com/{azure_tenant_id}/oauth2/v2.0/token"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, data=data)
        r.raise_for_status()
        return r.json()["access_token"]


async def list_unread_inbox_messages(token: str, user: str, top: int = 20) -> list[dict]:
    # Unread only
    url = f"{GRAPH_BASE}/users/{user}/mailFolders/Inbox/messages"
    params = {
        "$top": str(top),
        "$orderby": "receivedDateTime desc",
        "$filter": "isRead eq false",
        "$select": "id,internetMessageId,subject,receivedDateTime,bodyPreview,from,body",
    }

    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.json().get("value", [])


async def mark_message_read(token: str, user: str, message_id: str) -> None:
    url = f"{GRAPH_BASE}/users/{user}/messages/{message_id}"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"isRead": True}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.patch(url, headers=headers, json=payload)
        r.raise_for_status()