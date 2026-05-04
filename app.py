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
# Custom CSS untuk tampilan yang lebih baik
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

st.title("🎯 Loyalty Program Optimizer & Analyzer")
st.markdown("Aplikasi membantu memilih toko terbaik untuk program loyalty berdasarkan performa, skor, dan batasan yang fleksibel.")

# ============================================================
# Fungsi Bantuan
# ============================================================
def normalize(series):
    return (series - series.min()) / (series.max() - series.min() + 1e-9)

def to_excel_bytes_multi(selected_df, summary_df, trend_df=None):
    """Export multi-sheet Excel."""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        selected_df.to_excel(writer, index=False, sheet_name='Toko Terpilih')
        summary_df.to_excel(writer, index=False, sheet_name='Ringkasan Cluster')
        if trend_df is not None and not trend_df.empty:
            trend_df.to_excel(writer, index=False, sheet_name='Tren Bulanan')
        # Sheet metadata
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

def read_uploaded_file(uploaded_file):
    """Baca file: CSV, XLSX, atau Parquet."""
    fname = uploaded_file.name.lower()
    if fname.endswith(".csv"):
        return pd.read_csv(uploaded_file, dtype={'ID Toko': str})
    elif fname.endswith(".xlsx") or fname.endswith(".xls"):
        return pd.read_excel(uploaded_file, dtype={'ID Toko': str})
    elif fname.endswith(".parquet"):
        df = pd.read_parquet(uploaded_file)
        if 'ID Toko' in df.columns:
            df['ID Toko'] = df['ID Toko'].astype(str)
        return df
    else:
        raise ValueError(f"Format file tidak didukung: {uploaded_file.name}")

def compute_scores(agg_df, w1, w2, w3):
    """Hitung skor berdasarkan bobot."""
    temp = agg_df.copy()
    temp['Score'] = (
        w1 * temp['Ratio_vs_Cluster'] +
        w2 * normalize(temp['Avg_Trx']) +
        w3 * normalize(temp['Ton_Growth'])
    )
    return temp

# ============================================================
# LANGKAH 1: UPLOAD & PROSES DATA
# ============================================================
st.markdown('<div class="section-header">📁 Langkah 1: Upload & Proses Data Awal</div>', unsafe_allow_html=True)

uploaded_file = st.file_uploader(
    "📤 Upload file transaksi",
    type=["csv", "xlsx", "xls", "parquet"],
    help="Format yang didukung: CSV, Excel (.xlsx/.xls), dan Parquet"
)

