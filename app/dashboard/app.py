import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import plotly.express as px
from dotenv import load_dotenv
from email.header import decode_header, make_header
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)


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
    from app.core.i18n import t
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
        }width='stretch
    }
    </style>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

_AUTH_COOKIE = "mailai_auth"
_AUTH_TOKEN  = "ok"   # simple presence check — not a secret, just a marker


def _get_cookie_controller():
    """Lazily import and cache the cookie controller in session state."""
    if "_cookie_ctrl" not in st.session_state:
        try:
            from streamlit_cookies_controller import CookieController
            st.session_state["_cookie_ctrl"] = CookieController()
        except ImportError:
            st.session_state["_cookie_ctrl"] = None
    return st.session_state["_cookie_ctrl"]


def login_screen():
    ctrl = _get_cookie_controller()
    if ctrl is None:
        return False

    # 1. Tentar ler o valor do cookie do browser
    cookie_val = ctrl.get(_AUTH_COOKIE)

    # 2. Se o cookie for válido e não estivermos autenticados na sessão, sincroniza
    if not st.session_state.get("authenticated", False):
        if cookie_val == _AUTH_TOKEN:
            st.session_state["authenticated"] = True
            st.rerun()  # Força o rerun para mostrar a app imediatamente
        
        # 3. Lógica Anti-Refresh (Race Condition Fix):
        # O cookie_val pode vir None nos primeiros milissegundos do refresh.
        # Damos 2 tentativas ao script para encontrar o cookie antes de mostrar o login.
        if "retry_count" not in st.session_state:
            st.session_state["retry_count"] = 0

        if cookie_val is None and st.session_state["retry_count"] < 2:
            st.session_state["retry_count"] += 1
            st.rerun()

    # 4. Se após as verificações ainda não estiver autenticado, mostra a UI de Login
    if not st.session_state.get("authenticated", False):
        st.markdown(f"<h1 style='text-align: center; margin-top: 50px;'>{t('login.title')}</h1>", unsafe_allow_html=True)

        _, col2, _ = st.columns([1, 1, 1])
        with col2:
            with st.form("login_form"):
                user_input = st.text_input(t("login.username"), key="input_user")
                pw_input = st.text_input(t("login.password"), type="password", key="input_pw")
                submit = st.form_submit_button(t("login.submit"), use_container_width=True)

                if submit:
                    env_user = os.environ.get("DASHBOARD_USER", "admin")
                    env_pw = os.environ.get("DASHBOARD_PASSWORD", "mudar123")
                    
                    if user_input == env_user and pw_input == env_pw:
                        st.session_state["authenticated"] = True
                        st.session_state["retry_count"] = 0 # Reset do contador
                        
                        # Grava o cookie de forma persistente no browser
                        ctrl.set(_AUTH_COOKIE, _AUTH_TOKEN)
                        st.success(t("login.success"))
                        st.rerun()
                    else:
                        st.error(t("login.error"))
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
    st.title(t("dashboard.title"))

    def load_data():
        query = f"""
            SELECT
                subject        AS "{t('dashboard.col_subject')}",
                classification_label AS "{t('dashboard.col_category')}",
                CASE
                    WHEN sender_type = 'company' THEN '🏢 ' || COALESCE(sender_name, from_address)
                    WHEN sender_type = 'person'  THEN '👤 ' || COALESCE(sender_name, from_address)
                    ELSE COALESCE(sender_name, from_address)
                END            AS "{t('dashboard.col_sender')}",
                ai_confidence  AS "{t('dashboard.col_confidence')}",
                ai_source      AS "{t('dashboard.col_source')}",
                processing_time_seconds AS "{t('dashboard.col_time')}",
                processed_at   AS "{t('dashboard.col_date')}"
            FROM emails
            WHERE status = 'moved'
            ORDER BY processed_at DESC LIMIT 200
        """
        return pd.read_sql(query, engine)

    try:
        df = load_data()

        col_confidence = t("dashboard.col_confidence")
        col_time       = t("dashboard.col_time")

        if df.empty:
            st.warning(t("dashboard.warning_no_emails"))
            return

        c1, c2, c3 = st.columns(3)
        c1.metric(t("dashboard.metric_total_emails"), len(df))
        c2.metric(t("dashboard.metric_avg_confidence"), f"{df[col_confidence].mean()*100:.1f}%")
        _raw_times = pd.to_numeric(df[col_time], errors='coerce')
        avg_time = _raw_times.dropna().mean()
        c3.metric(t("dashboard.metric_avg_time"), f"{avg_time:.2f}s" if pd.notna(avg_time) else "—")

        st.divider()

        col_category = t("dashboard.col_category")
        col_source   = t("dashboard.col_source")

        g1, g2 = st.columns(2)
        with g1:
            st.plotly_chart(
                px.pie(df, names=col_category, hole=0.4, title=t("dashboard.chart_folder_dist")),
                width='stretch',
            )
        with g2:
            st.plotly_chart(
                px.histogram(df, x=col_source, color=col_source, title=t("dashboard.chart_decisions")),
                width='stretch',
            )

        st.subheader(t("dashboard.recent_records"))
        col_subject    = t("dashboard.col_subject")
        col_confidence = t("dashboard.col_confidence")
        col_time       = t("dashboard.col_time")
        df[col_subject]    = df[col_subject].apply(_decode_mime_header)
        df[col_confidence] = (df[col_confidence] * 100).round(0).astype(int)
        df[col_time]       = df[col_time].apply(
            lambda v: f"{float(v):.2f}s" if pd.notna(v) else "—"
        )
        st.dataframe(
            df,
            width='stretch',
            hide_index=True,
            column_config={
                col_confidence: st.column_config.NumberColumn(format="%d%%"),
            },
        )

    except Exception as e:
        st.error(t("dashboard.error_db", error=e))
        st.info(t("dashboard.error_db_hint"))


