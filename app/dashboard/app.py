import hashlib
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


# ---------------------------------------------------------------------------
# Flash message helpers — survive st.rerun() via session_state
# ---------------------------------------------------------------------------

def _set_flash(level: str, message: str) -> None:
    """Store a feedback message to show on the next render cycle."""
    st.session_state["_flash"] = (level, message)


def _show_flash() -> None:
    """Display and clear the stored flash message (call once per page, at the top)."""
    flash = st.session_state.pop("_flash", None)
    if not flash:
        return
    level, msg = flash
    {"success": st.success, "error": st.error, "warning": st.warning}.get(level, st.info)(msg)


def _page_help(section: str) -> None:
    """Render an ℹ️ help popover button for the given page section."""
    content = t(f"help.{section}")
    label = t("help._label")
    with st.popover(f"ℹ️ {label}"):
        st.markdown(content)


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

_PAGE_SIZE = 25


def _page_controls(page_key: str, total: int, page_size: int = _PAGE_SIZE) -> tuple[int, int]:
    """
    Render prev / next pagination controls and return (current_page, offset).
    The page resets to 0 automatically when page_key changes (e.g. on filter change).
    """
    total_pages = max(1, (total + page_size - 1) // page_size)

    page = int(st.session_state.get(page_key, 0))
    page = max(0, min(page, total_pages - 1))
    st.session_state[page_key] = page

    c_prev, c_info, c_next = st.columns([1, 6, 1])
    with c_prev:
        if st.button("◀", disabled=(page == 0), key=f"{page_key}_prev"):
            st.session_state[page_key] = page - 1
            st.rerun()
    with c_info:
        start = page * page_size + 1
        end   = min((page + 1) * page_size, total)
        st.caption(f"Page **{page + 1}** / {total_pages} — {start}–{end} of {total}")
    with c_next:
        if st.button("▶", disabled=(page >= total_pages - 1), key=f"{page_key}_next"):
            st.session_state[page_key] = page + 1
            st.rerun()

    return page, page * page_size

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
                submit = st.form_submit_button(t("login.submit"), width='stretch')

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
    engine = create_engine(sync_url)
    _backfill_invoice_sender_identity(engine)
    return engine


def _backfill_invoice_sender_identity(engine) -> None:
    """
    One-time backfill: emails that were processed by the invoice-worker before
    sender identity tracking was added end up with sender_type=NULL.
    For any such email that has an invoice record, set sender_type='company'
    and sender_name from the invoice's seller_name or nif_seller.
    Safe to run repeatedly — only updates rows where sender_type IS NULL.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE emails e
                SET
                    sender_type = 'company',
                    sender_name = COALESCE(
                        NULLIF(i.seller_name, ''),
                        NULLIF(i.nif_seller, ''),
                        e.sender_name
                    )
                FROM invoices i
                WHERE i.email_id = e.id
                  AND e.sender_type IS NULL
            """))
    except Exception:
        pass  # table may not exist yet during first boot


# ---------------------------------------------------------------------------
# Page: Dashboard
# ---------------------------------------------------------------------------

