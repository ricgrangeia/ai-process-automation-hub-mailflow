import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
import plotly.express as px
from dotenv import load_dotenv
import os
import sys

# 1. Resolver caminhos para permitir imports de 'app'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from app.config import get_settings
except ImportError:
    st.error("❌ Erro: Módulo 'app.config' não encontrado. Corre da raiz do projeto.")
    st.stop()

load_dotenv()

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="AI Supervisor Ops", layout="wide", page_icon="🤖")

# --- SISTEMA DE LOGIN (CORRIGIDO PARA EVITAR SUBMISSÃO PRECOCE) ---
def login_screen():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.markdown("<h1 style='text-align: center; margin-top: 50px;'>🔐 AI Supervisor Login</h1>", unsafe_allow_html=True)
        
        _, col2, _ = st.columns([1, 1, 1])
        
        with col2:
            # O st.form impede que o Streamlit submeta os dados enquanto saltas entre campos
            with st.form("login_form", clear_on_submit=False):
                user_input = st.text_input("Utilizador", key="input_user")
                pw_input = st.text_input("Password", type="password", key="input_pw")
                
                submit = st.form_submit_button("Entrar", use_container_width=True)
                
                if submit:
                    # Só aqui dentro é que validamos
                    env_user = os.environ.get("DASHBOARD_USER", "admin")
                    env_pw = os.environ.get("DASHBOARD_PASSWORD", "mudar123")
                    
                    if user_input == env_user and pw_input == env_pw:
                        st.session_state["authenticated"] = True
                        st.success("Acesso concedido!")
                        st.rerun()
                    else:
                        st.error("❌ Utilizador ou Password incorretos")
        return False
    return True

# --- INICIALIZAÇÃO DO DASHBOARD ---
if login_screen():
    
    @st.cache_resource
    def get_db_engine(db_url):
        # Converte URL assíncrona para síncrona
        sync_url = db_url.replace("+asyncpg", "")
        return create_engine(sync_url)

    try:
        settings = get_settings()
        raw_url = os.environ.get("DATABASE_URL") or settings.database_url
        engine = get_db_engine(raw_url)
    except Exception as e:
        st.error(f"❌ Erro de Configuração: {e}")
        st.stop()

    # Sidebar
    st.sidebar.title("🤖 AI Admin")
    st.sidebar.info(f"**Model:** {settings.llm_model}")
    st.sidebar.info(f"**Inbox:** {settings.inbox_folder}")
    
    if st.sidebar.button("Terminar Sessão (Logout)"):
        st.session_state["authenticated"] = False
        st.rerun()

    # Função de carregamento de dados
    def load_data():
        query = """
            SELECT 
                subject as "Assunto", 
                classification_label as "Categoria", 
                ai_confidence as "Confiança", 
                ai_source as "Origem", 
                processing_time_seconds as "Tempo(s)", 
                processed_at as "Data"
            FROM emails 
            WHERE status = 'moved' 
            ORDER BY processed_at DESC LIMIT 200
        """
        return pd.read_sql(query, engine)

    # Conteúdo Principal do Dashboard
    st.title("📊 Painel de Supervisão")
    
    try:
        df = load_data()
        
        if df.empty:
            st.warning("⚠️ O AI Worker ainda não processou e-mails suficientes.")
        else:
            # KPIs
            c1, c2, c3 = st.columns(3)
            c1.metric("Total de E-mails", len(df))
            c2.metric("Confiança Média", f"{df['Confiança'].mean()*100:.1f}%")
            c3.metric("Tempo Médio vLLM", f"{df['Tempo(s)'].mean():.2f}s")

            st.divider()

            # Gráficos
            g1, g2 = st.columns(2)
            with g1:
                fig_pie = px.pie(df, names='Categoria', hole=0.4, title="Distribuição de Pastas")
                st.plotly_chart(fig_pie, use_container_width=True)
            with g2:
                fig_bar = px.histogram(df, x='Origem', color='Origem', title="Decisões: Regras vs IA")
                st.plotly_chart(fig_bar, use_container_width=True)

            # Tabela de Auditoria
            st.subheader("📋 Registos Recentes")
            st.dataframe(df, use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"❌ Erro na Base de Dados: {e}")
        st.info("Dica: Confirme se as colunas de telemetria foram criadas no PostgreSQL.")