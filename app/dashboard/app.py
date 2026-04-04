import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import plotly.express as px
from dotenv import load_dotenv
from email.header import decode_header, make_header
import os
import sys
from pathlib import Path


def _decode_mime_header(value):
    """Decode RFC 2047 encoded-word subjects (=?UTF-8?Q?...?=) already stored in DB."""
    if not value or "=?" not in str(value):
        return value
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

# Ensure project root (parent of app/) is first in sys.path so `app.*` imports resolve
# regardless of where Streamlit is launched from.
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from app.core.config import get_settings
    from app.core.crypto import encrypt_secret, decrypt_secret
except ImportError:
    st.error("❌ Erro: Módulo 'app.core.config' não encontrado. Corre da raiz do projeto.")
    st.stop()

load_dotenv()

st.set_page_config(page_title="AI Supervisor Ops", layout="wide", page_icon="🤖")


# ---------------------------------------------------------------------------
# Mobile CSS
# ---------------------------------------------------------------------------

def _inject_mobile_css():
    st.markdown("""
    <style>
    /* ── Stack columns vertically on mobile ─────────────────────────────── */
    @media (max-width: 768px) {
        [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
        [data-testid="column"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 100% !important;
        }

        /* ── Touch-friendly buttons ──────────────────────────────────────── */
        [data-testid="stButton"] > button,
        [data-testid="stFormSubmitButton"] > button {
            min-height: 48px !important;
            width: 100% !important;
            font-size: 1rem !important;
        }

        /* ── Touch-friendly inputs ───────────────────────────────────────── */
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input {
            font-size: 1rem !important;
            min-height: 44px !important;
        }

        /* ── Dataframe: horizontal scroll instead of overflow ────────────── */
        [data-testid="stDataFrame"] {
            overflow-x: auto !important;
            -webkit-overflow-scrolling: touch !important;
        }

        /* ── Charts: ensure full width ───────────────────────────────────── */
        .js-plotly-plot, .plotly {
            width: 100% !important;
        }

        /* ── Sidebar: decent width when open ────────────────────────────── */
        [data-testid="stSidebar"] {
            min-width: 260px !important;
        }

        /* ── Reduce page padding ─────────────────────────────────────────── */
        .block-container {
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            padding-top: 1.5rem !important;
        }

        /* ── Scale down headings ─────────────────────────────────────────── */
        h1 { font-size: 1.6rem !important; }
        h2 { font-size: 1.3rem !important; }
        h3 { font-size: 1.1rem !important; }

        /* ── Tabs: scrollable on narrow screens ──────────────────────────── */
        .stTabs [data-baseweb="tab-list"] {
            overflow-x: auto !important;
            flex-wrap: nowrap !important;
        }
        .stTabs [data-baseweb="tab"] {
            min-width: max-content !important;
            padding: 10px 16px !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login_screen():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.markdown("<h1 style='text-align: center; margin-top: 50px;'>🔐 AI Supervisor Login</h1>", unsafe_allow_html=True)

        _, col2, _ = st.columns([1, 1, 1])
        with col2:
            with st.form("login_form", clear_on_submit=False):
                user_input = st.text_input("Utilizador", key="input_user")
                pw_input = st.text_input("Password", type="password", key="input_pw")
                submit = st.form_submit_button("Entrar", use_container_width=True)

                if submit:
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


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

@st.cache_resource
def get_db_engine(db_url):
    sync_url = db_url.replace("+asyncpg", "")
    return create_engine(sync_url)


# ---------------------------------------------------------------------------
# Page: Dashboard
# ---------------------------------------------------------------------------

def page_dashboard(engine, settings):
    st.title("📊 Painel de Supervisão")

    def load_data():
        query = """
            SELECT
                subject        AS "Assunto",
                classification_label AS "Categoria",
                ai_confidence  AS "Confiança",
                ai_source      AS "Origem",
                processing_time_seconds AS "Tempo(s)",
                processed_at   AS "Data"
            FROM emails
            WHERE status = 'moved'
            ORDER BY processed_at DESC LIMIT 200
        """
        return pd.read_sql(query, engine)

    try:
        df = load_data()

        if df.empty:
            st.warning("⚠️ O AI Worker ainda não processou e-mails suficientes.")
            return

        c1, c2, c3 = st.columns(3)
        c1.metric("Total de E-mails", len(df))
        c2.metric("Confiança Média", f"{df['Confiança'].mean()*100:.1f}%")
        c3.metric("Tempo Médio vLLM", f"{df['Tempo(s)'].mean():.2f}s")

        st.divider()

        g1, g2 = st.columns(2)
        with g1:
            st.plotly_chart(
                px.pie(df, names="Categoria", hole=0.4, title="Distribuição de Pastas"),
                use_container_width=True,
            )
        with g2:
            st.plotly_chart(
                px.histogram(df, x="Origem", color="Origem", title="Decisões: Regras vs IA"),
                use_container_width=True,
            )

        st.subheader("📋 Registos Recentes")
        df["Assunto"] = df["Assunto"].apply(_decode_mime_header)
        df["Confiança"] = (df["Confiança"] * 100).round(0).astype(int)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Confiança": st.column_config.NumberColumn(format="%d%%"),
            },
        )

    except Exception as e:
        st.error(f"❌ Erro na Base de Dados: {e}")
        st.info("Dica: Confirme se as colunas de telemetria foram criadas no PostgreSQL.")


# ---------------------------------------------------------------------------
# Page: Email Accounts
# ---------------------------------------------------------------------------

def page_email_accounts(engine, settings):
    st.title("✉️ Email Accounts")

    # ---- list accounts ----
    def load_accounts():
        return pd.read_sql(
            "SELECT id, tenant_id, provider, email, imap_host, imap_port, username, active "
            "FROM email_accounts ORDER BY id",
            engine,
        )

    df = load_accounts()

    st.subheader("Configured Accounts")

    if df.empty:
        st.info("No accounts configured yet.")
    else:
        # Show table with action buttons per row
        for _, row in df.iterrows():
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
                c1.markdown(f"**{row['email']}**  \n`{row['provider'].upper()}` · {row['imap_host'] or '—'}")
                c2.markdown(f"Tenant: `{row['tenant_id']}`  \nUser: `{row['username'] or '—'}`")
                status_label = "🟢 Active" if row["active"] else "🔴 Inactive"
                c3.markdown(f"<br>{status_label}", unsafe_allow_html=True)

                with c4:
                    st.write("")  # vertical align
                    toggle_label = "Deactivate" if row["active"] else "Activate"
                    if st.button(toggle_label, key=f"toggle_{row['id']}"):
                        with engine.begin() as conn:
                            conn.execute(
                                text("UPDATE email_accounts SET active = :val WHERE id = :id"),
                                {"val": not row["active"], "id": int(row["id"])},
                            )
                        st.rerun()

                new_pw_key = f"new_pw_{row['id']}"
                with st.expander(f"🔑 Reset password — {row['email']}"):
                    new_pw = st.text_input("New password", type="password", key=new_pw_key)
                    if st.button("Save new password", key=f"save_pw_{row['id']}"):
                        if not new_pw:
                            st.error("Password cannot be empty.")
                        else:
                            encrypted_pw = encrypt_secret(settings.master_key, new_pw)
                            with engine.begin() as conn:
                                conn.execute(
                                    text("UPDATE email_accounts SET password_encrypted = :pw WHERE id = :id"),
                                    {"pw": encrypted_pw, "id": int(row["id"])},
                                )
                            st.success("Password updated and encrypted.")

    st.divider()

    # ---- add account ----
    st.subheader("Add Account")
    tab_imap, tab_outlook = st.tabs(["IMAP", "Outlook / Microsoft 365"])

    # -- IMAP --
    with tab_imap:
        with st.form("add_imap_form"):
            col1, col2 = st.columns(2)
            tenant_id = col1.number_input("Tenant ID", min_value=1, value=1, step=1)
            email = col2.text_input("Email address")
            imap_host = col1.text_input("IMAP Host", placeholder="imap.gmail.com")
            imap_port = col2.number_input("IMAP Port", min_value=1, max_value=65535, value=993)
            username = col1.text_input("Username (usually same as email)")
            password = col2.text_input("Password", type="password")
            active = st.checkbox("Active", value=True)
            submitted = st.form_submit_button("Add IMAP Account", use_container_width=True)

        if submitted:
            if not email or not imap_host or not username or not password:
                st.error("All fields are required.")
            else:
                try:
                    encrypted_pw = encrypt_secret(settings.master_key, password)
                    with engine.begin() as conn:
                        conn.execute(
                            text("""
                                INSERT INTO email_accounts
                                    (tenant_id, provider, email, imap_host, imap_port, username, password_encrypted, active)
                                VALUES
                                    (:tenant_id, 'imap', :email, :host, :port, :username, :pw, :active)
                            """),
                            {
                                "tenant_id": int(tenant_id),
                                "email": email,
                                "host": imap_host,
                                "port": int(imap_port),
                                "username": username,
                                "pw": encrypted_pw,
                                "active": active,
                            },
                        )
                    st.success(f"IMAP account **{email}** added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    # -- Outlook --
    with tab_outlook:
        with st.form("add_outlook_form"):
            col1, col2 = st.columns(2)
            o_tenant_id = col1.number_input("Tenant ID", min_value=1, value=1, step=1, key="o_tid")
            o_email = col2.text_input("Email / UPN", key="o_email")
            o_active = st.checkbox("Active", value=True, key="o_active")
            submitted_o = st.form_submit_button("Add Outlook Account", use_container_width=True)

        if submitted_o:
            if not o_email:
                st.error("Email is required.")
            else:
                try:
                    with engine.begin() as conn:
                        conn.execute(
                            text("""
                                INSERT INTO email_accounts
                                    (tenant_id, provider, email, outlook_user, active)
                                VALUES
                                    (:tenant_id, 'outlook', :email, :outlook_user, :active)
                            """),
                            {
                                "tenant_id": int(o_tenant_id),
                                "email": o_email,
                                "outlook_user": o_email,
                                "active": o_active,
                            },
                        )
                    st.success(f"Outlook account **{o_email}** added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

_inject_mobile_css()

if login_screen():

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

    page = st.sidebar.radio("Navigation", ["📊 Dashboard", "✉️ Email Accounts"])

    if st.sidebar.button("Logout"):
        st.session_state["authenticated"] = False
        st.rerun()

    if page == "📊 Dashboard":
        page_dashboard(engine, settings)
    elif page == "✉️ Email Accounts":
        page_email_accounts(engine, settings)
