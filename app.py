import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime
import math
import altair as alt

# ============================================================
# 1. KONFIGURASI HALAMAN & CUSTOM CSS
# ============================================================
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
    .info-box { background: #e8f4fd; padding: 12px; border-left: 4px solid #2196F3; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

st.title("🎯 B2B Loyalty Program Optimizer")
st.markdown("Aplikasi pemilihan toko terbaik untuk program loyalty menggunakan **Multi-Criteria Scoring** dan **Integer Linear Programming (ILP)**.")

# ============================================================
# 2. KONSTANTA BISNIS & FUNGSI BANTUAN
# ============================================================
FB_PROVINCES = ['Kalimantan Timur', 'Kalimantan Utara', 'Sulawesi Tengah', 'Sulawesi Selatan']

def normalize(series):
    return (series - series.min()) / (series.max() - series.min() + 1e-9)

def to_excel_bytes_multi(selected_df, summary_df, trend_df=None):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        selected_df.to_excel(writer, index=False, sheet_name='Toko Terpilih')
        summary_df.to_excel(writer, index=False, sheet_name='Ringkasan Cluster')
        if trend_df is not None and not trend_df.empty:
            trend_df.to_excel(writer, index=False, sheet_name='Tren Bulanan')
    return output.getvalue()

def compute_scores(agg_df, w1, w2, w3):
    temp = agg_df.copy()
    temp['Score'] = (w1 * temp['Ratio_vs_Cluster'] + 
                     w2 * normalize(temp['Avg_Trx']) + 
                     w3 * normalize(temp['Ton_Growth']))
    return temp

# ============================================================
# 3. FUNGSI PEMROSESAN DATA (DENGAN PROTEKSI ERROR)
# ============================================================
@st.cache_data
def load_and_preprocess(uploaded_file):
    try:
        df_raw = pd.read_parquet(uploaded_file)
    except Exception as e:
        raise ValueError(f"Gagal membaca file parquet. Pastikan format file benar. Error detail: {e}")

    # PROTEKSI 1: Cek kelengkapan kolom wajib
    required_cols = ['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko', 'Brands', 'TON Quantity', 'Tanggal Transaksi']
    missing_cols = [c for c in required_cols if c not in df_raw.columns]
    if missing_cols:
        raise ValueError(f"Data tidak valid! Kolom berikut tidak ditemukan: {', '.join(missing_cols)}")

    # PROTEKSI 2: Atasi NaNs pada kolom teks agar fungsi sorted() tidak crash
    cat_cols = ['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko', 'Brands']
    for col in cat_cols:
        df_raw[col] = df_raw[col].fillna('Unknown').astype(str).str.strip()

    df_raw['Tanggal Transaksi'] = pd.to_datetime(df_raw['Tanggal Transaksi'], errors='coerce')
    df = df_raw.dropna(subset=['Tanggal Transaksi']).copy()
    df['TON Quantity'] = pd.to_numeric(df['TON Quantity'], errors='coerce').fillna(0)
    
    # Aturan Kategori Brand
    def get_brand_category(row):
        area = row['Area AP Toko'].upper()
        prov = row['Provinsi Toko']
        brand = row['Brands'].upper()
        
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
    
    # Aturan Estimasi Biaya (Reward Rate)
    def calculate_reward_rate(cluster, brand_cat):
        c_upper = cluster.upper()
        if c_upper in ['PLATINUM', 'SUPER PLATINUM']: mb_rate = 3750
        else: mb_rate = 2500
            
        if brand_cat == 'Main Brand': return mb_rate
        elif brand_cat in ['Companion Brand', 'Fighting Brand']: return mb_rate / 2
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

    # Menghitung Pertumbuhan (Growth)
    growths = []
    for sid in agg['ID Toko']:
        td = monthly[monthly['ID Toko'] == sid]
        last_val_series = td[td['Bulan'] == target_last_month]['Total_Ton']
        last_val = last_val_series.values[0] if len(last_val_series) > 0 else 0.0
        prev_data = td[td['Bulan'] < target_last_month]['Total_Ton']
        prev_mean = prev_data.mean() if len(prev_data) > 0 else 0.0
        growths.append((last_val - prev_mean) / prev_mean if prev_mean > 0 else 0.0)
    agg['Ton_Growth'] = growths

    # Total Estimasi Biaya per Bulan
    cost_total = df_input.groupby('ID Toko')['Biaya_Reward_Transaksi'].sum().reset_index()
    cost_total = cost_total.merge(agg[['ID Toko', 'Total_Bulan_Aktif']], on='ID Toko')
    cost_total['Estimated_Cost'] = cost_total['Biaya_Reward_Transaksi'] / cost_total['Total_Bulan_Aktif']
    
    agg = agg.merge(cost_total[['ID Toko', 'Estimated_Cost']], on='ID Toko', how='left')
    agg['Estimated_Cost'] = agg['Estimated_Cost'].fillna(0)

    # Ratio vs Cluster
    cluster_avg = agg.groupby('Cluster Pareto')['Avg_Ton'].mean().to_dict()
    agg['Ratio_vs_Cluster'] = agg.apply(lambda x: x['Avg_Ton'] / cluster_avg.get(x['Cluster Pareto'], 1.0), axis=1)

    return agg, monthly

# ============================================================
# 4. ANTARMUKA PENGGUNA (UI)
# ============================================================
st.markdown('<div class="section-header">📁 Langkah 1: Upload Data</div>', unsafe_allow_html=True)

uploaded_file = st.file_uploader("📤 Upload file .parquet", type=["parquet"])

if uploaded_file:
    if st.button("⚙️ Proses Data Agregasi", type="primary"):
        with st.spinner("Mengekstrak dan memproses fitur..."):
            try:
                df = load_and_preprocess(uploaded_file)
                agg, grouped = build_aggregation(df)
                st.session_state.agg = agg
                st.session_state.df = df
                st.session_state.grouped = grouped
                st.success(f"✅ Berhasil! {agg.shape[0]:,} toko aktif diproses.")
            except Exception as e:
                st.error(f"❌ Error Proses Data: {e}")
                st.stop()

st.markdown("---")

if 'agg' in st.session_state:
    base_agg = st.session_state.agg

    # ============================================================
    # PANEL KONTROL SIDEBAR
    # ============================================================
    with st.sidebar:
        st.markdown("## 🛠️ Panel Kontrol ILP")
        
        st.markdown("### 📍 Filter Geografis")
        available_areas_ap = sorted(base_agg['Area AP Toko'].unique())
        selected_areas_ap = st.multiselect("Area AP Toko", available_areas_ap, default=available_areas_ap)
        agg_filtered = base_agg[base_agg['Area AP Toko'].isin(selected_areas_ap)].copy() if selected_areas_ap else base_agg.copy()

        st.markdown("### 🏅 Filter Performa")
        min_bulan_aktif = st.number_input("Min. Bulan Aktif", min_value=1, value=3, step=1)
        agg = agg_filtered[agg_filtered['Total_Bulan_Aktif'] >= min_bulan_aktif].copy()

        # PROTEKSI 3: Hentikan eksekusi UI jika data menjadi kosong karena filter
        total_available = agg.shape[0]
        if total_available == 0:
            st.warning("⚠️ Data kosong. Harap sesuaikan kembali filter Anda di atas.")
            st.stop()

        st.markdown("### 💰 Anggaran & Kuota")
        max_budget = st.number_input("Anggaran Maks (Rp/Bulan)", 0, value=500_000_000, step=50_000_000)
        N_max = st.number_input("Jumlah Toko Maks (N_max)", min_value=1, max_value=total_available, value=min(1700, total_available), step=10)

        st.markdown("### ⚖️ Bobot Skor")
        w_ratio = st.slider("Ratio vs Cluster (%)", 0, 100, 47)
        w_trx   = st.slider("Avg Transaksi (%)", 0, 100, 41)
        w_growth = st.slider("Tonase Growth (%)", 0, 100, 12)
        total_w = w_ratio + w_trx + w_growth
        w1, w2, w3 = w_ratio/max(total_w,1), w_trx/max(total_w,1), w_growth/max(total_w,1)

        st.markdown("### 🎯 Batas Maks Cluster (Constraint)")
        cluster_pcts_existing = agg['Cluster Pareto'].value_counts(normalize=True).mul(100).to_dict()
        cluster_pct_inputs = {}
        for c in sorted(agg['Cluster Pareto'].unique()):
            default_val = round(cluster_pcts_existing.get(c, 0.0), 2)
            v = st.number_input(f"Maks {c} (%)", 0.0, 100.0, value=float(default_val), key=f"clpct_{c}")
            cluster_pct_inputs[c] = v

        run_optimize = st.button("▶️ Jalankan Optimasi ILP", type="primary", use_container_width=True)

    # ============================================================
    # MAIN AREA: SIMULASI WHAT-IF
    # ============================================================
    st.markdown(f'<div class="info-box">🗂️ <b>{total_available:,} toko</b> lolos filter dan siap dioptimasi.</div>', unsafe_allow_html=True)
    
    with st.expander("🔮 Simulasi What-If: Preview Distribusi Skor", expanded=False):
        if not agg.empty:
            preview_df = compute_scores(agg, w1, w2, w3)
            hist_chart = alt.Chart(preview_df).mark_bar(opacity=0.8).encode(
                x=alt.X('Score:Q', bin=alt.Bin(maxbins=30), title='Distribusi Skor Komposit'),
                y=alt.Y('count()', title='Jumlah Toko'),
                color=alt.Color('Cluster Pareto:N'),
                tooltip=['Cluster Pareto', 'count()']
            ).properties(height=250)
            st.altair_chart(hist_chart, use_container_width=True)

    # ============================================================
    # EKSEKUSI ILP (PULP)
    # ============================================================
    if run_optimize:
        agg_final = compute_scores(agg, w1, w2, w3)
        agg_final.sort_values('Score', ascending=False, inplace=True)

        st.session_state.total_eligible_stores = len(agg_final)
        st.session_state.max_budget_value_for_run = max_budget

        try:
            import pulp
        except ImportError:
            st.error("Library 'pulp' tidak ditemukan. Silakan jalankan `pip install pulp` di terminal Anda.")
            st.stop()

        with st.spinner("Menjalankan solver Integer Linear Programming (Harap Tunggu)..."):
            prob = pulp.LpProblem("Loyalty_Selection", pulp.LpMaximize)
            x_vars = {row['ID Toko']: pulp.LpVariable(f"x_{row['ID Toko']}", cat='Binary') for _, row in agg_final.iterrows()}
            
            prob += pulp.lpSum([row['Score'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()])
            prob += pulp.lpSum(x_vars.values()) <= int(N_max)
            prob += pulp.lpSum([row['Estimated_Cost'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()]) <= max_budget

            for cluster_name, max_pct in cluster_pct_inputs.items():
                if max_pct > 0:
                    members = agg_final[agg_final['Cluster Pareto'] == cluster_name]['ID Toko'].tolist()
                    cap = int(math.ceil((max_pct / 100.0) * float(N_max))) 
                    if members:
                        prob += pulp.lpSum([x_vars[sid] for sid in members]) <= cap

            prob.solve(pulp.PULP_CBC_CMD(msg=False))
            selected_ids = [str(sid) for sid, var in x_vars.items() if pulp.value(var) == 1]
            
            st.session_state.selected_df = agg_final[agg_final['ID Toko'].isin(selected_ids)].sort_values('Score', ascending=False, ignore_index=True)
            st.success(f"✅ Optimasi selesai! ILP berhasil memilih {len(selected_ids):,} toko terbaik.")

# ============================================================
# 5. DASHBOARD HASIL & ANALISIS
# ============================================================
if 'selected_df' in st.session_state:
    selected_df = st.session_state.selected_df
    total_eligible_stores = st.session_state.get('total_eligible_stores', 1)
    budget_used = selected_df['Estimated_Cost'].sum()
    budget_max = st.session_state.get('max_budget_value_for_run', 1)

    tab1, tab2, tab3, tab4 = st.tabs(["📊 Ringkasan", "📈 Efisiensi", "📅 Tren Bulanan", "📋 Data & Export"])

    # --- TAB 1: RINGKASAN ---
    with tab1:
        st.markdown('<div class="section-header">📊 Ringkasan Hasil ILP</div>', unsafe_allow_html=True)
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
                theta=alt.Theta('Jumlah:Q'), 
                color=alt.Color('Cluster:N'), 
                tooltip=['Cluster', 'Jumlah']
            )
            st.altair_chart(pie_chart, use_container_width=True)
            
        with cc2:
            st.subheader("Distribusi Geografis (Area AP)")
            area_dist = selected_df['Area AP Toko'].value_counts().reset_index()
            area_dist.columns = ['Area AP', 'Jumlah']
            area_chart = alt.Chart(area_dist).mark_bar(color='#FF6B6B').encode(
                x=alt.X('Jumlah:Q'), 
                y=alt.Y('Area AP:N', sort='-x'), 
                tooltip=['Area AP', 'Jumlah']
            )
            st.altair_chart(area_chart, use_container_width=True)

    # --- TAB 2: EFISIENSI ---
    with tab2:
        st.markdown('<div class="section-header">📈 Analisis Efisiensi Portofolio</div>', unsafe_allow_html=True)
        selected_df['Efisiensi (Skor/Juta)'] = (selected_df['Score'] / (selected_df['Estimated_Cost'] + 1e-9)) * 1_000_000
        chart_scatter = alt.Chart(selected_df).mark_circle().encode(
            x=alt.X('Estimated_Cost:Q', title='Estimasi Biaya Reward (Rp)'),
            y=alt.Y('Score:Q', title='Skor Performa ILP'),
            color=alt.Color('Cluster Pareto:N'),
            size=alt.Size('Avg_Ton:Q', title='Tonase'),
            tooltip=['Nama Toko', 'Cluster Pareto', 'Score', 'Estimated_Cost', 'Efisiensi (Skor/Juta)']
        ).interactive().properties(height=400)
        st.altair_chart(chart_scatter, use_container_width=True)

    # --- TAB 3: TREN BULANAN ---
    with tab4:
        st.markdown('<div class="section-header">📅 Analisis Tren Historis Terpilih</div>', unsafe_allow_html=True)
        if 'grouped' in st.session_state:
            g_data = st.session_state.grouped.copy()
            g_data['ID Toko'] = g_data['ID Toko'].astype(str)
            trend_data = g_data[g_data['ID Toko'].isin(selected_df['ID Toko'])]
            
            agg_trend = trend_data.groupby('Bulan').agg(Total_Ton=('Total_Ton', 'sum')).reset_index()
            tline = alt.Chart(agg_trend).mark_line(point=True, color='#1976D2', strokeWidth=3).encode(
                x=alt.X('Bulan:N', sort=None), 
                y=alt.Y('Total_Ton:Q', title='Total Tonase')
            ).properties(height=350)
            st.altair_chart(tline, use_container_width=True)

    # --- TAB 4: EXPORT ---
    with tab5:
        st.markdown('<div class="section-header">📋 Tabel Data & Export Laporan</div>', unsafe_allow_html=True)
        show_cols = ['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Provinsi Toko', 'Area AP Toko', 'Avg_Ton', 'Score', 'Estimated_Cost', 'Efisiensi (Skor/Juta)']
        st.dataframe(selected_df[show_cols].style.format({'Estimated_Cost': 'Rp {:,.0f}', 'Efisiensi (Skor/Juta)': '{:,.2f}', 'Score': '{:.4f}', 'Avg_Ton': '{:.1f}'}), use_container_width=True)
        
        st.markdown("---")
        excel_bytes = to_excel_bytes_multi(selected_df[show_cols], selected_df['Cluster Pareto'].value_counts().reset_index())
        st.download_button("📊 Download Laporan Final (Excel)", data=excel_bytes, file_name=f"optimasi_loyalty_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