if uploaded_file:
    col1, col2 = st.columns([3, 1])
    with col1:
        try:
            if 'df_raw' not in st.session_state or st.session_state.get('uploaded_filename') != uploaded_file.name:
                st.session_state.df_raw = read_uploaded_file(uploaded_file)
                st.session_state.uploaded_filename = uploaded_file.name

            df_raw = st.session_state.df_raw

            # Info file
            file_size_kb = uploaded_file.size / 1024
            ext = uploaded_file.name.split('.')[-1].upper()
            st.markdown(f'<div class="info-box">📄 <b>{uploaded_file.name}</b> — {ext} | {df_raw.shape[0]:,} baris × {df_raw.shape[1]} kolom | {file_size_kb:.1f} KB</div>', unsafe_allow_html=True)

            available_brands = sorted(df_raw['Brands'].dropna().unique())
            selected_brands = st.multiselect(
                "🏷️ Pilih Brand",
                available_brands,
                default=[b for b in ["SEMEN GRESIK", "DYNAMIX", "MERDEKA"] if b in available_brands]
            )
            st.session_state.selected_brands = selected_brands
        except Exception as e:
            st.error(f"Gagal membaca file: {e}")
            st.stop()

    with col2:
        st.write("👇 Setelah pilih brand, klik:")
        if st.button("⚙️ Proses Data & Hitung Skor", type="primary"):
            with st.spinner("Memproses data..."):
                df_raw = st.session_state.df_raw
                selected_brands = st.session_state.selected_brands

                required_cols = [
                    'Tanggal Transaksi', 'ID Toko', 'Nama Toko', 'Cluster Pareto',
                    'Area AP Toko', 'Provinsi Toko', 'Area Toko', 'Brands',
                    'Nama Produk', 'TON Quantity'
                ]
                if not all(c in df_raw.columns for c in required_cols):
                    missing = [c for c in required_cols if c not in df_raw.columns]
                    st.error(f"Kolom wajib hilang: {missing}")
                    st.stop()
                if not selected_brands:
                    st.warning("Pilih minimal 1 brand.")
                    st.stop()

                df = df_raw[df_raw['Brands'].isin(selected_brands)].copy()
                df['TON Quantity'] = df['TON Quantity'].fillna(0)
                df['Tanggal Transaksi'] = pd.to_datetime(df['Tanggal Transaksi'], errors='coerce')
                df.dropna(subset=['Tanggal Transaksi'], inplace=True)
                df.sort_values(by=['ID Toko', 'Tanggal Transaksi'], inplace=True)

                categorical_cols = ['Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko', 'Brands', 'Nama Produk']
                for col in categorical_cols:
                    if col in df.columns:
                        df[col] = df.groupby('ID Toko')[col].transform(lambda x: x.ffill().bfill())

                df.dropna(subset=['Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko'], inplace=True)

                if df.empty:
                    st.warning("Tidak ada data yang valid setelah dibersihkan.")
                    st.stop()

                df['Bulan'] = df['Tanggal Transaksi'].dt.to_period('M').astype(str)

                grouped = df.groupby(
                    ['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko', 'Bulan']
                ).agg(
                    Total_Ton=('TON Quantity', 'sum'),
                    Jumlah_Transaksi=('Tanggal Transaksi', 'count')
                ).reset_index()

                agg = grouped.groupby(
                    ['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko']
                ).agg(
                    Avg_Ton=('Total_Ton', 'mean'),
                    Avg_Trx=('Jumlah_Transaksi', 'mean'),
                    Total_Bulan_Aktif=('Bulan', 'nunique')
                ).reset_index()

                growths = []
                for sid in agg['ID Toko']:
                    toko_data = grouped[grouped['ID Toko'] == sid].sort_values('Bulan')
                    if len(toko_data) >= 2:
                        prev_mean = toko_data['Total_Ton'].iloc[:-1].mean()
                        last_val = toko_data['Total_Ton'].iloc[-1]
                        growth = (last_val - prev_mean) / prev_mean if prev_mean > 0 else 0.0
                    else:
                        growth = 0.0
                    growths.append(growth)
                agg['Ton_Growth'] = growths

                cluster_avg = agg.groupby('Cluster Pareto')['Avg_Ton'].mean().to_dict()
                agg['Ratio_vs_Cluster'] = agg.apply(
                    lambda x: x['Avg_Ton'] / cluster_avg.get(x['Cluster Pareto'], 1.0), axis=1
                )

                st.session_state.agg = agg
                st.session_state.df = df
                st.session_state.grouped = grouped
                st.success(f"✅ Data berhasil diproses! {agg.shape[0]:,} toko unik ditemukan.")

st.markdown("---")

