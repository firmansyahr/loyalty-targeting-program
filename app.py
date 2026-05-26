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

st.set_page_config(page_title="Loyalty Target Optimizer", layout="wide", page_icon="🎯")
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
    .info-box { background:#e8f4fd; padding:12px; border-left:4px solid #2196F3; border-radius:5px; }
    .warn-box { background:#fff8e1; padding:12px; border-left:4px solid #FF9800; border-radius:5px; }
    .ok-box   { background:#e8f5e9; padding:12px; border-left:4px solid #4CAF50; border-radius:5px; }
</style>
""", unsafe_allow_html=True)

st.title("🎯 Loyalty Program Optimizer & Analyzer")
st.markdown("Aplikasi memilih toko terbaik untuk program loyalty menggunakan "
            "**Multi-Criteria Scoring + ILP-A Mirror** (model terbaik berdasarkan riset).")

# ============================================================
# KONSTANTA
# ============================================================
JAWA_BALI = ['Jawa Barat','Jawa Tengah','Jawa Timur',
             'DKI Jakarta','Banten','DI Yogyakarta','Bali']

MIN_BULAN_AKTIF = 3

# FIX 1: UPPERCASE agar cocok dengan data aktual
FIGHTING_BRAND_PROVINCES = [
    'KALIMANTAN TIMUR','KALIMANTAN UTARA',
    'SULAWESI TENGAH','SULAWESI SELATAN',
]

REWARD_RATES = {
    'Platinum'      :{'Main Brand':3750,'Companion Brand':1875,'Fighting Brand':1875},
    'Super Platinum':{'Main Brand':3750,'Companion Brand':1875,'Fighting Brand':1875},
    'Gold'          :{'Main Brand':2500,'Companion Brand':1250,'Fighting Brand':1250},
    'Silver'        :{'Main Brand':2500,'Companion Brand':1250,'Fighting Brand':1250},
    'Bronze'        :{'Main Brand':2500,'Companion Brand':1250,'Fighting Brand':1250},
}

BRAND_MAP_BY_PROV = {
    'SP': {
        'ACEH'          :{'main':['PADANG'],'companion':['ANDALAS','DYNAMIX']},
        'RIAU DARATAN'  :{'main':['PADANG'],'companion':['DYNAMIX']},
        'RIAU KEPULAUAN':{'main':['PADANG'],'companion':['ANDALAS']},
        'SUMATERA BARAT':{'main':['PADANG'],'companion':[]},
        'SUMATERA UTARA':{'main':['PADANG'],'companion':['ANDALAS','DYNAMIX']},
        'BENGKULU'      :{'main':['PADANG'],'companion':['DYNAMIX']},
        'JAMBI'         :{'main':['PADANG'],'companion':[]},
    },
    'SMBR': {
        'SUMATERA SELATAN':{'main':['BATURAJA'],'companion':['PADANG','DYNAMIX']},
        'LAMPUNG'         :{'main':['BATURAJA'],'companion':['DYNAMIX']},
    },
    'ST': {
        'SULAWESI BARAT'    :{'main':['TONASA'],'companion':[]},
        'SULAWESI SELATAN'  :{'main':['TONASA'],'companion':[]},
        'SULAWESI TENGAH'   :{'main':['TONASA'],'companion':[]},
        'SULAWESI TENGGARA' :{'main':['TONASA'],'companion':[]},
        'SULAWESI UTARA'    :{'main':['TONASA'],'companion':[]},
        'GORONTALO'         :{'main':['TONASA'],'companion':[]},
        'MALUKU'            :{'main':['TONASA'],'companion':[]},
        'MALUKU UTARA'      :{'main':['TONASA'],'companion':[]},
        'N.T.T.'            :{'main':['TONASA'],'companion':[]},
        'N.T.B.'            :{'main':['TONASA'],'companion':['GRESIK']},
        'PAPUA'             :{'main':['TONASA'],'companion':['GRESIK']},
        'PAPUA BARAT'       :{'main':['TONASA'],'companion':['GRESIK']},
        'KALIMANTAN SELATAN':{'main':['TONASA'],'companion':['GRESIK']},
        'KALIMANTAN TIMUR'  :{'main':['TONASA'],'companion':['GRESIK']},
        'KALIMANTAN UTARA'  :{'main':['TONASA'],'companion':['GRESIK']},
    },
}

# ============================================================
# FUNGSI BANTUAN
# ============================================================
def normalize(series):
    return (series - series.min()) / (series.max() - series.min() + 1e-9)


def get_brand_category(area, brand, prov):
    area_up  = str(area).strip().upper()
    prov_up  = str(prov).strip().upper()   # FIX 2: .upper() sebelum cek
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
            'SP'  :{'main':['PADANG'],   'companion':['DYNAMIX','ANDALAS','BATURAJA']},
            'SMBR':{'main':['BATURAJA'], 'companion':['DYNAMIX','PADANG']},
            'ST'  :{'main':['TONASA'],   'companion':['GRESIK']},
        }
        prov_map = fallback.get(area_up, {'main':[],'companion':[]})

    if any(kw in brand_up for kw in prov_map['main']):
        return 'Main Brand'
    if prov_map['companion'] and any(kw in brand_up for kw in prov_map['companion']):
        return 'Companion Brand'
    # FIX 2: prov_up sudah UPPERCASE, FB_PROVINCES juga UPPERCASE → cocok
    if area_up == 'ST' and 'MERDEKA' in brand_up and prov_up in FIGHTING_BRAND_PROVINCES:
        return 'Fighting Brand'
    return 'Other'


def get_reward_per_ton(cluster, brand_cat):
    return REWARD_RATES.get(cluster, REWARD_RATES['Bronze']).get(brand_cat, 0.0)


def compute_spearman_weights(agg_df):
    vars_score = ['Ratio_vs_Cluster','Avg_Trx','Ton_Growth']
    raw = {}
    for v in vars_score:
        r, _ = spearmanr(agg_df[v], agg_df['Avg_Ton'])
        raw[v] = abs(r)
    total = sum(raw.values()) or 1
    return {k: v/total for k,v in raw.items()}


def compute_scores(agg_df, w1, w2, w3):
    temp = agg_df.copy()
    # Ratio_vs_Cluster tidak dinormalisasi (sudah scale-free)
    # Avg_Trx & Ton_Growth dinormalisasi karena satuan berbeda
    temp['Score'] = (
        w1 * temp['Ratio_vs_Cluster'] +
        w2 * normalize(temp['Avg_Trx']) +
        w3 * normalize(temp['Ton_Growth'])
    )
    return temp


def read_uploaded_file(uploaded_file):
    fname = uploaded_file.name.lower()
    if fname.endswith('.csv'):
        return pd.read_csv(uploaded_file, dtype={'ID Toko':str})
    elif fname.endswith(('.xlsx','.xls')):
        return pd.read_excel(uploaded_file, dtype={'ID Toko':str})
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
        pd.DataFrame({
            'Keterangan':['Tanggal Export','Total Toko','Est. Cost'],
            'Nilai':[datetime.now().strftime('%Y-%m-%d %H:%M'),
                     len(selected_df),
                     f"Rp {selected_df['Estimated_Cost'].sum():,.0f}"
                     if 'Estimated_Cost' in selected_df.columns else '-']
        }).to_excel(writer, index=False, sheet_name='Metadata')
    return output.getvalue()


# ============================================================
# LANGKAH 1 — UPLOAD & PROSES DATA
# ============================================================
st.markdown('<div class="section-header">📁 Langkah 1: Upload & Proses Data</div>',
            unsafe_allow_html=True)

col_up, col_ex = st.columns([2,1])
with col_up:
    uploaded_file = st.file_uploader("📤 Upload file transaksi",
                                      type=['csv','xlsx','xls','parquet'])
with col_ex:
    existing_file = st.file_uploader("📋 Upload list toko existing loyalty (CSV)",
                                      type=['csv'],
                                      help="1 kolom: ID Toko yang aktif di program loyalty")

if uploaded_file:
    col1, col2 = st.columns([3,1])
    with col1:
        try:
            cache_key = uploaded_file.name
            if ('df_raw' not in st.session_state
                    or st.session_state.get('uploaded_filename') != cache_key):
                st.session_state.df_raw = read_uploaded_file(uploaded_file)
                st.session_state.uploaded_filename = cache_key

            df_raw = st.session_state.df_raw
            st.markdown(
                f'<div class="info-box">📄 <b>{uploaded_file.name}</b> — '
                f'{df_raw.shape[0]:,} baris × {df_raw.shape[1]} kolom | '
                f'{uploaded_file.size/1024:.1f} KB</div>',
                unsafe_allow_html=True)

            available_brands = sorted(df_raw['Brands'].dropna().unique())
            selected_brands  = st.multiselect("🏷️ Pilih Brand",
                                               available_brands,
                                               default=available_brands)
            st.session_state.selected_brands = selected_brands
        except Exception as e:
            st.error(f"Gagal membaca file: {e}"); st.stop()

    with col2:
        st.write("👇 Klik untuk proses:")
        if st.button("⚙️ Proses Data & Hitung Skor", type="primary"):
            with st.spinner("Memproses data..."):
                df_raw = st.session_state.df_raw
                selected_brands = st.session_state.selected_brands

                required = ['Tanggal Transaksi','ID Toko','Nama Toko','Cluster Pareto',
                            'Area AP Toko','Provinsi Toko','Area Toko','Brands','TON Quantity']
                missing = [c for c in required if c not in df_raw.columns]
                if missing: st.error(f"Kolom wajib hilang: {missing}"); st.stop()
                if not selected_brands: st.warning("Pilih minimal 1 brand."); st.stop()

                df = df_raw[df_raw['Brands'].isin(selected_brands)].copy()
                df['TON Quantity'] = df['TON Quantity'].fillna(0)
                df['Tanggal Transaksi'] = pd.to_datetime(df['Tanggal Transaksi'], errors='coerce')
                df.dropna(subset=['Tanggal Transaksi'], inplace=True)
                df.sort_values(['ID Toko','Tanggal Transaksi'], inplace=True)

                for col in ['Nama Toko','Cluster Pareto','Area AP Toko',
                            'Provinsi Toko','Area Toko','Brands']:
                    if col in df.columns:
                        df[col] = df.groupby('ID Toko')[col].transform(
                            lambda x: x.ffill().bfill())

                df.dropna(subset=['Nama Toko','Cluster Pareto',
                                  'Area AP Toko','Provinsi Toko','Area Toko'], inplace=True)

                # FIX 3: Filter Jawa-Bali
                n_before = len(df)
                df = df[~df['Provinsi Toko'].isin(JAWA_BALI)].copy()
                n_removed = n_before - len(df)
                if n_removed > 0:
                    st.info(f"ℹ️ {n_removed:,} baris Jawa-Bali difilter (scope riset: luar Jawa-Bali)")

                if df.empty: st.warning("Tidak ada data valid."); st.stop()

                df['Bulan'] = df['Tanggal Transaksi'].dt.to_period('M').astype(str)

                # Brand category (dengan uppercase fix)
                df['Brand_Category'] = df.apply(
                    lambda r: get_brand_category(
                        r['Area AP Toko'], r['Brands'], r['Provinsi Toko']), axis=1)
                df['Reward_per_Ton'] = df.apply(
                    lambda r: get_reward_per_ton(r['Cluster Pareto'], r['Brand_Category']),
                    axis=1)

                # FIX 5: Filter Other SEBELUM agregasi Avg_Ton
                df_valid = df[df['Brand_Category'] != 'Other'].copy()
                if df_valid.empty: st.warning("Tidak ada transaksi brand valid."); st.stop()

                # Tahap 1: SUM per toko per bulan
                grouped = df_valid.groupby(
                    ['ID Toko','Nama Toko','Cluster Pareto',
                     'Area AP Toko','Provinsi Toko','Area Toko','Bulan']
                ).agg(
                    Total_Ton =('TON Quantity','sum'),
                    Jumlah_Trx=('Tanggal Transaksi','count')
                ).reset_index()

                # Tahap 2: MEAN antar bulan per toko (konsisten dengan notebook)
                agg = grouped.groupby(
                    ['ID Toko','Nama Toko','Cluster Pareto',
                     'Area AP Toko','Provinsi Toko','Area Toko']
                ).agg(
                    Avg_Ton          =('Total_Ton','mean'),
                    Avg_Trx          =('Jumlah_Trx','mean'),
                    Total_Bulan_Aktif=('Bulan','nunique')
                ).reset_index()

                # FIX 4: Filter min bulan aktif
                n_before = len(agg)
                agg = agg[agg['Total_Bulan_Aktif'] >= MIN_BULAN_AKTIF].copy()
                st.info(f"ℹ️ {n_before-len(agg):,} toko difilter (aktif < {MIN_BULAN_AKTIF} bulan)")

                # Ton_Growth survivorship-corrected
                target_last = grouped['Bulan'].max()
                growths = []
                for sid in agg['ID Toko']:
                    td = grouped[grouped['ID Toko']==sid].sort_values('Bulan')
                    lv = td[td['Bulan']==target_last]['Total_Ton']
                    lv = lv.values[0] if len(lv)>0 else 0.0
                    pv = td[td['Bulan']<target_last]['Total_Ton']
                    pm = pv.mean() if len(pv)>0 else 0.0
                    growths.append((lv-pm)/pm if pm>0 else 0.0)
                agg['Ton_Growth'] = growths

                # Ratio vs Cluster
                cl_avg = agg.groupby('Cluster Pareto')['Avg_Ton'].mean().to_dict()
                agg['Ratio_vs_Cluster'] = agg.apply(
                    lambda r: r['Avg_Ton']/cl_avg.get(r['Cluster Pareto'],1.0), axis=1)

                # Estimated_Cost brand-mix weighted
                ton_brand = (df_valid.groupby(['ID Toko','Brand_Category','Reward_per_Ton'])
                             ['TON Quantity'].sum().reset_index())
                ton_brand = ton_brand.merge(
                    agg[['ID Toko','Total_Bulan_Aktif']], on='ID Toko', how='left')
                ton_brand['Avg_Ton_Brand'] = ton_brand['TON Quantity']/ton_brand['Total_Bulan_Aktif']
                ton_brand['Cost_Brand']    = ton_brand['Avg_Ton_Brand']*ton_brand['Reward_per_Ton']
                cost = (ton_brand.groupby('ID Toko')['Cost_Brand'].sum().reset_index()
                        .rename(columns={'Cost_Brand':'Estimated_Cost'}))
                agg = agg.merge(cost, on='ID Toko', how='left')
                agg['Estimated_Cost'] = agg['Estimated_Cost'].fillna(0)

                # Bobot Spearman
                weights = compute_spearman_weights(agg)
                agg = compute_scores(agg, weights['Ratio_vs_Cluster'],
                                     weights['Avg_Trx'], weights['Ton_Growth'])

                st.session_state.agg     = agg
                st.session_state.df      = df_valid
                st.session_state.grouped = grouped
                st.session_state.weights = weights
                st.success(
                    f"✅ {agg.shape[0]:,} toko unik | "
                    f"w1={weights['Ratio_vs_Cluster']:.3f} "
                    f"w2={weights['Avg_Trx']:.3f} "
                    f"w3={weights['Ton_Growth']:.3f}")

st.markdown("---")

# ============================================================
# LANGKAH 2 — FILTER & OPTIMASI
# ============================================================
if 'agg' not in st.session_state:
    st.stop()

base_agg = st.session_state.agg
weights  = st.session_state.weights

existing_ids = set()
if existing_file:
    try:
        ex_df = pd.read_csv(existing_file, dtype=str)
        ex_df.columns = ['ID Toko']
        existing_ids = set(ex_df['ID Toko'].str.strip().unique())
    except Exception as e:
        st.warning(f"Gagal membaca file existing: {e}")

with st.sidebar:
    st.markdown("## 🛠️ Panel Kontrol")
    st.markdown("---")

    st.markdown("### 📍 Filter Geografis")
    avail_ap = sorted(base_agg['Area AP Toko'].unique())
    sel_ap   = st.multiselect("Area AP Toko (Wajib)", avail_ap, default=avail_ap)
    if not sel_ap: st.warning("Pilih minimal satu Area AP."); st.stop()

    agg_ap = base_agg[base_agg['Area AP Toko'].isin(sel_ap)].copy()
    avail_pv = sorted(agg_ap['Provinsi Toko'].unique())
    sel_pv   = st.multiselect("Provinsi Toko (opsional)", avail_pv, default=[])
    agg_pv   = agg_ap[agg_ap['Provinsi Toko'].isin(sel_pv)].copy() if sel_pv else agg_ap.copy()

    avail_at = sorted(agg_pv['Area Toko'].unique())
    sel_at   = st.multiselect("Area Toko (opsional)", avail_at, default=[])
    agg      = agg_pv[agg_pv['Area Toko'].isin(sel_at)].copy() if sel_at else agg_pv.copy()

    st.markdown("---")
    st.markdown("### ❌ Kecualikan ID Toko")
    excluded_str = st.text_area("ID Toko (satu per baris)", height=80)
    if excluded_str:
        excl = [x.strip() for x in excluded_str.splitlines() if x.strip()]
        agg['ID Toko'] = agg['ID Toko'].astype(str)
        agg = agg[~agg['ID Toko'].isin(excl)].copy()

    st.markdown("---")
    st.markdown("### 💰 Anggaran & Kuota")
    existing_in_pool = agg[agg['ID Toko'].isin(existing_ids)]
    auto_budget = existing_in_pool['Estimated_Cost'].sum()

    use_auto_budget = st.toggle("Budget dari existing roster (self-calibrating)", value=True)
    if use_auto_budget and auto_budget > 0:
        max_budget = auto_budget
        st.markdown(f'<div style="color:#aaa;font-size:12px;">Budget: Rp {auto_budget:,.0f}</div>',
                    unsafe_allow_html=True)
    else:
        max_budget = st.number_input("Anggaran Maks (Rp)", 0, value=1_000_000_000, step=50_000_000)

    auto_nmax = len(existing_ids & set(agg['ID Toko'])) if existing_ids else min(500, len(agg))
    use_auto_nmax = st.toggle("N_max = jumlah toko existing", value=bool(existing_ids))
    if use_auto_nmax and auto_nmax > 0:
        N_max = auto_nmax
        st.markdown(f'<div style="color:#aaa;font-size:12px;">N_max: {N_max:,}</div>',
                    unsafe_allow_html=True)
    else:
        N_max = st.number_input("Jumlah Toko Maks (N_max)", 1, max(1,len(agg)),
                                 value=min(500,len(agg)), step=1)

    st.markdown("---")
    st.markdown("### ⚖️ Bobot Skor")
    use_spearman = st.toggle("Bobot Spearman otomatis (recommended)", value=True)
    if use_spearman:
        w1,w2,w3 = weights['Ratio_vs_Cluster'],weights['Avg_Trx'],weights['Ton_Growth']
        st.markdown(f'<div style="color:#aaa;font-size:12px;">w1={w1:.3f} · w2={w2:.3f} · w3={w3:.3f}</div>',
                    unsafe_allow_html=True)
    else:
        wr = st.slider("Ratio_vs_Cluster (%)",0,100,47)
        wt = st.slider("Avg_Trx (%)",0,100,41)
        wg = st.slider("Ton_Growth (%)",0,100,12)
        tw = wr+wt+wg or 1
        w1,w2,w3 = wr/tw, wt/tw, wg/tw

    agg = compute_scores(agg, w1, w2, w3)

    st.markdown("---")
    st.markdown("### 🎯 Batas Cluster (Mirror Constraint)")
    existing_cluster_pcts = (existing_in_pool['Cluster Pareto']
                              .value_counts(normalize=True).mul(100).round(1).to_dict()
                              if len(existing_in_pool)>0 else {})
    clusters_list = sorted(agg['Cluster Pareto'].unique())
    cluster_pct_inputs = {}
    for c in clusters_list:
        default_pct = existing_cluster_pcts.get(c, 0.0)
        v = st.number_input(f"Maks {c} (%)", 0.0, 100.0,
                             value=round(default_pct,1), step=1.0, key=f"clpct_{c}",
                             help=f"Existing: {default_pct:.1f}%")
        cluster_pct_inputs[c] = v

    st.markdown("---")
    run_optimize = st.button("▶️ Jalankan Optimasi ILP-A", type="primary",
                              use_container_width=True)

st.markdown(
    f'<div class="info-box">🗂️ <b>{agg.shape[0]:,} toko</b> siap | '
    f'N_max=<b>{N_max:,}</b> · Budget=<b>Rp {max_budget:,.0f}</b></div>',
    unsafe_allow_html=True)

with st.expander("🔮 Simulasi What-If: Preview Skor", expanded=False):
    wc1,wc2,wc3 = st.columns(3)
    wi_r = wc1.slider("Ratio (%)",0,100,int(w1*100),key="wi_r")
    wi_t = wc2.slider("Trx (%)",  0,100,int(w2*100),key="wi_t")
    wi_g = wc3.slider("Growth (%)",0,100,int(w3*100),key="wi_g")
    ws = wi_r+wi_t+wi_g
    if ws>0 and not agg.empty:
        prev = compute_scores(agg, wi_r/ws, wi_t/ws, wi_g/ws)
        pc1,pc2,pc3 = st.columns(3)
        pc1.metric("Skor Max",  f"{prev['Score'].max():.4f}")
        pc2.metric("Skor Mean", f"{prev['Score'].mean():.4f}")
        pc3.metric("Skor Min",  f"{prev['Score'].min():.4f}")
        st.altair_chart(
            alt.Chart(prev).mark_bar(opacity=0.8)
            .encode(x=alt.X('Score:Q',bin=alt.Bin(maxbins=30)),
                    y='count()',color='Cluster Pareto:N')
            .properties(height=200),
            use_container_width=True)

st.markdown("---")

# ============================================================
# OPTIMASI ILP-A
# ============================================================
if run_optimize:
    agg_final = agg.drop_duplicates(subset=['ID Toko']).copy()
    agg_final.sort_values('Score', ascending=False, inplace=True, ignore_index=True)

    st.session_state.total_eligible = len(agg_final)
    st.session_state.N_max_run      = N_max
    st.session_state.budget_run     = max_budget

    with st.spinner("Menjalankan ILP-A Mirror..."):
        prob   = pulp.LpProblem("Loyalty_ILP_A", pulp.LpMaximize)
        x_vars = {row['ID Toko']: pulp.LpVariable(f"x_{i}", cat='Binary')
                   for i,row in agg_final.iterrows()}

        prob += pulp.lpSum(row['Score']*x_vars[row['ID Toko']]
                           for _,row in agg_final.iterrows())
        prob += pulp.lpSum(x_vars.values()) <= int(N_max)
        prob += pulp.lpSum(row['Estimated_Cost']*x_vars[row['ID Toko']]
                           for _,row in agg_final.iterrows()) <= max_budget

        for cl,pct in cluster_pct_inputs.items():
            if pct > 0:
                members = agg_final[agg_final['Cluster Pareto']==cl]['ID Toko'].tolist()
                cap = int(math.ceil((pct/100.0)*N_max))
                if members:
                    prob += pulp.lpSum(x_vars[s] for s in members) <= cap

        prob.solve(pulp.PULP_CBC_CMD(msg=False))

    selected_ids = [s for s,v in x_vars.items() if pulp.value(v)==1]
    agg_final['ID Toko'] = agg_final['ID Toko'].astype(str)
    sel = agg_final[agg_final['ID Toko'].isin(selected_ids)].sort_values(
        'Score', ascending=False, ignore_index=True)
    sel['Is_New_Store'] = ~sel['ID Toko'].isin(existing_ids)

    st.session_state.selected_df = sel
    st.success(f"✅ {len(sel):,} toko terpilih.")
    st.balloons()

if 'selected_df' not in st.session_state:
    st.stop()

selected_df = st.session_state.selected_df
n_eligible  = st.session_state.get('total_eligible',1)
budget_run  = st.session_state.get('budget_run',1)
budget_used = selected_df['Estimated_Cost'].sum()
budget_util = budget_used/budget_run*100 if budget_run else 0
n_new       = selected_df['Is_New_Store'].sum() if 'Is_New_Store' in selected_df.columns else 0
hidden_ton  = selected_df[selected_df['Is_New_Store']]['Avg_Ton'].sum() \
    if 'Is_New_Store' in selected_df.columns else 0

tab1,tab2,tab3,tab4,tab5,tab6 = st.tabs([
    "📊 Ringkasan","📈 Kontribusi","🔍 Perbandingan Toko",
    "📅 Tren Bulanan","📋 Data & Export","🔬 Skenario & Optimasi"])

# ════ TAB 1 ════
with tab1:
    st.markdown('<div class="section-header">✅ Ringkasan ILP-A Mirror</div>',
                unsafe_allow_html=True)
    m1,m2,m3,m4,m5 = st.columns(5)
    m1.metric("Toko Terpilih",    f"{len(selected_df):,}",
               f"{len(selected_df)/n_eligible*100:.1f}% dari {n_eligible:,}")
    m2.metric("Cluster",          f"{selected_df['Cluster Pareto'].nunique()}")
    m3.metric("Est. Budget/Bulan",f"Rp {budget_used:,.0f}")
    m4.metric("Utilisasi",        f"{budget_util:.1f}%")
    m5.metric("Toko Baru",        f"{n_new:,}",f"{hidden_ton:,.0f} ton tersembunyi")

    st.markdown("---")
    c1,c2 = st.columns(2)
    with c1:
        st.subheader("Komposisi Cluster")
        cs = selected_df['Cluster Pareto'].value_counts().reset_index()
        cs.columns = ['Cluster Pareto','Jumlah Toko']
        cs['Persen'] = (cs['Jumlah Toko']/len(selected_df)*100).round(2)
        cs['Est. Budget'] = cs['Cluster Pareto'].map(
            selected_df.groupby('Cluster Pareto')['Estimated_Cost'].sum().to_dict())
        st.dataframe(cs.style.format({'Persen':'{:.2f}%','Est. Budget':'Rp {:,.0f}'}),
                     use_container_width=True, hide_index=True)
    with c2:
        st.altair_chart(
            alt.Chart(cs).mark_arc(innerRadius=50)
            .encode(theta='Jumlah Toko:Q',color='Cluster Pareto:N',
                    tooltip=['Cluster Pareto','Jumlah Toko','Persen'])
            .properties(height=260,title="Distribusi Cluster"),
            use_container_width=True)

    g1,g2 = st.columns(2)
    with g1:
        pv = selected_df['Provinsi Toko'].value_counts().reset_index()
        pv.columns = ['Provinsi','Jumlah']
        st.altair_chart(
            alt.Chart(pv.head(15)).mark_bar()
            .encode(x='Jumlah:Q',y=alt.Y('Provinsi:N',sort='-x'),
                    color=alt.Color('Jumlah:Q',scale=alt.Scale(scheme='blues')),
                    tooltip=['Provinsi','Jumlah'])
            .properties(height=350,title="Per Provinsi"),
            use_container_width=True)
    with g2:
        av = selected_df['Area AP Toko'].value_counts().reset_index()
        av.columns = ['Area AP','Jumlah']
        st.altair_chart(
            alt.Chart(av).mark_bar(color='#FF6B6B')
            .encode(x='Jumlah:Q',y=alt.Y('Area AP:N',sort='-x'),tooltip=['Area AP','Jumlah'])
            .properties(height=350,title="Per Area AP"),
            use_container_width=True)

# ════ TAB 2 ════
with tab2:
    st.markdown('<div class="section-header">📈 Kontribusi & Efisiensi</div>',
                unsafe_allow_html=True)
    df2 = selected_df.copy()
    ts  = df2['Score'].sum(); tb = df2['Estimated_Cost'].sum()
    df2['Kontribusi_Skor_%']   = df2['Score']/(ts+1e-9)*100
    df2['Kontribusi_Budget_%'] = df2['Estimated_Cost']/(tb+1e-9)*100
    df2['Efisiensi']           = df2['Score']/(df2['Estimated_Cost']+1e-9)*1_000_000
    df2['Label']               = df2['ID Toko'].astype(str)+' — '+df2['Nama Toko']

    c1,c2 = st.columns(2)
    with c1:
        st.write("**Top 10 Kontributor Skor**")
        st.altair_chart(
            alt.Chart(df2.nlargest(10,'Kontribusi_Skor_%')).mark_bar(color='#4CAF50')
            .encode(x='Kontribusi_Skor_%:Q',y=alt.Y('Label:N',sort='-x'),
                    tooltip=['ID Toko','Nama Toko','Cluster Pareto','Kontribusi_Skor_%']),
            use_container_width=True)
    with c2:
        st.write("**Top 10 Kontributor Budget**")
        st.altair_chart(
            alt.Chart(df2.nlargest(10,'Kontribusi_Budget_%')).mark_bar(color='#FF9800')
            .encode(x='Kontribusi_Budget_%:Q',y=alt.Y('Label:N',sort='-x'),
                    tooltip=['ID Toko','Nama Toko','Cluster Pareto','Kontribusi_Budget_%']),
            use_container_width=True)

    st.subheader("Scatter: Skor vs Biaya")
    st.altair_chart(
        alt.Chart(df2).mark_circle()
        .encode(x='Estimated_Cost:Q',y='Score:Q',color='Cluster Pareto:N',
                size=alt.Size('Avg_Ton:Q'),
                tooltip=['ID Toko','Nama Toko','Cluster Pareto',
                         'Score','Estimated_Cost','Efisiensi'])
        .interactive().properties(height=350),
        use_container_width=True)

    st.subheader("Top 20 Toko Paling Efisien")
    te = df2.nlargest(20,'Efisiensi')[['ID Toko','Nama Toko','Cluster Pareto',
                                        'Score','Estimated_Cost','Efisiensi']]
    st.dataframe(te.style.format({'Score':'{:.4f}','Estimated_Cost':'Rp {:,.0f}',
                                   'Efisiensi':'{:,.2f}'}),
                 use_container_width=True, hide_index=True)

# ════ TAB 3 ════
with tab3:
    st.markdown('<div class="section-header">🔍 Perbandingan Toko Side-by-Side</div>',
                unsafe_allow_html=True)
    opts   = (selected_df['ID Toko']+' — '+selected_df['Nama Toko']).tolist()
    chosen = st.multiselect("Pilih 2–4 toko:", opts,
                             default=opts[:min(3,len(opts))], max_selections=4)
    if chosen:
        ids_c  = [t.split(' — ')[0] for t in chosen]
        cmp_df = selected_df[selected_df['ID Toko'].isin(ids_c)].copy()
        cols_c = st.columns(len(cmp_df))
        mets   = [('Score','Skor','{:.4f}'),('Avg_Ton','Avg Ton/Bulan','{:.2f} Ton'),
                  ('Avg_Trx','Avg Trx/Bulan','{:.1f}'),('Ton_Growth','Growth','{:.2%}'),
                  ('Ratio_vs_Cluster','Ratio vs Cluster','{:.2f}x'),
                  ('Estimated_Cost','Est. Biaya/Bulan','Rp {:,.0f}')]
        for col_ui,(_,row) in zip(cols_c, cmp_df.iterrows()):
            with col_ui:
                st.markdown(f"### 🏪 {row['Nama Toko']}")
                st.markdown(f"**ID:** {row['ID Toko']}  \n"
                            f"**Cluster:** {row['Cluster Pareto']}  \n"
                            f"**Provinsi:** {row['Provinsi Toko']}  \n"
                            f"**Status:** {'🆕 Baru' if row.get('Is_New_Store') else '✅ Existing'}")
                st.markdown("---")
                for fld,lbl,fmt in mets:
                    val = row.get(fld,'N/A')
                    st.metric(lbl, fmt.format(val) if isinstance(val,(int,float)) else str(val))

        if 'grouped' in st.session_state:
            grp = st.session_state.grouped.copy()
            grp['ID Toko'] = grp['ID Toko'].astype(str)
            tc = grp[grp['ID Toko'].isin(ids_c)]
            if not tc.empty:
                st.subheader("Tren Tonase")
                st.altair_chart(
                    alt.Chart(tc).mark_line(point=True)
                    .encode(x=alt.X('Bulan:N',sort=None),y='Total_Ton:Q',
                            color='Nama Toko:N',
                            tooltip=['ID Toko','Nama Toko','Bulan','Total_Ton'])
                    .interactive().properties(height=300),
                    use_container_width=True)

# ════ TAB 4 ════
with tab4:
    st.markdown('<div class="section-header">📅 Tren Bulanan</div>', unsafe_allow_html=True)
    if 'grouped' in st.session_state:
        grp   = st.session_state.grouped.copy()
        grp['ID Toko'] = grp['ID Toko'].astype(str)
        trend = grp[grp['ID Toko'].isin(selected_df['ID Toko'])]

        st.subheader("Tren Agregat")
        agg_tr = trend.groupby('Bulan').agg(
            Total_Ton=('Total_Ton','sum'), N_Aktif=('ID Toko','nunique')).reset_index()
        st.altair_chart(
            alt.Chart(agg_tr).mark_line(point=True,color='#1976D2')
            .encode(x=alt.X('Bulan:N',sort=None),y='Total_Ton:Q',
                    tooltip=['Bulan','Total_Ton','N_Aktif'])
            .properties(height=250),
            use_container_width=True)

        st.subheader("Tren per Cluster")
        sel_cl = selected_df[['ID Toko','Cluster Pareto']].drop_duplicates()
        trend_nc = trend.drop(columns=['Cluster Pareto'], errors='ignore')
        tr_m = trend_nc.merge(sel_cl, on='ID Toko', how='left').dropna(subset=['Cluster Pareto'])
        cl_t = tr_m.groupby(['Bulan','Cluster Pareto'])['Total_Ton'].sum().reset_index()
        st.altair_chart(
            alt.Chart(cl_t).mark_line(point=True)
            .encode(x=alt.X('Bulan:N',sort=None),y='Total_Ton:Q',
                    color='Cluster Pareto:N',tooltip=['Bulan','Cluster Pareto','Total_Ton'])
            .interactive().properties(height=300),
            use_container_width=True)

        st.subheader("Tren per Toko")
        t_opts = selected_df['Nama Toko'].unique().tolist()
        sel_t  = st.multiselect("Pilih toko (maks 10):", t_opts,
                                 default=t_opts[:5], max_selections=10)
        if sel_t:
            st.altair_chart(
                alt.Chart(trend[trend['Nama Toko'].isin(sel_t)]).mark_line(point=True)
                .encode(x=alt.X('Bulan:N',sort=None),y='Total_Ton:Q',
                        color='Nama Toko:N',
                        tooltip=['ID Toko','Nama Toko','Bulan','Total_Ton'])
                .interactive().properties(height=350),
                use_container_width=True)

# ════ TAB 5 ════
with tab5:
    st.markdown('<div class="section-header">📋 Data & Export</div>', unsafe_allow_html=True)
    srch = st.text_input("🔎 Cari ID / Nama / Provinsi","")
    disp = selected_df.copy()
    if srch:
        msk = (disp['ID Toko'].str.contains(srch,case=False,na=False) |
               disp['Nama Toko'].str.contains(srch,case=False,na=False) |
               disp['Provinsi Toko'].str.contains(srch,case=False,na=False))
        disp = disp[msk]
        st.info(f"{len(disp):,} hasil untuk '{srch}'")

    show_cols = ['ID Toko','Nama Toko','Cluster Pareto','Area AP Toko','Provinsi Toko',
                 'Area Toko','Avg_Ton','Avg_Trx','Ton_Growth','Score','Estimated_Cost','Is_New_Store']
    avail = [c for c in show_cols if c in disp.columns]
    st.dataframe(disp[avail].style.format(
        {k:v for k,v in {'Avg_Ton':'{:.2f}','Avg_Trx':'{:.1f}','Ton_Growth':'{:.2%}',
                          'Score':'{:.4f}','Estimated_Cost':'Rp {:,.0f}'}.items() if k in avail}),
        use_container_width=True, height=400, hide_index=True)

    st.markdown("---")
    cs_exp = selected_df['Cluster Pareto'].value_counts().reset_index()
    cs_exp.columns = ['Cluster Pareto','Jumlah Toko']
    trend_exp = None
    if 'grouped' in st.session_state:
        g = st.session_state.grouped.copy()
        g['ID Toko'] = g['ID Toko'].astype(str)
        trend_exp = g[g['ID Toko'].isin(selected_df['ID Toko'])]

    e1,e2,e3 = st.columns(3)
    with e1:
        st.download_button("📊 Excel (Multi-Sheet)",
            data=to_excel_bytes_multi(disp[avail], cs_exp, trend_exp),
            file_name=f"loyalty_ilpa_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            use_container_width=True)
    with e2:
        st.download_button("📄 CSV",
            data=selected_df[avail].to_csv(index=False).encode('utf-8-sig'),
            file_name=f"loyalty_ilpa_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime='text/csv', use_container_width=True)
    with e3:
        buf = BytesIO()
        selected_df[avail].to_parquet(buf, index=False)
        st.download_button("🗜️ Parquet",
            data=buf.getvalue(),
            file_name=f"loyalty_ilpa_{datetime.now().strftime('%Y%m%d_%H%M')}.parquet",
            mime='application/octet-stream', use_container_width=True)

# ════ TAB 6: SKENARIO & OPTIMASI ════
with tab6:
    st.markdown('<div class="section-header">🔬 Bandingkan Skenario — Temukan yang Terbaik</div>',
                unsafe_allow_html=True)

    st.markdown("""
    <div class="info-box">
    💡 <b>Cara kerja:</b> Sistem menjalankan beberapa kombinasi parameter sekaligus
    dan membandingkan hasilnya — sehingga kamu bisa melihat trade-off antara
    jumlah toko, budget, dan performa skor tanpa harus trial-error manual.
    </div>
    """, unsafe_allow_html=True)

    if 'agg' not in st.session_state:
        st.warning("Proses data terlebih dahulu.")
    else:
        agg_s6 = st.session_state.agg.copy()
        agg_s6 = compute_scores(agg_s6, w1, w2, w3)
        agg_s6.drop_duplicates(subset=['ID Toko'], inplace=True)

        st.markdown("### ⚙️ Konfigurasi Skenario")

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            st.markdown("**Variasikan Budget (%)**")
            budget_base  = max_budget if max_budget > 0 else agg_s6['Estimated_Cost'].sum() * 0.5
            budget_pcts  = st.multiselect(
                "Persentase dari budget acuan",
                [50, 70, 80, 90, 100, 110, 120, 150],
                default=[80, 100, 120],
                help="100% = budget yang sedang diset di sidebar"
            )
        with col_s2:
            st.markdown("**Variasikan Jumlah Toko (N_max)**")
            n_base = N_max if N_max > 0 else 500
            nmax_opts = st.multiselect(
                "Pilihan N_max",
                [int(n_base*p) for p in [0.7, 0.8, 0.9, 1.0, 1.1, 1.2]],
                default=[int(n_base*0.8), n_base, int(n_base*1.2)],
                help="Berapa toko yang ingin dipilih di tiap skenario"
            )

        use_cluster_s6 = st.checkbox(
            "Terapkan Mirror Constraint (proporsi cluster dari existing)",
            value=bool(existing_ids),
            help="Centang untuk ILP-A Mirror, kosongkan untuk ILP-C (bebas)"
        )

        run_scenarios = st.button("▶️ Jalankan Semua Skenario", type="primary",
                                   use_container_width=True,
                                   disabled=(not budget_pcts or not nmax_opts))

        if run_scenarios:
            # Hitung cluster_pcts jika Mirror
            cl_pcts_s6 = {}
            if use_cluster_s6 and len(existing_in_pool) > 0:
                cl_pcts_s6 = (existing_in_pool['Cluster Pareto']
                               .value_counts(normalize=True).mul(100).to_dict())

            results_s6 = []
            total_runs  = len(budget_pcts) * len(nmax_opts)
            prog        = st.progress(0, text="Menjalankan skenario...")

            for idx, (bp, nm) in enumerate(
                    [(b,n) for b in sorted(budget_pcts) for n in sorted(nmax_opts)]):

                budget_s6 = budget_base * bp / 100
                nmax_s6   = int(nm)

                # Jalankan ILP
                prob_s6   = pulp.LpProblem(f"S_{bp}_{nm}", pulp.LpMaximize)
                xv_s6     = {row['ID Toko']: pulp.LpVariable(f"x_{i}", cat='Binary')
                              for i,row in agg_s6.iterrows()}

                prob_s6  += pulp.lpSum(row['Score']*xv_s6[row['ID Toko']]
                                       for _,row in agg_s6.iterrows())
                prob_s6  += pulp.lpSum(xv_s6.values()) <= nmax_s6
                prob_s6  += pulp.lpSum(row['Estimated_Cost']*xv_s6[row['ID Toko']]
                                       for _,row in agg_s6.iterrows()) <= budget_s6

                if cl_pcts_s6:
                    for cl,pct in cl_pcts_s6.items():
                        mem = agg_s6[agg_s6['Cluster Pareto']==cl]['ID Toko'].tolist()
                        cap = int(math.ceil(pct/100*nmax_s6))
                        if mem and cap > 0:
                            prob_s6 += pulp.lpSum(xv_s6[s] for s in mem if s in xv_s6) <= cap

                prob_s6.solve(pulp.PULP_CBC_CMD(msg=False))

                sel_ids = [s for s,v in xv_s6.items() if pulp.value(v)==1]
                sel_s6  = agg_s6[agg_s6['ID Toko'].isin(sel_ids)]

                actual_cost  = sel_s6['Estimated_Cost'].sum()
                total_score  = sel_s6['Score'].sum()
                total_ton    = sel_s6['Avg_Ton'].sum()
                n_new_s6     = (~sel_s6['ID Toko'].isin(existing_ids)).sum()
                hidden_s6    = agg_s6[agg_s6['ID Toko'].isin(
                                   set(sel_ids) - existing_ids)]['Avg_Ton'].sum()
                score_per_rp = total_score / (actual_cost/1e6) if actual_cost > 0 else 0

                # Feasibility check
                feasible = actual_cost <= budget_s6 * 1.03

                results_s6.append({
                    'Skenario'          : f"Budget {bp}% | N={nm:,}",
                    'Budget_Setting_%'  : bp,
                    'N_max'             : nm,
                    'N_Terpilih'        : len(sel_ids),
                    'Budget_Ceiling'    : round(budget_s6, 0),
                    'Budget_Terpakai'   : round(actual_cost, 0),
                    'Budget_Util_%'     : round(actual_cost/budget_s6*100, 1) if budget_s6 > 0 else 0,
                    'Total_Score'       : round(total_score, 2),
                    'Avg_Score_per_Toko': round(sel_s6['Score'].mean(), 4),
                    'Total_Est_Ton'     : round(total_ton, 2),
                    'Avg_Ton_per_Toko'  : round(sel_s6['Avg_Ton'].mean(), 2),
                    'N_Toko_Baru'       : int(n_new_s6),
                    'Hidden_Ton'        : round(hidden_s6, 2),
                    'Score_per_Juta_Rp' : round(score_per_rp, 4),
                    'Feasible'          : '✅' if feasible else '❌',
                })

                prog.progress((idx+1)/total_runs,
                               text=f"Skenario {idx+1}/{total_runs}: Budget {bp}% | N={nm:,}")

            prog.empty()
            df_s6 = pd.DataFrame(results_s6)
            st.session_state.scenario_df = df_s6

        if 'scenario_df' in st.session_state:
            df_s6 = st.session_state.scenario_df

            # ── Highlight skenario terbaik ────────────────────────
            st.markdown("### 📊 Hasil Perbandingan Skenario")

            # Terbaik = Score tertinggi yang feasible
            feasible_df = df_s6[df_s6['Feasible']=='✅']
            if not feasible_df.empty:
                best_idx   = feasible_df['Total_Score'].idxmax()
                best_label = df_s6.loc[best_idx,'Skenario']

                # Efficient = skor per rupiah tertinggi
                efficient_idx   = feasible_df['Score_per_Juta_Rp'].idxmax()
                efficient_label = df_s6.loc[efficient_idx,'Skenario']

                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    st.markdown(f"""
                    <div class="ok-box">
                    🏆 <b>Skor Tertinggi:</b> {best_label}<br>
                    Total Score = {df_s6.loc[best_idx,'Total_Score']:,.2f} |
                    Avg Ton = {df_s6.loc[best_idx,'Avg_Ton_per_Toko']:.2f} |
                    N = {df_s6.loc[best_idx,'N_Terpilih']:,}
                    </div>""", unsafe_allow_html=True)
                with col_b2:
                    st.markdown(f"""
                    <div class="info-box">
                    💰 <b>Paling Efisien (Skor/Rupiah):</b> {efficient_label}<br>
                    Score/Juta = {df_s6.loc[efficient_idx,'Score_per_Juta_Rp']:.4f} |
                    Budget Util = {df_s6.loc[efficient_idx,'Budget_Util_%']:.1f}%
                    </div>""", unsafe_allow_html=True)

            st.markdown("#### Tabel Lengkap")
            def highlight_best(row):
                if row['Skenario'] == best_label:
                    return ['background-color:#e8f5e9']*len(row)
                if row['Feasible'] == '❌':
                    return ['background-color:#fff3e0']*len(row)
                return ['']*len(row)

            show_cols_s6 = ['Skenario','N_Terpilih','Budget_Util_%','Total_Score',
                            'Avg_Score_per_Toko','Avg_Ton_per_Toko',
                            'N_Toko_Baru','Hidden_Ton','Score_per_Juta_Rp','Feasible']
            st.dataframe(
                df_s6[show_cols_s6].style
                .format({'Budget_Util_%':'{:.1f}%','Total_Score':'{:,.2f}',
                         'Avg_Score_per_Toko':'{:.4f}','Avg_Ton_per_Toko':'{:.2f}',
                         'Hidden_Ton':'{:,.2f}','Score_per_Juta_Rp':'{:.4f}'})
                .apply(highlight_best, axis=1),
                use_container_width=True, hide_index=True)

            # ── Chart: Score vs Budget Utilization ───────────────
            st.markdown("#### Visualisasi Trade-off")
            col_v1, col_v2 = st.columns(2)
            with col_v1:
                chart_score = (
                    alt.Chart(df_s6[df_s6['Feasible']=='✅'])
                    .mark_circle(size=120)
                    .encode(
                        x=alt.X('N_max:Q', title='N_max (Jumlah Toko)'),
                        y=alt.Y('Total_Score:Q', title='Total Score'),
                        color=alt.Color('Budget_Setting_%:O',
                                        title='Budget %',
                                        scale=alt.Scale(scheme='blues')),
                        tooltip=['Skenario','N_Terpilih','Total_Score',
                                 'Budget_Util_%','Avg_Ton_per_Toko']
                    )
                    .properties(height=280, title='Total Score vs N_max (per Budget %)')
                    .interactive()
                )
                st.altair_chart(chart_score, use_container_width=True)

            with col_v2:
                chart_eff = (
                    alt.Chart(df_s6[df_s6['Feasible']=='✅'])
                    .mark_circle(size=120)
                    .encode(
                        x=alt.X('Budget_Util_%:Q', title='Budget Utilization (%)'),
                        y=alt.Y('Score_per_Juta_Rp:Q', title='Score per Juta Rp'),
                        color=alt.Color('N_max:O',
                                        title='N_max',
                                        scale=alt.Scale(scheme='greens')),
                        tooltip=['Skenario','N_Terpilih','Score_per_Juta_Rp',
                                 'Budget_Util_%','Total_Score']
                    )
                    .properties(height=280, title='Efisiensi: Score/Rupiah vs Budget Utilization')
                    .interactive()
                )
                st.altair_chart(chart_eff, use_container_width=True)

            # ── Bar: Avg Ton per Toko antar skenario ─────────────
            bar_ton = (
                alt.Chart(df_s6)
                .mark_bar()
                .encode(
                    x=alt.X('Skenario:N', sort='-y', title=''),
                    y=alt.Y('Avg_Ton_per_Toko:Q', title='Avg Ton/Toko'),
                    color=alt.Color('Feasible:N',
                                    scale=alt.Scale(domain=['✅','❌'],
                                                    range=['#4CAF50','#FF9800'])),
                    tooltip=['Skenario','Avg_Ton_per_Toko','N_Terpilih','Budget_Util_%']
                )
                .properties(height=250, title='Rata-rata Tonase per Toko per Skenario')
            )
            st.altair_chart(bar_ton, use_container_width=True)

            # ── Panduan membaca ───────────────────────────────────
            st.markdown("""
            <div class="info-box">
            📖 <b>Cara membaca hasil:</b><br><br>
            • <b>Total Score tinggi</b> = portfolio secara keseluruhan lebih berkualitas<br>
            • <b>Avg Ton/Toko tinggi</b> = rata-rata toko yang dipilih punya volume besar<br>
            • <b>Score/Juta Rp tinggi</b> = nilai per rupiah yang dikeluarkan lebih efisien<br>
            • <b>N_Toko_Baru</b> = berapa toko di luar program existing yang berhasil diidentifikasi<br>
            • <b>Hidden Ton</b> = estimasi tonase tambahan dari toko baru tersebut<br><br>
            💡 <b>Rekomendasi:</b> Pilih skenario dengan <b>Score/Juta Rp tertinggi</b>
            yang masih dalam budget feasible — bukan selalu yang paling banyak toko atau budget terbesar.
            </div>
            """, unsafe_allow_html=True)

            # Download
            st.download_button(
                "📊 Download Semua Skenario (CSV)",
                data=df_s6.to_csv(index=False).encode('utf-8-sig'),
                file_name=f"skenario_ilp_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime='text/csv', use_container_width=True)
