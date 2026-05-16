"""
╔══════════════════════════════════════════════════════════════════════╗
║   LOYALTY STORE SELECTION OPTIMIZER                                  ║
║   Incorporating Research Best Practices:                             ║
║   • Spearman-based data-driven weights (validated)                   ║
║   • Province-level brand categorization                              ║
║   • Correct Ton_Growth (survivorship-corrected, anchored to T_max)   ║
║   • Correct Estimated_Cost (brand-mix weighted per cluster)          ║
║   • math.ceil() on cluster caps (not floor)                          ║
║   • ILP-A Mirror as recommended method                               ║
║   • Self-calibrating budget from existing roster                     ║
║   • Jaccard robustness check on sensitivity                          ║
║                                                                      ║
║   Run: streamlit run app_loyalty_optimizer.py                        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pulp
import math
import warnings
from scipy.stats import spearmanr
from io import BytesIO
from datetime import datetime

warnings.filterwarnings('ignore')

# ════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Loyalty Store Optimizer",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', sans-serif; }
    .stApp { background: #F8FAFC; }
    [data-testid="stSidebar"] { background: #1E2A3A; }
    [data-testid="stSidebar"] .stMarkdown { color: #9EAFC2; }
    [data-testid="stSidebar"] label { color: #C8D8E8 !important; }
    [data-testid="metric-container"] {
        background: white; border: 1px solid #E2E8F0;
        border-radius: 12px; padding: 16px;
    }
    .section-hdr {
        font-size: 11px; font-weight: 700; letter-spacing: 0.1em;
        text-transform: uppercase; color: #3B82F6;
        border-bottom: 1px solid #E2E8F0; padding-bottom: 8px; margin-bottom: 16px;
    }
    .card {
        background: white; border: 1px solid #E2E8F0;
        border-radius: 12px; padding: 18px 20px; margin-bottom: 12px;
    }
    .card-blue  { border-left: 4px solid #3B82F6; }
    .card-green { border-left: 4px solid #10B981; }
    .card-amber { border-left: 4px solid #F59E0B; }
    .card-red   { border-left: 4px solid #EF4444; }
    .badge {
        display: inline-block; padding: 2px 10px; border-radius: 20px;
        font-size: 11px; font-weight: 600;
    }
    .badge-ok  { background: #D1FAE5; color: #065F46; }
    .badge-warn{ background: #FEF3C7; color: #92400E; }
    .badge-bad { background: #FEE2E2; color: #991B1B; }
    #MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════
# RESEARCH-VALIDATED CONSTANTS
# ════════════════════════════════════════════════════════════════════

# Optimal weights from Spearman correlation (research validated)
RESEARCH_WEIGHTS = {
    'Ratio_vs_Cluster': 0.4719,
    'Avg_Trx':          0.4140,
    'Ton_Growth':       0.1141,
}

# Reward rates per cluster × brand role (Rp/ton) — from company policy
REWARD_RATES = {
    'Platinum':       {'Main Brand': 3750, 'Companion Brand': 1875, 'Fighting Brand': 1875},
    'Super Platinum': {'Main Brand': 3750, 'Companion Brand': 1875, 'Fighting Brand': 1875},
    'Gold':           {'Main Brand': 2500, 'Companion Brand': 1250, 'Fighting Brand': 1250},
    'Silver':         {'Main Brand': 2500, 'Companion Brand': 1250, 'Fighting Brand': 1250},
    'Bronze':         {'Main Brand': 2500, 'Companion Brand': 1250, 'Fighting Brand': 1250},
}

# Province-level brand mapping — anonymized (Area → Province → roles)
FIGHTING_BRAND_PROVINCES = [
    'Kalimantan Timur', 'Kalimantan Utara',
    'Sulawesi Tengah', 'Sulawesi Selatan',
]

BRAND_MAP_BY_PROV = {
    'SP': {
        'ACEH':            {'main': ['PADANG'], 'companion': ['ANDALAS', 'DYNAMIX']},
        'RIAU DARATAN':    {'main': ['PADANG'], 'companion': ['DYNAMIX']},
        'RIAU KEPULAUAN':  {'main': ['PADANG'], 'companion': ['ANDALAS']},
        'SUMATERA BARAT':  {'main': ['PADANG'], 'companion': []},
        'SUMATERA UTARA':  {'main': ['PADANG'], 'companion': ['ANDALAS', 'DYNAMIX']},
        'BENGKULU':        {'main': ['PADANG'], 'companion': ['DYNAMIX']},
        'JAMBI':           {'main': ['PADANG'], 'companion': []},
    },
    'SMBR': {
        'SUMATERA SELATAN': {'main': ['BATURAJA'], 'companion': ['PADANG', 'DYNAMIX']},
        'LAMPUNG':           {'main': ['BATURAJA'], 'companion': ['DYNAMIX']},
    },
    'ST': {
        'SULAWESI BARAT':    {'main': ['TONASA'], 'companion': []},
        'SULAWESI SELATAN':  {'main': ['TONASA'], 'companion': []},
        'SULAWESI TENGAH':   {'main': ['TONASA'], 'companion': []},
        'SULAWESI TENGGARA': {'main': ['TONASA'], 'companion': []},
        'SULAWESI UTARA':    {'main': ['TONASA'], 'companion': []},
        'GORONTALO':         {'main': ['TONASA'], 'companion': []},
        'MALUKU':            {'main': ['TONASA'], 'companion': []},
        'MALUKU UTARA':      {'main': ['TONASA'], 'companion': []},
        'N.T.T.':            {'main': ['TONASA'], 'companion': []},
        'N.T.B.':            {'main': ['TONASA'], 'companion': ['GRESIK']},
        'PAPUA':             {'main': ['TONASA'], 'companion': ['GRESIK']},
        'PAPUA BARAT':       {'main': ['TONASA'], 'companion': ['GRESIK']},
        'KALIMANTAN SELATAN':{'main': ['TONASA'], 'companion': ['GRESIK']},
        'KALIMANTAN TIMUR':  {'main': ['TONASA'], 'companion': ['GRESIK']},
        'KALIMANTAN UTARA':  {'main': ['TONASA'], 'companion': ['GRESIK']},
    },
}

CLUSTER_ORDER  = ['Bronze', 'Silver', 'Gold', 'Platinum', 'Super Platinum']
CLUSTER_COLORS = {
    'Bronze':        '#CD7F32',
    'Silver':        '#94A3B8',
    'Gold':          '#EAB308',
    'Platinum':      '#60A5FA',
    'Super Platinum':'#8B5CF6',
}

COL_TGL     = 'Tanggal Transaksi'
COL_ID      = 'ID Toko'
COL_NAMA    = 'Nama Toko'
COL_CLUSTER = 'Cluster Pareto'
COL_PROV    = 'Provinsi Toko'
COL_AREA_AP = 'Area AP Toko'
COL_AREA    = 'Area Toko'
COL_BRAND   = 'Brands'
COL_TON     = 'TON Quantity'

# ════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ════════════════════════════════════════════════════════════════════

def normalize(s: pd.Series) -> pd.Series:
    mn, mx = s.min(), s.max()
    return (s - mn) / (mx - mn + 1e-9)

def get_brand_category(area: str, brand: str, prov: str) -> str:
    """Province-level brand categorization (research-validated)."""
    area_map = BRAND_MAP_BY_PROV.get(str(area).strip().upper(), None)
    if area_map is None:
        return 'Other'

    prov_upper = str(prov).strip().upper()
    brand_upper = str(brand).strip().upper()

    # Exact match first, then partial match
    prov_map = area_map.get(prov_upper)
    if prov_map is None:
        for key in area_map:
            if key in prov_upper or prov_upper in key:
                prov_map = area_map[key]
                break

    if prov_map is None:
        # Fallback to area-level
        fallback = {
            'SP':   {'main': ['PADANG'],   'companion': ['DYNAMIX', 'ANDALAS', 'BATURAJA']},
            'SMBR': {'main': ['BATURAJA'], 'companion': ['DYNAMIX', 'PADANG']},
            'ST':   {'main': ['TONASA'],   'companion': ['GRESIK']},
        }
        prov_map = fallback.get(str(area).strip().upper(), {'main': [], 'companion': []})

    if any(kw in brand_upper for kw in prov_map['main']):
        return 'Main Brand'
    if prov_map['companion'] and any(kw in brand_upper for kw in prov_map['companion']):
        return 'Companion Brand'
    # Fighting Brand — only ST + specific provinces
    if str(area).strip().upper() == 'ST' and 'MERDEKA' in brand_upper:
        if str(prov).strip() in FIGHTING_BRAND_PROVINCES:
            return 'Fighting Brand'
    return 'Other'

def get_reward_per_ton(cluster: str, brand_cat: str) -> float:
    """Research-validated reward rate lookup."""
    cluster_map = REWARD_RATES.get(cluster, REWARD_RATES['Bronze'])
    return cluster_map.get(brand_cat, 0.0)

def compute_spearman_weights(agg_df: pd.DataFrame) -> dict:
    """Data-driven weight determination via Spearman correlation."""
    vars_score = ['Ratio_vs_Cluster', 'Avg_Trx', 'Ton_Growth']
    raw_w = {}
    for v in vars_score:
        r, _ = spearmanr(agg_df[v], agg_df['Avg_Ton'])
        raw_w[v] = abs(r)
    total = sum(raw_w.values())
    return {k: v / total for k, v in raw_w.items()}

def compute_scores(agg_df: pd.DataFrame, w1: float, w2: float, w3: float) -> pd.DataFrame:
    """Compute composite scores."""
    df = agg_df.copy()
    df['Score'] = (
        w1 * df['Ratio_vs_Cluster'] +
        w2 * normalize(df['Avg_Trx']) +
        w3 * normalize(df['Ton_Growth'])
    )
    return df

def jaccard(a, b) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if len(sa | sb) > 0 else 0.0

def fmt_rp(val: float) -> str:
    return f"Rp {val:,.0f}"

@st.cache_data(show_spinner=False)
def load_parquet(file_bytes: bytes) -> pd.DataFrame:
    import io
    df = pd.read_parquet(io.BytesIO(file_bytes))
    df[COL_ID]  = df[COL_ID].astype(str).str.strip()
    df[COL_TGL] = pd.to_datetime(df[COL_TGL], errors='coerce')
    return df.dropna(subset=[COL_TGL])

@st.cache_data(show_spinner=False)
def read_uploaded_file(file_bytes: bytes, fname: str) -> pd.DataFrame:
    import io
    if fname.endswith('.csv'):
        return pd.read_csv(io.BytesIO(file_bytes), dtype={COL_ID: str})
    elif fname.endswith(('.xlsx', '.xls')):
        return pd.read_excel(io.BytesIO(file_bytes), dtype={COL_ID: str})
    elif fname.endswith('.parquet'):
        df = pd.read_parquet(io.BytesIO(file_bytes))
        df[COL_ID] = df[COL_ID].astype(str)
        return df
    raise ValueError(f"Format tidak didukung: {fname}")

def build_agg(df_input: pd.DataFrame, min_bulan: int = 3) -> pd.DataFrame:
    """
    Aggregate transactions to store level.
    Research best practices applied:
    - Survivorship-corrected Ton_Growth anchored to T_max
    - Brand-mix-weighted Estimated_Cost per cluster
    - Province-level brand categorization
    """
    df = df_input.copy()
    df['Bulan'] = df[COL_TGL].dt.to_period('M').astype(str)

    # Apply brand categorization
    df['Brand_Category'] = df.apply(
        lambda r: get_brand_category(r[COL_AREA_AP], r[COL_BRAND], r[COL_PROV]), axis=1
    )
    df['Reward_per_Ton'] = df.apply(
        lambda r: get_reward_per_ton(r[COL_CLUSTER], r['Brand_Category']), axis=1
    )

    # Monthly aggregation
    monthly = (
        df.groupby([COL_ID, COL_NAMA, COL_CLUSTER, COL_AREA_AP, COL_PROV, COL_AREA, 'Bulan'])
        .agg(Total_Ton=(COL_TON, 'sum'), Jumlah_Trx=(COL_TGL, 'count'))
        .reset_index()
    )

    # Store-level aggregation
    agg = (
        monthly.groupby([COL_ID, COL_NAMA, COL_CLUSTER, COL_AREA_AP, COL_PROV, COL_AREA])
        .agg(
            Avg_Ton   =('Total_Ton',    'mean'),
            Avg_Trx   =('Jumlah_Trx',  'mean'),
            Total_Bulan=('Bulan',       'nunique'),
        )
        .reset_index()
    )

    # ── Survivorship-corrected Ton_Growth (RESEARCH FIX) ──────────
    # Anchor last_val to T_max — stores inactive in final month get 0
    target_last = monthly['Bulan'].max()
    growths = []
    for sid in agg[COL_ID]:
        td = monthly[monthly[COL_ID] == sid]
        last_series = td[td['Bulan'] == target_last]['Total_Ton']
        last_val    = last_series.values[0] if len(last_series) > 0 else 0.0
        prev_data   = td[td['Bulan'] < target_last]['Total_Ton']
        prev_mean   = prev_data.mean() if len(prev_data) > 0 else 0.0
        growths.append((last_val - prev_mean) / prev_mean if prev_mean > 0 else 0.0)
    agg['Ton_Growth'] = growths

    # Ratio vs Cluster
    cluster_avg = agg.groupby(COL_CLUSTER)['Avg_Ton'].mean().to_dict()
    agg['Ratio_vs_Cluster'] = agg.apply(
        lambda r: r['Avg_Ton'] / cluster_avg.get(r[COL_CLUSTER], 1.0), axis=1
    )

    # ── Brand-mix-weighted Estimated_Cost (RESEARCH FIX) ──────────
    # C_i = SUM_b (Avg_Ton_{i,b} * R_b^(k_i))
    df_valid = df[df['Brand_Category'] != 'Other'].copy()
    ton_brand = (
        df_valid.groupby([COL_ID, 'Brand_Category', 'Reward_per_Ton'])[COL_TON]
        .sum().reset_index()
    )
    ton_brand = ton_brand.merge(agg[[COL_ID, 'Total_Bulan']], on=COL_ID, how='left')
    ton_brand['Avg_Ton_Brand'] = ton_brand[COL_TON] / ton_brand['Total_Bulan']
    ton_brand['Cost_Brand']    = ton_brand['Avg_Ton_Brand'] * ton_brand['Reward_per_Ton']
    cost_per_store = (
        ton_brand.groupby(COL_ID)['Cost_Brand']
        .sum().reset_index()
        .rename(columns={'Cost_Brand': 'Estimated_Cost'})
    )
    agg = agg.merge(cost_per_store, on=COL_ID, how='left')
    agg['Estimated_Cost'] = agg['Estimated_Cost'].fillna(0)

    # Dormancy Risk proxy
    max_bulan = monthly['Bulan'].nunique()
    agg['Dormancy_Risk'] = 1 - (agg['Total_Bulan'] / max(max_bulan, 1))

    # Filter min active months
    agg = agg[agg['Total_Bulan'] >= min_bulan].copy().reset_index(drop=True)
    return agg, monthly

def run_ilp(agg_scored: pd.DataFrame, n_max: int, budget: float,
            cluster_pcts: dict = None, relax: float = 1.0) -> list:
    """
    ILP solver — math.ceil on cluster caps (RESEARCH FIX).
    """
    df = agg_scored.drop_duplicates(subset=[COL_ID]).copy()
    prob   = pulp.LpProblem("Loyalty", pulp.LpMaximize)
    x_vars = {
        row[COL_ID]: pulp.LpVariable(f"x_{i}", cat='Binary')
        for i, row in df.iterrows()
    }

    # Objective
    prob += pulp.lpSum(row['Score'] * x_vars[row[COL_ID]] for _, row in df.iterrows())

    # C1: count quota
    prob += pulp.lpSum(x_vars.values()) <= int(n_max)

    # C2: budget
    if budget > 0:
        prob += pulp.lpSum(
            row['Estimated_Cost'] * x_vars[row[COL_ID]] for _, row in df.iterrows()
        ) <= budget

    # C3: cluster cap — math.ceil (RESEARCH FIX, not floor)
    if cluster_pcts:
        for cl, pct in cluster_pcts.items():
            members = df[df[COL_CLUSTER] == cl][COL_ID].tolist()
            cap = int(math.ceil((pct * relax / 100.0) * n_max))
            if members and cap > 0:
                prob += pulp.lpSum(x_vars[s] for s in members if s in x_vars) <= cap

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    return [s for s, v in x_vars.items() if pulp.value(v) == 1]

def to_excel_multi(sheets: dict) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31], index=False)
        pd.DataFrame({
            'Info':  ['Export Date', 'Tool', 'Version'],
            'Value': [datetime.now().strftime('%Y-%m-%d %H:%M'), 'Loyalty Optimizer', '2.0']
        }).to_excel(w, sheet_name='Metadata', index=False)
    return buf.getvalue()

# ════════════════════════════════════════════════════════════════════
# SIDEBAR
# ════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🎯 Loyalty Optimizer")
    st.markdown('<div style="color:#9EAFC2;font-size:12px;margin-bottom:16px;">v2.0 · Research Edition</div>', unsafe_allow_html=True)

    st.markdown('<div class="section-hdr" style="color:#9EAFC2;">📂 Upload Data</div>', unsafe_allow_html=True)
    uploaded_file   = st.file_uploader("File Transaksi", type=['csv','xlsx','xls','parquet'])
    existing_file   = st.file_uploader("List Toko Existing (CSV)", type=['csv'],
                                        help="1 kolom ID Toko yang saat ini aktif di loyalty program")

    st.markdown('<div class="section-hdr" style="color:#9EAFC2;">📍 Filter Geografis</div>', unsafe_allow_html=True)
    filter_area_ap  = st.multiselect("Area AP Toko", [], key='f_area_ap')
    filter_prov     = st.multiselect("Provinsi Toko", [], key='f_prov')
    filter_area     = st.multiselect("Area Toko", [], key='f_area')

    st.markdown('<div class="section-hdr" style="color:#9EAFC2;">🏅 Filter Performa</div>', unsafe_allow_html=True)
    filter_cluster  = st.multiselect("Cluster Pareto", CLUSTER_ORDER, key='f_cluster')
    min_bulan       = st.number_input("Min. Bulan Aktif", 1, 24, 3)
    min_avg_ton     = st.number_input("Min. Avg Ton/Bulan", 0.0, step=1.0, value=0.0)

    st.markdown('<div class="section-hdr" style="color:#9EAFC2;">❌ Exclude Toko</div>', unsafe_allow_html=True)
    excluded_str    = st.text_area("ID Toko (satu/baris)", height=80)

    st.markdown('<div class="section-hdr" style="color:#9EAFC2;">⚖️ Metode Pembobotan</div>', unsafe_allow_html=True)
    weight_mode     = st.radio("Metode", ['Spearman (Recommended)', 'Manual'], index=0)
    if weight_mode == 'Manual':
        wr = st.slider("w₁ Ratio_vs_Cluster (%)", 0, 100, 47)
        wt = st.slider("w₂ Avg_Trx (%)",           0, 100, 41)
        wg = st.slider("w₃ Ton_Growth (%)",         0, 100, 12)
        wsum = wr + wt + wg
        manual_w = {
            'Ratio_vs_Cluster': wr / max(wsum, 1),
            'Avg_Trx':          wt / max(wsum, 1),
            'Ton_Growth':       wg / max(wsum, 1),
        }

    st.markdown('<div class="section-hdr" style="color:#9EAFC2;">🎯 ILP Settings</div>', unsafe_allow_html=True)
    n_max_mode      = st.radio("Kuota (N_max)", ['= Jumlah Existing', 'Manual'], index=0)
    n_max_manual    = st.number_input("N_max Manual", 1, 10000, 1000,
                                       disabled=(n_max_mode=='= Jumlah Existing'))
    budget_mode     = st.radio("Budget", ['= Biaya Existing (Self-Calibrating)', 'Manual'], index=0)
    budget_manual   = st.number_input("Budget Manual (Rp)", 0, value=1_000_000_000,
                                       disabled=(budget_mode=='= Biaya Existing (Self-Calibrating)'))

    st.markdown("**Cluster Constraint (λ)**")
    ilp_scenario    = st.select_slider(
        "Skenario ILP",
        options=['A: Mirror (λ=1.0)', 'B: Relaxed (λ=1.5)', 'C: Unconstrained'],
        value='A: Mirror (λ=1.0)',
        help="Penelitian: Robustness boundary di λ=1.20x. ILP-A (Mirror) direkomendasikan."
    )
    lambda_val = 1.0 if 'Mirror' in ilp_scenario else (1.5 if 'Relaxed' in ilp_scenario else 999)
    apply_cluster_cap = lambda_val < 999

    st.markdown("---")
    run_btn = st.button("▶  Jalankan Optimasi", type="primary", use_container_width=True,
                         disabled=(uploaded_file is None))

# ════════════════════════════════════════════════════════════════════
# MAIN HEADER
# ════════════════════════════════════════════════════════════════════
st.markdown('<div style="font-size:26px;font-weight:700;color:#1E293B;margin-bottom:4px;">🎯 Loyalty Store Selection Optimizer</div>', unsafe_allow_html=True)
st.markdown('<div style="font-size:14px;color:#64748B;margin-bottom:24px;">Data-Driven Portfolio Optimization · MCS + Integer Linear Programming · Research Edition</div>', unsafe_allow_html=True)

if uploaded_file is None:
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("""
        <div class="card card-blue">
            <div class="section-hdr">Cara Penggunaan</div>
            <p style="color:#475569;font-size:14px;line-height:1.7;">
            <b>1.</b> Upload file transaksi di sidebar<br>
            <b>2.</b> Upload list toko existing loyalty (CSV, 1 kolom ID)<br>
            <b>3.</b> Atur filter dan parameter<br>
            <b>4.</b> Klik Jalankan Optimasi
            </p>
        </div>
        """, unsafe_allow_html=True)
    with col_b:
        st.markdown("""
        <div class="card card-green">
            <div class="section-hdr">Struktur Reward (per Ton)</div>
            <table style="width:100%;font-size:13px;border-collapse:collapse;">
            <tr style="color:#374151;font-weight:600;border-bottom:1px solid #E2E8F0;">
                <td style="padding:6px 0;">Cluster</td><td>Main Brand</td><td>CB / FB</td>
            </tr>
            <tr><td style="padding:4px 0;color:#6B7280;">Platinum & Super Plat.</td>
                <td style="color:#059669;font-weight:600;">Rp 3,750</td><td>Rp 1,875</td></tr>
            <tr><td style="color:#6B7280;">Gold / Silver / Bronze</td>
                <td style="color:#059669;font-weight:600;">Rp 2,500</td><td>Rp 1,250</td></tr>
            </table>
        </div>
        """, unsafe_allow_html=True)
    st.stop()

# ════════════════════════════════════════════════════════════════════
# LOAD & PROCESS DATA
# ════════════════════════════════════════════════════════════════════
with st.spinner("Memuat data..."):
    df_raw = read_uploaded_file(uploaded_file.getvalue(), uploaded_file.name.lower())
    df_raw[COL_ID] = df_raw[COL_ID].astype(str).str.strip()
    df_raw[COL_TGL] = pd.to_datetime(df_raw[COL_TGL], errors='coerce')
    df_raw = df_raw.dropna(subset=[COL_TGL])

# Update sidebar filters dynamically
all_areas_ap = sorted(df_raw[COL_AREA_AP].dropna().unique())
all_provs    = sorted(df_raw[COL_PROV].dropna().unique())
all_areas    = sorted(df_raw[COL_AREA].dropna().unique())

# Load existing loyalty list
existing_ids = set()
if existing_file:
    ex_df = pd.read_csv(existing_file, dtype=str)
    ex_df.columns = [COL_ID]
    existing_ids = set(ex_df[COL_ID].str.strip().unique())

# ── Apply geographic filters ──────────────────────────────────────
df = df_raw.copy()
if filter_area_ap: df = df[df[COL_AREA_AP].isin(filter_area_ap)]
if filter_prov:    df = df[df[COL_PROV].isin(filter_prov)]
if filter_area:    df = df[df[COL_AREA].isin(filter_area)]
if filter_cluster: df = df[df[COL_CLUSTER].isin(filter_cluster)]

if df.empty:
    st.warning("Tidak ada data setelah filter. Sesuaikan filter di sidebar.")
    st.stop()

# ── Build aggregation ─────────────────────────────────────────────
with st.spinner("Menghitung agregasi dan skor..."):
    agg, monthly = build_agg(df, min_bulan)

if min_avg_ton > 0:
    agg = agg[agg['Avg_Ton'] >= min_avg_ton].copy()

# Exclude IDs
if excluded_str:
    excluded = [x.strip() for x in excluded_str.splitlines() if x.strip()]
    agg = agg[~agg[COL_ID].isin(excluded)].copy()

# ── Weights ───────────────────────────────────────────────────────
if weight_mode == 'Spearman (Recommended)':
    weights = compute_spearman_weights(agg)
else:
    weights = manual_w

W1 = weights['Ratio_vs_Cluster']
W2 = weights['Avg_Trx']
W3 = weights['Ton_Growth']

# ── Score ─────────────────────────────────────────────────────────
agg = compute_scores(agg, W1, W2, W3)

# ── N_max & Budget ────────────────────────────────────────────────
existing_in_pool = agg[agg[COL_ID].isin(existing_ids)]
N_MAX = len(existing_ids & set(agg[COL_ID])) if n_max_mode == '= Jumlah Existing' else n_max_manual
N_MAX = max(N_MAX, 1)

BUDGET = existing_in_pool['Estimated_Cost'].sum() \
    if budget_mode == '= Biaya Existing (Self-Calibrating)' else budget_manual

# ── Cluster proportions from existing ────────────────────────────
cluster_pcts = None
if apply_cluster_cap and len(existing_in_pool) > 0:
    cluster_pcts = (
        existing_in_pool[COL_CLUSTER]
        .value_counts(normalize=True).mul(100).round(2).to_dict()
    )

# ════════════════════════════════════════════════════════════════════
# OVERVIEW METRICS
# ════════════════════════════════════════════════════════════════════
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Toko Kandidat", f"{len(agg):,}")
c2.metric("Existing Loyalty", f"{len(existing_ids):,}")
c3.metric("N_max", f"{N_MAX:,}")
c4.metric("Budget Ceiling", fmt_rp(BUDGET) if BUDGET > 0 else "Tidak terbatas")
c5.metric("Bobot Terpilih", weight_mode.split()[0])

st.markdown("---")

# ════════════════════════════════════════════════════════════════════
# TABS
# ════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Dataset & Skor",
    "⚖️ Analisis Bobot",
    "🚀 Optimasi & Benchmark",
    "📅 Tren Bulanan",
    "🔬 Sensitivitas",
    "📋 Export",
])

# ════════════════════════════════════════════════════════════════════
# TAB 1: DATASET & SCORE OVERVIEW
# ════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown('<div class="section-hdr">Statistik Deskriptif per Cluster</div>', unsafe_allow_html=True)

    desc = (agg.groupby(COL_CLUSTER)
            .agg(N_Toko=(COL_ID,'count'), Avg_Ton_Mean=('Avg_Ton','mean'),
                 Avg_Ton_Med=('Avg_Ton','median'), Avg_Trx_Mean=('Avg_Trx','mean'),
                 Growth_Mean=('Ton_Growth','mean'), Cost_Mean=('Estimated_Cost','mean'))
            .reset_index())
    desc[COL_CLUSTER] = pd.Categorical(desc[COL_CLUSTER], CLUSTER_ORDER, ordered=True)
    desc = desc.sort_values(COL_CLUSTER)

    st.dataframe(desc.style.format({
        'N_Toko': '{:,}', 'Avg_Ton_Mean': '{:.2f}', 'Avg_Ton_Med': '{:.2f}',
        'Avg_Trx_Mean': '{:.2f}', 'Growth_Mean': '{:.3f}', 'Cost_Mean': '{:,.0f}',
    }).background_gradient(subset=['Avg_Ton_Mean'], cmap='Blues'),
    use_container_width=True, hide_index=True)

    col_a, col_b = st.columns(2)
    with col_a:
        fig = px.bar(desc, x=COL_CLUSTER, y='N_Toko',
                     color=COL_CLUSTER, color_discrete_map=CLUSTER_COLORS,
                     title='Jumlah Toko per Cluster', template='plotly_white')
        fig.update_layout(showlegend=False, height=300)
        st.plotly_chart(fig, use_container_width=True)
    with col_b:
        fig2 = px.bar(desc, x=COL_CLUSTER, y='Avg_Ton_Mean',
                      color=COL_CLUSTER, color_discrete_map=CLUSTER_COLORS,
                      title='Avg Tonase/Bulan per Cluster', template='plotly_white')
        fig2.update_layout(showlegend=False, height=300)
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown('<div class="section-hdr">Distribusi Variabel Scoring</div>', unsafe_allow_html=True)
    fig3 = make_subplots(rows=1, cols=3,
                          subplot_titles=['Ratio_vs_Cluster', 'Avg_Trx', 'Ton_Growth'])
    colors3 = ['#3B82F6', '#10B981', '#F59E0B']
    for i, (v, c) in enumerate(zip(['Ratio_vs_Cluster','Avg_Trx','Ton_Growth'], colors3), 1):
        skew = agg[v].skew()
        fig3.add_trace(go.Histogram(x=agg[v], nbinsx=40, marker_color=c,
                                     name=v, showlegend=False), row=1, col=i)
        fig3.add_annotation(
            x=0.5, y=1.05, xref=f'x{i} domain', yref=f'y{i} domain',
            text=f"skewness = {skew:+.2f}", showarrow=False,
            font=dict(size=10, color='#6B7280')
        )
    fig3.update_layout(template='plotly_white', height=280,
                        title_text='Distribusi Variabel (skewness > 1 → Spearman lebih tepat dari Pearson)')
    st.plotly_chart(fig3, use_container_width=True)

# ════════════════════════════════════════════════════════════════════
# TAB 2: WEIGHT ANALYSIS
# ════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown('<div class="section-hdr">Perbandingan Metode Pembobotan Objektif</div>', unsafe_allow_html=True)

    # Compute all three methods
    from scipy.stats import pearsonr
    vars_score = ['Ratio_vs_Cluster', 'Avg_Trx', 'Ton_Growth']
    rows_w = []
    for v in vars_score:
        rp, _ = pearsonr(agg[v], agg['Avg_Ton'])
        rs, _ = spearmanr(agg[v], agg['Avg_Ton'])
        rows_w.append({
            'Variabel': v, 'Skewness': round(agg[v].skew(), 3),
            'Pearson_r': abs(rp), 'Spearman_r': abs(rs),
        })
    w_df = pd.DataFrame(rows_w)
    for method, col_r in [('Pearson', 'Pearson_r'), ('Spearman', 'Spearman_r')]:
        total = w_df[col_r].sum()
        w_df[f'{method}_w'] = w_df[col_r] / total

    # EWM
    data_norm = agg[vars_score].copy().reset_index(drop=True)
    data_norm = data_norm.apply(lambda x: (x - x.min()) / (x.max() - x.min() + 1e-9))
    prop = data_norm.apply(lambda x: x / (x.sum() + 1e-9))
    entropy = -(prop * np.log(prop + 1e-9)).sum(axis=0) / np.log(len(data_norm) + 1e-9)
    d = 1 - entropy
    ewm_w = (d / d.sum()).to_dict()
    w_df['EWM_w'] = w_df['Variabel'].map(ewm_w)
    w_df['Final_w'] = w_df['Variabel'].map(weights)

    st.dataframe(w_df.style.format({
        'Skewness': '{:+.3f}', 'Pearson_r': '{:.4f}', 'Spearman_r': '{:.4f}',
        'Pearson_w': '{:.4f}', 'Spearman_w': '{:.4f}', 'EWM_w': '{:.4f}', 'Final_w': '{:.4f}'
    }).highlight_max(subset=['Final_w'], color='#DBEAFE'),
    use_container_width=True, hide_index=True)

    col_w1, col_w2 = st.columns([3, 2])
    with col_w1:
        # Grouped bar chart comparison
        melt = []
        for _, r in w_df.iterrows():
            for m, col in [('Pearson', 'Pearson_w'), ('Spearman', 'Spearman_w'),
                           ('EWM', 'EWM_w'), ('Final', 'Final_w')]:
                melt.append({'Variabel': r['Variabel'], 'Metode': m, 'Bobot': r[col]})
        melt_df = pd.DataFrame(melt)
        fig_w = px.bar(melt_df, x='Variabel', y='Bobot', color='Metode',
                       barmode='group', template='plotly_white',
                       title='Perbandingan Bobot — 3 Metode + Final',
                       color_discrete_sequence=['#3B82F6','#10B981','#F59E0B','#EF4444'])
        fig_w.update_layout(height=320)
        st.plotly_chart(fig_w, use_container_width=True)
    with col_w2:
        st.markdown(f"""
        <div class="card card-green">
            <div class="section-hdr">Bobot Final Terkunci</div>
            <div style="font-family:monospace;font-size:14px;line-height:2;">
                w₁ Ratio = {W1:.4f}<br>
                w₂ Trx   = {W2:.4f}<br>
                w₃ Growth = {W3:.4f}
            </div>
            <div style="font-size:12px;color:#6B7280;margin-top:8px;">
                Metode: {weight_mode.split()[0]}<br>
                Penelitian: Spearman terbukti superior via walk-forward validation.
                Spearman cocok karena skewness semua variabel >6.
            </div>
        </div>
        """, unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════
# TAB 3: OPTIMIZATION & BENCHMARK
# ════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown('<div class="section-hdr">Optimasi ILP & Benchmark 6 Metode</div>', unsafe_allow_html=True)

    if not run_btn and 'opt_results' not in st.session_state:
        st.info("👆 Klik **▶ Jalankan Optimasi** di sidebar untuk menjalankan semua 6 metode.")
    else:
        if run_btn:
            results = {}
            with st.spinner("Menjalankan 6 metode seleksi..."):

                # ── 1. Manual ──────────────────────────────────────
                manual_ids = list(existing_ids & set(agg[COL_ID]))

                # ── 2. Top-N Tonnage ───────────────────────────────
                topn_ids = agg.sort_values('Avg_Ton', ascending=False).head(N_MAX)[COL_ID].tolist()

                # ── 3. Greedy ──────────────────────────────────────
                greedy_ids = agg.sort_values('Score', ascending=False).head(N_MAX)[COL_ID].tolist()

                # ── 4. ILP-A Mirror ────────────────────────────────
                ilpa_ids = run_ilp(agg, N_MAX, BUDGET, cluster_pcts, relax=1.0)

                # ── 5. ILP-B Relaxed ───────────────────────────────
                ilpb_ids = run_ilp(agg, N_MAX, BUDGET, cluster_pcts, relax=1.5)

                # ── 6. ILP-C Unconstrained ─────────────────────────
                ilpc_ids = run_ilp(agg, N_MAX, BUDGET, None, relax=1.0)

            # ── Evaluate each method ─────────────────────────────
            def eval_method(ids, label):
                sel = agg[agg[COL_ID].isin(ids)]
                man = agg[agg[COL_ID].isin(manual_ids)]
                man_ton = man['Avg_Ton'].sum()
                total_cost = sel['Estimated_Cost'].sum()
                total_score = sel['Score'].sum()
                hidden = agg[agg[COL_ID].isin(set(ids) - set(manual_ids))]['Avg_Ton'].sum()
                feasible = (total_cost <= BUDGET * 1.03) if BUDGET > 0 else True
                return {
                    'Metode': label,
                    'N_Toko': len(ids),
                    'Total_Score': round(total_score, 2),
                    'Avg_Score': round(sel['Score'].mean(), 4),
                    'Est_Cost': round(total_cost, 0),
                    'Budget_Util_pct': round(total_cost / BUDGET * 100 if BUDGET > 0 else 0, 2),
                    'Total_Est_Ton': round(sel['Avg_Ton'].sum(), 2),
                    'Delta_Ton_vs_Manual_pct': round(
                        (sel['Avg_Ton'].sum() - man_ton) / man_ton * 100 if man_ton > 0 else 0, 2),
                    'Ton_Tersembunyi': round(hidden, 2),
                    'Overlap_Manual_pct': round(
                        len(set(ids) & set(manual_ids)) / len(manual_ids) * 100 if manual_ids else 0, 2),
                    'Feasible': '✅ Ya' if feasible else '❌ Infeasible',
                    **{f'Cl_{k}': round(
                        sel[COL_CLUSTER].value_counts(normalize=True).get(k, 0) * 100, 1)
                       for k in CLUSTER_ORDER},
                }

            bench = pd.DataFrame([
                eval_method(manual_ids, 'Manual (Existing)'),
                eval_method(topn_ids,   'Top-N Tonnage'),
                eval_method(greedy_ids, 'Greedy'),
                eval_method(ilpa_ids,   'ILP-A Mirror ★'),
                eval_method(ilpb_ids,   'ILP-B Relaxed 1.5×'),
                eval_method(ilpc_ids,   'ILP-C Unconstrained'),
            ])

            selected_final = agg[agg[COL_ID].isin(ilpa_ids)].copy()
            selected_final['Is_New'] = ~selected_final[COL_ID].isin(existing_ids)

            st.session_state['opt_results']     = bench
            st.session_state['ilpa_ids']        = ilpa_ids
            st.session_state['manual_ids']      = manual_ids
            st.session_state['selected_final']  = selected_final
            st.session_state['monthly']         = monthly

        bench = st.session_state['opt_results']
        ilpa_ids = st.session_state['ilpa_ids']
        selected_final = st.session_state['selected_final']

        # ── Summary cards (ILP-A) ────────────────────────────────
        ilpa_row = bench[bench['Metode']=='ILP-A Mirror ★'].iloc[0]
        man_row  = bench[bench['Metode']=='Manual (Existing)'].iloc[0]

        cc1, cc2, cc3, cc4 = st.columns(4)
        cc1.metric("ILP-A Toko Dipilih", f"{ilpa_row['N_Toko']:,}",
                    f"dari N_max {N_MAX:,}")
        cc2.metric("Total Score ILP-A", f"{ilpa_row['Total_Score']:,.2f}",
                    f"+{ilpa_row['Total_Score']-man_row['Total_Score']:,.2f} vs Manual")
        cc3.metric("Budget Utilization", f"{ilpa_row['Budget_Util_pct']:.1f}%")
        cc4.metric("Δ Ton vs Manual", f"+{ilpa_row['Delta_Ton_vs_Manual_pct']:.2f}%")

        st.markdown("---")

        # ── Benchmark table ──────────────────────────────────────
        st.markdown('<div class="section-hdr">Tabel Benchmark 6 Metode</div>', unsafe_allow_html=True)

        show_bench = bench[[
            'Metode', 'N_Toko', 'Total_Score', 'Avg_Score',
            'Budget_Util_pct', 'Total_Est_Ton',
            'Delta_Ton_vs_Manual_pct', 'Ton_Tersembunyi',
            'Overlap_Manual_pct', 'Feasible'
        ]].copy()

        def highlight_feasible(row):
            base = [''] * len(row)
            if row['Feasible'] == '❌ Infeasible':
                return ['background-color:#FEF2F2'] * len(row)
            if 'ILP-A' in str(row['Metode']):
                return ['background-color:#ECFDF5'] * len(row)
            return base

        st.dataframe(
            show_bench.style
            .format({
                'Total_Score': '{:,.2f}', 'Avg_Score': '{:.4f}',
                'Budget_Util_pct': '{:.2f}%', 'Total_Est_Ton': '{:,.2f}',
                'Delta_Ton_vs_Manual_pct': '{:+.2f}%',
                'Ton_Tersembunyi': '{:,.2f}', 'Overlap_Manual_pct': '{:.1f}%',
            })
            .apply(highlight_feasible, axis=1),
            use_container_width=True, hide_index=True
        )

        st.markdown(f"""
        <div class="card card-amber">
        <b>Catatan Feasibilitas:</b> Metode dengan Budget Utilization >103% tidak dapat diterapkan
        (anggaran melebihi batas). Top-N Tonnage dan Greedy biasanya infeasible.
        Di antara metode yang feasible, <b>ILP-A Mirror</b> menghasilkan portfolio terbaik
        berdasarkan riset (validasi prospektif Jan–Mar 2026: Δ=+21.24%, p&lt;0.001, r=0.180).
        </div>
        """, unsafe_allow_html=True)

        # ── Charts ───────────────────────────────────────────────
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            fig_s = px.bar(bench, x='Metode', y='Total_Score',
                           color='Feasible',
                           color_discrete_map={'✅ Ya':'#10B981','❌ Infeasible':'#EF4444'},
                           title='Total Composite Score per Metode',
                           template='plotly_white')
            fig_s.update_layout(showlegend=True, height=320, xaxis_tickangle=-20)
            st.plotly_chart(fig_s, use_container_width=True)
        with col_c2:
            fig_t = px.bar(bench, x='Metode', y='Total_Est_Ton',
                           color='Feasible',
                           color_discrete_map={'✅ Ya':'#3B82F6','❌ Infeasible':'#EF4444'},
                           title='Total Estimasi Tonase Portfolio',
                           template='plotly_white')
            fig_t.update_layout(showlegend=False, height=320, xaxis_tickangle=-20)
            st.plotly_chart(fig_t, use_container_width=True)

        # ── Cluster composition heatmap ──────────────────────────
        st.markdown('<div class="section-hdr">Komposisi Cluster per Metode (%)</div>', unsafe_allow_html=True)
        cl_cols = [c for c in bench.columns if c.startswith('Cl_')]
        if cl_cols:
            hm = bench.set_index('Metode')[cl_cols]
            hm.columns = [c.replace('Cl_','') for c in cl_cols]
            fig_hm = px.imshow(hm.T, color_continuous_scale='Blues',
                                text_auto='.1f', template='plotly_white',
                                title='Heatmap Komposisi Cluster (%)')
            fig_hm.update_layout(height=280)
            st.plotly_chart(fig_hm, use_container_width=True)

        # ── Selected stores table ────────────────────────────────
        st.markdown('<div class="section-hdr">Daftar Toko ILP-A Terpilih</div>', unsafe_allow_html=True)
        search = st.text_input("🔎 Cari ID / Nama / Provinsi", "")
        disp = selected_final.copy()
        if search:
            mask = (
                disp[COL_ID].str.contains(search, case=False, na=False) |
                disp[COL_NAMA].str.contains(search, case=False, na=False) |
                disp[COL_PROV].str.contains(search, case=False, na=False)
            )
            disp = disp[mask]
            st.info(f"{len(disp):,} hasil untuk '{search}'")

        show_cols = [COL_ID, COL_NAMA, COL_CLUSTER, COL_PROV, COL_AREA_AP,
                     'Avg_Ton', 'Avg_Trx', 'Ton_Growth', 'Score', 'Estimated_Cost', 'Is_New']
        avail = [c for c in show_cols if c in disp.columns]
        st.dataframe(
            disp[avail].style.format({
                'Avg_Ton': '{:.2f}', 'Avg_Trx': '{:.1f}',
                'Ton_Growth': '{:.3f}', 'Score': '{:.4f}',
                'Estimated_Cost': '{:,.0f}',
            }),
            use_container_width=True, height=380, hide_index=True
        )

# ════════════════════════════════════════════════════════════════════
# TAB 4: TREND BULANAN
# ════════════════════════════════════════════════════════════════════
with tab4:
    st.markdown('<div class="section-hdr">Tren Performa Bulanan</div>', unsafe_allow_html=True)

    monthly_data = st.session_state.get('monthly', monthly)
    ilpa_set = set(st.session_state.get('ilpa_ids', []))
    manual_set = set(st.session_state.get('manual_ids', []))

    if monthly_data is not None and not monthly_data.empty:
        # Aggregate trend for ILP-A selected stores
        trend_ilpa = monthly_data[monthly_data[COL_ID].isin(ilpa_set)].copy()
        agg_trend = (trend_ilpa.groupby('Bulan')
                     .agg(Total_Ton=('Total_Ton','sum'),
                          Total_Trx=('Jumlah_Trx','sum'),
                          N_Aktif=(COL_ID,'nunique'))
                     .reset_index())

        fig_tr = go.Figure()
        fig_tr.add_trace(go.Scatter(
            x=agg_trend['Bulan'], y=agg_trend['Total_Ton'],
            mode='lines+markers', name='Total Tonase',
            line=dict(color='#3B82F6', width=2), marker=dict(size=6)
        ))
        fig_tr.update_layout(title='Tren Tonase Agregat — Toko ILP-A',
                               template='plotly_white', height=280)
        st.plotly_chart(fig_tr, use_container_width=True)

        # Per-cluster trend
        sel_cluster_map = agg[agg[COL_ID].isin(ilpa_set)][[COL_ID, COL_CLUSTER]].set_index(COL_ID)[COL_CLUSTER].to_dict()
        trend_ilpa['Cluster'] = trend_ilpa[COL_ID].map(sel_cluster_map)
        cl_trend = (trend_ilpa.dropna(subset=['Cluster'])
                    .groupby(['Bulan','Cluster'])['Total_Ton'].sum().reset_index())
        fig_cl = px.line(cl_trend, x='Bulan', y='Total_Ton', color='Cluster',
                          color_discrete_map=CLUSTER_COLORS,
                          title='Tren Tonase per Cluster Pareto',
                          template='plotly_white', markers=True)
        fig_cl.update_layout(height=300)
        st.plotly_chart(fig_cl, use_container_width=True)

        # Per-store trend (up to 10)
        st.markdown('<div class="section-hdr">Tren Toko Individual</div>', unsafe_allow_html=True)
        store_opts = agg[agg[COL_ID].isin(ilpa_set)][COL_NAMA].unique().tolist()
        sel_stores = st.multiselect("Pilih toko (max 10)", store_opts,
                                     default=store_opts[:5], max_selections=10)
        if sel_stores:
            trend_indiv = monthly_data[monthly_data[COL_NAMA].isin(sel_stores)]
            fig_ind = px.line(trend_indiv, x='Bulan', y='Total_Ton', color=COL_NAMA,
                               title='Tren Tonase per Toko', template='plotly_white',
                               markers=True)
            fig_ind.update_layout(height=350)
            st.plotly_chart(fig_ind, use_container_width=True)

# ════════════════════════════════════════════════════════════════════
# TAB 5: SENSITIVITY ANALYSIS
# ════════════════════════════════════════════════════════════════════
with tab5:
    st.markdown('<div class="section-hdr">Sensitivity Analysis — Robustness Model</div>', unsafe_allow_html=True)

    if 'ilpa_ids' not in st.session_state:
        st.info("Jalankan Optimasi terlebih dahulu.")
    else:
        baseline_set = set(st.session_state['ilpa_ids'])

        st.markdown("**Weight Perturbation Sensitivity (Jaccard vs ILP-A Baseline)**")
        sens_scenarios = [
            ('Baseline',     W1,        W2,        W3),
            ('Ratio +10pp',  W1+0.10,   W2-0.05,   W3-0.05),
            ('Ratio +20pp',  W1+0.20,   W2-0.10,   W3-0.10),
            ('Trx +10pp',    W1-0.05,   W2+0.10,   W3-0.05),
            ('Trx +20pp',    W1-0.10,   W2+0.20,   W3-0.10),
            ('Growth +10pp', W1-0.05,   W2-0.05,   W3+0.10),
            ('Growth +20pp', W1-0.10,   W2-0.10,   W3+0.20),
            ('Equal Weight', 1/3,       1/3,        1/3),
        ]

        with st.spinner("Menghitung sensitivity bobot..."):
            sens_rows = []
            for name, nw1, nw2, nw3 in sens_scenarios:
                total = max(nw1 + nw2 + nw3, 1e-9)
                nw1, nw2, nw3 = nw1/total, nw2/total, nw3/total
                agg_s = compute_scores(agg.copy(), nw1, nw2, nw3)
                sel_s = run_ilp(agg_s, N_MAX, BUDGET, cluster_pcts, 1.0)
                j = jaccard(baseline_set, sel_s)
                sens_rows.append({
                    'Skenario': name, 'w1': round(nw1,4), 'w2': round(nw2,4), 'w3': round(nw3,4),
                    'N_Berubah': len(set(sel_s) - baseline_set),
                    'Jaccard': round(j, 4),
                    'Robust': '✅ Ya' if j >= 0.75 else '❌ Tidak',
                })
        sens_df = pd.DataFrame(sens_rows)

        st.dataframe(sens_df.style
            .format({'w1':'{:.4f}','w2':'{:.4f}','w3':'{:.4f}','Jaccard':'{:.4f}'})
            .apply(lambda r: ['background-color:#ECFDF5' if r['Robust']=='✅ Ya'
                               else 'background-color:#FEF2F2']*len(r), axis=1),
            use_container_width=True, hide_index=True)

        fig_j = px.bar(sens_df, x='Skenario', y='Jaccard',
                        color='Robust', color_discrete_map={'✅ Ya':'#10B981','❌ Tidak':'#EF4444'},
                        title='Jaccard Similarity vs ILP-A Baseline',
                        template='plotly_white')
        fig_j.add_hline(y=0.75, line_dash='dash', line_color='#F59E0B',
                         annotation_text='Robustness Threshold (0.75)')
        fig_j.update_layout(height=300, xaxis_tickangle=-20)
        st.plotly_chart(fig_j, use_container_width=True)

        avg_jac = sens_df[sens_df['Skenario']!='Baseline']['Jaccard'].mean()
        st.markdown(f"""
        <div class="card card-green">
        📊 <b>Rata-rata Jaccard:</b> {avg_jac:.4f}
        &nbsp;|&nbsp; <b>Min Jaccard:</b> {sens_df['Jaccard'].min():.4f}
        &nbsp;|&nbsp; Penelitian menemukan batas robustness cluster di λ=1.20x (Jaccard=0.783).
        </div>
        """, unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════
# TAB 6: EXPORT
# ════════════════════════════════════════════════════════════════════
with tab6:
    st.markdown('<div class="section-hdr">Download Hasil</div>', unsafe_allow_html=True)

    if 'opt_results' not in st.session_state:
        st.info("Jalankan Optimasi terlebih dahulu.")
    else:
        bench = st.session_state['opt_results']
        sel   = st.session_state['selected_final']

        sheets = {'Benchmark_6Metode': bench, 'ILP_A_Terpilih': sel}
        if 'sens_df' in dir():
            sheets['Sensitivity_Bobot'] = sens_df

        col_e1, col_e2, col_e3 = st.columns(3)
        with col_e1:
            st.download_button(
                "📊 Excel Multi-Sheet",
                data=to_excel_multi(sheets),
                file_name=f"loyalty_optimizer_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                use_container_width=True
            )
        with col_e2:
            st.download_button(
                "📄 CSV Toko ILP-A",
                data=sel.to_csv(index=False).encode('utf-8-sig'),
                file_name=f"toko_ilp_a_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime='text/csv',
                use_container_width=True
            )
        with col_e3:
            buf = BytesIO()
            sel.to_parquet(buf, index=False)
            st.download_button(
                "🗜️ Parquet Toko ILP-A",
                data=buf.getvalue(),
                file_name=f"toko_ilp_a_{datetime.now().strftime('%Y%m%d_%H%M')}.parquet",
                mime='application/octet-stream',
                use_container_width=True
            )

        # Executive summary
        ilpa_row = bench[bench['Metode']=='ILP-A Mirror ★'].iloc[0]
        man_row  = bench[bench['Metode']=='Manual (Existing)'].iloc[0]
        st.markdown(f"""
        <div class="card card-green" style="margin-top:16px;">
            <div class="section-hdr">Executive Summary — ILP-A Mirror</div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;">
                <div>
                    <div style="font-size:12px;color:#6B7280;">Toko Dipilih</div>
                    <div style="font-size:22px;font-weight:700;color:#1E293B;">{ilpa_row['N_Toko']:,}</div>
                </div>
                <div>
                    <div style="font-size:12px;color:#6B7280;">Budget Utilization</div>
                    <div style="font-size:22px;font-weight:700;color:#1E293B;">{ilpa_row['Budget_Util_pct']:.1f}%</div>
                </div>
                <div>
                    <div style="font-size:12px;color:#6B7280;">Δ Ton vs Manual</div>
                    <div style="font-size:22px;font-weight:700;color:#059669;">+{ilpa_row['Delta_Ton_vs_Manual_pct']:.2f}%</div>
                </div>
                <div>
                    <div style="font-size:12px;color:#6B7280;">Total Score</div>
                    <div style="font-size:22px;font-weight:700;color:#1E293B;">{ilpa_row['Total_Score']:,.2f}</div>
                </div>
                <div>
                    <div style="font-size:12px;color:#6B7280;">Hidden Tonnage</div>
                    <div style="font-size:22px;font-weight:700;color:#3B82F6;">{ilpa_row['Ton_Tersembunyi']:,.0f}</div>
                </div>
                <div>
                    <div style="font-size:12px;color:#6B7280;">Avg Score / Toko</div>
                    <div style="font-size:22px;font-weight:700;color:#1E293B;">{ilpa_row['Avg_Score']:.4f}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

# Footer
st.markdown("---")
st.markdown("""
<div style="text-align:center;color:#94A3B8;font-size:12px;padding:8px 0;">
Loyalty Store Selection Optimizer v2.0 · Research Edition · MCS + ILP + DSR
</div>
""", unsafe_allow_html=True)
