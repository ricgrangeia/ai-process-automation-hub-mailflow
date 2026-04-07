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
    from app.core.operation_mode import MODES, OPERATION_MODE_KEY
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
                CASE
                    WHEN sender_type = 'company' THEN '🏢 ' || COALESCE(sender_name, from_address)
                    WHEN sender_type = 'person'  THEN '👤 ' || COALESCE(sender_name, from_address)
                    ELSE COALESCE(sender_name, from_address)
                END            AS "Remetente",
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
        avg_time = df['Tempo(s)'].dropna().mean()
        c3.metric("Tempo Médio vLLM", f"{avg_time:.2f}s" if pd.notna(avg_time) else "—")

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
        df["Tempo(s)"] = df["Tempo(s)"].apply(
            lambda v: round(float(v), 2) if pd.notna(v) else None
        )
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Confiança": st.column_config.NumberColumn(format="%d%%"),
                "Tempo(s)": st.column_config.NumberColumn(format="%.2f s"),
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
                        new_val = not row["active"]
                        with engine.begin() as conn:
                            conn.execute(
                                text("UPDATE email_accounts SET active = :val WHERE id = :id"),
                                {"val": new_val, "id": int(row["id"])},
                            )
                        from app.core.audit import log_audit_sync
                        log_audit_sync(
                            engine,
                            actor_type="dashboard",
                            actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                            action="account.toggled",
                            entity_type="account",
                            entity_id=int(row["id"]),
                            details={"email": row["email"], "active": new_val},
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
                            from app.core.audit import log_audit_sync
                            log_audit_sync(
                                engine,
                                actor_type="dashboard",
                                actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                                action="account.password_changed",
                                entity_type="account",
                                entity_id=int(row["id"]),
                                details={"email": row["email"]},
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
                    from app.core.audit import log_audit_sync
                    log_audit_sync(
                        engine,
                        actor_type="dashboard",
                        actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                        action="account.added",
                        entity_type="account",
                        details={"email": email, "provider": "imap", "host": imap_host},
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
                    from app.core.audit import log_audit_sync
                    log_audit_sync(
                        engine,
                        actor_type="dashboard",
                        actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                        action="account.added",
                        entity_type="account",
                        details={"email": o_email, "provider": "outlook"},
                    )
                    st.success(f"Outlook account **{o_email}** added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Page: Learned Rules
# ---------------------------------------------------------------------------

FOLDERS = ["Invoices", "Work", "Personal", "Marketing", "Spam", "Other"]
MATCH_FIELDS = ["sender_domain", "sender_email", "subject_contains", "body_contains"]


def _actions_summary(actions: list) -> str:
    """Human-readable summary of a rule's actions list."""
    parts = []
    for a in actions or []:
        if a.get("type") == "move_folder":
            parts.append(f"📁 Move → {a.get('folder', '?')}")
        elif a.get("type") == "export_pdf":
            parts.append(f"📄 PDF → {a.get('path', '?')}")
    return "  |  ".join(parts) if parts else "—"


def page_learned_rules(engine, settings):
    st.title("📚 Learned Rules")
    st.caption("Rules are applied before the AI classifier — zero LLM cost, instant decisions.")

    try:
        df = pd.read_sql(
            """
            SELECT id, tenant_id, match_field, match_value, actions,
                   hit_count, active, created_at
            FROM learned_rules
            ORDER BY active DESC, hit_count DESC, created_at DESC
            """,
            engine,
        )
    except Exception as e:
        st.error(f"❌ Could not load rules: {e}")
        return

    if df.empty:
        st.info("No learned rules yet. Rules are created when you correct an email in Telegram.")
    else:
        st.metric("Total rules", len(df))
        active_count = df["active"].sum()
        st.caption(f"{active_count} active · {len(df) - active_count} disabled")

        for _, row in df.iterrows():
            rule_id = int(row["id"])
            is_active = bool(row["active"])
            border_color = "#34d399" if is_active else "#4a4a4a"

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 2, 1, 1])

                with c1:
                    field_icon = {
                        "sender_domain": "🌐",
                        "sender_email": "📧",
                        "subject_contains": "📝",
                        "body_contains": "📄",
                    }.get(row["match_field"], "❓")
                    st.markdown(
                        f"**{field_icon} {row['match_field']}** = `{row['match_value']}`  \n"
                        f"{_actions_summary(row['actions'])}"
                    )

                with c2:
                    st.markdown(
                        f"Hits: **{int(row['hit_count'])}**  \n"
                        f"Tenant: `{row['tenant_id']}`  \n"
                        f"Created: {str(row['created_at'])[:10]}"
                    )

                with c3:
                    st.markdown(f"{'🟢 Active' if is_active else '🔴 Disabled'}")

                with c4:
                    toggle_label = "Disable" if is_active else "Enable"
                    if st.button(toggle_label, key=f"rule_toggle_{rule_id}"):
                        with engine.begin() as conn:
                            conn.execute(
                                text("UPDATE learned_rules SET active = :val WHERE id = :id"),
                                {"val": not is_active, "id": rule_id},
                            )
                        from app.core.audit import log_audit_sync
                        log_audit_sync(
                            engine,
                            actor_type="dashboard",
                            actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                            action="rule.toggled",
                            entity_type="rule",
                            entity_id=rule_id,
                            details={
                                "match_field": row["match_field"],
                                "match_value": row["match_value"],
                                "active": not is_active,
                            },
                        )
                        st.rerun()

                # Edit expander
                with st.expander(f"✏️ Edit rule #{rule_id}"):
                    with st.form(f"edit_rule_{rule_id}"):
                        ef1, ef2 = st.columns(2)
                        new_field = ef1.selectbox(
                            "Match field", MATCH_FIELDS,
                            index=MATCH_FIELDS.index(row["match_field"])
                            if row["match_field"] in MATCH_FIELDS else 0,
                            key=f"field_{rule_id}",
                        )
                        new_value = ef2.text_input(
                            "Match value", value=row["match_value"], key=f"value_{rule_id}"
                        )

                        # Parse existing actions for prefill
                        acts = row["actions"] or []
                        existing_folder = next(
                            (a.get("folder") for a in acts if a.get("type") == "move_folder"), None
                        )
                        existing_pdf = next(
                            (a.get("path") for a in acts if a.get("type") == "export_pdf"), None
                        )

                        af1, af2 = st.columns(2)
                        new_folder = af1.selectbox(
                            "Move to folder",
                            ["(none)"] + FOLDERS,
                            index=(FOLDERS.index(existing_folder) + 1)
                            if existing_folder in FOLDERS else 0,
                            key=f"folder_{rule_id}",
                        )
                        new_pdf_path = af2.text_input(
                            "Export PDF path (blank = off)",
                            value=existing_pdf or "",
                            key=f"pdf_{rule_id}",
                        )

                        if st.form_submit_button("💾 Save changes", use_container_width=True):
                            new_actions = []
                            if new_folder != "(none)":
                                new_actions.append({"type": "move_folder", "folder": new_folder})
                            if new_pdf_path.strip():
                                new_actions.append({"type": "export_pdf", "path": new_pdf_path.strip()})

                            import json as _json
                            with engine.begin() as conn:
                                conn.execute(
                                    text(
                                        "UPDATE learned_rules "
                                        "SET match_field = :mf, match_value = :mv, actions = :ac::jsonb "
                                        "WHERE id = :id"
                                    ),
                                    {
                                        "mf": new_field,
                                        "mv": new_value,
                                        "ac": _json.dumps(new_actions),
                                        "id": rule_id,
                                    },
                                )
                            from app.core.audit import log_audit_sync
                            log_audit_sync(
                                engine,
                                actor_type="dashboard",
                                actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                                action="rule.updated",
                                entity_type="rule",
                                entity_id=rule_id,
                                details={
                                    "match_field": new_field,
                                    "match_value": new_value,
                                    "actions": new_actions,
                                },
                            )
                            st.success("Rule updated.")
                            st.rerun()

                    # Delete button outside form
                    if st.button("🗑️ Delete rule", key=f"del_{rule_id}", type="secondary"):
                        with engine.begin() as conn:
                            conn.execute(
                                text("DELETE FROM learned_rules WHERE id = :id"), {"id": rule_id}
                            )
                        from app.core.audit import log_audit_sync
                        log_audit_sync(
                            engine,
                            actor_type="dashboard",
                            actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                            action="rule.deleted",
                            entity_type="rule",
                            entity_id=rule_id,
                            details={
                                "match_field": row["match_field"],
                                "match_value": row["match_value"],
                            },
                        )
                        st.rerun()

    st.divider()

    # ── Add rule manually ──
    st.subheader("➕ Add Rule Manually")
    with st.form("add_rule_form"):
        ac1, ac2 = st.columns(2)
        a_tenant = ac1.number_input("Tenant ID", min_value=1, value=1, step=1)
        a_field = ac2.selectbox("Match field", MATCH_FIELDS)
        a_value = st.text_input("Match value", placeholder="amazon.com")
        ac3, ac4 = st.columns(2)
        a_folder = ac3.selectbox("Move to folder", ["(none)"] + FOLDERS)
        a_pdf = ac4.text_input("Export PDF path (blank = off)", placeholder="Company/{year}/{month}/")
        submitted = st.form_submit_button("Add Rule", use_container_width=True)

    if submitted:
        if not a_value.strip():
            st.error("Match value is required.")
        else:
            import json as _json
            new_actions = []
            if a_folder != "(none)":
                new_actions.append({"type": "move_folder", "folder": a_folder})
            if a_pdf.strip():
                new_actions.append({"type": "export_pdf", "path": a_pdf.strip()})
            if not new_actions:
                st.error("At least one action (folder or PDF) is required.")
            else:
                try:
                    with engine.begin() as conn:
                        conn.execute(
                            text(
                                "INSERT INTO learned_rules "
                                "(tenant_id, match_field, match_value, actions, active) "
                                "VALUES (:tid, :mf, :mv, :ac::jsonb, true)"
                            ),
                            {
                                "tid": int(a_tenant),
                                "mf": a_field,
                                "mv": a_value.strip(),
                                "ac": _json.dumps(new_actions),
                            },
                        )
                    from app.core.audit import log_audit_sync
                    log_audit_sync(
                        engine,
                        actor_type="dashboard",
                        actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                        action="rule.created",
                        entity_type="rule",
                        details={
                            "match_field": a_field,
                            "match_value": a_value.strip(),
                            "actions": new_actions,
                        },
                        tenant_id=int(a_tenant),
                    )
                    st.success(f"Rule added: `{a_field}` = `{a_value.strip()}`")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Page: Audit Log
# ---------------------------------------------------------------------------

def page_audit_log(engine):
    st.title("📋 Audit Log")

    # Filters
    col_actor, col_action, col_days = st.columns(3)
    actor_filter = col_actor.text_input("Actor contains", placeholder="@username, alembic, ai-worker…")
    action_filter = col_action.selectbox(
        "Action",
        ["(all)", "email.classified", "email.reclassified", "email.approved",
         "email.sender_corrected", "rule.created", "rule.updated", "rule.toggled",
         "rule.deleted", "system.restart", "system.recover", "system.learning_mode",
         "query.searched", "db.migrated", "account.added", "account.toggled",
         "account.password_changed"],
    )
    days = col_days.number_input("Last N days", min_value=1, max_value=365, value=7)

    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
    conditions = ["created_at >= %(cutoff)s"]
    params: dict = {"cutoff": cutoff}

    if actor_filter:
        conditions.append("actor_name ILIKE %(actor)s")
        params["actor"] = f"%{actor_filter}%"
    if action_filter != "(all)":
        conditions.append("action = %(action)s")
        params["action"] = action_filter

    where = " AND ".join(conditions)
    query = f"""
        SELECT
            created_at   AS "Time",
            actor_type   AS "Type",
            actor_name   AS "Actor",
            action       AS "Action",
            entity_type  AS "Entity",
            entity_id    AS "ID",
            details      AS "Details",
            tenant_id    AS "Tenant"
        FROM audit_logs
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT 500
    """

    try:
        df = pd.read_sql(query, engine, params=params)

        if df.empty:
            st.info("No audit events found for the selected filters.")
            return

        st.metric("Events shown", len(df))

        # Colour-code the Action column using a map for readability
        action_icons = {
            "email.classified": "🤖",
            "email.reclassified": "✏️",
            "email.approved": "✅",
            "email.sender_corrected": "🏷️",
            "rule.created": "📚",
            "rule.updated": "✏️",
            "rule.toggled": "🔁",
            "rule.deleted": "🗑️",
            "system.restart": "🔄",
            "system.recover": "♻️",
            "system.learning_mode": "🎓",
            "query.searched": "🔍",
            "db.migrated": "🗄️",
            "account.added": "➕",
            "account.toggled": "🔁",
            "account.password_changed": "🔑",
        }
        df["Action"] = df["Action"].apply(lambda a: f"{action_icons.get(a, '')} {a}")

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Time": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss"),
                "Details": st.column_config.TextColumn(width="large"),
            },
        )
    except Exception as e:
        st.error(f"❌ Could not load audit log: {e}")
        st.info("Run the latest migrations and restart ai-worker to create the audit_logs table.")


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

    # Operation Mode selector
    st.sidebar.markdown("---")
    st.sidebar.markdown("**⚙️ Operation Mode**")
    try:
        import redis as _redis_sync
        _r = _redis_sync.from_url(settings.redis_url, decode_responses=True)
        _current_mode = _r.get(OPERATION_MODE_KEY) or "hybrid"
        _r.close()
    except Exception:
        _current_mode = "hybrid"

    _mode_keys = list(MODES.keys())
    _mode_labels = [f"{MODES[m]}" for m in _mode_keys]
    _current_idx = _mode_keys.index(_current_mode) if _current_mode in _mode_keys else 0

    _selected_label = st.sidebar.selectbox(
        "Mode",
        options=_mode_labels,
        index=_current_idx,
        label_visibility="collapsed",
    )
    _selected_mode = _mode_keys[_mode_labels.index(_selected_label)]

    if _selected_mode != _current_mode:
        try:
            import redis as _redis_sync2
            _r2 = _redis_sync2.from_url(settings.redis_url, decode_responses=True)
            _r2.set(OPERATION_MODE_KEY, _selected_mode)
            _r2.close()
            from app.core.audit import log_audit_sync
            from sqlalchemy import create_engine as _ce
            _sync_eng = _ce((os.environ.get("DATABASE_URL") or settings.database_url).replace("+asyncpg", ""))
            log_audit_sync(
                _sync_eng,
                actor_type="dashboard",
                actor_name="admin",
                action="mode.changed",
                entity_type="system",
                details={"from": _current_mode, "to": _selected_mode},
            )
            st.sidebar.success(f"Mode → {_selected_mode}")
            st.rerun()
        except Exception as _e:
            st.sidebar.error(f"Failed to set mode: {_e}")

    st.sidebar.markdown("---")

    page = st.sidebar.radio("Navigation", ["📊 Dashboard", "✉️ Email Accounts", "📚 Learned Rules", "📋 Audit Log"])

    if st.sidebar.button("Logout"):
        st.session_state["authenticated"] = False
        st.rerun()

    if page == "📊 Dashboard":
        page_dashboard(engine, settings)
    elif page == "✉️ Email Accounts":
        page_email_accounts(engine, settings)
    elif page == "📚 Learned Rules":
        page_learned_rules(engine, settings)
    elif page == "📋 Audit Log":
        page_audit_log(engine)
