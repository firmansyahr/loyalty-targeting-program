# app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime
import math

# --- Import library tambahan ---
from sklearn.preprocessing import StandardScaler

# Konfigurasi halaman Streamlit
st.set_page_config(page_title="Loyalty Target Optimizer", layout="wide")
st.title("🔎 Loyalty Target Optimizer & Analyzer")
st.markdown("Aplikasi untuk optimasi penargetan toko loyalitas dan analisis kontribusi dari toko-toko terpilih.")

# ------------- Fungsi Bantuan -------------
def normalize(series):
    return (series - series.min()) / (series.max() - series.min() + 1e-9)

def to_excel_bytes(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Hasil Optimasi')
    return output.getvalue()

# ------------- TAHAP 1: UPLOAD DAN PROSES DATA -------------
st.header("Langkah 1: Upload & Proses Data Awal")
uploaded_file = st.file_uploader("📤 Upload file transaksi (CSV / XLSX)", type=["csv", "xlsx"])

if uploaded_file:
    # ... (Logika di bagian ini sama persis seperti sebelumnya) ...
    col1, col2 = st.columns([3, 1])
    with col1:
        try:
            if 'df_raw' not in st.session_state or st.session_state.get('uploaded_filename') != uploaded_file.name:
                if uploaded_file.name.lower().endswith(".csv"): st.session_state.df_raw = pd.read_csv(uploaded_file)
                else: st.session_state.df_raw = pd.read_excel(uploaded_file)
                st.session_state.uploaded_filename = uploaded_file.name
            df_raw = st.session_state.df_raw
            available_brands = sorted(df_raw['Brands'].dropna().unique())
            selected_brands = st.multiselect("🏷️ Pilih Brand", available_brands, default=[b for b in ["SEMEN GRESIK","DYNAMIX","MERDEKA"] if b in available_brands])
            st.session_state.selected_brands = selected_brands
        except Exception as e: st.error(f"Gagal membaca file: {e}"); st.stop()
    with col2:
        st.write("👇 Setelah pilih brand, klik")
        if st.button("⚙️ Proses Data & Hitung Skor", type="primary"):
            with st.spinner("Memproses data..."):
                df_raw = st.session_state.df_raw; selected_brands = st.session_state.selected_brands
                required_cols = ['Tanggal Transaksi', 'ID Toko', 'Nama Toko', 'Cluster', 'Area', 'Brands', 'Nama Produk', 'Total Ton']
                if not all(c in df_raw.columns for c in required_cols): st.error(f"Kolom wajib hilang: {required_cols}"); st.stop()
                if not selected_brands: st.warning("Pilih minimal 1 brand."); st.stop()
                df = df_raw[df_raw['Brands'].isin(selected_brands)].copy()
                df['Tanggal Transaksi'] = pd.to_datetime(df['Tanggal Transaksi'], errors='coerce')
                df.dropna(subset=['Tanggal Transaksi'], inplace=True)
                if df.empty: st.warning("Tidak ada data transaksi."); st.stop()
                df['Bulan'] = df['Tanggal Transaksi'].dt.to_period('M')
                grouped = df.groupby(['ID Toko', 'Nama Toko', 'Cluster', 'Area', 'Bulan']).agg(Total_Ton=('Total Ton', 'sum'), Jumlah_Transaksi=('Tanggal Transaksi', 'count')).reset_index()
                agg = grouped.groupby(['ID Toko', 'Nama Toko', 'Cluster', 'Area']).agg(Avg_Ton=('Total_Ton', 'mean'), Avg_Trx=('Jumlah_Transaksi', 'mean')).reset_index()
                growths = []
                for sid in agg['ID Toko']:
                    toko_data = grouped[grouped['ID Toko'] == sid].sort_values('Bulan')
                    if len(toko_data) >= 2:
                        prev_mean = toko_data['Total_Ton'][:-1].mean(); last_val = toko_data['Total_Ton'].iloc[-1]
                        growth = (last_val - prev_mean) / prev_mean if prev_mean > 0 else 0.0
                    else: growth = 0.0
                    growths.append(growth)
                agg['Ton_Growth'] = growths
                cluster_avg = agg.groupby('Cluster')['Avg_Ton'].mean().to_dict()
                agg['Ratio_vs_Cluster'] = agg.apply(lambda x: x['Avg_Ton'] / cluster_avg.get(x['Cluster'], 1.0), axis=1)
                st.session_state.agg = agg; st.session_state.df = df
                st.success("Data berhasil diproses!")
st.markdown("---")

# ------------- TAHAP 2: PENGATURAN PARAMETER DAN OPTIMASI -------------
if 'agg' in st.session_state:
    st.header("Langkah 2: Atur Parameter & Jalankan Optimasi")
    base_agg = st.session_state.agg
    
    st.subheader("📍 Pengaturan Area")
    available_areas = sorted(base_agg['Area'].unique())
    selected_areas = st.multiselect("Pilih Area", available_areas, default=available_areas)
    if not selected_areas: st.warning("Pilih minimal satu Area."); st.stop()
    agg = base_agg[base_agg['Area'].isin(selected_areas)].copy()
    
    st.subheader("💰 Pengaturan Anggaran & Seleksi")
    col1, col2 = st.columns(2)
    with col1: max_budget = st.number_input("Anggaran Maksimal (Rp)", 0, value=1_000_000_000, step=50_000_000)
    with col2:
        total_available = agg.shape[0]
        N_max = st.number_input("Jumlah Toko Maksimal (N_max)", 1, max(1, total_available), value=min(500, total_available), step=1)

    st.subheader("⚙️ Pengaturan Bobot & Batasan Cluster")
    st.write("Atur Bobot Skor")
    w_col1, w_col2, w_col3 = st.columns(3)
    with w_col1: w_ratio = st.number_input("Bobot: Ratio_vs_Cluster (%)", 0.0, value=50.0)
    with w_col2: w_trx = st.number_input("Bobot: Avg_Trx (%)", 0.0, value=30.0)
    with w_col3: w_growth = st.number_input("Bobot: Ton_Growth (%)", 0.0, value=20.0)
    total_w_pct = w_ratio + w_trx + w_growth
    w1, w2, w3 = (w_ratio/total_w_pct, w_trx/total_w_pct, w_growth/total_w_pct) if total_w_pct > 0 else (0.5, 0.3, 0.2)
    
    clusters_list = sorted(agg['Cluster'].unique())
    st.write("Atur Batas Maksimal per Cluster (isi 0 jika tanpa batas)")
    cols = st.columns(len(clusters_list))
    cluster_pct_inputs = {}
    for i, c in enumerate(clusters_list):
        with cols[i]:
            v = st.number_input(f"{c} (%)", 0.0, 100.0, value=0.0, key=f"clpct_{c}")
            cluster_pct_inputs[c] = v

    st.markdown("---")
    if st.button("▶️ Jalankan Optimasi", type="primary"):
        agg_final = agg.copy()
        agg_final['Score'] = (w1*agg_final['Ratio_vs_Cluster'] + w2*normalize(agg_final['Avg_Trx']) + w3*normalize(agg_final['Ton_Growth']))
        poin_to_rupiah = {'BRONZE': 10000, 'SILVER': 12500, 'GOLD': 15000, 'PLATINUM': 17500, 'SUPER PLATINUM': 20000}
        agg_final['Rupiah_per_Poin'] = agg_final['Cluster'].str.upper().map(poin_to_rupiah).fillna(0)
        agg_final['Estimated_Cost'] = agg_final['Avg_Ton'] * agg_final['Rupiah_per_Poin']
        agg_final.sort_values('Score', ascending=False, inplace=True)
        agg_final.drop_duplicates(subset=['ID Toko'], keep='first', inplace=True, ignore_index=True)
        
        try: import pulp
        except ImportError: st.error("Library 'pulp' tidak ditemukan."); st.stop()
        
        with st.spinner("Menjalankan optimasi..."):
            prob = pulp.LpProblem("Loyalty_Selection", pulp.LpMaximize)
            x_vars = {row['ID Toko']: pulp.LpVariable(f"x_{row['ID Toko']}", cat='Binary') for _, row in agg_final.iterrows()}
            prob += pulp.lpSum([row['Score'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()])
            prob += pulp.lpSum(x_vars.values()) <= int(N_max)
            prob += pulp.lpSum([row['Estimated_Cost'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()]) <= max_budget
            for cluster_name, max_pct in cluster_pct_inputs.items():
                if max_pct > 0:
                    members = agg_final[agg_final['Cluster'] == cluster_name]['ID Toko'].tolist()
                    cap = int(math.floor((max_pct / 100.0) * float(N_max)))
                    if members: prob += pulp.lpSum([x_vars[sid] for sid in members]) <= cap
            prob.solve(pulp.PULP_CBC_CMD(msg=False))
            selected_ids = [sid for sid, var in x_vars.items() if pulp.value(var) == 1]
            st.session_state.selected_df = agg_final[agg_final['ID Toko'].isin(selected_ids)].sort_values('Score', ascending=False, ignore_index=True)
            st.success("Optimasi selesai!")

if 'selected_df' in st.session_state:
    st.header("✅ Hasil Seleksi Optimasi")
    selected_df = st.session_state.selected_df
    total_estimated_budget = selected_df['Estimated_Cost'].sum()
    total_score = selected_df['Score'].sum()
    
    res_col1, res_col2 = st.columns(2)
    res_col1.metric("Total Toko Terpilih", f"{len(selected_df)}", f"dari target maks. {st.session_state.get('N_max', N_max)}")
    res_col2.metric("Estimasi Budget Bulanan", f"Rp {total_estimated_budget:,.0f}", f"dari maks. Rp {st.session_state.get('max_budget', max_budget):,.0f}")
    
    st.write("Distribusi cluster dari toko terpilih:")
    if not selected_df.empty:
        cluster_dist = selected_df['Cluster'].value_counts().reset_index()
        cluster_dist.columns = ['Cluster', 'Count']
        cluster_dist['Percentage'] = (cluster_dist['Count'] / len(selected_df) * 100).map('{:.2f}%'.format)
        st.dataframe(cluster_dist, use_container_width=True)
    else:
        st.write("Tidak ada toko yang terpilih.")

    # --- MODIFIKASI: Penambahan Fitur Analisis Kontribusi Toko Terpilih ---
    st.markdown("---")
    st.header("📊 Analisis Kontribusi Toko Terpilih")

    if not selected_df.empty:
        # 1. Hitung metrik kontribusi dan efisiensi
        selected_df['Kontribusi_Skor_%'] = (selected_df['Score'] / total_score * 100)
        selected_df['Kontribusi_Budget_%'] = (selected_df['Estimated_Cost'] / total_estimated_budget * 100)
        selected_df['Efisiensi (Skor per 1 Juta Biaya)'] = (selected_df['Score'] / (selected_df['Estimated_Cost'] + 1e-9)) * 1_000_000

        # 2. Tampilkan visualisasi
        st.subheader("Kontribusi Teratas")
        c1, c2 = st.columns(2)
        with c1:
            st.write("**Top 10 Kontributor Skor**")
            top_score_contributors = selected_df.nlargest(10, 'Kontribusi_Skor_%')
            st.bar_chart(top_score_contributors, x='Nama Toko', y='Kontribusi_Skor_%')
        with c2:
            st.write("**Top 10 Kontributor Budget**")
            top_budget_contributors = selected_df.nlargest(10, 'Kontribusi_Budget_%')
            st.bar_chart(top_budget_contributors, x='Nama Toko', y='Kontribusi_Budget_%')

        st.subheader("Analisis Efisiensi (Value for Money)")
        st.write("Grafik ini memetakan semua toko terpilih. Cari toko di **pojok kiri atas**: skor tinggi dengan biaya rendah.")
     # Ganti blok st.scatter_plot yang lama dengan ini:
st.write("Grafik ini memetakan semua toko terpilih. Cari toko di **pojok kiri atas**: skor tinggi dengan biaya rendah.")

chart = alt.Chart(selected_df).mark_circle().encode(
    x=alt.X('Estimated_Cost', title='Estimasi Biaya (Rp)'),
    y=alt.Y('Score', title='Skor Performa'),
    color='Cluster',
    tooltip=['Nama Toko', 'Cluster', 'Score', 'Estimated_Cost'],
    size='Avg_Ton'
).interactive()

st.altair_chart(chart, use_container_width=True)

        # 3. Tampilkan tabel lengkap dengan data analisis
        st.subheader("Data Lengkap Toko Terpilih dengan Analisis")
        st.dataframe(selected_df[[
            'Nama Toko', 'Cluster', 'Area', 'Score', 'Estimated_Cost', 
            'Kontribusi_Skor_%', 'Kontribusi_Budget_%', 'Efisiensi (Skor per 1 Juta Biaya)'
        ]].style.format({
            'Estimated_Cost': "Rp {:,.0f}",
            'Kontribusi_Skor_%': "{:.2f}%",
            'Kontribusi_Budget_%': "{:.2f}%",
            'Efisiensi (Skor per 1 Juta Biaya)': "{:,.2f}"
        }))
        
        excel_bytes = to_excel_bytes(selected_df)
        st.download_button("⬇️ Download Hasil Lengkap (Excel)", data=excel_bytes, file_name="analisis_optimasi_toko.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.balloons()
    # --- AKHIR MODIFIKASI ---