def page_dashboard(engine, settings):
    st.title(t("dashboard.title"))
    _page_help("dashboard")

    try:
        # ── 1. Aggregate metrics (single fast query, no row transfer) ──────────
        with engine.connect() as _conn:
            agg = _conn.execute(text(
                "SELECT COUNT(*) AS total, "
                "AVG(ai_confidence) AS avg_conf, "
                "AVG(processing_time_seconds) AS avg_time "
                "FROM emails WHERE status = 'moved'"
            )).fetchone()

        total_emails = int(agg.total or 0)
        if total_emails == 0:
            st.warning(t("dashboard.warning_no_emails"))
            return

        c1, c2, c3 = st.columns(3)
        c1.metric(t("dashboard.metric_total_emails"), total_emails)
        avg_conf = float(agg.avg_conf or 0)
        c2.metric(t("dashboard.metric_avg_confidence"), f"{avg_conf * 100:.1f}%")
        avg_time_val = float(agg.avg_time or 0)
        c3.metric(t("dashboard.metric_avg_time"), f"{avg_time_val:.2f}s" if avg_time_val else "—")

        st.divider()

        # ── 2. Chart data (aggregated, tiny result sets) ───────────────────────
        df_cat = pd.read_sql(
            "SELECT classification_label AS cat, COUNT(*) AS n "
            "FROM emails WHERE status = 'moved' GROUP BY 1 ORDER BY 2 DESC",
            engine,
        )
        df_src = pd.read_sql(
            "SELECT COALESCE(ai_source, 'unknown') AS src, COUNT(*) AS n "
            "FROM emails WHERE status = 'moved' GROUP BY 1",
            engine,
        )

        g1, g2 = st.columns(2)
        with g1:
            st.plotly_chart(
                px.pie(df_cat, names="cat", values="n", hole=0.4,
                       title=t("dashboard.chart_folder_dist")),
                width='stretch',
            )
        with g2:
            st.plotly_chart(
                px.histogram(df_src, x="src", y="n", color="src",
                             title=t("dashboard.chart_decisions")),
                width='stretch',
            )

        # ── 3. Paginated table (only loads current page rows) ─────────────────
        st.subheader(t("dashboard.recent_records"))
        _page, _offset = _page_controls("dashboard_table", total_emails)

        col_subject    = t("dashboard.col_subject")
        col_category   = t("dashboard.col_category")
        col_sender     = t("dashboard.col_sender")
        col_confidence = t("dashboard.col_confidence")
        col_source     = t("dashboard.col_source")
        col_time       = t("dashboard.col_time")
        col_date       = t("dashboard.col_date")

        df_page = pd.read_sql(
            text(f"""
                SELECT
                    subject        AS "{col_subject}",
                    classification_label AS "{col_category}",
                    CASE
                        WHEN sender_type = 'company' THEN '🏢 ' || COALESCE(sender_name, from_address)
                        WHEN sender_type = 'person'  THEN '👤 ' || COALESCE(sender_name, from_address)
                        ELSE COALESCE(sender_name, from_address)
                    END            AS "{col_sender}",
                    ai_confidence  AS "{col_confidence}",
                    ai_source      AS "{col_source}",
                    processing_time_seconds AS "{col_time}",
                    processed_at   AS "{col_date}"
                FROM emails
                WHERE status = 'moved'
                ORDER BY processed_at DESC
                LIMIT {_PAGE_SIZE} OFFSET {_offset}
            """),
            engine,
        )

        df_page[col_subject]    = df_page[col_subject].apply(_decode_mime_header)
        df_page[col_confidence] = (
            pd.to_numeric(df_page[col_confidence], errors="coerce") * 100
        ).round(0).astype("Int64")  # nullable int — handles NaN from invoice-worker emails
        df_page[col_time]       = df_page[col_time].apply(
            lambda v: f"{float(v):.2f}s" if pd.notna(v) else "—"
        )
        st.dataframe(
            df_page,
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
    _show_flash()
    _page_help("accounts")

    # ---- list accounts ----
    def load_accounts():
        return pd.read_sql(
            "SELECT id, tenant_id, provider, email, imap_host, imap_port, username, active, "
            "COALESCE(managed_by, 'ai_worker') AS managed_by "
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
                c1, c2, c3, c4, c5 = st.columns([3, 2, 1, 1, 1])
                c1.markdown(f"**{row['email']}**  \n`{row['provider'].upper()}` · {row['imap_host'] or '—'}")
                c2.markdown(f"Tenant: `{row['tenant_id']}`  \nUser: `{row['username'] or '—'}`")
                status_label = "🟢 " + t("page.accounts.active_label") if row["active"] else "🔴 Inactive"
                c3.markdown(f"<br>{status_label}", unsafe_allow_html=True)

                managed_by = row.get("managed_by") or "ai_worker"
                mode_icon  = "🤖" if managed_by == "ai_worker" else "🧾"
                mode_label = t("page.accounts.mode_ai") if managed_by == "ai_worker" else t("page.accounts.mode_invoice")
                c4.markdown(f"<br>{mode_icon} {mode_label}", unsafe_allow_html=True)

                with c5:
                    st.write("")  # vertical align
                    toggle_label = t("page.accounts.deactivate") if row["active"] else t("page.accounts.activate")
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

                with st.expander(f"⚙️ {t('page.accounts.settings_label')} — {row['email']}"):
                    # Flash message from previous rerun
                    _flash_key = f"_mode_flash_{row['id']}"
                    if st.session_state.get(_flash_key):
                        _flash_type, _flash_msg = st.session_state.pop(_flash_key)
                        if _flash_type == "success":
                            st.success(_flash_msg)
                        else:
                            st.error(_flash_msg)

                    # Worker mode selector
                    worker_options = ["ai_worker", "invoice_worker"]
                    worker_labels  = [t("page.accounts.mode_ai_full"), t("page.accounts.mode_invoice_full")]
                    current_idx = worker_options.index(managed_by) if managed_by in worker_options else 0
                    new_mode = st.radio(
                        t("page.accounts.worker_mode_label"),
                        options=worker_labels,
                        index=current_idx,
                        horizontal=True,
                        key=f"mode_{row['id']}",
                    )
                    if st.button(t("page.accounts.save_mode_btn"), key=f"save_mode_{row['id']}"):
                        new_managed_by = worker_options[worker_labels.index(new_mode)]
                        try:
                            with engine.begin() as conn:
                                conn.execute(
                                    text("UPDATE email_accounts SET managed_by = :mb WHERE id = :id"),
                                    {"mb": new_managed_by, "id": int(row["id"])},
                                )
                            from app.core.audit import log_audit_sync
                            log_audit_sync(
                                engine,
                                actor_type="dashboard",
                                actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                                action="account.managed_by_changed",
                                entity_type="account",
                                entity_id=int(row["id"]),
                                details={"email": row["email"], "managed_by": new_managed_by},
                            )
                            st.session_state[_flash_key] = ("success", t("page.accounts.mode_saved"))
                        except Exception as _e:
                            st.session_state[_flash_key] = ("error", f"❌ {_e}")
                        st.rerun()

                    st.divider()

                    # Password reset
                    new_pw_key = f"new_pw_{row['id']}"
                    new_pw = st.text_input(t("page.accounts.reset_pw_label"), type="password", key=new_pw_key)
                    if st.button(t("page.accounts.save_pw_btn"), key=f"save_pw_{row['id']}"):
                        if not new_pw:
                            st.error(t("page.accounts.pw_empty_error"))
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
                            st.success(t("page.accounts.pw_saved"))

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
                    _set_flash("success", f"✅ IMAP account **{email}** added.")
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
                    _set_flash("success", f"✅ Outlook account **{o_email}** added.")
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
    _show_flash()
    _page_help("rules")
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
        total = len(df)
        active_count = int(df["active"].sum())

        # ── Filters ───────────────────────────────────────────────────────────
        with st.container(border=True):
            fc1, fc2, fc3 = st.columns([3, 2, 1])
            search_q = fc1.text_input(
                t("page.rules.filter_search"),
                placeholder=t("page.rules.filter_search_hint"),
                label_visibility="collapsed",
            )

            # Collect all unique folders from actions across all rules
            _all_folders = set()
            for acts in df["actions"]:
                for a in (acts or []):
                    if a.get("type") == "move_folder" and a.get("folder"):
                        _all_folders.add(a["folder"])
            folder_options = [t("page.rules.filter_all_folders")] + sorted(_all_folders)
            filter_folder = fc2.selectbox(
                t("page.rules.filter_folder"),
                folder_options,
                label_visibility="collapsed",
            )

            status_options = [
                t("page.rules.filter_all_status"),
                t("page.rules.filter_active"),
                t("page.rules.filter_disabled"),
            ]
            filter_status = fc3.selectbox(
                t("page.rules.filter_status"),
                status_options,
                label_visibility="collapsed",
            )

        # Apply filters
        filtered = df.copy()
        if search_q.strip():
            q = search_q.strip().lower()
            def _row_matches(row):
                for c in (row["conditions"] or []):
                    if q in str(c.get("value", "")).lower():
                        return True
                return False
            filtered = filtered[filtered.apply(_row_matches, axis=1)]

        if filter_folder != t("page.rules.filter_all_folders"):
            def _has_folder(row):
                return any(
                    a.get("folder") == filter_folder
                    for a in (row["actions"] or [])
                    if a.get("type") == "move_folder"
                )
            filtered = filtered[filtered.apply(_has_folder, axis=1)]

        if filter_status == t("page.rules.filter_active"):
            filtered = filtered[filtered["active"] == True]
        elif filter_status == t("page.rules.filter_disabled"):
            filtered = filtered[filtered["active"] == False]

        filtered_total = len(filtered)
        st.caption(
            f"{t('page.rules.showing')} **{filtered_total}** / {total} — "
            f"{active_count} {t('page.rules.filter_active').lower()} · "
            f"{total - active_count} {t('page.rules.filter_disabled').lower()}"
        )

        # Pagination — key includes filter fingerprint so page resets on filter change
        _rules_fhash = hashlib.md5(
            str((search_q, filter_folder, filter_status)).encode()
        ).hexdigest()[:8]
        _rules_page, _rules_offset = _page_controls(
            f"rules_page_{_rules_fhash}", filtered_total
        )
        filtered_page = filtered.iloc[_rules_offset : _rules_offset + _PAGE_SIZE]

        for _, row in filtered_page.iterrows():
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
                    if cond_lines:
                        cond_text = "  \n".join(cond_lines)
                        st.markdown(
                            f"{cond_text}  \n"
                            f"_match ≥ {min_match}_  \n"
                            f"{_actions_summary(row['actions'])}"
                        )
                    else:
                        st.error("⚠️ **No conditions** — this rule is inactive (skipped by classifier). Edit it to add at least one condition, or delete it.")

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

                            if not new_conditions:
                                st.error("❌ At least one condition is required. A rule with no conditions would match every email.")
                            else:
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
                                _set_flash("success", "✅ Rule updated.")
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
                        _set_flash("success", f"🗑️ Rule #{rule_id} deleted.")
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
                    _set_flash("success", f"✅ Rule added with {len(new_conditions)} condition(s).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Page: Folders
# ---------------------------------------------------------------------------

def page_folders(engine, settings):
    st.title(t("page.folders.title"))
    st.caption(t("page.folders.caption"))
    _page_help("folders")

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
# Shared SQL helper (avoids pd.read_sql + SQLAlchemy 2.x immutabledict bug)
# ---------------------------------------------------------------------------

def _sql(engine, sql: str, params: dict | None = None) -> "pd.DataFrame":
    """Execute raw SQL and return a DataFrame."""
    with engine.connect() as _conn:
        result = _conn.execute(text(sql), params or {})
        return pd.DataFrame(result.fetchall(), columns=list(result.keys()))


# ---------------------------------------------------------------------------
# Page: Audit Log
# ---------------------------------------------------------------------------

def page_invoices(engine):
    st.title(t("dashboard.invoices.title"))
    _show_flash()
    _page_help("invoices")
    st.caption(t("dashboard.invoices.caption"))

    from datetime import datetime, timezone, timedelta

    # ── Load accounts for filter dropdown ──
    try:
        _accs = pd.read_sql(
            "SELECT username FROM email_accounts WHERE active = true ORDER BY username",
            engine,
        )
        account_options = ["— All accounts —"] + _accs["username"].tolist()
    except Exception:
        account_options = ["— All accounts —"]

    # ── Filters ──
    col_months, col_nif, col_search, col_acc = st.columns([1, 2, 2, 2])
    months_back   = col_months.number_input(t("dashboard.invoices.filter_months"), min_value=1, max_value=24, value=3)
    nif_filter    = col_nif.text_input(t("dashboard.invoices.filter_nif"), placeholder="123456789")
    search_filter = col_search.text_input(t("dashboard.invoices.filter_search"), placeholder="FT 2026/1")
    account_sel   = col_acc.selectbox(t("dashboard.invoices.filter_account"), account_options)
    account_filter = None if account_sel.startswith("—") else account_sel

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(months_back) * 30)

    conditions = ["i.extracted_at >= %(cutoff)s"]
    params: dict = {"cutoff": cutoff}

    if nif_filter:
        conditions.append("i.nif_seller ILIKE %(nif)s")
        params["nif"] = f"%{nif_filter}%"

    if search_filter:
        conditions.append("(i.invoice_number ILIKE %(search)s OR i.atcud ILIKE %(search)s)")
        params["search"] = f"%{search_filter}%"

    if account_filter:
        conditions.append("a.username = %(account)s")
        params["account"] = account_filter

    where = " AND ".join(conditions)

    try:
        df = pd.read_sql(
            f"""
            SELECT
                i.id,
                i.email_id,
                e.subject,
                e.from_address,
                a.username AS account,
                i.invoice_origin,
                i.document_type,
                i.document_type_description,
                i.nif_seller,
                i.nif_buyer,
                i.invoice_number,
                i.receipt_number,
                i.atcud,
                i.invoice_date,
                i.taxable_amount,
                i.vat_amount,
                i.vat_rate,
                i.total_amount,
                i.currency,
                i.mb_entidade,
                i.mb_referencia,
                i.mb_valor,
                i.mb_data_limite,
                i.seller_name,
                i.seller_country,
                i.payment_method,
                i.card_last4,
                i.extracted_at
            FROM invoices i
            LEFT JOIN emails e ON e.id = i.email_id
            LEFT JOIN email_accounts a ON a.id = e.account_id
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

    # Split by origin
    df_at   = df[df["invoice_origin"].isin(["pt_at", None, ""]) | df["invoice_origin"].isna()].copy()
    df_intl = df[df["invoice_origin"] == "international"].copy()

    # ── Monthly totals chart (AT invoices only — matches the AT tab below) ────
    df_monthly = df_at.copy()
    df_monthly["_date"] = pd.to_datetime(df_monthly["invoice_date"], errors="coerce")
    df_monthly = df_monthly.dropna(subset=["_date"])

    if not df_monthly.empty:
        df_monthly["_month"] = df_monthly["_date"].dt.to_period("M")
        monthly = (
            df_monthly.groupby("_month")
            .agg(
                gross=("total_amount",   lambda x: pd.to_numeric(x, errors="coerce").sum()),
                vat=  ("vat_amount",     lambda x: pd.to_numeric(x, errors="coerce").sum()),
                count=("id",             "count"),
            )
            .reset_index()
            .sort_values("_month")
        )
        monthly["Month"] = monthly["_month"].dt.strftime("%Y-%m")

        fig_monthly = px.bar(
            monthly,
            x="Month",
            y="gross",
            text="count",
            labels={"gross": "Total (€)", "count": "Invoices"},
            title=t("dashboard.invoices.chart_monthly_title"),
            color_discrete_sequence=["#4C78A8"],
        )
        fig_monthly.update_traces(
            texttemplate="%{text}",
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Total: € %{y:,.2f}<br>Invoices: %{text}<extra></extra>",
        )
        fig_monthly.update_layout(
            xaxis_title="",
            yaxis_title="€",
            showlegend=False,
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_monthly, use_container_width=True)

    st.divider()

    # ── Two tabs ──
    tab_at, tab_intl = st.tabs([
        t("dashboard.invoices.tab_at"),
        t("dashboard.invoices.tab_international"),
    ])

    # ── Helper: currency-aware amount formatter ──
    def _fmt_amount(v, currency_val="€"):
        if pd.isna(v):
            return "—"
        symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(str(currency_val).upper(), str(currency_val) + " ")
        return f"{symbol} {float(v):,.2f}"

    # ════════════════════════════════
    # TAB 1 — PT AT Invoices
    # ════════════════════════════════
    with tab_at:
        if df_at.empty:
            st.info(t("dashboard.invoices.empty"))
        else:
            # ── KPI row — computed from the same df_at that the table shows ──
            at_gross   = pd.to_numeric(df_at["total_amount"],   errors="coerce").sum()
            at_vat     = pd.to_numeric(df_at["vat_amount"],     errors="coerce").sum()
            at_taxable = pd.to_numeric(df_at["taxable_amount"], errors="coerce").sum()
            at_sellers = df_at["nif_seller"].nunique()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric(t("dashboard.invoices.metric_total_gross"), f"€ {at_gross:,.2f}")
            k2.metric(t("dashboard.invoices.metric_total_vat"),   f"€ {at_vat:,.2f}")
            k3.metric(t("dashboard.invoices.metric_taxable"),     f"€ {at_taxable:,.2f}")
            k4.metric(t("dashboard.invoices.metric_sellers"),     at_sellers)

            # ── Top 5 sellers chart ──
            with st.container():
                if not df_at["nif_seller"].isna().all():
                    # Build NIF → name map for readable labels
                    nif_name = (
                        df_at.dropna(subset=["nif_seller"])
                        .drop_duplicates("nif_seller")
                        .set_index("nif_seller")["seller_name"]
                    )
                    seller_amounts = (
                        df_at.groupby("nif_seller")["total_amount"]
                        .apply(lambda x: pd.to_numeric(x, errors="coerce").sum())
                        .sort_values(ascending=False)
                    )
                    top5   = seller_amounts.head(5)
                    # Label = "Name (NIF)" if name known, else just NIF — always str
                    def _seller_label(nif):
                        name = nif_name.get(nif)
                        return f"{name}\n({nif})" if name else str(nif)
                    labels = [_seller_label(nif) for nif in top5.index]
                    values = list(top5.values)
                    colors = ["#4a9eff"] * len(top5)
                    chart_df = pd.DataFrame({
                        "seller": labels,
                        "total_col": values,
                        "color": colors,
                    })
                    # category_orders keeps bars in sorted order without Plotly re-sorting
                    fig = px.bar(
                        chart_df, x="seller", y="total_col",
                        title=t("dashboard.invoices.chart_title"),
                        labels={"seller": t("dashboard.invoices.chart_x_label"), "total_col": "Total (€)"},
                        color="color", color_discrete_map="identity",
                        category_orders={"seller": labels},
                    )
                    fig.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        showlegend=False,
                        xaxis_type="category",  # force string axis — prevents numeric abbreviation
                    )
                    st.plotly_chart(fig, width='stretch')

            # Table — paginated with inline delete
            _fhash_at = hashlib.md5(
                str((months_back, nif_filter, search_filter, "at")).encode()
            ).hexdigest()[:8]
            _at_page, _at_offset = _page_controls(f"inv_at_{_fhash_at}", len(df_at))
            df_at_page = df_at.iloc[_at_offset : _at_offset + _PAGE_SIZE].copy()

            display_at = df_at_page[[c for c in [
                "id", "invoice_date", "document_type", "document_type_description",
                "invoice_number", "atcud", "nif_seller", "seller_name", "nif_buyer",
                "taxable_amount", "vat_amount", "total_amount",
                "mb_entidade", "mb_referencia", "mb_valor", "mb_data_limite",
                "subject", "email_id",
            ] if c in df_at_page.columns]].copy()

            for col in ["taxable_amount", "vat_amount", "total_amount"]:
                display_at[col] = pd.to_numeric(display_at[col], errors="coerce").apply(
                    lambda v: f"€ {v:,.2f}" if pd.notna(v) else "—"
                )
            display_at["mb_valor"] = pd.to_numeric(display_at["mb_valor"], errors="coerce").apply(
                lambda v: f"€ {v:,.2f}" if pd.notna(v) else "—"
            )
            display_at["invoice_date"] = pd.to_datetime(display_at["invoice_date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("—")
            display_at.insert(0, "🗑️", False)

            edited_at = st.data_editor(
                display_at.rename(columns={
                    "invoice_date":              t("dashboard.invoices.col_date"),
                    "document_type":             t("dashboard.invoices.col_doc_type"),
                    "document_type_description": t("dashboard.invoices.col_doc_type_desc"),
                    "invoice_number":            t("dashboard.invoices.col_invoice_num"),
                    "atcud":          "ATCUD",
                    "nif_seller":     t("dashboard.invoices.col_nif_seller"),
                    "seller_name":    t("dashboard.invoices.col_seller_name"),
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
                column_config={
                    "🗑️": st.column_config.CheckboxColumn("🗑️", help=t("dashboard.invoices.delete_col_help"), width="small"),
                    "id": st.column_config.NumberColumn("ID", width="small"),
                },
                disabled=[c for c in display_at.columns if c != "🗑️"],
                width='stretch',
                hide_index=True,
                key="editor_at",
            )
            to_delete_at = display_at.loc[edited_at["🗑️"].values, "id"].tolist() if "🗑️" in edited_at.columns else []
            if to_delete_at:
                st.warning(t("dashboard.invoices.delete_warning", count=len(to_delete_at)))
                if st.button(t("dashboard.invoices.delete_selected_btn"), type="primary", key="del_at"):
                    with engine.begin() as conn:
                        conn.execute(text("DELETE FROM invoices WHERE id = ANY(:ids)"), {"ids": to_delete_at})
                    _set_flash("success", t("dashboard.invoices.delete_success", count=len(to_delete_at)))
                    st.rerun()

            csv_at = df_at.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Export CSV", csv_at, "invoices_at.csv", "text/csv", key="csv_at")

    # ════════════════════════════════
    # TAB 2 — International / Foreign
    # ════════════════════════════════
    with tab_intl:
        if df_intl.empty:
            st.info(t("dashboard.invoices.intl_empty"))
        else:
            intl_gross   = pd.to_numeric(df_intl["total_amount"],   errors="coerce").sum()
            intl_vat     = pd.to_numeric(df_intl["vat_amount"],     errors="coerce").sum()
            intl_taxable = pd.to_numeric(df_intl["taxable_amount"], errors="coerce").sum()
            intl_count   = len(df_intl)
            ci1, ci2, ci3, ci4 = st.columns(4)
            ci1.metric(t("dashboard.invoices.metric_total_gross"), f"€ {intl_gross:,.2f}")
            ci2.metric(t("dashboard.invoices.metric_total_vat"),   f"€ {intl_vat:,.2f}")
            ci3.metric(t("dashboard.invoices.metric_taxable"),     f"€ {intl_taxable:,.2f}")
            ci4.metric(t("dashboard.invoices.intl_count"),         intl_count)

            # Table — paginated with inline delete
            _fhash_intl = hashlib.md5(
                str((months_back, nif_filter, search_filter, "intl")).encode()
            ).hexdigest()[:8]
            _intl_page, _intl_offset = _page_controls(f"inv_intl_{_fhash_intl}", len(df_intl))
            df_intl_page = df_intl.iloc[_intl_offset : _intl_offset + _PAGE_SIZE].copy()

            display_intl = df_intl_page[[c for c in [
                "id", "invoice_date", "seller_name", "seller_country",
                "invoice_number", "receipt_number",
                "taxable_amount", "vat_amount", "vat_rate", "total_amount", "currency",
                "payment_method", "card_last4",
                "subject", "email_id",
            ] if c in df_intl_page.columns]].copy()

            display_intl["invoice_date"] = pd.to_datetime(display_intl["invoice_date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("—")

            for amt_col in ["taxable_amount", "vat_amount", "total_amount"]:
                display_intl[amt_col] = display_intl.apply(
                    lambda row, c=amt_col: _fmt_amount(row[c], row.get("currency", "€")),
                    axis=1,
                )
            if "vat_rate" in display_intl.columns:
                display_intl["vat_rate"] = pd.to_numeric(display_intl["vat_rate"], errors="coerce").apply(
                    lambda v: f"{v*100:.0f}%" if pd.notna(v) else "—"
                )
            display_intl.insert(0, "🗑️", False)

            edited_intl = st.data_editor(
                display_intl.rename(columns={
                    "invoice_date":   t("dashboard.invoices.col_date"),
                    "seller_name":    t("dashboard.invoices.col_seller_name"),
                    "seller_country": t("dashboard.invoices.col_seller_country"),
                    "invoice_number": t("dashboard.invoices.col_invoice_num"),
                    "receipt_number": t("dashboard.invoices.col_receipt_num"),
                    "taxable_amount": t("dashboard.invoices.col_taxable"),
                    "vat_amount":     t("dashboard.invoices.col_vat"),
                    "vat_rate":       t("dashboard.invoices.col_vat_rate"),
                    "total_amount":   t("dashboard.invoices.col_total"),
                    "currency":       t("dashboard.invoices.col_currency"),
                    "payment_method": t("dashboard.invoices.col_payment_method"),
                    "card_last4":     t("dashboard.invoices.col_card_last4"),
                    "subject":        t("dashboard.invoices.col_subject"),
                    "email_id":       "Email ID",
                }),
                column_config={
                    "🗑️": st.column_config.CheckboxColumn("🗑️", help=t("dashboard.invoices.delete_col_help"), width="small"),
                    "id": st.column_config.NumberColumn("ID", width="small"),
                },
                disabled=[c for c in display_intl.columns if c != "🗑️"],
                width='stretch',
                hide_index=True,
                key="editor_intl",
            )
            to_delete_intl = display_intl.loc[edited_intl["🗑️"].values, "id"].tolist() if "🗑️" in edited_intl.columns else []
            if to_delete_intl:
                st.warning(t("dashboard.invoices.delete_warning", count=len(to_delete_intl)))
                if st.button(t("dashboard.invoices.delete_selected_btn"), type="primary", key="del_intl"):
                    with engine.begin() as conn:
                        conn.execute(text("DELETE FROM invoices WHERE id = ANY(:ids)"), {"ids": to_delete_intl})
                    _set_flash("success", t("dashboard.invoices.delete_success", count=len(to_delete_intl)))
                    st.rerun()

            csv_intl = df_intl.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Export CSV", csv_intl, "invoices_international.csv", "text/csv", key="csv_intl")



def page_settings(engine):
    st.title(t("page.settings.title"))
    _show_flash()
    _page_help("settings")

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
                        _set_flash("success", t("page.settings.saved"))
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")

        if reset:
            try:
                set_setting(engine, FOLDER_STRUCTURE_KEY, FOLDER_STRUCTURE_DEFAULT)
                _set_flash("success", t("page.settings.reset_done"))
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

    # ── Inbox Filter Keywords ─────────────────────────────────────────────────
    st.divider()
    st.subheader(t("page.settings.keywords_header"))
    st.caption(t("page.settings.keywords_caption"))

    try:
        from app.core.system_settings import (
            INBOX_KEYWORDS_KEY, DEFAULT_PLAIN_KEYWORDS,
            get_inbox_keywords, set_inbox_keywords,
        )
    except ImportError as e:
        st.error(f"❌ Could not import keyword helpers: {e}")
    else:
        current_kws = get_inbox_keywords(engine)

        if not current_kws:
            st.warning(t("page.settings.keywords_none"))
        else:
            st.caption(t("page.settings.keywords_pills_hint"))
            st.markdown("""
            <style>
            /* 1. The main button container */
            div[data-testid="stPills"] button[kind="pillsActive"] {
                background-color: rgba(8, 145, 178, 0.1) !important;
                border: 1px solid rgb(8, 145, 178) !important;
                color: rgb(8, 145, 178) !important;
            }

            /* 2. The inner div and markdown container */
            div[data-testid="stPills"] button[kind="pillsActive"] div, 
            div[data-testid="stPills"] button[kind="pillsActive"] p {
                color: rgb(8, 145, 178) !important;
            }

            /* 3. Hover state for the cyan pill */
            div[data-testid="stPills"] button[kind="pillsActive"]:hover {
                background-color: rgba(8, 145, 178, 0.2) !important;
                border-color: rgb(14, 116, 144) !important;
            }

            /* 4. Ensure inactive pills stay neutral (not red) */
            div[data-testid="stPills"] button[kind="pillsSecondary"] {
                border-color: #94a3b8 !important;
                color: #64748b !important;
                background-color: transparent !important;
            }

            /* 5. Inactive pill text color */
            div[data-testid="stPills"] button[kind="pillsSecondary"] p {
                color: #64748b !important;
            }
            </style>
            """, unsafe_allow_html=True)
            # st.pills returns the currently-selected subset; deselecting = marking for removal
            remaining = st.pills(
                label="keywords",
                options=current_kws,
                default=current_kws,
                selection_mode="multi",
                label_visibility="collapsed",
                key="kw_pills",
            )
            removed = set(current_kws) - set(remaining or [])
            if removed:
                removed_list = ", ".join(f"`{k}`" for k in sorted(removed))
                col_apply, col_cancel = st.columns([3, 1])
                if col_apply.button(
                    t("page.settings.keywords_remove_btn", n=len(removed)),
                    type="primary",
                    key="btn_kw_apply",
                ):
                    set_inbox_keywords(engine, [k for k in current_kws if k not in removed])
                    from app.core.audit import log_audit_sync
                    log_audit_sync(
                        engine,
                        actor_type="dashboard",
                        actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                        action="keywords.removed",
                        entity_type="system",
                        details={"removed": sorted(removed)},
                    )
                    _set_flash("success", t("page.settings.keywords_saved"))
                    st.rerun()
                if col_cancel.button(t("page.settings.keywords_cancel"), key="btn_kw_cancel"):
                    st.rerun()
                st.caption(f"Will remove: {removed_list}")

        # Add new keyword + reset
        with st.form("add_keyword_form", clear_on_submit=True):
            new_kw = st.text_input(
                t("page.settings.keywords_add_label"),
                placeholder="e.g. fatura",
            )
            col_add, col_reset = st.columns([3, 1])
            submitted = col_add.form_submit_button(t("page.settings.keywords_add_btn"))
            reset_kws = col_reset.form_submit_button(t("page.settings.keywords_reset"))

            if submitted:
                kw = new_kw.strip().lower()
                if not kw:
                    st.error(t("page.settings.keywords_empty"))
                elif kw in current_kws:
                    st.warning(t("page.settings.keywords_exists"))
                else:
                    set_inbox_keywords(engine, current_kws + [kw])
                    from app.core.audit import log_audit_sync
                    log_audit_sync(
                        engine,
                        actor_type="dashboard",
                        actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                        action="keywords.added",
                        entity_type="system",
                        details={"added": kw},
                    )
                    _set_flash("success", t("page.settings.keywords_saved"))
                    st.rerun()

            if reset_kws:
                set_inbox_keywords(engine, DEFAULT_PLAIN_KEYWORDS)
                from app.core.audit import log_audit_sync
                log_audit_sync(
                    engine,
                    actor_type="dashboard",
                    actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                    action="keywords.reset",
                    entity_type="system",
                    details={"reset_to": DEFAULT_PLAIN_KEYWORDS},
                )
                _set_flash("success", t("page.settings.keywords_reset_done"))
                st.rerun()

    # ── 🧪 Testing tools ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("🧪 Testing Tools")
    st.caption("⚠️ For testing only — will be removed when no longer needed.")

    with st.expander("🔄 Re-process emails", expanded=False):
        st.markdown(
            "Resets the status of selected emails back to `new`. "
            "**ai-worker** emails are pushed to the Redis queue immediately. "
            "**invoice-worker** emails are reset so the invoice-worker picks them up on its next IMAP poll. "
            "Does **not** re-fetch from IMAP — uses whatever is already stored in the database."
        )

        # Base query — join account for managed_by; check attachments to infer classification
        _BASE_Q = """
            SELECT e.id, e.tenant_id, e.account_id, e.status, e.classification_label,
                   COALESCE(a.managed_by, 'ai_worker') AS managed_by,
                   EXISTS(
                       SELECT 1 FROM attachments att
                       WHERE att.email_id = e.id
                         AND (att.filename ILIKE '%.pdf' OR att.mime_type = 'application/pdf')
                   ) AS has_pdf
            FROM emails e
            LEFT JOIN email_accounts a ON a.id = e.account_id
        """

        # Status summary
        try:
            status_df = _sql(engine, "SELECT status, COUNT(*) AS n FROM emails GROUP BY status ORDER BY n DESC")
            status_summary = "  ·  ".join(
                f"`{r['status']}` **{r['n']}**" for _, r in status_df.iterrows()
            )
            st.caption(f"Emails in DB: {status_summary}")
        except Exception:
            pass

        scope = st.radio(
            "Which emails to re-process:",
            [
                "Last N emails (any status)",
                "Already in folders (status=moved)",
                "Specific email IDs",
                "All emails",
            ],
            horizontal=True,
            key="requeue_scope",
        )

        rows = pd.DataFrame()

        if scope == "Last N emails (any status)":
            n = st.number_input("Number of emails", min_value=1, max_value=500, value=10, step=1)
            rows = _sql(engine, _BASE_Q + f"ORDER BY e.id DESC LIMIT {int(n)}")
            moved = int((rows["status"] == "moved").sum())
            st.caption(f"{len(rows)} email(s) — {moved} already in folders.")

        elif scope == "Already in folders (status=moved)":
            folder_filter = st.text_input("Filter by folder name (blank = all)", placeholder="Faturas")
            if folder_filter.strip():
                rows = _sql(
                    engine,
                    _BASE_Q + "WHERE e.status = 'moved' AND e.classification_label ILIKE :f ORDER BY e.id DESC",
                    {"f": f"%{folder_filter.strip()}%"},
                )
            else:
                rows = _sql(engine, _BASE_Q + "WHERE e.status = 'moved' ORDER BY e.id DESC")
            folder_counts = rows["classification_label"].value_counts().to_dict()
            summary = "  ·  ".join(f"{k}: **{v}**" for k, v in list(folder_counts.items())[:6])
            st.caption(f"{len(rows)} email(s) — {summary}")

        elif scope == "Specific email IDs":
            raw = st.text_input("Email IDs (comma-separated)", placeholder="1, 2, 3")
            if raw.strip():
                try:
                    parsed_ids = [int(x.strip()) for x in raw.split(",") if x.strip()]
                    # IDs are validated ints — safe to interpolate directly
                    id_list = ",".join(str(i) for i in parsed_ids)
                    rows = _sql(engine, _BASE_Q + f"WHERE e.id = ANY(ARRAY[{id_list}])")
                    st.caption(f"{len(rows)} email(s) found: " +
                               ", ".join(f"#{r['id']} [{r['status']}] ({r['managed_by']})"
                                         for _, r in rows.iterrows()))
                except ValueError:
                    st.error("Invalid IDs — use comma-separated numbers.")

        else:  # All emails
            rows = _sql(engine, _BASE_Q + "ORDER BY e.id DESC")
            st.caption(f"{len(rows)} email(s) total.")

        # Show worker split before confirming
        if not rows.empty:
            ai_count  = int((rows["managed_by"] == "ai_worker").sum())
            inv_count = int((rows["managed_by"] == "invoice_worker").sum())
            st.info(
                f"🤖 **{ai_count}** ai-worker email(s) → pushed to `mailai:jobs:email`  \n"
                f"🧾 **{inv_count}** invoice-worker email(s) → pushed to `mailai:jobs:invoice`"
            )

        if not rows.empty and st.button("🔄 Reset & Re-process", type="primary", key="btn_requeue"):
            try:
                import json as _json
                import redis as _redis_sync

                email_ids = rows["id"].tolist()

                # 1 — reset status for all selected emails
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE emails SET status = 'new' WHERE id = ANY(:ids)"),
                        {"ids": email_ids},
                    )

                _r = _redis_sync.from_url(settings.redis_url, decode_responses=True)

                # 2 — push ai_worker emails to their queue
                ai_rows = rows[rows["managed_by"] == "ai_worker"]
                for _, row in ai_rows.iterrows():
                    _r.lpush("mailai:jobs:email", _json.dumps({
                        "type":      "process_email",
                        "tenant_id": int(row["tenant_id"]),
                        "email_id":  int(row["id"]),
                    }))

                # 3 — push invoice_worker emails to their queue
                #     infer classification from stored attachments (has_pdf column)
                inv_rows = rows[rows["managed_by"] == "invoice_worker"]
                for _, row in inv_rows.iterrows():
                    classification = "pdf_invoice" if row.get("has_pdf") else "financial_body"
                    _r.lpush("mailai:jobs:invoice", _json.dumps({
                        "type":           "process_invoice",
                        "tenant_id":      int(row["tenant_id"]),
                        "email_id":       int(row["id"]),
                        "classification": classification,
                    }))

                _r.close()

                msg = f"✅ Reset {len(email_ids)} email(s)."
                if not ai_rows.empty:
                    msg += f" {len(ai_rows)} pushed to ai-worker queue."
                if not inv_rows.empty:
                    msg += f" {len(inv_rows)} pushed to invoice-worker queue."
                _set_flash("success", msg)
                st.rerun()
            except Exception as e:
                st.error(f"❌ Re-process failed: {e}")

    # ── 🗑️ Full Data Reset ────────────────────────────────────────────────────
    st.divider()
    st.subheader(t("page.settings.full_reset_header"))
    st.caption(t("page.settings.full_reset_caption"))

    # Two-step confirmation: first click expands the form, second click executes.
    if not st.session_state.get("_full_reset_open"):
        if st.button(t("page.settings.full_reset_btn"), type="secondary", key="btn_full_reset_open"):
            st.session_state["_full_reset_open"] = True
            st.rerun()
    else:
        with st.form("full_reset_confirm_form", clear_on_submit=True):
            st.warning(
                "⚠️ This will permanently delete:\n"
                "- All emails and attachments\n"
                "- All extracted invoices\n"
                "- All learned classification rules\n"
                "- The seller / NIF cache\n\n"
                "**Accounts, companies, folders and keyword settings will NOT be touched.**"
            )
            confirm_text = st.text_input(
                t("page.settings.full_reset_confirm_label"),
                placeholder="RESET",
            )
            col_confirm, col_cancel = st.columns([2, 1])
            do_reset = col_confirm.form_submit_button(
                t("page.settings.full_reset_confirm_btn"), type="primary"
            )
            do_cancel = col_cancel.form_submit_button(t("page.settings.full_reset_cancel"))

            if do_cancel:
                st.session_state.pop("_full_reset_open", None)
                st.rerun()

            if do_reset:
                if confirm_text.strip() != "RESET":
                    st.error(t("page.settings.full_reset_wrong"))
                else:
                    try:
                        # 1 — wipe processing tables (FK-safe order)
                        with engine.begin() as conn:
                            conn.execute(text("DELETE FROM invoices"))
                            conn.execute(text("DELETE FROM attachments"))
                            conn.execute(text("DELETE FROM emails"))
                            conn.execute(text("DELETE FROM learned_rules"))
                            conn.execute(text("DELETE FROM sellers"))

                        # 2 — flush Redis queues + skip sets so stale jobs don't replay
                        import redis as _redis_sync
                        _r = _redis_sync.from_url(settings.redis_url, decode_responses=True)
                        _r.delete("mailai:jobs:email", "mailai:jobs:invoice")
                        # Clear per-account non-financial skip sets so the IMAP worker
                        # re-evaluates every UNSEEN message from scratch.
                        for _sk in _r.scan_iter("mailai:skipped:*"):
                            _r.delete(_sk)
                        _r.close()

                        from app.core.audit import log_audit_sync
                        log_audit_sync(
                            engine,
                            actor_type="dashboard",
                            actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                            action="system.full_reset",
                            entity_type="system",
                            details={
                                "tables_cleared": ["invoices", "attachments", "emails", "learned_rules", "sellers"],
                                "queues_flushed": ["mailai:jobs:email", "mailai:jobs:invoice"],
                            },
                        )

                        st.session_state.pop("_full_reset_open", None)
                        _set_flash("success", t("page.settings.full_reset_done"))
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Reset failed: {e}")


def page_sellers(engine, settings):
    st.title(t("page.sellers.title"))
    st.caption(t("page.sellers.caption"))
    _show_flash()

    # ── Lookup button ─────────────────────────────────────────────────────────
    with st.expander(t("page.sellers.lookup_header"), expanded=False):
        st.markdown(t("page.sellers.lookup_help"))
        col_nif, col_btn = st.columns([3, 1])
        lookup_nif = col_nif.text_input("NIF", placeholder="508517592", label_visibility="collapsed")
        if col_btn.button(t("page.sellers.lookup_btn"), type="primary") and lookup_nif.strip():
            nif = lookup_nif.strip()
            tool_server_url = getattr(settings, "tool_server_url", None)
            api_key = getattr(settings, "tool_server_api_key", "") or ""
            if not tool_server_url:
                st.error(t("page.sellers.no_tool_server"))
            else:
                import asyncio, os
                from app.invoices.nif_lookup import resolve_seller_name
                db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
                with st.spinner(t("page.sellers.looking_up")):
                    name = asyncio.run(resolve_seller_name(nif, db_url, tool_server_url, api_key))
                if name:
                    _set_flash("success", t("page.sellers.lookup_found", nif=nif, name=name))
                else:
                    _set_flash("warning", t("page.sellers.lookup_not_found", nif=nif))
                st.rerun()

    # ── Add seller manually ───────────────────────────────────────────────────
    with st.expander(t("page.sellers.add_header"), expanded=False):
        with st.form("add_seller_form", clear_on_submit=True):
            col_nif2, col_name2 = st.columns([2, 3])
            new_nif      = col_nif2.text_input("NIF *", placeholder="508517592")
            new_name     = col_name2.text_input(t("page.sellers.col_name") + " *", placeholder="Empresa Exemplo Lda")
            col_a, col_b, col_c = st.columns(3)
            new_activity = col_a.text_input(t("page.sellers.col_activity"), placeholder="Comércio por grosso")
            new_cae      = col_b.text_input(t("page.sellers.col_cae"), placeholder="46900")
            new_situation= col_c.text_input(t("page.sellers.col_situation"), placeholder="Ativo")
            new_address  = st.text_input(t("page.sellers.col_address"), placeholder="Rua Exemplo, 1 — 1000-001 Lisboa")
            submitted = st.form_submit_button(t("page.sellers.add_btn"), type="primary")
            if submitted:
                nif_v = new_nif.strip()
                name_v = new_name.strip()
                if not nif_v or not name_v:
                    st.error(t("page.sellers.add_required"))
                elif not nif_v.isdigit() or len(nif_v) != 9:
                    st.error(t("page.sellers.add_nif_invalid"))
                else:
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO sellers (nif, name, activity, cae, address, situation)
                                VALUES (:nif, :name, :activity, :cae, :address, :situation)
                                ON CONFLICT (nif) DO UPDATE SET
                                    name      = EXCLUDED.name,
                                    activity  = EXCLUDED.activity,
                                    cae       = EXCLUDED.cae,
                                    address   = EXCLUDED.address,
                                    situation = EXCLUDED.situation,
                                    updated_at = now()
                            """), {
                                "nif": nif_v, "name": name_v,
                                "activity": new_activity.strip() or None,
                                "cae": new_cae.strip() or None,
                                "address": new_address.strip() or None,
                                "situation": new_situation.strip() or None,
                            })
                        _set_flash("success", t("page.sellers.add_success", nif=nif_v, name=name_v))
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")

    # ── Sellers table ─────────────────────────────────────────────────────────
    try:
        df = _sql(engine, """
            SELECT
                s.nif, s.name, s.activity, s.cae, s.address, s.situation,
                COUNT(i.id) AS invoice_count,
                MAX(i.invoice_date) AS last_invoice
            FROM sellers s
            LEFT JOIN invoices i ON i.nif_seller = s.nif
            GROUP BY s.id, s.nif, s.name, s.activity, s.cae, s.address, s.situation
            ORDER BY invoice_count DESC, s.name
        """)
    except Exception as e:
        st.error(t("page.sellers.load_error", error=e))
        return

    if df.empty:
        st.info(t("page.sellers.empty"))
        return

    # ── Search filter ─────────────────────────────────────────────────────────
    search = st.text_input(t("page.sellers.search"), placeholder="NIF or name…", label_visibility="collapsed")
    if search.strip():
        mask = (
            df["nif"].str.contains(search.strip(), case=False, na=False) |
            df["name"].str.contains(search.strip(), case=False, na=False) |
            df["activity"].fillna("").str.contains(search.strip(), case=False, na=False)
        )
        df = df[mask]

    st.caption(f"{len(df)} {t('page.sellers.count_label')}")

    # ── Pagination ────────────────────────────────────────────────────────────
    import hashlib as _hashlib
    _sellers_fhash = _hashlib.md5(search.strip().encode()).hexdigest()[:8]
    _sellers_page, _sellers_offset = _page_controls(f"sellers_page_{_sellers_fhash}", len(df))
    df_page = df.iloc[_sellers_offset : _sellers_offset + _PAGE_SIZE].copy()

    # ── Format display columns (keep originals for edit tracking) ─────────────
    df_page["last_invoice"] = pd.to_datetime(df_page["last_invoice"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("—")
    df_page["invoice_count"] = df_page["invoice_count"].fillna(0).astype(int)
    df_page.insert(0, "🗑️", False)

    # Editable cols: name, activity, cae, address, situation (nif is the key — keep disabled)
    _editable = ["🗑️", "name", "activity", "cae", "address", "situation"]
    _disabled = [c for c in df_page.columns if c not in _editable]

    col_labels = {
        "nif":           t("page.sellers.col_nif"),
        "name":          t("page.sellers.col_name"),
        "activity":      t("page.sellers.col_activity"),
        "cae":           t("page.sellers.col_cae"),
        "address":       t("page.sellers.col_address"),
        "situation":     t("page.sellers.col_situation"),
        "invoice_count": t("page.sellers.col_invoices"),
        "last_invoice":  t("page.sellers.col_last_invoice"),
    }

    # ── Data editor ───────────────────────────────────────────────────────────
    edited = st.data_editor(
        df_page.rename(columns=col_labels),
        column_config={
            "🗑️": st.column_config.CheckboxColumn("🗑️", help=t("dashboard.invoices.delete_col_help"), width="small"),
        },
        disabled=[col_labels.get(c, c) for c in _disabled],
        use_container_width=True,
        hide_index=True,
        key=f"editor_sellers_{_sellers_fhash}",
    )

    # ── Save edits ────────────────────────────────────────────────────────────
    # Map label columns back to original names for comparison
    inv_labels = {v: k for k, v in col_labels.items()}
    edited_orig = edited.rename(columns=inv_labels)

    changed_rows = []
    for idx in df_page.index:
        pos = df_page.index.get_loc(idx)
        for col in ["name", "activity", "cae", "address", "situation"]:
            orig_val = df_page.at[idx, col]
            new_val  = edited_orig.iloc[pos][col] if col in edited_orig.columns else orig_val
            # Treat NaN and None as equal
            orig_str = "" if pd.isna(orig_val) else str(orig_val)
            new_str  = "" if pd.isna(new_val)  else str(new_val)
            if orig_str != new_str:
                changed_rows.append(idx)
                break

    if changed_rows:
        if st.button(t("page.sellers.save_edits_btn"), type="primary", key="save_sellers"):
            try:
                with engine.begin() as conn:
                    for idx in changed_rows:
                        pos = df_page.index.get_loc(idx)
                        row = edited_orig.iloc[pos]
                        nif_key = df_page.at[idx, "nif"]
                        conn.execute(text("""
                            UPDATE sellers SET
                                name      = :name,
                                activity  = :activity,
                                cae       = :cae,
                                address   = :address,
                                situation = :situation,
                                updated_at = now()
                            WHERE nif = :nif
                        """), {
                            "nif":       nif_key,
                            "name":      row.get("name") or None,
                            "activity":  row.get("activity") or None,
                            "cae":       row.get("cae") or None,
                            "address":   row.get("address") or None,
                            "situation": row.get("situation") or None,
                        })
                _set_flash("success", t("page.sellers.save_edits_success", count=len(changed_rows)))
                st.rerun()
            except Exception as e:
                st.error(f"❌ {e}")

    # ── Delete ────────────────────────────────────────────────────────────────
    to_delete = df_page.loc[edited["🗑️"].values, "nif"].tolist() if "🗑️" in edited.columns else []
    if to_delete:
        st.warning(t("page.sellers.delete_warning", count=len(to_delete)))
        if st.button(t("page.sellers.delete_btn"), type="primary", key="del_sellers"):
            try:
                nif_list = ", ".join(f"'{n}'" for n in to_delete)
                with engine.begin() as conn:
                    conn.execute(text(f"DELETE FROM sellers WHERE nif IN ({nif_list})"))
                _set_flash("success", t("page.sellers.deleted_count", count=len(to_delete)))
                st.rerun()
            except Exception as e:
                st.error(f"❌ {e}")

    csv_sellers = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Export CSV", csv_sellers, "sellers.csv", "text/csv", key="csv_sellers")


def page_companies(engine):
    st.title(t("page.companies.title"))
    st.caption(t("page.companies.caption"))
    _show_flash()
    _page_help("companies")

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
                        _set_flash("success", f"✅ Added {new_name.strip()}")
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
                            _set_flash("success", t("page.settings.saved"))
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ {e}")

                if delete_clicked:
                    try:
                        with engine.connect() as conn:
                            conn.execute(text("DELETE FROM companies WHERE id=:id"), {"id": cid})
                            conn.commit()
                        _set_flash("success", t("page.companies.deleted", name=name))
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")


def page_audit_log(engine):
    st.title(t("page.audit.title"))
    _page_help("audit")

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

    try:
        _count_df = pd.read_sql(
            f"SELECT COUNT(*) AS n FROM audit_logs WHERE {where}", engine, params=params
        )
        audit_total = int(_count_df["n"].iloc[0] if not _count_df.empty else 0)

        if audit_total == 0:
            st.info(t("page.audit.no_events"))
            return

        _audit_fhash = hashlib.md5(
            str((actor_filter, action_filter, days)).encode()
        ).hexdigest()[:8]
        _audit_page, _audit_offset = _page_controls(
            f"audit_page_{_audit_fhash}", audit_total
        )

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
            LIMIT {_PAGE_SIZE} OFFSET {_audit_offset}
        """

        df = pd.read_sql(query, engine, params=params)

        if df.empty:
            st.info(t("page.audit.no_events"))
            return

        st.metric("Events shown", audit_total)

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

    _mode_desc = t(f"help.sidebar.{_selected_mode}")
    if _mode_desc and _mode_desc != f"help.sidebar.{_selected_mode}":
        st.sidebar.caption(_mode_desc)

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

    # ── Workers control panel ─────────────────────────────────────────────────
    st.sidebar.markdown("---")
    st.sidebar.markdown(t("sidebar.workers_header"))

    # Containers we manage — telegram-bot and query-worker kept running always
    _WORKER_CONTAINERS = [
        ("mailflow-email-worker",    t("sidebar.workers_imap")),
        ("mailflow-invoice-worker",  t("sidebar.workers_invoice")),
        ("mailflow-ai-worker",       t("sidebar.workers_ai")),
        ("mailflow-review-worker",   t("sidebar.workers_review")),
    ]

    def _docker_client():
        try:
            import docker as _docker
            return _docker.from_env()
        except Exception:
            return None

    def _get_statuses(client):
        if not client:
            return {}
        try:
            containers = {c.name: c for c in client.containers.list(all=True)}
            return {name: containers[name].status if name in containers else "not found"
                    for name, _ in _WORKER_CONTAINERS}
        except Exception:
            return {}

    _dc = _docker_client()
    _statuses = _get_statuses(_dc)

    if not _dc:
        st.sidebar.caption(t("sidebar.workers_no_docker"))
    else:
        # Status table
        for _cname, _clabel in _WORKER_CONTAINERS:
            _s = _statuses.get(_cname, "?")
            _icon = "🟢" if _s == "running" else "🔴" if _s in ("exited", "stopped") else "🟡"
            st.sidebar.caption(f"{_icon} {_clabel}")

        _any_running = any(
            _statuses.get(n) == "running" for n, _ in _WORKER_CONTAINERS
        )
        _any_stopped = any(
            _statuses.get(n) in ("exited", "stopped") for n, _ in _WORKER_CONTAINERS
        )

        _col_stop, _col_start = st.sidebar.columns(2)

        if _col_stop.button(t("sidebar.workers_stop_all"), disabled=not _any_running, key="btn_stop_workers"):
            _errors = []
            _stopped = []
            for _cname, _clabel in _WORKER_CONTAINERS:
                try:
                    _c = _dc.containers.get(_cname)
                    if _c.status == "running":
                        _c.stop(timeout=10)
                        _stopped.append(_cname)
                except Exception as _e:
                    _errors.append(f"{_clabel}: {_e}")
            if _errors:
                st.sidebar.error("\n".join(_errors))
            else:
                st.sidebar.success(t("sidebar.workers_stopped_ok"))
                if _stopped:
                    from app.core.audit import log_audit_sync
                    log_audit_sync(
                        engine,
                        actor_type="dashboard",
                        actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                        action="system.workers_stopped",
                        entity_type="system",
                        details={"containers": _stopped},
                    )
            st.rerun()

        if _col_start.button(t("sidebar.workers_start_all"), disabled=not _any_stopped, key="btn_start_workers"):
            _errors = []
            _started = []
            for _cname, _clabel in _WORKER_CONTAINERS:
                try:
                    _c = _dc.containers.get(_cname)
                    if _c.status != "running":
                        _c.start()
                        _started.append(_cname)
                except Exception as _e:
                    _errors.append(f"{_clabel}: {_e}")
            if _errors:
                st.sidebar.error("\n".join(_errors))
            else:
                st.sidebar.success(t("sidebar.workers_started_ok"))
                if _started:
                    from app.core.audit import log_audit_sync
                    log_audit_sync(
                        engine,
                        actor_type="dashboard",
                        actor_name=os.environ.get("DASHBOARD_USER", "admin"),
                        action="system.workers_started",
                        entity_type="system",
                        details={"containers": _started},
                    )
            st.rerun()

        if _dc:
            try:
                _dc.close()
            except Exception:
                pass

    st.sidebar.markdown("---")

    _nav_items = [
        t("sidebar.nav_dashboard"),
        t("sidebar.nav_accounts"),
        t("sidebar.nav_rules"),
        t("sidebar.nav_folders"),
        t("sidebar.nav_companies"),
        t("sidebar.nav_sellers"),
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
    elif page == t("sidebar.nav_sellers"):
        page_sellers(engine, settings)
    elif page == t("sidebar.nav_invoices"):
        page_invoices(engine)
    elif page == t("sidebar.nav_audit"):
        page_audit_log(engine)
    elif page == t("sidebar.nav_settings"):
        page_settings(engine)
