import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime
import math
import altair as alt
import pulp
import warnings
from scipy.stats import spearmanr

warnings.filterwarnings('ignore')

# ============================================================
# Konfigurasi halaman
# ============================================================
st.set_page_config(
    page_title="Loyalty Target Optimizer",
    layout="wide",
    page_icon="🎯"
)

st.markdown("""
<style>
    .stMetric { background: #f0f4ff; border-radius: 10px; padding: 10px; }
    .block-container { padding-top: 1.5rem; }
    div[data-testid="stSidebar"] { background: #1a1a2e; color: white; }
    div[data-testid="stSidebar"] .stMarkdown { color: #ccc; }
    .section-header {
        background: linear-gradient(90deg, #1a1a2e, #16213e);
        color: white; padding: 12px 20px; border-radius: 10px;
        margin: 1rem 0 0.5rem 0; font-weight: 600;
    }
    .info-box  { background: #e8f4fd; padding: 12px; border-left: 4px solid #2196F3; border-radius: 5px; }
    .warn-box  { background: #fff8e1; padding: 12px; border-left: 4px solid #FF9800; border-radius: 5px; }
    .ok-box    { background: #e8f5e9; padding: 12px; border-left: 4px solid #4CAF50; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

st.title("🎯 Loyalty Program Optimizer & Analyzer")
st.markdown("Aplikasi memilih toko terbaik untuk program loyalty menggunakan "
            "**Multi-Criteria Scoring + ILP-A Mirror** (model terbaik berdasarkan riset).")

# ============================================================
# KONSTANTA — REWARD STRUCTURE (dari riset)
# ============================================================
# Reward rate per ton per cluster × brand role (Rp/ton)
REWARD_RATES = {
    'Platinum':       {'Main Brand': 3750, 'Companion Brand': 1875, 'Fighting Brand': 1875},
    'Super Platinum': {'Main Brand': 3750, 'Companion Brand': 1875, 'Fighting Brand': 1875},
    'Gold':           {'Main Brand': 2500, 'Companion Brand': 1250, 'Fighting Brand': 1250},
    'Silver':         {'Main Brand': 2500, 'Companion Brand': 1250, 'Fighting Brand': 1250},
    'Bronze':         {'Main Brand': 2500, 'Companion Brand': 1250, 'Fighting Brand': 1250},
}

# Province-level brand mapping (dari riset)
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
        'LAMPUNG':          {'main': ['BATURAJA'], 'companion': ['DYNAMIX']},
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

# ============================================================
# FUNGSI BANTUAN
# ============================================================

def normalize(series):
    return (series - series.min()) / (series.max() - series.min() + 1e-9)


def get_brand_category(area, brand, prov):
    """Province-level brand categorization (research best practice)."""
    area_up  = str(area).strip().upper()
    prov_up  = str(prov).strip().upper()
    brand_up = str(brand).strip().upper()

    area_map = BRAND_MAP_BY_PROV.get(area_up)
    if area_map is None:
        return 'Other'

    prov_map = area_map.get(prov_up)
    if prov_map is None:
        for key in area_map:
            if key in prov_up or prov_up in key:
                prov_map = area_map[key]
                break

    if prov_map is None:
        fallback = {
            'SP':   {'main': ['PADANG'],   'companion': ['DYNAMIX', 'ANDALAS', 'BATURAJA']},
            'SMBR': {'main': ['BATURAJA'], 'companion': ['DYNAMIX', 'PADANG']},
            'ST':   {'main': ['TONASA'],   'companion': ['GRESIK']},
        }
        prov_map = fallback.get(area_up, {'main': [], 'companion': []})

    if any(kw in brand_up for kw in prov_map['main']):
        return 'Main Brand'
    if prov_map['companion'] and any(kw in brand_up for kw in prov_map['companion']):
        return 'Companion Brand'
    if area_up == 'ST' and 'MERDEKA' in brand_up:
        if str(prov).strip() in FIGHTING_BRAND_PROVINCES:
            return 'Fighting Brand'
    return 'Other'


def get_reward_per_ton(cluster, brand_cat):
    """Reward rate dari tabel riset."""
    return REWARD_RATES.get(cluster, REWARD_RATES['Bronze']).get(brand_cat, 0.0)


def compute_spearman_weights(agg_df):
    """
    Bobot data-driven via Spearman rank correlation (research validated).
    Spearman dipilih karena distribusi variabel sangat skewed (skewness > 6).
    """
    vars_score = ['Ratio_vs_Cluster', 'Avg_Trx', 'Ton_Growth']
    raw = {}
    for v in vars_score:
        r, _ = spearmanr(agg_df[v], agg_df['Avg_Ton'])
        raw[v] = abs(r)
    total = sum(raw.values()) or 1
    return {k: v / total for k, v in raw.items()}


def compute_scores(agg_df, w1, w2, w3):
    """Skor komposit: S_i = w1*Ratio + w2*norm(Trx) + w3*norm(Growth)."""
    temp = agg_df.copy()
    temp['Score'] = (
        w1 * temp['Ratio_vs_Cluster'] +
        w2 * normalize(temp['Avg_Trx']) +
        w3 * normalize(temp['Ton_Growth'])
    )
    return temp


def read_uploaded_file(uploaded_file):
    fname = uploaded_file.name.lower()
    if fname.endswith('.csv'):
        return pd.read_csv(uploaded_file, dtype={'ID Toko': str})
    elif fname.endswith(('.xlsx', '.xls')):
        return pd.read_excel(uploaded_file, dtype={'ID Toko': str})
    elif fname.endswith('.parquet'):
        df = pd.read_parquet(uploaded_file)
        if 'ID Toko' in df.columns:
            df['ID Toko'] = df['ID Toko'].astype(str)
        return df
    raise ValueError(f"Format tidak didukung: {uploaded_file.name}")


def to_excel_bytes_multi(selected_df, summary_df, trend_df=None):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        selected_df.to_excel(writer, index=False, sheet_name='Toko Terpilih')
        summary_df.to_excel(writer, index=False, sheet_name='Ringkasan Cluster')
        if trend_df is not None and not trend_df.empty:
            trend_df.to_excel(writer, index=False, sheet_name='Tren Bulanan')
        meta = pd.DataFrame({
            'Keterangan': ['Tanggal Export', 'Total Toko Terpilih', 'Estimasi Cost'],
            'Nilai': [
                datetime.now().strftime('%Y-%m-%d %H:%M'),
                len(selected_df),
                f"Rp {selected_df['Estimated_Cost'].sum():,.0f}"
                if 'Estimated_Cost' in selected_df.columns else '-'
            ]
        })
        meta.to_excel(writer, index=False, sheet_name='Metadata')
    return output.getvalue()


# ============================================================
# LANGKAH 1 — UPLOAD & PROSES DATA
# ============================================================
st.markdown('<div class="section-header">📁 Langkah 1: Upload & Proses Data</div>',
            unsafe_allow_html=True)

col_up, col_ex = st.columns([2, 1])
with col_up:
    uploaded_file = st.file_uploader(
        "📤 Upload file transaksi",
        type=['csv', 'xlsx', 'xls', 'parquet'],
        help="Format: CSV, Excel, atau Parquet"
    )
with col_ex:
    existing_file = st.file_uploader(
        "📋 Upload list toko existing loyalty (CSV)",
        type=['csv'],
        help="1 kolom: ID Toko yang saat ini aktif di program loyalty"
    )

if uploaded_file:
    col1, col2 = st.columns([3, 1])
    with col1:
        try:
            cache_key = uploaded_file.name
            if ('df_raw' not in st.session_state
                    or st.session_state.get('uploaded_filename') != cache_key):
                st.session_state.df_raw = read_uploaded_file(uploaded_file)
                st.session_state.uploaded_filename = cache_key

            df_raw = st.session_state.df_raw
            file_size_kb = uploaded_file.size / 1024
            ext = uploaded_file.name.split('.')[-1].upper()
            st.markdown(
                f'<div class="info-box">📄 <b>{uploaded_file.name}</b> — {ext} | '
                f'{df_raw.shape[0]:,} baris × {df_raw.shape[1]} kolom | '
                f'{file_size_kb:.1f} KB</div>',
                unsafe_allow_html=True
            )

            available_brands = sorted(df_raw['Brands'].dropna().unique())
            selected_brands = st.multiselect(
                "🏷️ Pilih Brand",
                available_brands,
                default=available_brands
            )
            st.session_state.selected_brands = selected_brands

        except Exception as e:
            st.error(f"Gagal membaca file: {e}")
            st.stop()

    with col2:
        st.write("👇 Klik untuk proses:")
        if st.button("⚙️ Proses Data & Hitung Skor", type="primary"):
            with st.spinner("Memproses data..."):
                df_raw = st.session_state.df_raw
                selected_brands = st.session_state.selected_brands

                required_cols = [
                    'Tanggal Transaksi', 'ID Toko', 'Nama Toko', 'Cluster Pareto',
                    'Area AP Toko', 'Provinsi Toko', 'Area Toko', 'Brands', 'TON Quantity'
                ]
                missing = [c for c in required_cols if c not in df_raw.columns]
                if missing:
                    st.error(f"Kolom wajib hilang: {missing}")
                    st.stop()
                if not selected_brands:
                    st.warning("Pilih minimal 1 brand.")
                    st.stop()

                df = df_raw[df_raw['Brands'].isin(selected_brands)].copy()
                df['TON Quantity'] = df['TON Quantity'].fillna(0)
                df['Tanggal Transaksi'] = pd.to_datetime(
                    df['Tanggal Transaksi'], errors='coerce')
                df.dropna(subset=['Tanggal Transaksi'], inplace=True)
                df.sort_values(['ID Toko', 'Tanggal Transaksi'], inplace=True)

                # Fill categorical columns per toko
                cat_cols = ['Nama Toko', 'Cluster Pareto', 'Area AP Toko',
                            'Provinsi Toko', 'Area Toko', 'Brands']
                for col in cat_cols:
                    if col in df.columns:
                        df[col] = df.groupby('ID Toko')[col].transform(
                            lambda x: x.ffill().bfill())

                df.dropna(subset=['Nama Toko', 'Cluster Pareto',
                                  'Area AP Toko', 'Provinsi Toko', 'Area Toko'],
                          inplace=True)
                if df.empty:
                    st.warning("Tidak ada data valid setelah dibersihkan.")
                    st.stop()

                df['Bulan'] = df['Tanggal Transaksi'].dt.to_period('M').astype(str)

                # ── Penambahan Brand Category (research best practice) ──────
                df['Brand_Category'] = df.apply(
                    lambda r: get_brand_category(
                        r['Area AP Toko'], r['Brands'], r['Provinsi Toko']), axis=1
                )
                df['Reward_per_Ton'] = df.apply(
                    lambda r: get_reward_per_ton(r['Cluster Pareto'], r['Brand_Category']),
                    axis=1
                )

                # Monthly aggregation
                grouped = df.groupby(
                    ['ID Toko', 'Nama Toko', 'Cluster Pareto',
                     'Area AP Toko', 'Provinsi Toko', 'Area Toko', 'Bulan']
                ).agg(
                    Total_Ton=('TON Quantity', 'sum'),
                    Jumlah_Transaksi=('Tanggal Transaksi', 'count')
                ).reset_index()

                # Store-level aggregation
                agg = grouped.groupby(
                    ['ID Toko', 'Nama Toko', 'Cluster Pareto',
                     'Area AP Toko', 'Provinsi Toko', 'Area Toko']
                ).agg(
                    Avg_Ton=('Total_Ton', 'mean'),
                    Avg_Trx=('Jumlah_Transaksi', 'mean'),
                    Total_Bulan_Aktif=('Bulan', 'nunique')
                ).reset_index()

                # ── Survivorship-corrected Ton_Growth (research fix) ────────
                target_last = grouped['Bulan'].max()
                growths = []
                for sid in agg['ID Toko']:
                    td = grouped[grouped['ID Toko'] == sid].sort_values('Bulan')
                    last_series = td[td['Bulan'] == target_last]['Total_Ton']
                    last_val = last_series.values[0] if len(last_series) > 0 else 0.0
                    prev_data = td[td['Bulan'] < target_last]['Total_Ton']
                    prev_mean = prev_data.mean() if len(prev_data) > 0 else 0.0
                    g = (last_val - prev_mean) / prev_mean if prev_mean > 0 else 0.0
                    growths.append(g)
                agg['Ton_Growth'] = growths

                # Ratio vs Cluster
                cluster_avg = agg.groupby('Cluster Pareto')['Avg_Ton'].mean().to_dict()
                agg['Ratio_vs_Cluster'] = agg.apply(
                    lambda r: r['Avg_Ton'] / cluster_avg.get(r['Cluster Pareto'], 1.0),
                    axis=1
                )

                # ── Brand-mix weighted Estimated_Cost (research fix) ────────
                df_valid = df[df['Brand_Category'] != 'Other'].copy()
                ton_brand = (
                    df_valid.groupby(['ID Toko', 'Brand_Category', 'Reward_per_Ton'])
                    ['TON Quantity'].sum().reset_index()
                )
                ton_brand = ton_brand.merge(
                    agg[['ID Toko', 'Total_Bulan_Aktif']], on='ID Toko', how='left')
                ton_brand['Avg_Ton_Brand'] = (
                    ton_brand['TON Quantity'] / ton_brand['Total_Bulan_Aktif'])
                ton_brand['Cost_Brand'] = (
                    ton_brand['Avg_Ton_Brand'] * ton_brand['Reward_per_Ton'])
                cost_per_store = (
                    ton_brand.groupby('ID Toko')['Cost_Brand']
                    .sum().reset_index()
                    .rename(columns={'Cost_Brand': 'Estimated_Cost'})
                )
                agg = agg.merge(cost_per_store, on='ID Toko', how='left')
                agg['Estimated_Cost'] = agg['Estimated_Cost'].fillna(0)

                # Bobot via Spearman (research validated)
                weights = compute_spearman_weights(agg)
                agg = compute_scores(
                    agg,
                    weights['Ratio_vs_Cluster'],
                    weights['Avg_Trx'],
                    weights['Ton_Growth']
                )

                st.session_state.agg      = agg
                st.session_state.df       = df
                st.session_state.grouped  = grouped
                st.session_state.weights  = weights
                st.success(
                    f"✅ Data berhasil diproses! {agg.shape[0]:,} toko unik ditemukan. "
                    f"Bobot Spearman: w1={weights['Ratio_vs_Cluster']:.3f}, "
                    f"w2={weights['Avg_Trx']:.3f}, "
                    f"w3={weights['Ton_Growth']:.3f}"
                )

st.markdown("---")

# ============================================================
# LANGKAH 2 — FILTER & OPTIMASI
# ============================================================
if 'agg' not in st.session_state:
    st.stop()

base_agg = st.session_state.agg
weights  = st.session_state.weights

# Load existing loyalty list
existing_ids = set()
if existing_file:
    try:
        ex_df = pd.read_csv(existing_file, dtype=str)
        ex_df.columns = ['ID Toko']
        existing_ids = set(ex_df['ID Toko'].str.strip().unique())
    except Exception as e:
        st.warning(f"Gagal membaca file existing: {e}")

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🛠️ Panel Kontrol")
    st.markdown("---")

    st.markdown("### 📍 Filter Geografis")
    avail_ap = sorted(base_agg['Area AP Toko'].unique())
    sel_ap   = st.multiselect("Area AP Toko (Wajib)", avail_ap, default=avail_ap)
    if not sel_ap:
        st.warning("Pilih minimal satu Area AP.")
        st.stop()

    agg_ap   = base_agg[base_agg['Area AP Toko'].isin(sel_ap)].copy()
    avail_pv = sorted(agg_ap['Provinsi Toko'].unique())
    sel_pv   = st.multiselect("Provinsi Toko (opsional)", avail_pv, default=[])
    agg_pv   = agg_ap[agg_ap['Provinsi Toko'].isin(sel_pv)].copy() if sel_pv else agg_ap.copy()

    avail_at = sorted(agg_pv['Area Toko'].unique())
    sel_at   = st.multiselect("Area Toko (opsional)", avail_at, default=[])
    agg      = agg_pv[agg_pv['Area Toko'].isin(sel_at)].copy() if sel_at else agg_pv.copy()

    st.markdown("---")
    st.markdown("### 🏅 Filter Cluster & Performa")
    all_clusters = sorted(agg['Cluster Pareto'].unique())
    sel_cluster  = st.multiselect("Cluster Pareto (opsional)", all_clusters, default=[])
    if sel_cluster:
        agg = agg[agg['Cluster Pareto'].isin(sel_cluster)].copy()

    min_avg_ton = st.number_input(
        "Min. Rata-rata Tonase / Bulan", min_value=0.0, value=0.0, step=0.5)
    if min_avg_ton > 0:
        agg = agg[agg['Avg_Ton'] >= min_avg_ton].copy()

    min_bulan = st.number_input("Min. Bulan Aktif Transaksi", min_value=1, value=3, step=1)
    if min_bulan > 1 and 'Total_Bulan_Aktif' in agg.columns:
        agg = agg[agg['Total_Bulan_Aktif'] >= min_bulan].copy()

    st.markdown("---")
    st.markdown("### ❌ Kecualikan ID Toko")
    excluded_str = st.text_area(
        "ID Toko (satu per baris)", placeholder="Tempel ID dari Excel...", height=100)
    if excluded_str:
        excl = [x.strip() for x in excluded_str.splitlines() if x.strip()]
        agg['ID Toko'] = agg['ID Toko'].astype(str)
        agg = agg[~agg['ID Toko'].isin(excl)].copy()

    st.markdown("---")
    st.markdown("### 💰 Anggaran & Kuota")

    # Self-calibrating budget dari existing (research best practice)
    existing_in_pool = agg[agg['ID Toko'].isin(existing_ids)]
    auto_budget = existing_in_pool['Estimated_Cost'].sum()
    use_auto_budget = st.toggle(
        "Gunakan budget dari existing roster (self-calibrating)",
        value=True,
        help="Budget = total estimasi biaya toko yang saat ini aktif. "
             "Ini memastikan sistem tidak minta anggaran lebih besar."
    )
    if use_auto_budget and auto_budget > 0:
        max_budget = auto_budget
        st.markdown(
            f'<div style="color:#aaa;font-size:12px;">Budget: Rp {auto_budget:,.0f}</div>',
            unsafe_allow_html=True)
    else:
        max_budget = st.number_input(
            "Anggaran Maks (Rp)", 0, value=1_000_000_000, step=50_000_000)

    total_available = agg.shape[0]
    # N_max dari jumlah existing (research best practice: apple-to-apple)
    auto_nmax = len(existing_ids & set(agg['ID Toko'])) if existing_ids else min(500, total_available)
    use_auto_nmax = st.toggle(
        "N_max = jumlah toko existing (apple-to-apple)",
        value=bool(existing_ids),
        help="Kuota = jumlah toko yang saat ini aktif di program loyalty."
    )
    if use_auto_nmax and auto_nmax > 0:
        N_max = auto_nmax
        st.markdown(
            f'<div style="color:#aaa;font-size:12px;">N_max: {N_max:,} toko</div>',
            unsafe_allow_html=True)
    else:
        N_max = st.number_input(
            "Jumlah Toko Maks (N_max)", 1, max(1, total_available),
            value=min(500, total_available), step=1)

    st.markdown("---")
    st.markdown("### ⚖️ Bobot Skor")
    st.markdown(
        "**Mode Spearman (Riset)** — bobot dihitung otomatis dari data. "
        "Bisa override manual jika diperlukan."
    )
    use_spearman = st.toggle("Gunakan bobot Spearman (recommended)", value=True)
    if use_spearman:
        w1 = weights['Ratio_vs_Cluster']
        w2 = weights['Avg_Trx']
        w3 = weights['Ton_Growth']
        st.markdown(
            f'<div style="color:#aaa;font-size:12px;">'
            f'w1={w1:.3f} · w2={w2:.3f} · w3={w3:.3f}</div>',
            unsafe_allow_html=True)
    else:
        wr = st.slider("Ratio_vs_Cluster (%)", 0, 100, 47)
        wt = st.slider("Avg_Trx (%)",          0, 100, 41)
        wg = st.slider("Ton_Growth (%)",        0, 100, 12)
        total_w = wr + wt + wg or 1
        w1, w2, w3 = wr/total_w, wt/total_w, wg/total_w

    # Re-score dengan bobot yang dipilih
    agg = compute_scores(agg, w1, w2, w3)

    st.markdown("---")
    st.markdown("### 🎯 Batas Cluster (Mirror Constraint)")
    st.markdown(
        "ILP-A Mirror: komposisi cluster = proporsi existing roster. "
        "Bisa relaksasi per cluster di bawah ini."
    )
    # Hitung proporsi existing untuk dijadikan default cap
    if len(existing_in_pool) > 0:
        existing_cluster_pcts = (
            existing_in_pool['Cluster Pareto']
            .value_counts(normalize=True).mul(100).round(1).to_dict()
        )
    else:
        existing_cluster_pcts = {}

    clusters_list = sorted(agg['Cluster Pareto'].unique())
    cluster_pct_inputs = {}
    for c in clusters_list:
        default_pct = existing_cluster_pcts.get(c, 0.0)
        v = st.number_input(
            f"Maks {c} (%)",
            0.0, 100.0,
            value=round(default_pct, 1),
            step=1.0,
            key=f"clpct_{c}",
            help=f"Existing: {default_pct:.1f}%. Set 0 = tidak ada batasan cluster ini."
        )
        cluster_pct_inputs[c] = v

    st.markdown("---")
    run_optimize = st.button("▶️ Jalankan Optimasi ILP-A", type="primary",
                              use_container_width=True)

# ── Status pool kandidat ──────────────────────────────────────
st.markdown(
    f'<div class="info-box">🗂️ <b>{agg.shape[0]:,} toko</b> siap dioptimasi. '
    f'N_max = <b>{N_max:,}</b> · '
    f'Budget = <b>Rp {max_budget:,.0f}</b></div>',
    unsafe_allow_html=True
)

# ── What-If preview ───────────────────────────────────────────
with st.expander("🔮 Simulasi What-If: Preview Distribusi Skor", expanded=False):
    st.markdown("Preview bobot sebelum menjalankan optimasi penuh.")
    wif1, wif2, wif3 = st.columns(3)
    with wif1:
        wi_r = st.slider("Ratio_vs_Cluster (%)", 0, 100,
                          int(w1 * 100), key="wi_r")
    with wif2:
        wi_t = st.slider("Avg_Trx (%)", 0, 100,
                          int(w2 * 100), key="wi_t")
    with wif3:
        wi_g = st.slider("Ton_Growth (%)", 0, 100,
                          int(w3 * 100), key="wi_g")

    wi_sum = wi_r + wi_t + wi_g
    if wi_sum > 0 and not agg.empty:
        prev = compute_scores(agg, wi_r/wi_sum, wi_t/wi_sum, wi_g/wi_sum)
        pc1, pc2, pc3 = st.columns(3)
        pc1.metric("Skor Tertinggi", f"{prev['Score'].max():.4f}")
        pc2.metric("Skor Rata-rata", f"{prev['Score'].mean():.4f}")
        pc3.metric("Skor Terendah",  f"{prev['Score'].min():.4f}")
        hist = (
            alt.Chart(prev)
            .mark_bar(opacity=0.8)
            .encode(
                x=alt.X('Score:Q', bin=alt.Bin(maxbins=30), title='Skor'),
                y=alt.Y('count()', title='Jumlah Toko'),
                color=alt.Color('Cluster Pareto:N'),
                tooltip=['Cluster Pareto', 'count()']
            )
            .properties(height=220)
        )
        st.altair_chart(hist, use_container_width=True)

st.markdown("---")

# ============================================================
# JALANKAN OPTIMASI
# ============================================================
if run_optimize:
    agg_final = agg.copy()
    agg_final.drop_duplicates(subset=['ID Toko'], keep='first', inplace=True)
    agg_final.sort_values('Score', ascending=False, inplace=True, ignore_index=True)

    st.session_state.total_eligible  = len(agg_final)
    st.session_state.N_max_run       = N_max
    st.session_state.budget_run      = max_budget

    with st.spinner("Menjalankan ILP-A Mirror..."):
        prob   = pulp.LpProblem("Loyalty_ILP_A", pulp.LpMaximize)
        x_vars = {
            row['ID Toko']: pulp.LpVariable(f"x_{i}", cat='Binary')
            for i, row in agg_final.iterrows()
        }

        # Objective: maximise total score
        prob += pulp.lpSum(
            row['Score'] * x_vars[row['ID Toko']]
            for _, row in agg_final.iterrows()
        )

        # C1: kuota toko
        prob += pulp.lpSum(x_vars.values()) <= int(N_max)

        # C2: budget
        prob += pulp.lpSum(
            row['Estimated_Cost'] * x_vars[row['ID Toko']]
            for _, row in agg_final.iterrows()
        ) <= max_budget

        # C3: cluster cap — math.ceil (research fix, bukan floor)
        for cl, pct in cluster_pct_inputs.items():
            if pct > 0:
                members = agg_final[agg_final['Cluster Pareto'] == cl]['ID Toko'].tolist()
                cap = int(math.ceil((pct / 100.0) * N_max))
                if members:
                    prob += pulp.lpSum(x_vars[s] for s in members) <= cap

        prob.solve(pulp.PULP_CBC_CMD(msg=False))

    selected_ids = [s for s, v in x_vars.items() if pulp.value(v) == 1]
    agg_final['ID Toko'] = agg_final['ID Toko'].astype(str)
    sel = agg_final[agg_final['ID Toko'].isin(selected_ids)].sort_values(
        'Score', ascending=False, ignore_index=True)

    # Tandai toko baru (tidak ada di existing)
    sel['Is_New_Store'] = ~sel['ID Toko'].isin(existing_ids)

    st.session_state.selected_df = sel
    st.success(f"✅ Optimasi selesai! {len(sel):,} toko terpilih.")
    st.balloons()

# ============================================================
# HASIL & ANALISIS
# ============================================================
if 'selected_df' not in st.session_state:
    st.stop()

selected_df = st.session_state.selected_df
n_eligible  = st.session_state.get('total_eligible', 1)
N_max_run   = st.session_state.get('N_max_run', 1)
budget_run  = st.session_state.get('budget_run', 1)

budget_used = selected_df['Estimated_Cost'].sum()
pct_selected = len(selected_df) / n_eligible * 100 if n_eligible else 0
budget_util  = budget_used / budget_run * 100 if budget_run else 0
n_new        = selected_df['Is_New_Store'].sum() if 'Is_New_Store' in selected_df.columns else 0
hidden_ton   = selected_df[selected_df['Is_New_Store']]['Avg_Ton'].sum() \
    if 'Is_New_Store' in selected_df.columns else 0

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Ringkasan",
    "📈 Kontribusi & Efisiensi",
    "🔍 Perbandingan Toko",
    "📅 Tren Bulanan",
    "📋 Data & Export",
])

# ════════════ TAB 1: RINGKASAN ════════════
with tab1:
    st.markdown('<div class="section-header">✅ Ringkasan Hasil ILP-A Mirror</div>',
                unsafe_allow_html=True)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Toko Terpilih", f"{len(selected_df):,}",
               f"{pct_selected:.1f}% dari {n_eligible:,}")
    m2.metric("Cluster Terwakili", f"{selected_df['Cluster Pareto'].nunique()}")
    m3.metric("Est. Budget/Bulan", f"Rp {budget_used:,.0f}")
    m4.metric("Utilisasi Anggaran", f"{budget_util:.1f}%")
    m5.metric("Toko Baru (Hidden)", f"{n_new:,}",
               f"{hidden_ton:,.0f} ton tersembunyi")

    st.markdown("---")

    # Cluster composition
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.subheader("Komposisi Cluster Pareto")
        cs = selected_df['Cluster Pareto'].value_counts().reset_index()
        cs.columns = ['Cluster Pareto', 'Jumlah Toko']
        cs['Persen'] = (cs['Jumlah Toko'] / len(selected_df) * 100).round(2)
        cs['Est. Budget'] = cs['Cluster Pareto'].map(
            selected_df.groupby('Cluster Pareto')['Estimated_Cost'].sum().to_dict())
        st.dataframe(
            cs.style.format({'Persen': '{:.2f}%', 'Est. Budget': 'Rp {:,.0f}'}),
            use_container_width=True, hide_index=True
        )
    with col_c2:
        pie = (
            alt.Chart(cs)
            .mark_arc(innerRadius=50)
            .encode(
                theta=alt.Theta('Jumlah Toko:Q'),
                color=alt.Color('Cluster Pareto:N'),
                tooltip=['Cluster Pareto', 'Jumlah Toko', 'Persen']
            )
            .properties(height=260, title="Distribusi Cluster")
        )
        st.altair_chart(pie, use_container_width=True)

    # Geo distribution
    st.subheader("Distribusi Geografis")
    g1, g2 = st.columns(2)
    with g1:
        pv = selected_df['Provinsi Toko'].value_counts().reset_index()
        pv.columns = ['Provinsi', 'Jumlah']
        prov_chart = (
            alt.Chart(pv.head(15))
            .mark_bar()
            .encode(
                x=alt.X('Jumlah:Q'),
                y=alt.Y('Provinsi:N', sort='-x'),
                color=alt.Color('Jumlah:Q', scale=alt.Scale(scheme='blues')),
                tooltip=['Provinsi', 'Jumlah']
            )
            .properties(height=350, title="Per Provinsi")
        )
        st.altair_chart(prov_chart, use_container_width=True)
    with g2:
        av = selected_df['Area AP Toko'].value_counts().reset_index()
        av.columns = ['Area AP', 'Jumlah']
        area_chart = (
            alt.Chart(av)
            .mark_bar(color='#FF6B6B')
            .encode(
                x=alt.X('Jumlah:Q'),
                y=alt.Y('Area AP:N', sort='-x'),
                tooltip=['Area AP', 'Jumlah']
            )
            .properties(height=350, title="Per Area AP")
        )
        st.altair_chart(area_chart, use_container_width=True)

# ════════════ TAB 2: KONTRIBUSI ════════════
with tab2:
    st.markdown('<div class="section-header">📈 Analisis Kontribusi & Efisiensi</div>',
                unsafe_allow_html=True)

    if not selected_df.empty:
        df2 = selected_df.copy()
        total_score  = df2['Score'].sum()
        total_budget = df2['Estimated_Cost'].sum()
        df2['Kontribusi_Skor_%']   = df2['Score'] / total_score * 100
        df2['Kontribusi_Budget_%'] = df2['Estimated_Cost'] / (total_budget + 1e-9) * 100
        df2['Efisiensi']           = df2['Score'] / (df2['Estimated_Cost'] + 1e-9) * 1_000_000
        df2['Label']               = df2['ID Toko'].astype(str) + ' — ' + df2['Nama Toko']

        c1, c2 = st.columns(2)
        with c1:
            st.write("**Top 10 Kontributor Skor**")
            top_s = df2.nlargest(10, 'Kontribusi_Skor_%')
            st.altair_chart(
                alt.Chart(top_s).mark_bar(color='#4CAF50').encode(
                    x=alt.X('Kontribusi_Skor_%:Q', title='Kontribusi Skor (%)'),
                    y=alt.Y('Label:N', sort='-x', title=''),
                    tooltip=['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Kontribusi_Skor_%']
                ),
                use_container_width=True
            )
        with c2:
            st.write("**Top 10 Kontributor Budget**")
            top_b = df2.nlargest(10, 'Kontribusi_Budget_%')
            st.altair_chart(
                alt.Chart(top_b).mark_bar(color='#FF9800').encode(
                    x=alt.X('Kontribusi_Budget_%:Q', title='Kontribusi Budget (%)'),
                    y=alt.Y('Label:N', sort='-x', title=''),
                    tooltip=['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Kontribusi_Budget_%']
                ),
                use_container_width=True
            )

        st.subheader("Scatter: Skor vs Biaya")
        scatter = (
            alt.Chart(df2)
            .mark_circle()
            .encode(
                x=alt.X('Estimated_Cost:Q', title='Est. Biaya (Rp)'),
                y=alt.Y('Score:Q', title='Skor'),
                color=alt.Color('Cluster Pareto:N'),
                size=alt.Size('Avg_Ton:Q', title='Avg Ton'),
                tooltip=['ID Toko', 'Nama Toko', 'Cluster Pareto',
                         'Score', 'Estimated_Cost', 'Efisiensi']
            )
            .interactive()
            .properties(height=350)
        )
        st.altair_chart(scatter, use_container_width=True)

        st.subheader("Top 20 Toko Paling Efisien (Skor per Juta Rp Biaya)")
        top_eff = df2.nlargest(20, 'Efisiensi')[
            ['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Score',
             'Estimated_Cost', 'Efisiensi']
        ].copy()
        st.dataframe(
            top_eff.style.format({
                'Score': '{:.4f}',
                'Estimated_Cost': 'Rp {:,.0f}',
                'Efisiensi': '{:,.2f}'
            }),
            use_container_width=True, hide_index=True
        )

# ════════════ TAB 3: PERBANDINGAN TOKO ════════════
with tab3:
    st.markdown('<div class="section-header">🔍 Perbandingan Toko Side-by-Side</div>',
                unsafe_allow_html=True)

    all_opts = (selected_df['ID Toko'] + ' — ' + selected_df['Nama Toko']).tolist()
    chosen   = st.multiselect(
        "Pilih 2–4 toko:", all_opts,
        default=all_opts[:min(3, len(all_opts))],
        max_selections=4
    )

    if chosen:
        ids_chosen = [t.split(' — ')[0] for t in chosen]
        cmp_df     = selected_df[selected_df['ID Toko'].isin(ids_chosen)].copy()

        cols_cmp = st.columns(len(cmp_df))
        metrics  = [
            ('Score',            'Skor',            '{:.4f}'),
            ('Avg_Ton',          'Avg Ton/Bulan',   '{:.2f} Ton'),
            ('Avg_Trx',          'Avg Trx/Bulan',   '{:.1f}'),
            ('Ton_Growth',       'Ton Growth',       '{:.2%}'),
            ('Ratio_vs_Cluster', 'Ratio vs Cluster', '{:.2f}x'),
            ('Estimated_Cost',   'Est. Biaya/Bulan', 'Rp {:,.0f}'),
        ]

        for col_ui, (_, row) in zip(cols_cmp, cmp_df.iterrows()):
            with col_ui:
                st.markdown(f"### 🏪 {row['Nama Toko']}")
                is_new = row.get('Is_New_Store', False)
                badge = '🆕 Toko Baru' if is_new else '✅ Existing'
                st.markdown(
                    f"**ID:** {row['ID Toko']}  \n"
                    f"**Cluster:** {row['Cluster Pareto']}  \n"
                    f"**Provinsi:** {row['Provinsi Toko']}  \n"
                    f"**Status:** {badge}"
                )
                st.markdown("---")
                for field, label, fmt in metrics:
                    val = row.get(field, 'N/A')
                    if isinstance(val, (int, float)):
                        st.metric(label, fmt.format(val))
                    else:
                        st.metric(label, str(val))

        # Multi-dim comparison bar chart
        st.markdown("---")
        st.subheader("Perbandingan Multi-Dimensi (Ternormalisasi)")
        mets = ['Score', 'Avg_Ton', 'Ton_Growth', 'Avg_Trx', 'Ratio_vs_Cluster']
        radar_rows = []
        for _, row in cmp_df.iterrows():
            for m in mets:
                norm_val = float(normalize(cmp_df[m])[cmp_df['ID Toko'] == row['ID Toko']].values[0])
                radar_rows.append({'Toko': row['Nama Toko'], 'Metrik': m, 'Nilai': norm_val})
        radar_df = pd.DataFrame(radar_rows)
        radar_chart = (
            alt.Chart(radar_df)
            .mark_bar()
            .encode(
                x=alt.X('Toko:N', title=''),
                y=alt.Y('Nilai:Q', title='0–1'),
                color=alt.Color('Toko:N'),
                column=alt.Column('Metrik:N'),
                tooltip=['Toko', 'Metrik', 'Nilai']
            )
            .properties(width=90, height=200)
        )
        st.altair_chart(radar_chart)

        # Trend comparison
        if 'grouped' in st.session_state:
            st.subheader("Tren Tonase Toko Terpilih")
            grp = st.session_state.grouped.copy()
            grp['ID Toko'] = grp['ID Toko'].astype(str)
            trend_cmp = grp[grp['ID Toko'].isin(ids_chosen)]
            if not trend_cmp.empty:
                st.altair_chart(
                    alt.Chart(trend_cmp)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X('Bulan:N', sort=None),
                        y=alt.Y('Total_Ton:Q', title='Total Ton'),
                        color=alt.Color('Nama Toko:N'),
                        tooltip=['ID Toko', 'Nama Toko', 'Bulan', 'Total_Ton']
                    )
                    .interactive()
                    .properties(height=300),
                    use_container_width=True
                )

# ════════════ TAB 4: TREN BULANAN ════════════
with tab4:
    st.markdown('<div class="section-header">📅 Tren Performa Bulanan</div>',
                unsafe_allow_html=True)

    if 'grouped' in st.session_state and not selected_df.empty:
        grp = st.session_state.grouped.copy()
        grp['ID Toko'] = grp['ID Toko'].astype(str)
        trend = grp[grp['ID Toko'].isin(selected_df['ID Toko'])]

        # Agregat semua toko terpilih
        st.subheader("Tren Agregat Semua Toko Terpilih")
        agg_tr = (trend.groupby('Bulan')
                  .agg(Total_Ton=('Total_Ton', 'sum'),
                       N_Aktif=('ID Toko', 'nunique'))
                  .reset_index())
        st.altair_chart(
            alt.Chart(agg_tr)
            .mark_line(point=True, color='#1976D2')
            .encode(
                x=alt.X('Bulan:N', sort=None),
                y=alt.Y('Total_Ton:Q', title='Total Tonase'),
                tooltip=['Bulan', 'Total_Ton', 'N_Aktif']
            )
            .properties(height=250),
            use_container_width=True
        )

        # Per cluster
        st.subheader("Tren per Cluster Pareto")
        sel_cl = selected_df[['ID Toko', 'Cluster Pareto']].drop_duplicates()
        tr_merged = trend.merge(sel_cl, on='ID Toko', how='left').dropna(
            subset=['Cluster Pareto'])
        cl_trend = (tr_merged.groupby(['Bulan', 'Cluster Pareto'])['Total_Ton']
                    .sum().reset_index())
        st.altair_chart(
            alt.Chart(cl_trend)
            .mark_line(point=True)
            .encode(
                x=alt.X('Bulan:N', sort=None),
                y=alt.Y('Total_Ton:Q', title='Total Tonase'),
                color='Cluster Pareto:N',
                tooltip=['Bulan', 'Cluster Pareto', 'Total_Ton']
            )
            .interactive()
            .properties(height=300),
            use_container_width=True
        )

        # Per toko
        st.subheader("Perbandingan Tren per Toko")
        toko_opts = selected_df['Nama Toko'].unique().tolist()
        sel_toko  = st.multiselect(
            "Pilih toko (maks 10):", toko_opts,
            default=toko_opts[:5], max_selections=10
        )
        if sel_toko:
            st.altair_chart(
                alt.Chart(trend[trend['Nama Toko'].isin(sel_toko)])
                .mark_line(point=True)
                .encode(
                    x=alt.X('Bulan:N', sort=None),
                    y=alt.Y('Total_Ton:Q', title='Total Tonase'),
                    color=alt.Color('Nama Toko:N'),
                    tooltip=['ID Toko', 'Nama Toko', 'Bulan', 'Total_Ton']
                )
                .interactive()
                .properties(height=350),
                use_container_width=True
            )

# ════════════ TAB 5: DATA & EXPORT ════════════
with tab5:
    st.markdown('<div class="section-header">📋 Data Lengkap & Export</div>',
                unsafe_allow_html=True)

    search = st.text_input("🔎 Cari ID / Nama Toko / Provinsi", "")
    disp   = selected_df.copy()
    if search:
        mask = (
            disp['ID Toko'].str.contains(search, case=False, na=False) |
            disp['Nama Toko'].str.contains(search, case=False, na=False) |
            disp['Provinsi Toko'].str.contains(search, case=False, na=False)
        )
        disp = disp[mask]
        st.info(f"Menampilkan {len(disp):,} hasil untuk: '{search}'")

    show_cols = [
        'ID Toko', 'Nama Toko', 'Cluster Pareto', 'Area AP Toko',
        'Provinsi Toko', 'Area Toko', 'Avg_Ton', 'Avg_Trx', 'Ton_Growth',
        'Score', 'Estimated_Cost', 'Is_New_Store'
    ]
    avail = [c for c in show_cols if c in disp.columns]
    fmt   = {
        'Avg_Ton': '{:.2f}', 'Avg_Trx': '{:.1f}',
        'Ton_Growth': '{:.2%}', 'Score': '{:.4f}',
        'Estimated_Cost': 'Rp {:,.0f}',
    }
    st.dataframe(
        disp[avail].style.format({k: v for k, v in fmt.items() if k in avail}),
        use_container_width=True, height=400, hide_index=True
    )

    st.markdown("---")
    st.subheader("⬇️ Download Hasil")

    cluster_sum = selected_df['Cluster Pareto'].value_counts().reset_index()
    cluster_sum.columns = ['Cluster Pareto', 'Jumlah Toko']
    trend_exp = None
    if 'grouped' in st.session_state:
        g = st.session_state.grouped.copy()
        g['ID Toko'] = g['ID Toko'].astype(str)
        trend_exp = g[g['ID Toko'].isin(selected_df['ID Toko'])]

    e1, e2, e3 = st.columns(3)
    with e1:
        st.download_button(
            "📊 Download Excel (Multi-Sheet)",
            data=to_excel_bytes_multi(disp[avail], cluster_sum, trend_exp),
            file_name=f"loyalty_ilpa_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            use_container_width=True
        )
    with e2:
        st.download_button(
            "📄 Download CSV",
            data=selected_df[avail].to_csv(index=False).encode('utf-8-sig'),
            file_name=f"loyalty_ilpa_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime='text/csv',
            use_container_width=True
        )
    with e3:
        buf = BytesIO()
        selected_df[avail].to_parquet(buf, index=False)
        st.download_button(
            "🗜️ Download Parquet",
            data=buf.getvalue(),
            file_name=f"loyalty_ilpa_{datetime.now().strftime('%Y%m%d_%H%M')}.parquet",
            mime='application/octet-stream',
            use_container_width=True
        )