# ============================================================
# LANGKAH 2: FILTER & OPTIMASI (Sidebar + Main)
# ============================================================
if 'agg' in st.session_state:
    base_agg = st.session_state.agg

    # ---- Sidebar: semua kontrol parameter ----
    with st.sidebar:
        st.markdown("## 🛠️ Panel Kontrol")
        st.markdown("---")

        st.markdown("### 📍 Filter Geografis")
        available_areas_ap = sorted(base_agg['Area AP Toko'].unique())
        selected_areas_ap = st.multiselect("Area AP Toko (Wajib)", available_areas_ap, default=available_areas_ap)
        if not selected_areas_ap:
            st.warning("Pilih minimal satu Area AP Toko.")
            st.stop()
        agg_filtered_ap = base_agg[base_agg['Area AP Toko'].isin(selected_areas_ap)].copy()

        available_provinsi = sorted(agg_filtered_ap['Provinsi Toko'].unique())
        selected_provinsi = st.multiselect("Provinsi Toko (opsional)", available_provinsi, default=[])
        agg_filtered_prov = agg_filtered_ap[agg_filtered_ap['Provinsi Toko'].isin(selected_provinsi)].copy() if selected_provinsi else agg_filtered_ap.copy()

        available_area_toko = sorted(agg_filtered_prov['Area Toko'].unique())
        selected_area_toko = st.multiselect("Area Toko (opsional)", available_area_toko, default=[])
        agg = agg_filtered_prov[agg_filtered_prov['Area Toko'].isin(selected_area_toko)].copy() if selected_area_toko else agg_filtered_prov.copy()

        st.markdown("---")
        st.markdown("### 🏅 Filter Cluster & Performa")
        all_clusters = sorted(agg['Cluster Pareto'].unique())
        selected_clusters = st.multiselect("Cluster Pareto (opsional)", all_clusters, default=[])
        if selected_clusters:
            agg = agg[agg['Cluster Pareto'].isin(selected_clusters)].copy()

        min_avg_ton = st.number_input("Min. Rata-rata Tonase / Bulan", min_value=0.0, value=0.0, step=0.5,
                                       help="Hanya tampilkan toko dengan Avg_Ton >= nilai ini")
        if min_avg_ton > 0:
            agg = agg[agg['Avg_Ton'] >= min_avg_ton].copy()

        min_bulan_aktif = st.number_input("Min. Bulan Aktif Transaksi", min_value=1, value=1, step=1,
                                           help="Toko dengan rekam jejak minimal N bulan")
        if min_bulan_aktif > 1 and 'Total_Bulan_Aktif' in agg.columns:
            agg = agg[agg['Total_Bulan_Aktif'] >= min_bulan_aktif].copy()

        st.markdown("---")
        st.markdown("### ❌ Kecualikan ID Toko")
        excluded_ids_str = st.text_area("ID Toko (satu per baris)", placeholder="Tempel ID dari Excel...", height=100)
        if excluded_ids_str:
            excluded_ids_list = [x.strip() for x in excluded_ids_str.splitlines() if x.strip()]
            agg['ID Toko'] = agg['ID Toko'].astype(str)
            agg = agg[~agg['ID Toko'].isin(excluded_ids_list)]

        st.markdown("---")
        st.markdown("### 💰 Anggaran & Kuota")
        max_budget = st.number_input("Anggaran Maks (Rp)", 0, value=1_000_000_000, step=50_000_000)
        total_available = agg.shape[0]
        N_max = st.number_input("Jumlah Toko Maks (N_max)", 1, max(1, total_available),
                                  value=min(500, total_available), step=1)

        st.markdown("---")
        st.markdown("### ⚖️ Bobot Skor")
        w_ratio = st.slider("Ratio_vs_Cluster (%)", 0, 100, 50)
        w_trx   = st.slider("Avg_Trx (%)", 0, 100, 30)
        w_growth = st.slider("Ton_Growth (%)", 0, 100, 20)
        total_w = w_ratio + w_trx + w_growth
        if total_w == 0:
            w1, w2, w3 = 0.5, 0.3, 0.2
        else:
            w1, w2, w3 = w_ratio/total_w, w_trx/total_w, w_growth/total_w

        st.markdown("---")
        st.markdown("### 🎯 Batas Maks per Cluster Pareto")
        clusters_list = sorted(agg['Cluster Pareto'].unique())
        cluster_pct_inputs = {}
        for c in clusters_list:
            v = st.number_input(f"Maks {c} (%)", 0.0, 100.0, value=0.0, key=f"clpct_{c}")
            cluster_pct_inputs[c] = v

        st.markdown("---")
        run_optimize = st.button("▶️ Jalankan Optimasi", type="primary", use_container_width=True)

    # ---- Main: status data ----
    st.markdown(f'<div class="info-box">🗂️ <b>{agg.shape[0]:,} toko</b> siap dioptimasi berdasarkan filter aktif.</div>', unsafe_allow_html=True)
    st.markdown("")

    # ============================================================
    # FITUR BARU: SIMULASI WHAT-IF (preview sebelum optimasi)
    # ============================================================
    with st.expander("🔮 Simulasi What-If: Preview Distribusi Skor", expanded=False):
        st.markdown("Lihat bagaimana perubahan bobot memengaruhi distribusi skor **sebelum** menjalankan optimasi penuh.")
        wif1, wif2, wif3 = st.columns(3)
        with wif1:
            wi_ratio = st.slider("Ratio_vs_Cluster (%)", 0, 100, int(w_ratio*100/max(total_w,1)), key="wi_ratio")
        with wif2:
            wi_trx = st.slider("Avg_Trx (%)", 0, 100, int(w_trx*100/max(total_w,1)), key="wi_trx")
        with wif3:
            wi_growth = st.slider("Ton_Growth (%)", 0, 100, int(w_growth*100/max(total_w,1)), key="wi_growth")

        wi_total = wi_ratio + wi_trx + wi_growth
        if wi_total > 0 and not agg.empty:
            wi_w1, wi_w2, wi_w3 = wi_ratio/wi_total, wi_trx/wi_total, wi_growth/wi_total
            preview_df = compute_scores(agg, wi_w1, wi_w2, wi_w3)
            pc1, pc2, pc3 = st.columns(3)
            pc1.metric("Skor Tertinggi", f"{preview_df['Score'].max():.4f}")
            pc2.metric("Skor Rata-rata", f"{preview_df['Score'].mean():.4f}")
            pc3.metric("Skor Terendah", f"{preview_df['Score'].min():.4f}")

            hist_chart = alt.Chart(preview_df).mark_bar(color='#2196F3', opacity=0.8).encode(
                x=alt.X('Score:Q', bin=alt.Bin(maxbins=30), title='Distribusi Skor'),
                y=alt.Y('count()', title='Jumlah Toko'),
                color=alt.Color('Cluster Pareto:N'),
                tooltip=['Cluster Pareto', 'count()']
            ).properties(height=250)
            st.altair_chart(hist_chart, use_container_width=True)

    st.markdown("---")

    # ============================================================
    # JALANKAN OPTIMASI
    # ============================================================
    if run_optimize:
        agg_final = agg.copy()
        agg_final = compute_scores(agg_final, w1, w2, w3)

        poin_to_rupiah = {'BRONZE': 5000, 'SILVER': 5000, 'GOLD': 5000, 'PLATINUM': 6250, 'SUPER PLATINUM': 6250}
        agg_final['Rupiah_per_Poin'] = agg_final['Cluster Pareto'].str.upper().map(poin_to_rupiah).fillna(0)
        agg_final['Estimated_Cost'] = agg_final['Avg_Ton'] * agg_final['Rupiah_per_Poin']
        agg_final.sort_values('Score', ascending=False, inplace=True)
        agg_final.drop_duplicates(subset=['ID Toko'], keep='first', inplace=True, ignore_index=True)

        st.session_state.total_eligible_stores = len(agg_final)
        st.session_state.total_eligible_clusters = agg_final['Cluster Pareto'].nunique()
        st.session_state.n_max_value_for_run = N_max
        st.session_state.max_budget_value_for_run = max_budget

        try:
            import pulp
        except ImportError:
            st.error("Library 'pulp' tidak ditemukan. Jalankan: pip install pulp")
            st.stop()

        with st.spinner("Menjalankan optimasi Integer Linear Programming..."):
            prob = pulp.LpProblem("Loyalty_Selection", pulp.LpMaximize)
            x_vars = {row['ID Toko']: pulp.LpVariable(f"x_{row['ID Toko']}", cat='Binary') for _, row in agg_final.iterrows()}
            prob += pulp.lpSum([row['Score'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()])
            prob += pulp.lpSum(x_vars.values()) <= int(N_max)
            prob += pulp.lpSum([row['Estimated_Cost'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()]) <= max_budget

            for cluster_name, max_pct in cluster_pct_inputs.items():
                if max_pct > 0:
                    members = agg_final[agg_final['Cluster Pareto'] == cluster_name]['ID Toko'].tolist()
                    cap = int(math.floor((max_pct / 100.0) * float(N_max)))
                    if members:
                        prob += pulp.lpSum([x_vars[sid] for sid in members]) <= cap

            prob.solve(pulp.PULP_CBC_CMD(msg=False))
            selected_ids = [str(sid) for sid, var in x_vars.items() if pulp.value(var) == 1]
            agg_final['ID Toko'] = agg_final['ID Toko'].astype(str)
            st.session_state.selected_df = agg_final[agg_final['ID Toko'].isin(selected_ids)].sort_values('Score', ascending=False, ignore_index=True)
            st.session_state.agg_final_scored = agg_final  # simpan semua skor untuk perbandingan
            st.success(f"✅ Optimasi selesai! {len(st.session_state.selected_df):,} toko terpilih.")
            st.balloons()

# ============================================================
# HASIL & ANALISIS
# ============================================================
if 'selected_df' in st.session_state:
    selected_df = st.session_state.selected_df
    total_eligible_stores = st.session_state.get('total_eligible_stores', 1)
    total_eligible_clusters = st.session_state.get('total_eligible_clusters', 1)
    percent_selected = (len(selected_df) / total_eligible_stores) * 100 if total_eligible_stores > 0 else 0
    unique_clusters_selected = selected_df['Cluster Pareto'].nunique()
    percent_clusters = (unique_clusters_selected / total_eligible_clusters) * 100 if total_eligible_clusters > 0 else 0
    budget_used = selected_df['Estimated_Cost'].sum()
    budget_max = st.session_state.get('max_budget_value_for_run', 1)
    budget_utilization = (budget_used / budget_max * 100) if budget_max > 0 else 0

    # ---- Tab navigasi hasil ----
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 Ringkasan & Komposisi",
        "📈 Analisis Kontribusi",
        "🔍 Perbandingan Toko",
        "📅 Tren Bulanan",
        "📋 Data Lengkap & Export"
    ])

    # ======================================================
    # TAB 1: RINGKASAN
    # ======================================================
    with tab1:
        st.markdown('<div class="section-header">✅ Ringkasan Hasil Seleksi</div>', unsafe_allow_html=True)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Toko Terpilih", f"{len(selected_df):,}", f"{percent_selected:.1f}% dari {total_eligible_stores:,}")
        m2.metric("Cluster Terwakili", f"{unique_clusters_selected}", f"{percent_clusters:.0f}% dari {total_eligible_clusters}")
        m3.metric("Estimasi Budget Bulanan", f"Rp {budget_used:,.0f}")
        m4.metric("Utilisasi Anggaran", f"{budget_utilization:.1f}%")

        st.markdown("---")
        st.subheader("Komposisi Cluster Pareto")
        if not selected_df.empty:
            cluster_summary = selected_df['Cluster Pareto'].value_counts().reset_index()
            cluster_summary.columns = ['Cluster Pareto', 'Jumlah Toko']
            cluster_summary['Persentase'] = (cluster_summary['Jumlah Toko'] / len(selected_df) * 100).round(2)
            cluster_summary['Estimasi Budget'] = cluster_summary['Cluster Pareto'].map(
                selected_df.groupby('Cluster Pareto')['Estimated_Cost'].sum().to_dict()
            )

            cc1, cc2 = st.columns(2)
            with cc1:
                st.dataframe(cluster_summary.style.format({'Persentase': '{:.2f}%', 'Estimasi Budget': 'Rp {:,.0f}'}),
                             use_container_width=True)
            with cc2:
                pie_chart = alt.Chart(cluster_summary).mark_arc(innerRadius=50).encode(
                    theta=alt.Theta('Jumlah Toko:Q'),
                    color=alt.Color('Cluster Pareto:N'),
                    tooltip=['Cluster Pareto', 'Jumlah Toko', 'Persentase']
                ).properties(height=280, title="Distribusi Cluster")
                st.altair_chart(pie_chart, use_container_width=True)

        st.subheader("Distribusi Geografis")
        geo1, geo2 = st.columns(2)
        with geo1:
            prov_dist = selected_df['Provinsi Toko'].value_counts().reset_index()
            prov_dist.columns = ['Provinsi', 'Jumlah Toko']
            st.markdown("**Per Provinsi**")
            prov_chart = alt.Chart(prov_dist.head(15)).mark_bar().encode(
                x=alt.X('Jumlah Toko:Q'),
                y=alt.Y('Provinsi:N', sort='-x'),
                color=alt.Color('Jumlah Toko:Q', scale=alt.Scale(scheme='blues')),
                tooltip=['Provinsi', 'Jumlah Toko']
            ).properties(height=350)
            st.altair_chart(prov_chart, use_container_width=True)
        with geo2:
            area_dist = selected_df['Area AP Toko'].value_counts().reset_index()
            area_dist.columns = ['Area AP', 'Jumlah Toko']
            st.markdown("**Per Area AP**")
            area_chart = alt.Chart(area_dist).mark_bar(color='#FF6B6B').encode(
                x=alt.X('Jumlah Toko:Q'),
                y=alt.Y('Area AP:N', sort='-x'),
                tooltip=['Area AP', 'Jumlah Toko']
            ).properties(height=350)
            st.altair_chart(area_chart, use_container_width=True)

    # ======================================================
    # TAB 2: ANALISIS KONTRIBUSI
    # ======================================================
    with tab2:
        st.markdown('<div class="section-header">📈 Analisis Kontribusi & Efisiensi</div>', unsafe_allow_html=True)

        if not selected_df.empty:
            total_score = selected_df['Score'].sum()
            total_estimated_budget = selected_df['Estimated_Cost'].sum()
            selected_df['Kontribusi_Skor_%'] = (selected_df['Score'] / total_score * 100)
            selected_df['Kontribusi_Budget_%'] = (selected_df['Estimated_Cost'] / (total_estimated_budget + 1e-9) * 100)
            selected_df['Efisiensi (Skor/Juta)'] = (selected_df['Score'] / (selected_df['Estimated_Cost'] + 1e-9)) * 1_000_000
            selected_df['ID_dan_Nama'] = selected_df['ID Toko'].astype(str) + ' - ' + selected_df['Nama Toko']

            c1, c2 = st.columns(2)
            with c1:
                st.write("**Top 10 Kontributor Skor**")
                top_score = selected_df.nlargest(10, 'Kontribusi_Skor_%')
                chart_s = alt.Chart(top_score).mark_bar(color='#4CAF50').encode(
                    x=alt.X('Kontribusi_Skor_%:Q', title='Kontribusi Skor (%)'),
                    y=alt.Y('ID_dan_Nama:N', sort='-x', title=''),
                    tooltip=['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Kontribusi_Skor_%']
                )
                st.altair_chart(chart_s, use_container_width=True)
            with c2:
                st.write("**Top 10 Kontributor Budget**")
                top_budget = selected_df.nlargest(10, 'Kontribusi_Budget_%')
                chart_b = alt.Chart(top_budget).mark_bar(color='#FF9800').encode(
                    x=alt.X('Kontribusi_Budget_%:Q', title='Kontribusi Budget (%)'),
                    y=alt.Y('ID_dan_Nama:N', sort='-x', title=''),
                    tooltip=['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Kontribusi_Budget_%']
                )
                st.altair_chart(chart_b, use_container_width=True)

            st.subheader("Scatter: Skor vs Biaya (Efisiensi)")
            chart_scatter = alt.Chart(selected_df).mark_circle().encode(
                x=alt.X('Estimated_Cost:Q', title='Estimasi Biaya (Rp)'),
                y=alt.Y('Score:Q', title='Skor Performa'),
                color=alt.Color('Cluster Pareto:N'),
                size=alt.Size('Avg_Ton:Q', title='Rata-rata Tonase'),
                tooltip=['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Provinsi Toko', 'Score', 'Estimated_Cost', 'Efisiensi (Skor/Juta)']
            ).interactive().properties(height=350)
            st.altair_chart(chart_scatter, use_container_width=True)

            st.subheader("Top 20 Toko Paling Efisien (Skor per 1 Juta Biaya)")
            top_eff = selected_df.nlargest(20, 'Efisiensi (Skor/Juta)')[['ID Toko', 'Nama Toko', 'Cluster Pareto', 'Score', 'Estimated_Cost', 'Efisiensi (Skor/Juta)']].copy()
            st.dataframe(top_eff.style.format({'Estimated_Cost': 'Rp {:,.0f}', 'Efisiensi (Skor/Juta)': '{:,.2f}', 'Score': '{:.4f}'}), use_container_width=True)

    # ======================================================
    # TAB 3: PERBANDINGAN TOKO (FITUR BARU)
    # ======================================================
    with tab3:
        st.markdown('<div class="section-header">🔍 Perbandingan Toko Side-by-Side</div>', unsafe_allow_html=True)
        st.markdown("Pilih 2–4 toko untuk dibandingkan detailnya secara langsung.")

        all_toko_options = (selected_df['ID Toko'] + ' — ' + selected_df['Nama Toko']).tolist()
        toko_dipilih = st.multiselect("Pilih toko untuk dibandingkan:", all_toko_options, default=all_toko_options[:min(3, len(all_toko_options))], max_selections=4)

        if toko_dipilih:
            ids_dipilih = [t.split(' — ')[0] for t in toko_dipilih]
            compare_df = selected_df[selected_df['ID Toko'].isin(ids_dipilih)].copy()

            # Kartu perbandingan
            cols_compare = st.columns(len(compare_df))
            metrics_to_show = [
                ('Score', 'Skor Performa', '{:.4f}'),
                ('Avg_Ton', 'Rata-rata Tonase/Bulan', '{:.2f} Ton'),
                ('Avg_Trx', 'Rata-rata Transaksi/Bulan', '{:.1f}'),
                ('Ton_Growth', 'Growth Tonase', '{:.2%}'),
                ('Ratio_vs_Cluster', 'Ratio vs Cluster', '{:.2f}x'),
                ('Estimated_Cost', 'Estimasi Biaya/Bulan', 'Rp {:,.0f}'),
                ('Efisiensi (Skor/Juta)', 'Efisiensi', '{:,.2f}'),
            ]

            for col_ui, row in zip(cols_compare, compare_df.itertuples()):
                with col_ui:
                    st.markdown(f"### 🏪 {row._asdict()['Nama Toko']}")
                    st.markdown(f"**ID:** {row._asdict()['ID Toko']}  \n**Cluster:** {row._asdict()['Cluster Pareto']}  \n**Provinsi:** {row._asdict()['Provinsi Toko']}  \n**Area AP:** {row._asdict()['Area AP Toko']}")
                    st.markdown("---")
                    for field, label, fmt in metrics_to_show:
                        val = row._asdict().get(field, 'N/A')
                        if isinstance(val, (int, float)):
                            st.metric(label, fmt.format(val))
                        else:
                            st.metric(label, str(val))

            # Radar chart perbandingan menggunakan bar chart grouped
            st.markdown("---")
            st.subheader("Visualisasi Perbandingan Multi-Dimensi")
            compare_metrics = ['Score', 'Avg_Ton', 'Ton_Growth', 'Avg_Trx', 'Ratio_vs_Cluster']
            radar_data = []
            for _, row in compare_df.iterrows():
                for m in compare_metrics:
                    radar_data.append({
                        'Toko': row['Nama Toko'],
                        'Metrik': m,
                        'Nilai_Norm': float(normalize(compare_df[m])[compare_df['ID Toko'] == row['ID Toko']].values[0])
                    })
            radar_df = pd.DataFrame(radar_data)
            radar_chart = alt.Chart(radar_df).mark_bar().encode(
                x=alt.X('Toko:N', title=''),
                y=alt.Y('Nilai_Norm:Q', title='Nilai Ternormalisasi (0-1)'),
                color=alt.Color('Toko:N'),
                column=alt.Column('Metrik:N'),
                tooltip=['Toko', 'Metrik', 'Nilai_Norm']
            ).properties(width=100, height=200)
            st.altair_chart(radar_chart)

            # Tren perbandingan toko terpilih
            if 'grouped' in st.session_state:
                st.subheader("Tren Tonase Toko yang Dibandingkan")
                grouped_data = st.session_state.grouped.copy()
                grouped_data['ID Toko'] = grouped_data['ID Toko'].astype(str)
                trend_compare = grouped_data[grouped_data['ID Toko'].isin(ids_dipilih)]
                if not trend_compare.empty:
                    trend_c = alt.Chart(trend_compare).mark_line(point=True).encode(
                        x=alt.X('Bulan:N', sort=None, title='Bulan'),
                        y=alt.Y('Total_Ton:Q', title='Total Tonase'),
                        color=alt.Color('Nama Toko:N'),
                        tooltip=['ID Toko', 'Nama Toko', 'Bulan', 'Total_Ton', 'Jumlah_Transaksi']
                    ).interactive().properties(height=300)
                    st.altair_chart(trend_c, use_container_width=True)

    # ======================================================
    # TAB 4: TREN BULANAN
    # ======================================================
    with tab4:
        st.markdown('<div class="section-header">📅 Analisis Tren Performa Bulanan</div>', unsafe_allow_html=True)

        if 'grouped' in st.session_state and not selected_df.empty:
            grouped_monthly_data = st.session_state.grouped.copy()
            grouped_monthly_data['ID Toko'] = grouped_monthly_data['ID Toko'].astype(str)
            trend_data = grouped_monthly_data[grouped_monthly_data['ID Toko'].isin(selected_df['ID Toko'])]

            st.subheader("Tren Agregat Semua Toko Terpilih")
            agg_trend = trend_data.groupby('Bulan').agg(
                Total_Ton=('Total_Ton', 'sum'),
                Total_Transaksi=('Jumlah_Transaksi', 'sum'),
                Jumlah_Toko_Aktif=('ID Toko', 'nunique')
            ).reset_index()

            tline = alt.Chart(agg_trend).mark_line(point=True, color='#1976D2').encode(
                x=alt.X('Bulan:N', sort=None),
                y=alt.Y('Total_Ton:Q', title='Total Tonase'),
                tooltip=['Bulan', 'Total_Ton', 'Total_Transaksi', 'Jumlah_Toko_Aktif']
            ).properties(height=250)
            tbar = alt.Chart(agg_trend).mark_bar(opacity=0.3, color='#90CAF9').encode(
                x=alt.X('Bulan:N', sort=None),
                y=alt.Y('Jumlah_Toko_Aktif:Q', title='Jumlah Toko Aktif'),
            )
            st.altair_chart(tline, use_container_width=True)

            st.subheader("Tren per Cluster Pareto")
            cluster_trend = trend_data.merge(
                selected_df[['ID Toko', 'Cluster Pareto']], on='ID Toko', how='left'
            ).groupby(['Bulan', 'Cluster Pareto'])['Total_Ton'].sum().reset_index()
            cluster_trend_chart = alt.Chart(cluster_trend).mark_line(point=True).encode(
                x=alt.X('Bulan:N', sort=None),
                y=alt.Y('Total_Ton:Q', title='Total Tonase'),
                color='Cluster Pareto:N',
                tooltip=['Bulan', 'Cluster Pareto', 'Total_Ton']
            ).interactive().properties(height=300)
            st.altair_chart(cluster_trend_chart, use_container_width=True)

            st.subheader("Perbandingan Tren per Toko")
            list_toko_terpilih = selected_df['Nama Toko'].unique().tolist()
            toko_untuk_dibandingkan = st.multiselect(
                "Pilih toko (maks 10):", list_toko_terpilih, default=list_toko_terpilih[:5], max_selections=10
            )
            if toko_untuk_dibandingkan:
                comp_data = trend_data[trend_data['Nama Toko'].isin(toko_untuk_dibandingkan)]
                tc = alt.Chart(comp_data).mark_line(point=True).encode(
                    x=alt.X('Bulan:N', sort=None),
                    y=alt.Y('Total_Ton:Q', title='Total Tonase'),
                    color=alt.Color('Nama Toko:N'),
                    tooltip=['ID Toko', 'Nama Toko', 'Bulan', 'Total_Ton', 'Jumlah_Transaksi']
                ).interactive().properties(height=350)
                st.altair_chart(tc, use_container_width=True)

    # ======================================================
    # TAB 5: DATA LENGKAP & EXPORT
    # ======================================================
    with tab5:
        st.markdown('<div class="section-header">📋 Data Lengkap & Export</div>', unsafe_allow_html=True)

        # Filter pencarian di tabel
        search_q = st.text_input("🔎 Cari berdasarkan ID / Nama Toko / Provinsi", "")
        display_df = selected_df.copy()
        if search_q:
            mask = (
                display_df['ID Toko'].str.contains(search_q, case=False, na=False) |
                display_df['Nama Toko'].str.contains(search_q, case=False, na=False) |
                display_df['Provinsi Toko'].str.contains(search_q, case=False, na=False)
            )
            display_df = display_df[mask]
            st.info(f"Menampilkan {len(display_df):,} hasil pencarian untuk: '{search_q}'")

        show_cols = [
            'ID Toko', 'Nama Toko', 'Cluster Pareto', 'Area AP Toko', 'Provinsi Toko', 'Area Toko',
            'Avg_Ton', 'Avg_Trx', 'Ton_Growth', 'Score', 'Estimated_Cost',
            'Kontribusi_Skor_%', 'Kontribusi_Budget_%', 'Efisiensi (Skor/Juta)'
        ]
        avail_cols = [c for c in show_cols if c in display_df.columns]

        fmt_map = {
            'Estimated_Cost': 'Rp {:,.0f}',
            'Kontribusi_Skor_%': '{:.2f}%',
            'Kontribusi_Budget_%': '{:.2f}%',
            'Efisiensi (Skor/Juta)': '{:,.2f}',
            'Score': '{:.4f}',
            'Avg_Ton': '{:.2f}',
            'Avg_Trx': '{:.1f}',
            'Ton_Growth': '{:.2%}',
        }
        active_fmt = {k: v for k, v in fmt_map.items() if k in avail_cols}
        st.dataframe(display_df[avail_cols].style.format(active_fmt), use_container_width=True, height=400)

        st.markdown("---")
        st.subheader("⬇️ Download Hasil")

        ecol1, ecol2 = st.columns(2)
        with ecol1:
            # Multi-sheet Excel
            cluster_summary_exp = selected_df['Cluster Pareto'].value_counts().reset_index()
            cluster_summary_exp.columns = ['Cluster Pareto', 'Jumlah Toko']
            trend_exp = None
            if 'grouped' in st.session_state:
                g = st.session_state.grouped.copy()
                g['ID Toko'] = g['ID Toko'].astype(str)
                trend_exp = g[g['ID Toko'].isin(selected_df['ID Toko'])]

            excel_bytes = to_excel_bytes_multi(selected_df[avail_cols], cluster_summary_exp, trend_exp)
            st.download_button(
                "📊 Download Excel (Multi-Sheet)",
                data=excel_bytes,
                file_name=f"optimasi_loyalty_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        with ecol2:
            # CSV
            csv_bytes = selected_df[avail_cols].to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                "📄 Download CSV",
                data=csv_bytes,
                file_name=f"optimasi_loyalty_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                use_container_width=True
            )

        # Download Parquet
        st.markdown("")
        parquet_buffer = BytesIO()
        selected_df[avail_cols].to_parquet(parquet_buffer, index=False)
        st.download_button(
            "🗜️ Download Parquet (kompak)",
            data=parquet_buffer.getvalue(),
            file_name=f"optimasi_loyalty_{datetime.now().strftime('%Y%m%d_%H%M')}.parquet",
            mime="application/octet-stream",
            use_container_width=True
        )
