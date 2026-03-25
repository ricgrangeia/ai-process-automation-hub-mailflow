import asyncio
import os
from dotenv import load_dotenv

# 1. Carregar o .env da raiz antes de importar o resto
load_dotenv() 

from sqlalchemy import update
from datetime import datetime, timezone
from app.db import make_engine, make_session_factory
from app.models import EmailMessage
from app.config import get_settings

async def test():
    try:
        # Agora o get_settings() vai encontrar REDIS_URL, DATABASE_URL, etc. no .env
        settings = get_settings()
        engine = make_engine(settings.database_url)
        session_factory = make_session_factory(engine)
        
        async with session_factory() as session:
            print("🚀 A iniciar teste de escrita de telemetria e tokens...")
            
            stmt = (
                update(EmailMessage)
                .where(EmailMessage.id > 0)
                .values(
                    status="test_roi_success",
                    classification_label="FINANCE",
                    ai_confidence=0.98,
                    ai_source="qwen_2.5_test",
                    processed_at=datetime.now(timezone.utc),
                    prompt_tokens=450,
                    completion_tokens=50,
                    total_tokens=500
                )
            )
            
            result = await session.execute(stmt)
            await session.commit()
            
            if result.rowcount > 0:
                print(f"✅ Sucesso! {result.rowcount} linhas atualizadas na DB.")
            else:
                print("⚠️ O comando correu mas nenhuma linha foi afetada (tabela vazia?).")
                
    except Exception as e:
        print(f"❌ ERRO: {e}")

if __name__ == "__main__":
    asyncio.run(test())