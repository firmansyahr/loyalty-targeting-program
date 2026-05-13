import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime
import math
import altair as alt

# Konfigurasi halaman Streamlit
st.set_page_config(page_title="Loyalty Target Optimizer", layout="wide", page_icon="🎯")

# ============================================================
# Custom CSS
# ============================================================
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
    .info-box { background: #e8f4fd; padding: 12px; border-left: 4px solid #2196F3; border-radius: 5px; }
    .warning-box { background: #fff8e1; padding: 12px; border-left: 4px solid #FF9800; border-radius: 5px; }
    .success-box { background: #e8f5e9; padding: 12px; border-left: 4px solid #4CAF50; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

st.title("🎯 B2B Loyalty Program Optimizer")
st.markdown("Aplikasi membantu memilih toko terbaik untuk program loyalty berdasarkan *Multi-Criteria Scoring* dan *Integer Linear Programming*.")

# ============================================================
# Konstanta Bisnis
# ============================================================
FB_PROVINCES = ['Kalimantan Timur', 'Kalimantan Utara', 'Sulawesi Tengah', 'Sulawesi Selatan']

# ============================================================
# Fungsi Bantuan
# ============================================================
def normalize(series):
    return (series - series.min()) / (series.max() - series.min() + 1e-9)

def to_excel_bytes_multi(selected_df, summary_df, trend_df=None):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        selected_df.to_excel(writer, index=False, sheet_name='Toko Terpilih')
        summary_df.to_excel(writer, index=False, sheet_name='Ringkasan Cluster')
        if trend_df is not None and not trend_df.empty:
            trend_df.to_excel(writer, index=False, sheet_name='Tren Bulanan')
        meta = pd.DataFrame({
            'Keterangan': ['Tanggal Export', 'Total Toko Terpilih', 'Total Estimasi Budget'],
            'Nilai': [
                datetime.now().strftime('%Y-%m-%d %H:%M'),
                len(selected_df),
                f"Rp {selected_df['Estimated_Cost'].sum():,.0f}" if 'Estimated_Cost' in selected_df.columns else '-'
            ]
        })
        meta.to_excel(writer, index=False, sheet_name='Metadata')
    return output.getvalue()

@st.cache_data
def load_and_preprocess(uploaded_file):
    fname = uploaded_file.name.lower()
    if fname.endswith(".parquet"):
        df_raw = pd.read_parquet(uploaded_file)
    else:
        raise ValueError("Gunakan format .parquet untuk performa optimal dataset besar.")

    df_raw['ID Toko'] = df_raw['ID Toko'].astype(str).str.strip()
    df_raw['Tanggal Transaksi'] = pd.to_datetime(df_raw['Tanggal Transaksi'], errors='coerce')
    df = df_raw.dropna(subset=['Tanggal Transaksi']).copy()
    df['TON Quantity'] = df['TON Quantity'].fillna(0)
    
    # 1. Logika Kategori Brand (Sesuai Aturan Perusahaan)
    def get_brand_category(row):
        area = str(row['Area AP Toko']).strip().upper()
        prov = str(row['Provinsi Toko']).strip()
        brand = str(row['Brands']).strip().upper()
        
        if area == 'SP':
            if 'PADANG' in brand: return 'Main Brand'
            if 'DYNAMIX' in brand or 'BATURAJA' in brand: return 'Companion Brand'
        elif area == 'SMBR':
            if 'BATURAJA' in brand: return 'Main Brand'
            if 'DYNAMIX' in brand or 'PADANG' in brand: return 'Companion Brand'
        elif area == 'ST':
            if 'TONASA' in brand: return 'Main Brand'
            if 'GRESIK' in brand: return 'Companion Brand'
            if 'MERDEKA' in brand and prov in FB_PROVINCES: return 'Fighting Brand'
        return 'Other'

    df['Brand_Category'] = df.apply(get_brand_category, axis=1)
    df = df[df['Brand_Category'] != 'Other'].copy()
    
    # 2. Logika Reward Cost Aktual
    def calculate_reward_rate(cluster, brand_cat):
        if str(cluster).strip().upper() in ['PLATINUM', 'SUPER PLATINUM']:
            mb_rate = 3750
        else:
            mb_rate = 2500
            
        if brand_cat == 'Main Brand': return mb_rate
        elif brand_cat in ['Companion Brand', 'Fighting Brand']: return mb_rate / 2 # Rasio 2:1
        else: return 0

    df['Reward_Rate'] = df.apply(lambda r: calculate_reward_rate(r['Cluster Pareto'], r['Brand_Category']), axis=1)
    df['Biaya_Reward_Transaksi'] = df['TON Quantity'] * df['Reward_Rate']
    
    return df

@st.cache_data
def build_aggregation(df_input):
    df_input['Bulan'] = df_input['Tanggal Transaksi'].dt.to_period('M').astype(str)
    target_last_month = df_input['Bulan'].max()

    monthly = (df_input.groupby(['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko', 'Bulan'])
               .agg(Total_Ton=('TON Quantity', 'sum'), Jumlah_Trx=('Tanggal Transaksi', 'count')).reset_index())

    agg = (monthly.groupby(['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko'])
           .agg(Avg_Ton=('Total_Ton', 'mean'), Avg_Trx=('Jumlah_Trx', 'mean'), Total_Bulan_Aktif=('Bulan', 'nunique')).reset_index())

    # Mencegah Survivorship Bias (Growth)
    growths = []
    for sid in agg['ID Toko']:
        td = monthly[monthly['ID Toko'] == sid]
        last_val_series = td[td['Bulan'] == target_last_month]['Total_Ton']
        last_val = last_val_series.values[0] if len(last_val_series) > 0 else 0.0
        prev_data = td[td['Bulan'] < target_last_month]['Total_Ton']
        prev_mean = prev_data.mean() if len(prev_data) > 0 else 0.0
        growths.append((last_val - prev_mean) / prev_mean if prev_mean > 0 else 0.0)
    agg['Ton_Growth'] = growths

    # Hitung Estimasi Cost Bulanan yg Presisi (Berdasarkan Kategori Brand & Cluster Aktual)
    cost_total = df_input.groupby('ID Toko')['Biaya_Reward_Transaksi'].sum().reset_index()
    cost_total = cost_total.merge(agg[['ID Toko', 'Total_Bulan_Aktif']], on='ID Toko')
    cost_total['Estimated_Cost'] = cost_total['Biaya_Reward_Transaksi'] / cost_total['Total_Bulan_Aktif']
    
    agg = agg.merge(cost_total[['ID Toko', 'Estimated_Cost']], on='ID Toko', how='left')
    agg['Estimated_Cost'] = agg['Estimated_Cost'].fillna(0)

    cluster_avg = agg.groupby('Cluster Pareto')['Avg_Ton'].mean().to_dict()
    agg['Ratio_vs_Cluster'] = agg.apply(lambda x: x['Avg_Ton'] / cluster_avg.get(x['Cluster Pareto'], 1.0), axis=1)

    return agg, monthly

def compute_scores(agg_df, w1, w2, w3):
    temp = agg_df.copy()
    temp['Score'] = (w1 * temp['Ratio_vs_Cluster'] + w2 * normalize(temp['Avg_Trx']) + w3 * normalize(temp['Ton_Growth']))
    return temp

# ============================================================
# LANGKAH 1: UPLOAD & PROSES DATA
# ============================================================
st.markdown('<div class="section-header">📁 Langkah 1: Upload & Proses Data Awal</div>', unsafe_allow_html=True)

uploaded_file = st.file_uploader("📤 Upload file transaksi (Hanya .parquet disarankan)", type=["parquet"])

if uploaded_file:
    col1, col2 = st.columns([3, 1])
    with col1:
        file_size_kb = uploaded_file.size / 1024
        st.markdown(f'<div class="info-box">📄 <b>{uploaded_file.name}</b> | {file_size_kb:.1f} KB</div>', unsafe_allow_html=True)
        
    with col2:
        if st.button("⚙️ Proses Data Agregasi", type="primary"):
            with st.spinner("Memproses aturan Brand dan estimasi biaya per Toko..."):
                try:
                    df = load_and_preprocess(uploaded_file)
                    agg, grouped = build_aggregation(df)
                    st.session_state.agg = agg
                    st.session_state.df = df
                    st.session_state.grouped = grouped
                    st.success(f"✅ Berhasil! {agg.shape[0]:,} toko aktif ditemukan.")
                except Exception as e:
                    st.error(f"Error proses data: {e}")
                    st.stop()

st.markdown("---")

# ============================================================
# LANGKAH 2: FILTER & OPTIMASI (Sidebar + Main)
# ============================================================
if 'agg' in st.session_state:
    base_agg = st.session_state.agg

    # ---- Sidebar: Kontrol Parameter ----
    with st.sidebar:
        st.markdown("## 🛠️ Panel Kontrol")
        
        st.markdown("### 📍 Filter Geografis")
        available_areas_ap = sorted(base_agg['Area AP Toko'].unique())
        selected_areas_ap = st.multiselect("Area AP Toko", available_areas_ap, default=available_areas_ap)
        agg_filtered = base_agg[base_agg['Area AP Toko'].isin(selected_areas_ap)].copy() if selected_areas_ap else base_agg.copy()

        st.markdown("### 🏅 Filter Performa")
        min_bulan_aktif = st.number_input("Min. Bulan Aktif (Default 3)", min_value=1, value=3, step=1)
        agg = agg_filtered[agg_filtered['Total_Bulan_Aktif'] >= min_bulan_aktif].copy()

        st.markdown("### 💰 Anggaran & Kuota")
        max_budget = st.number_input("Anggaran Maks (Rp/Bulan)", 0, value=500_000_000, step=50_000_000)
        total_available = agg.shape[0]
        N_max = st.number_input("Jumlah Toko Maks (N_max)", 1, max(1, total_available), value=min(1700, total_available), step=10)

        st.markdown("### ⚖️ Bobot Skor (Default: Spearman)")
        w_ratio = st.slider("Ratio_vs_Cluster (%)", 0, 100, 47)
        w_trx   = st.slider("Avg_Trx (%)", 0, 100, 41)
        w_growth = st.slider("Ton_Growth (%)", 0, 100, 12)
        total_w = w_ratio + w_trx + w_growth
        w1, w2, w3 = w_ratio/max(total_w,1), w_trx/max(total_w,1), w_growth/max(total_w,1)

        st.markdown("### 🎯 Batas Maks Cluster (Skenario Mirror)")
        cluster_pcts_existing = agg['Cluster Pareto'].value_counts(normalize=True).mul(100).to_dict()
        cluster_pct_inputs = {}
        for c in sorted(agg['Cluster Pareto'].unique()):
            default_val = cluster_pcts_existing.get(c, 0.0)
            v = st.number_input(f"Maks {c} (%)", 0.0, 100.0, value=float(default_val), key=f"clpct_{c}")
            cluster_pct_inputs[c] = v

        run_optimize = st.button("▶️ Jalankan Optimasi ILP", type="primary", use_container_width=True)

    # ---- Main: Simulasi & Optimasi ----
    st.markdown(f'<div class="info-box">🗂️ <b>{agg.shape[0]:,} toko</b> siap dioptimasi (Filter: Aktif {min_bulan_aktif} bulan).</div>', unsafe_allow_html=True)
    
    with st.expander("🔮 Simulasi What-If: Preview Distribusi Skor"):
        st.markdown("Distribusi skor toko berdasarkan bobot saat ini di sidebar.")
        if not agg.empty:
            preview_df = compute_scores(agg, w1, w2, w3)
            hist_chart = alt.Chart(preview_df).mark_bar(opacity=0.8).encode(
                x=alt.X('Score:Q', bin=alt.Bin(maxbins=30), title='Distribusi Skor'),
                y=alt.Y('count()', title='Jumlah Toko'),
                color=alt.Color('Cluster Pareto:N'),
                tooltip=['Cluster Pareto', 'count()']
            ).properties(height=250)
            st.altair_chart(hist_chart, use_container_width=True)

    # ============================================================
    # JALANKAN ILP
    # ============================================================
    if run_optimize:
        agg_final = compute_scores(agg, w1, w2, w3)
        agg_final.sort_values('Score', ascending=False, inplace=True)

        st.session_state.total_eligible_stores = len(agg_final)
        st.session_state.max_budget_value_for_run = max_budget

        try:
            import pulp
        except ImportError:
            st.error("Library 'pulp' tidak ditemukan. Jalankan: pip install pulp")
            st.stop()

        with st.spinner("Menjalankan solver Integer Linear Programming..."):
            prob = pulp.LpProblem("Loyalty_Selection", pulp.LpMaximize)
            x_vars = {row['ID Toko']: pulp.LpVariable(f"x_{row['ID Toko']}", cat='Binary') for _, row in agg_final.iterrows()}
            
            prob += pulp.lpSum([row['Score'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()])
            prob += pulp.lpSum(x_vars.values()) <= int(N_max)
            prob += pulp.lpSum([row['Estimated_Cost'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()]) <= max_budget

            for cluster_name, max_pct in cluster_pct_inputs.items():
                if max_pct > 0:
                    members = agg_final[agg_final['Cluster Pareto'] == cluster_name]['ID Toko'].tolist()
                    cap = int(math.ceil((max_pct / 100.0) * float(N_max))) # Menggunakan ceil
                    if members:
                        prob += pulp.lpSum([x_vars[sid] for sid in members]) <= cap

            prob.solve(pulp.PULP_CBC_CMD(msg=False))
            selected_ids = [str(sid) for sid, var in x_vars.items() if pulp.value(var) == 1]
            
            st.session_state.selected_df = agg_final[agg_final['ID Toko'].isin(selected_ids)].sort_values('Score', ascending=False, ignore_index=True)
            st.success(f"✅ Optimasi selesai! {len(selected_ids):,} toko terpilih.")
            st.balloons()

# ============================================================
# HASIL & ANALISIS
# ============================================================
if 'selected_df' in st.session_state:
    selected_df = st.session_state.selected_df
    total_eligible_stores = st.session_state.get('total_eligible_stores', 1)
    budget_used = selected_df['Estimated_Cost'].sum()
    budget_max = st.session_state.get('max_budget_value_for_run', 1)

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 Ringkasan & Komposisi", "📈 Analisis Kontribusi", "🔍 Perbandingan Toko", "📅 Tren Bulanan", "📋 Data & Export"
    ])

    # --- TAB 1: RINGKASAN ---
    with tab1:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Toko Terpilih", f"{len(selected_df):,}", f"Dari total {total_eligible_stores:,}")
        m2.metric("Total Proyeksi Tonase", f"{selected_df['Avg_Ton'].sum():,.1f} Ton")
        m3.metric("Estimasi Budget Bulanan", f"Rp {budget_used:,.0f}")
        m4.metric("Utilisasi Anggaran", f"{(budget_used / budget_max * 100):.1f}%" if budget_max>0 else "0%")

        cc1, cc2 = st.columns(2)
        with cc1:
            st.subheader("Distribusi Cluster Pareto")
            cluster_summary = selected_df['Cluster Pareto'].value_counts().reset_index()
            cluster_summary.columns = ['Cluster', 'Jumlah']
            pie_chart = alt.Chart(cluster_summary).mark_arc(innerRadius=50).encode(
                theta=alt.Theta('Jumlah:Q'), color=alt.Color('Cluster:N'), tooltip=['Cluster', 'Jumlah']
            )
            st.altair_chart(pie