# ---------------------------------------------------------------------------
# Page: Email Accounts
# ---------------------------------------------------------------------------

def page_email_accounts(engine, settings):
    st.title(t("page.accounts.title"))

    # ---- list accounts ----
    def load_accounts():
        return pd.read_sql(
            "SELECT id, tenant_id, provider, email, imap_host, imap_port, username, active "
            "FROM email_accounts ORDER BY id",
            engine,
        )

    df = load_accounts()

    st.subheader(t("page.accounts.accounts_header"))

    if df.empty:
        st.info(t("page.accounts.no_accounts"))
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
    st.subheader(t("page.accounts.add_header"))
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
            submitted = st.form_submit_button("Add IMAP Account", width='stretch')

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
            submitted_o = st.form_submit_button("Add Outlook Account", width='stretch')

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

_DEFAULT_FOLDERS = ["Invoices", "Work", "Personal", "Marketing", "Spam", "Other"]


def _get_folder_names(engine) -> list[str]:
    """Load active folder names from DB. Falls back to defaults if table not yet migrated."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM folders WHERE is_active = true ORDER BY name")
            )
            names = [r[0] for r in rows]
        return names if names else list(_DEFAULT_FOLDERS)
    except Exception:
        return list(_DEFAULT_FOLDERS)


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
    st.title(t("page.rules.title"))
    st.caption(t("page.rules.caption"))
    FOLDERS = _get_folder_names(engine)

    try:
        df = pd.read_sql(
            """
            SELECT id, tenant_id, conditions, min_match, actions,
                   hit_count, active, created_at
            FROM learned_rules
            ORDER BY active DESC, hit_count DESC, created_at DESC
            """,
            engine,
        )
    except Exception as e:
        st.error(t("page.rules.load_error", error=e))
        return

    if df.empty:
        st.info(t("page.rules.no_rules"))
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
                    conditions = row["conditions"] or []
                    _type_icon = {"sender_email": "📧", "sender_domain": "🌐", "keyword": "🔑"}
                    cond_lines = []
                    for c in conditions:
                        icon = _type_icon.get(c.get("type", ""), "❓")
                        cond_lines.append(f"{icon} `{c.get('value','')}`")
                    min_match = int(row.get("min_match") or 1)
                    cond_text = "  \n".join(cond_lines) if cond_lines else "_no conditions_"
                    st.markdown(
                        f"{cond_text}  \n"
                        f"_match ≥ {min_match}_  \n"
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
                                "conditions": row["conditions"],
                                "active": not is_active,
                            },
                        )
                        st.rerun()

                # Edit expander
                with st.expander(f"✏️ Edit rule #{rule_id}"):
                    with st.form(f"edit_rule_{rule_id}"):
                        st.caption("Conditions (one per line, format: `type:value`)")
                        existing_conditions = row["conditions"] or []
                        cond_text_default = "\n".join(
                            f"{c.get('type','')}:{c.get('value','')}"
                            for c in existing_conditions
                        )
                        cond_text_input = st.text_area(
                            "Conditions",
                            value=cond_text_default,
                            help="Types: sender_email, sender_domain, keyword\nExample:\nsender_email:invoices@company.pt\nkeyword:Fatura\nkeyword:pagamento",
                            key=f"cond_{rule_id}",
                        )
                        new_min_match = st.number_input(
                            "Min conditions to match",
                            min_value=1, max_value=10,
                            value=int(row.get("min_match") or 1),
                            key=f"min_{rule_id}",
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

                        if st.form_submit_button("💾 Save changes", width='stretch'):
                            # Parse conditions from text area
                            new_conditions = []
                            for line in cond_text_input.strip().splitlines():
                                line = line.strip()
                                if ":" in line:
                                    ctype, _, cval = line.partition(":")
                                    if ctype.strip() and cval.strip():
                                        new_conditions.append({"type": ctype.strip(), "value": cval.strip()})

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
                                        "SET conditions = CAST(:cond AS jsonb), min_match = :mm, actions = CAST(:ac AS jsonb) "
                                        "WHERE id = :id"
                                    ),
                                    {
                                        "cond": _json.dumps(new_conditions),
                                        "mm": int(new_min_match),
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
                                    "conditions": new_conditions,
                                    "min_match": int(new_min_match),
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
                                "conditions": row["conditions"],
                            },
                        )
                        st.rerun()

    st.divider()

    # ── Add rule manually ──
    st.subheader(t("page.rules.add_header"))
    with st.form("add_rule_form"):
        a_tenant = st.number_input("Tenant ID", min_value=1, value=1, step=1)
        st.caption("Conditions — one per line, format `type:value`")
        a_cond_text = st.text_area(
            "Conditions",
            placeholder="sender_email:invoices@company.pt\nkeyword:Fatura\nkeyword:pagamento",
            help="Types: sender_email, sender_domain, keyword",
        )
        a_min_match = st.number_input("Min conditions to match", min_value=1, max_value=10, value=1, step=1)
        ac1, ac2 = st.columns(2)
        a_folder = ac1.selectbox("Move to folder", ["(none)"] + FOLDERS)
        a_pdf = ac2.text_input("Export PDF path (blank = off)", placeholder="Company/{year}/{month}/")
        submitted = st.form_submit_button("Add Rule", width='stretch')

    if submitted:
        import json as _json
        new_conditions = []
        for line in a_cond_text.strip().splitlines():
            line = line.strip()
            if ":" in line:
                ctype, _, cval = line.partition(":")
                if ctype.strip() and cval.strip():
                    new_conditions.append({"type": ctype.strip(), "value": cval.strip()})

        if not new_conditions:
            st.error("At least one condition is required.")
        else:
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
                                "(tenant_id, conditions, min_match, actions, active) "
                                "VALUES (:tid, CAST(:cond AS jsonb), :mm, CAST(:ac AS jsonb), true)"
                            ),
                            {
                                "tid": int(a_tenant),
                                "cond": _json.dumps(new_conditions),
                                "mm": int(a_min_match),
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
                        details={"conditions": new_conditions, "min_match": int(a_min_match), "actions": new_actions},
                        tenant_id=int(a_tenant),
                    )
                    st.success(f"Rule added with {len(new_conditions)} condition(s).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Page: Folders
# ---------------------------------------------------------------------------

def page_folders(engine, settings):
    st.title(t("page.folders.title"))
    st.caption(t("page.folders.caption"))

    # Show IMAP rename feedback persisted across st.rerun()
    if "_folder_imap_msg" in st.session_state:
        msg, level = st.session_state.pop("_folder_imap_msg")
        if level == "info":
            st.info(msg)
        else:
            st.warning(msg)

    try:
        df = pd.read_sql(
            "SELECT id, name, is_active, created_at FROM folders ORDER BY name",
            engine,
        )
    except Exception as e:
        st.error(t("page.folders.load_error", error=e))
        return

    st.metric("Total folders", len(df))

    for _, row in df.iterrows():
        folder_id = int(row["id"])
        is_active = bool(row["is_active"])

        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])

            with c1:
                st.markdown(f"**📁 {row['name']}**")
                st.caption(f"Created: {str(row['created_at'])[:10]}")

            with c2:
                st.markdown(f"{'🟢 Active' if is_active else '🔴 Disabled'}")

            with c3:
                toggle_label = "Disable" if is_active else "Enable"
                if st.button(toggle_label, key=f"folder_toggle_{folder_id}"):
                    with engine.begin() as conn:
                        conn.execute(
                            text("UPDATE folders SET is_active = :val WHERE id = :id"),
                            {"val": not is_active, "id": folder_id},
                        )
                    from app.core.audit import log_audit_sync
                    log_audit_sync(
                        engine,
                        actor_type="dashboard",
                        actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                        action="folder.toggled",
                        entity_type="folder",
                        entity_id=folder_id,
                        details={"name": row["name"], "active": not is_active},
                    )
                    st.rerun()

            with c4:
                if st.button("Delete", key=f"folder_delete_{folder_id}"):
                    # Only delete if no emails reference this folder
                    with engine.connect() as conn:
                        count = conn.execute(
                            text("SELECT COUNT(*) FROM emails WHERE classification_label = :name"),
                            {"name": row["name"]},
                        ).scalar()
                    if count > 0:
                        st.error(f"Cannot delete — {count} email(s) use this folder. Disable it instead.")
                    else:
                        folder_name = row["name"]
                        print(f"[folders] delete: '{folder_name}'", flush=True)
                        with engine.begin() as conn:
                            conn.execute(
                                text("DELETE FROM folders WHERE id = :id"),
                                {"id": folder_id},
                            )
                        print(f"[folders] folder '{folder_name}' deleted from DB", flush=True)

                        # Delete IMAP folder on all active accounts
                        from app.ingestion.imap.client import connect_imap
                        from app.core.crypto import decrypt_secret
                        imap_results = []
                        try:
                            accounts_df = pd.read_sql(
                                "SELECT id, imap_host, imap_port, username, password_encrypted "
                                "FROM email_accounts WHERE active = true AND provider = 'imap'",
                                engine,
                            )
                            print(f"[folders] IMAP delete '{folder_name}': {len(accounts_df)} account(s)", flush=True)
                            for _, acc in accounts_df.iterrows():
                                try:
                                    password = decrypt_secret(settings.master_key, acc["password_encrypted"])
                                    conn_imap = connect_imap(
                                        acc["imap_host"],
                                        int(acc["imap_port"] or 993),
                                        acc["username"],
                                        password,
                                    )
                                    from app.ingestion.imap.client import _get_imap_separator, _normalize_folder, _list_imap_folder_names
                                    sep = _get_imap_separator(conn_imap)
                                    imap_name = _normalize_folder(folder_name, sep)
                                    existing = _list_imap_folder_names(conn_imap)
                                    print(f"[folders] IMAP sep='{sep}' imap_name='{imap_name}' exists={imap_name in existing}", flush=True)
                                    if imap_name in existing:
                                        status, resp = conn_imap.delete(imap_name)
                                        ok = status == "OK"
                                        print(f"[folders] IMAP delete status={status} resp={resp}", flush=True)
                                    else:
                                        ok = False
                                    conn_imap.logout()
                                    result_line = f"{'✅' if ok else '⚠️ not in IMAP:'} {acc['username']}"
                                    imap_results.append(result_line)
                                    print(f"[folders] IMAP delete result: {result_line}", flush=True)
                                except Exception as imap_e:
                                    result_line = f"❌ {acc['username']}: {imap_e}"
                                    imap_results.append(result_line)
                                    print(f"[folders] IMAP delete error: {result_line}", flush=True)
                        except Exception as e:
                            imap_results.append(f"⚠️ Could not load IMAP accounts: {e}")
                            print(f"[folders] IMAP accounts load error: {e}", flush=True)

                        from app.core.audit import log_audit_sync
                        log_audit_sync(
                            engine,
                            actor_type="dashboard",
                            actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                            action="folder.deleted",
                            entity_type="folder",
                            entity_id=folder_id,
                            details={"name": folder_name},
                        )
                        if imap_results:
                            all_ok = all(r.startswith("✅") for r in imap_results)
                            bullets = "\n".join(f"- {r}" for r in imap_results)
                            msg = f"Folder '{folder_name}' deleted from DB.\n\nIMAP results:\n\n{bullets}"
                            if not all_ok:
                                msg += "\n\n⚠️ Could not delete folder on some accounts (may not exist in IMAP)."
                            st.session_state["_folder_imap_msg"] = (msg, "info" if all_ok else "warning")
                        st.rerun()

            # Rename expander
            with st.expander(f"✏️ Rename '{row['name']}'"):
                with st.form(f"rename_folder_{folder_id}"):
                    new_name = st.text_input("New folder name", value=row["name"], key=f"rename_input_{folder_id}")
                    submitted = st.form_submit_button("Rename", width='stretch')

                if submitted:
                    print(f"[folders] rename form submitted: old='{row['name']}' new='{new_name}'", flush=True)
                    new_name = new_name.strip()
                    old_name = row["name"]
                    if not new_name:
                        st.error("Folder name cannot be empty.")
                    elif new_name == old_name:
                        st.info("Name unchanged.")
                    else:
                        try:
                            # 1. Update DB folder name
                            with engine.begin() as conn:
                                conn.execute(
                                    text("UPDATE folders SET name = :new WHERE id = :id"),
                                    {"new": new_name, "id": folder_id},
                                )
                                # 2. Update all emails that used the old label
                                conn.execute(
                                    text("UPDATE emails SET classification_label = :new WHERE classification_label = :old"),
                                    {"new": new_name, "old": old_name},
                                )

                            # 3. Rename IMAP folder on all active IMAP accounts
                            from app.ingestion.imap.client import connect_imap, rename_imap_folder
                            from app.core.crypto import decrypt_secret
                            imap_results = []
                            try:
                                accounts_df = pd.read_sql(
                                    "SELECT id, imap_host, imap_port, username, password_encrypted "
                                    "FROM email_accounts WHERE active = true AND provider = 'imap'",
                                    engine,
                                )
                                print(f"[folders] IMAP rename '{old_name}'→'{new_name}': {len(accounts_df)} account(s) found", flush=True)
                                if accounts_df.empty:
                                    imap_results.append("⚠️ No active IMAP accounts found in DB.")
                                for _, acc in accounts_df.iterrows():
                                    try:
                                        password = decrypt_secret(settings.master_key, acc["password_encrypted"])
                                        conn_imap = connect_imap(
                                            acc["imap_host"],
                                            int(acc["imap_port"] or 993),
                                            acc["username"],
                                            password,
                                        )
                                        ok = rename_imap_folder(conn_imap, old_name, new_name)
                                        conn_imap.logout()
                                        result_line = f"{'✅' if ok else '⚠️ not found:'} {acc['username']}"
                                        imap_results.append(result_line)
                                        print(f"[folders] IMAP rename result: {result_line}", flush=True)
                                    except Exception as imap_e:
                                        result_line = f"❌ {acc['username']}: {imap_e}"
                                        imap_results.append(result_line)
                                        print(f"[folders] IMAP rename error: {result_line}", flush=True)
                            except Exception as e:
                                imap_results.append(f"⚠️ Could not load IMAP accounts: {e}")
                                print(f"[folders] IMAP accounts load error: {e}", flush=True)

                            from app.core.audit import log_audit_sync
                            log_audit_sync(
                                engine,
                                actor_type="dashboard",
                                actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                                action="folder.renamed",
                                entity_type="folder",
                                entity_id=folder_id,
                                details={"old": old_name, "new": new_name},
                            )
                            all_ok = bool(imap_results) and all(r.startswith("✅") for r in imap_results)
                            bullets = "\n".join(f"- {r}" for r in imap_results)
                            msg = f"Renamed '{old_name}' → '{new_name}' in DB.\n\nIMAP results:\n\n{bullets}"
                            if not all_ok:
                                msg += "\n\n⚠️ Folder not found or rename failed. If no emails were ever moved there the IMAP label may not exist yet — future moves will use the new name."
                            st.session_state["_folder_imap_msg"] = (msg, "info" if all_ok else "warning")
                            st.rerun()
                        except Exception as e:
                            print(f"[folders] rename exception: {e}", flush=True)
                            st.error(f"Rename failed: {e}")

    st.divider()

    # ── Add folder ──
    st.subheader(t("page.folders.add_header"))
    with st.form("add_folder_form"):
        new_folder_name = st.text_input(t("page.folders.folder_name"), placeholder="Legal")
        submitted_add = st.form_submit_button("Add Folder", width='stretch')

    if submitted_add:
        name = new_folder_name.strip()
        if not name:
            st.error("Folder name is required.")
        else:
            try:
                print(f"[folders] create form submitted: name='{name}'", flush=True)
                with engine.begin() as conn:
                    conn.execute(
                        text("INSERT INTO folders (name, is_active) VALUES (:name, true)"),
                        {"name": name},
                    )
                print(f"[folders] folder '{name}' created in DB", flush=True)

                # Create IMAP folder on all active accounts
                from app.ingestion.imap.client import connect_imap, ensure_folder_exists
                from app.core.crypto import decrypt_secret
                imap_results = []
                try:
                    accounts_df = pd.read_sql(
                        "SELECT id, imap_host, imap_port, username, password_encrypted "
                        "FROM email_accounts WHERE active = true AND provider = 'imap'",
                        engine,
                    )
                    print(f"[folders] IMAP create '{name}': {len(accounts_df)} account(s) found", flush=True)
                    if accounts_df.empty:
                        imap_results.append("⚠️ No active IMAP accounts found.")
                    for _, acc in accounts_df.iterrows():
                        try:
                            password = decrypt_secret(settings.master_key, acc["password_encrypted"])
                            conn_imap = connect_imap(
                                acc["imap_host"],
                                int(acc["imap_port"] or 993),
                                acc["username"],
                                password,
                            )
                            ensure_folder_exists(conn_imap, name)
                            conn_imap.logout()
                            result_line = f"✅ {acc['username']}"
                            imap_results.append(result_line)
                            print(f"[folders] IMAP create result: {result_line}", flush=True)
                        except Exception as imap_e:
                            result_line = f"❌ {acc['username']}: {imap_e}"
                            imap_results.append(result_line)
                            print(f"[folders] IMAP create error: {result_line}", flush=True)
                except Exception as e:
                    imap_results.append(f"⚠️ Could not load IMAP accounts: {e}")
                    print(f"[folders] IMAP accounts load error: {e}", flush=True)

                from app.core.audit import log_audit_sync
                log_audit_sync(
                    engine,
                    actor_type="dashboard",
                    actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                    action="folder.created",
                    entity_type="folder",
                    details={"name": name},
                )
                all_ok = bool(imap_results) and all(r.startswith("✅") for r in imap_results)
                bullets = "\n".join(f"- {r}" for r in imap_results)
                msg = f"Folder '{name}' created.\n\nIMAP results:\n\n{bullets}"
                if not all_ok:
                    msg += "\n\n⚠️ Could not create folder on some accounts."
                st.session_state["_folder_imap_msg"] = (msg, "info" if all_ok else "warning")
                st.rerun()
            except Exception as e:
                print(f"[folders] create exception: {e}", flush=True)
                st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Page: Audit Log
# ---------------------------------------------------------------------------

def page_invoices(engine):
    st.title(t("dashboard.invoices.title"))
    st.caption(t("dashboard.invoices.caption"))

    from datetime import datetime, timezone, timedelta

    # ── Filters ──
    col_months, col_nif, col_search = st.columns([1, 2, 2])
    months_back = col_months.number_input(t("dashboard.invoices.filter_months"), min_value=1, max_value=24, value=3)
    nif_filter = col_nif.text_input(t("dashboard.invoices.filter_nif"), placeholder="123456789")
    search_filter = col_search.text_input(t("dashboard.invoices.filter_search"), placeholder="FT 2026/1")

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(months_back) * 30)

    conditions = ["i.extracted_at >= %(cutoff)s"]
    params: dict = {"cutoff": cutoff}

    if nif_filter:
        conditions.append("i.nif_seller ILIKE %(nif)s")
        params["nif"] = f"%{nif_filter}%"

    if search_filter:
        conditions.append("(i.invoice_number ILIKE %(search)s OR i.atcud ILIKE %(search)s)")
        params["search"] = f"%{search_filter}%"

    where = " AND ".join(conditions)

    try:
        df = pd.read_sql(
            f"""
            SELECT
                i.id,
                i.email_id,
                e.subject,
                e.from_address,
                i.document_type,
                i.nif_seller,
                i.nif_buyer,
                i.invoice_number,
                i.atcud,
                i.invoice_date,
                i.taxable_amount,
                i.vat_amount,
                i.total_amount,
                i.mb_entidade,
                i.mb_referencia,
                i.mb_valor,
                i.mb_data_limite,
                i.extracted_at
            FROM invoices i
            LEFT JOIN emails e ON e.id = i.email_id
            WHERE {where}
            ORDER BY i.invoice_date DESC NULLS LAST, i.extracted_at DESC
            """,
            engine,
            params=params,
        )
    except Exception as e:
        st.error(t("dashboard.invoices.error_load", error=e))
        return

    if df.empty:
        st.info(t("dashboard.invoices.empty"))
        return

    # ── KPI row ──
    total_gross    = pd.to_numeric(df["total_amount"],   errors="coerce").sum()
    total_vat      = pd.to_numeric(df["vat_amount"],     errors="coerce").sum()
    total_taxable  = pd.to_numeric(df["taxable_amount"], errors="coerce").sum()
    unique_sellers = df["nif_seller"].nunique()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(t("dashboard.invoices.metric_total_gross"),  f"€ {total_gross:,.2f}")
    k2.metric(t("dashboard.invoices.metric_total_vat"),    f"€ {total_vat:,.2f}")
    k3.metric(t("dashboard.invoices.metric_taxable"),      f"€ {total_taxable:,.2f}")
    k4.metric(t("dashboard.invoices.metric_sellers"),      unique_sellers)

    st.divider()

    # ── Option B: Total card (left) + Top 5 + Others bar chart (right) ──
    chart_left, chart_right = st.columns([1, 2])

    with chart_left:
        st.metric(t("dashboard.invoices.chart_grand_total"), f"€ {total_gross:,.2f}")
        st.caption(t("dashboard.invoices.chart_sellers_count", count=unique_sellers))

    with chart_right:
        if not df["nif_seller"].isna().all():
            seller_amounts = (
                df.groupby("nif_seller")["total_amount"]
                .apply(lambda x: pd.to_numeric(x, errors="coerce").sum())
                .sort_values(ascending=False)
            )
            top5      = seller_amounts.head(5)
            others    = seller_amounts.iloc[5:].sum()
            labels    = list(top5.index)
            values    = list(top5.values)
            colors    = ["#4a9eff"] * len(top5)
            if others > 0:
                labels.append(t("dashboard.invoices.others_label"))
                values.append(others)
                colors.append("#888888")

            chart_df = pd.DataFrame({
                "nif_seller": labels,
                "total_col":  values,
                "color":      colors,
            })

            fig = px.bar(
                chart_df,
                x="nif_seller",
                y="total_col",
                title=t("dashboard.invoices.chart_title"),
                labels={
                    "nif_seller": t("dashboard.invoices.chart_x_label"),
                    "total_col":  "Total (€)",
                },
                color="color",
                color_discrete_map="identity",
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    # ── Table ──
    display = df[[
        c for c in [
            "invoice_date", "document_type", "invoice_number", "atcud",
            "nif_seller", "nif_buyer",
            "taxable_amount", "vat_amount", "total_amount",
            "mb_entidade", "mb_referencia", "mb_valor", "mb_data_limite",
            "subject", "email_id",
        ] if c in df.columns
    ]].copy()

    for col in ["taxable_amount", "vat_amount", "total_amount"]:
        display[col] = pd.to_numeric(display[col], errors="coerce").apply(
            lambda v: f"€ {v:,.2f}" if pd.notna(v) else "—"
        )
    display["mb_valor"] = pd.to_numeric(display["mb_valor"], errors="coerce").apply(
        lambda v: f"€ {v:,.2f}" if pd.notna(v) else "—"
    )
    display["invoice_date"] = pd.to_datetime(display["invoice_date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("—")

    st.dataframe(
        display.rename(columns={
            "invoice_date":   t("dashboard.invoices.col_date"),
            "document_type":  t("dashboard.invoices.col_doc_type"),
            "invoice_number": t("dashboard.invoices.col_invoice_num"),
            "atcud":          "ATCUD",
            "nif_seller":     t("dashboard.invoices.col_nif_seller"),
            "nif_buyer":      t("dashboard.invoices.col_nif_buyer"),
            "taxable_amount": t("dashboard.invoices.col_taxable"),
            "vat_amount":     t("dashboard.invoices.col_vat"),
            "total_amount":   t("dashboard.invoices.col_total"),
            "mb_entidade":    t("dashboard.invoices.col_mb_entity"),
            "mb_referencia":  t("dashboard.invoices.col_mb_ref"),
            "mb_valor":       t("dashboard.invoices.col_mb_amount"),
            "mb_data_limite": t("dashboard.invoices.col_mb_due"),
            "subject":        t("dashboard.invoices.col_subject"),
            "email_id":       "Email ID",
        }),
        use_container_width=True,
        hide_index=True,
    )

    # ── CSV export ──
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Export CSV", csv, "invoices.csv", "text/csv")

    # ── Delete ──
    st.divider()
    with st.expander("🗑️ Delete invoices"):
        # Build a human-readable label for each row
        def _row_label(row) -> str:
            num  = row.get("invoice_number") or row.get("atcud") or f"ID {row['id']}"
            date = row.get("invoice_date") or "?"
            nif  = row.get("nif_seller") or "?"
            return f"{num}  ·  {date}  ·  NIF {nif}"

        options: dict[str, int] = {
            _row_label(row): int(row["id"])
            for _, row in df.iterrows()
        }

        selected_labels = st.multiselect(
            "Select invoices to delete",
            options=list(options.keys()),
            placeholder="Choose one or more invoices…",
        )

        if selected_labels:
            ids_to_delete = [options[label] for label in selected_labels]
            st.warning(
                f"⚠️ {len(ids_to_delete)} invoice record(s) will be permanently deleted. "
                "This does **not** delete the original email."
            )
            confirmed = st.checkbox("Yes, I want to delete these records")
            if confirmed:
                if st.button("🗑️ Delete selected", type="primary"):
                    try:
                        with engine.begin() as conn:
                            conn.execute(
                                text("DELETE FROM invoices WHERE id = ANY(:ids)"),
                                {"ids": ids_to_delete},
                            )
                        st.success(f"✅ Deleted {len(ids_to_delete)} invoice(s).")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Delete failed: {e}")


def page_settings(engine):
    st.title(t("page.settings.title"))

    try:
        from app.core.system_settings import (
            get_setting, set_setting,
            FOLDER_STRUCTURE_KEY, FOLDER_STRUCTURE_DEFAULT, FOLDER_TOKENS,
        )
    except ImportError as e:
        st.error(f"❌ Could not import system_settings: {e}")
        return

    # ── Folder Structure ──────────────────────────────────────────────────────
    st.subheader(t("page.settings.archive_header"))

    current = get_setting(engine, FOLDER_STRUCTURE_KEY)

    with st.expander(t("page.settings.tokens_expander"), expanded=False):
        for token, desc in FOLDER_TOKENS:
            st.markdown(f"- `{token}` — {desc}")

    st.markdown(f"**Files root:** `{os.environ.get('FILES_ROOT', '/files')}`")

    with st.form("folder_structure_form"):
        new_template = st.text_input(
            "Path template",
            value=current,
            help="Segments separated by /. Example: {company}/{year}/{month}-{month_name}/{category}/{supplier}",
        )

        col_save, col_reset = st.columns([3, 1])
        save  = col_save.form_submit_button("💾 Save")
        reset = col_reset.form_submit_button("↩️ Reset to default")

        if save:
            val = new_template.strip().strip("/")
            if not val:
                st.error(t("page.settings.template_empty"))
            else:
                import string
                tokens_used = [f[1] for f in string.Formatter().parse(val) if f[1]]
                valid = {tk.strip("{}") for tk, _ in FOLDER_TOKENS}
                bad = [tk for tk in tokens_used if tk not in valid]
                if bad:
                    st.error(f"Unknown token(s): {', '.join('{'+tk+'}' for tk in bad)}")
                else:
                    try:
                        set_setting(engine, FOLDER_STRUCTURE_KEY, val)
                        st.success(t("page.settings.saved"))
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")

        if reset:
            try:
                set_setting(engine, FOLDER_STRUCTURE_KEY, FOLDER_STRUCTURE_DEFAULT)
                st.success(t("page.settings.reset_done"))
                st.rerun()
            except Exception as e:
                st.error(f"❌ {e}")

    st.markdown(t("page.settings.preview_label"))
    preview_tokens = {
        "company": "Acme Lda",
        "year": "2025",
        "month": "04",
        "month_name": "April",
        "category": "Faturas",
        "supplier": "EDP Comercial",
    }
    tpl = current.strip("/")
    try:
        preview = tpl.format_map(preview_tokens)
        files_root = os.environ.get("FILES_ROOT", "/files")
        st.code(f"{files_root}/{preview}/invoice.pdf")
    except Exception as e:
        st.warning(f"Preview error: {e}")


def page_companies(engine):
    st.title(t("page.companies.title"))
    st.caption(t("page.companies.caption"))

    # ── Add company form ──────────────────────────────────────────────────────
    with st.expander(t("page.companies.add_header"), expanded=False):
        with st.form("add_company_form", clear_on_submit=True):
            col_name, col_nif = st.columns([3, 2])
            new_name  = col_name.text_input("Company Name *", placeholder="Acme Lda")
            new_nif   = col_nif.text_input("NIF *", placeholder="123456789")
            new_notes = st.text_input(t("page.companies.notes_label"), placeholder="Optional description")
            submitted = st.form_submit_button("Add Company")
            if submitted:
                if not new_name.strip() or not new_nif.strip():
                    st.error(t("page.companies.required_error"))
                else:
                    try:
                        with engine.connect() as conn:
                            conn.execute(
                                text("INSERT INTO companies (name, nif, notes) VALUES (:name, :nif, :notes)"),
                                {"name": new_name.strip(), "nif": new_nif.strip(), "notes": new_notes.strip() or None},
                            )
                            conn.commit()
                        st.success(f"✅ Added {new_name.strip()}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")

    # ── Company list ──────────────────────────────────────────────────────────
    try:
        df = pd.read_sql(
            "SELECT id, name, nif, active, notes, created_at FROM companies ORDER BY name",
            engine,
        )
    except Exception as e:
        st.error(t("page.companies.load_error", error=e))
        return

    if df.empty:
        st.info(t("page.companies.no_companies"))
        return

    st.metric("Total companies", len(df))

    for _, row in df.iterrows():
        cid   = int(row["id"])
        name  = row["name"]
        nif   = row["nif"]
        active = bool(row["active"])
        notes = row["notes"] or ""

        status_icon = "🟢" if active else "🔴"
        with st.expander(f"{status_icon} {name}  —  NIF: {nif}", expanded=False):
            with st.form(f"edit_company_{cid}"):
                col_n, col_v = st.columns([3, 2])
                edit_name  = col_n.text_input("Name", value=name, key=f"en_{cid}")
                edit_nif   = col_v.text_input("NIF",  value=nif,  key=f"ev_{cid}")
                edit_notes = st.text_input("Notes", value=notes,  key=f"eno_{cid}")
                edit_active = st.checkbox("Active", value=active, key=f"ea_{cid}")

                col_save, col_del = st.columns([3, 1])
                save_clicked   = col_save.form_submit_button("💾 Save changes")
                delete_clicked = col_del.form_submit_button("🗑️ Delete", type="secondary")

                if save_clicked:
                    if not edit_name.strip() or not edit_nif.strip():
                        st.error(t("page.companies.required_error"))
                    else:
                        try:
                            with engine.connect() as conn:
                                conn.execute(
                                    text("""
                                        UPDATE companies
                                        SET name=:name, nif=:nif, notes=:notes, active=:active
                                        WHERE id=:id
                                    """),
                                    {
                                        "name": edit_name.strip(),
                                        "nif": edit_nif.strip(),
                                        "notes": edit_notes.strip() or None,
                                        "active": edit_active,
                                        "id": cid,
                                    },
                                )
                                conn.commit()
                            st.success(t("page.settings.saved"))
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ {e}")

                if delete_clicked:
                    try:
                        with engine.connect() as conn:
                            conn.execute(text("DELETE FROM companies WHERE id=:id"), {"id": cid})
                            conn.commit()
                        st.success(t("page.companies.deleted", name=name))
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")


def page_audit_log(engine):
    st.title(t("page.audit.title"))

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
            st.info(t("page.audit.no_events"))
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
            width='stretch',
            hide_index=True,
            column_config={
                "Time": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss"),
                "Details": st.column_config.TextColumn(width="large"),
            },
        )
    except Exception as e:
        st.error(t("page.audit.load_error", error=e))
        st.info(t("page.audit.migrations_hint"))


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
    st.sidebar.title(t("sidebar.title"))
    st.sidebar.info(t("sidebar.model", model=settings.llm_model))
    st.sidebar.info(t("sidebar.inbox", folder=settings.inbox_folder))

    # Operation Mode selector
    st.sidebar.markdown("---")
    st.sidebar.markdown(t("sidebar.op_mode"))
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
            st.sidebar.success(t("sidebar.mode_changed", mode=_selected_mode))
            st.rerun()
        except Exception as _e:
            st.sidebar.error(t("sidebar.mode_error", error=_e))

    st.sidebar.markdown("---")

    _nav_items = [
        t("sidebar.nav_dashboard"),
        t("sidebar.nav_accounts"),
        t("sidebar.nav_rules"),
        t("sidebar.nav_folders"),
        t("sidebar.nav_companies"),
        t("sidebar.nav_invoices"),
        t("sidebar.nav_audit"),
        t("sidebar.nav_settings"),
    ]
    page = st.sidebar.radio(t("sidebar.nav_label"), _nav_items)

    if st.sidebar.button(t("sidebar.logout")):
        st.session_state["authenticated"] = False
        if "_init_complete" in st.session_state:
            del st.session_state["_init_complete"]
        _ctrl = _get_cookie_controller()
        if _ctrl is not None:
            try:
                _ctrl.remove(_AUTH_COOKIE)
            except Exception:
                pass
        st.rerun()

    if page == t("sidebar.nav_dashboard"):
        page_dashboard(engine, settings)
    elif page == t("sidebar.nav_accounts"):
        page_email_accounts(engine, settings)
    elif page == t("sidebar.nav_rules"):
        page_learned_rules(engine, settings)
    elif page == t("sidebar.nav_folders"):
        page_folders(engine, settings)
    elif page == t("sidebar.nav_companies"):
        page_companies(engine)
    elif page == t("sidebar.nav_invoices"):
        page_invoices(engine)
    elif page == t("sidebar.nav_audit"):
        page_audit_log(engine)
    elif page == t("sidebar.nav_settings"):
        page_settings(engine)
