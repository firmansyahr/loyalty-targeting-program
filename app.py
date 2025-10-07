# app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime
import math

st.set_page_config(page_title="Loyalty Target Optimizer", layout="wide")
st.title("🔎 Loyalty Target Optimizer (SIG Retail Insight)")

st.markdown("""
Aplikasi: Upload data transaksi, klik proses, atur semua parameter (termasuk anggaran), lalu jalankan optimasi.
""")

# ------------- Helper functions -------------
def normalize(series):
    return (series - series.min()) / (series.max() - series.min() + 1e-9)

def to_excel_bytes(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Hasil')
    return output.getvalue()

# ------------- TAHAP 1: UPLOAD DAN PROSES DATA -------------
st.subheader("Langkah 1: Upload & Proses Data Awal")

uploaded_file = st.file_uploader("📤 Upload file transaksi (CSV / XLSX)", type=["csv", "xlsx"])

if uploaded_file:
    col1, col2 = st.columns([3, 1])
    with col1:
        try:
            if uploaded_file.name.lower().endswith(".csv"):
                df_raw = pd.read_csv(uploaded_file)
            else:
                df_raw = pd.read_excel(uploaded_file)
            st.session_state.df_raw = df_raw
            available_brands = sorted(df_raw['Brands'].dropna().unique())
            selected_brands = st.multiselect("🏷️ Pilih Brand yang dihitung", available_brands,
                                              default=[b for b in ["SEMEN GRESIK","DYNAMIX","MERDEKA"] if b in available_brands])
            st.session_state.selected_brands = selected_brands
        except Exception as e:
            st.error(f"Gagal membaca file: {e}")
            st.stop()
    with col2:
        st.write("👇 Setelah pilih brand, klik di sini")
        process_button = st.button("⚙️ Proses Data & Hitung Skor Awal", type="primary")

    if process_button:
        with st.spinner("Memproses data..."):
            df_raw = st.session_state.df_raw
            selected_brands = st.session_state.selected_brands
            required_cols = ['Tanggal Transaksi', 'ID Toko', 'Nama Toko', 'Cluster', 'Area', 'Brands', 'Nama Produk', 'Total Ton']
            if not all(c in df_raw.columns for c in required_cols):
                st.error(f"Kolom wajib hilang. Pastikan file memiliki kolom: {required_cols}")
                st.stop()
            if not selected_brands:
                st.warning("Pilih minimal 1 brand.")
                st.stop()

            df = df_raw[df_raw['Brands'].isin(selected_brands)].copy()
            df['Tanggal Transaksi'] = pd.to_datetime(df['Tanggal Transaksi'], errors='coerce')
            df.dropna(subset=['Tanggal Transaksi'], inplace=True)
            if df.empty:
                st.warning("Tidak ada data transaksi untuk brand yang dipilih.")
                st.stop()
            df['Bulan'] = df['Tanggal Transaksi'].dt.to_period('M')

            grouped = df.groupby(['ID Toko', 'Nama Toko', 'Cluster', 'Area', 'Bulan']).agg(
                Total_Ton=('Total Ton', 'sum'),
                Jumlah_Transaksi=('Tanggal Transaksi', 'count')
            ).reset_index()

            agg = grouped.groupby(['ID Toko', 'Nama Toko', 'Cluster', 'Area']).agg(
                Avg_Ton=('Total_Ton', 'mean'),
                Avg_Trx=('Jumlah_Transaksi', 'mean'),
            ).reset_index()

            growths = []
            for sid in agg['ID Toko']:
                toko_data = grouped[grouped['ID Toko'] == sid].sort_values('Bulan')
                if len(toko_data) >= 2:
                    prev_mean = toko_data['Total_Ton'][:-1].mean()
                    last_val = toko_data['Total_Ton'].iloc[-1]
                    growth = (last_val - prev_mean) / prev_mean if prev_mean > 0 else 0.0
                else:
                    growth = 0.0
                growths.append(growth)
            agg['Ton_Growth'] = growths

            cluster_avg = agg.groupby('Cluster')['Avg_Ton'].mean().to_dict()
            agg['Ratio_vs_Cluster'] = agg.apply(lambda x: x['Avg_Ton'] / cluster_avg.get(x['Cluster'], 1.0), axis=1)
            
            st.session_state.agg = agg
            st.session_state.df = df
            st.success("Data berhasil diproses! Silakan atur parameter di bawah.")

st.markdown("---")

# ------------- TAHAP 2: PENGATURAN PARAMETER DAN OPTIMASI -------------
if 'agg' in st.session_state:
    agg = st.session_state.agg
    df = st.session_state.df
    
    st.subheader("Langkah 2: Atur Parameter & Jalankan Optimasi")
    st.subheader("📈 Statistik ringkas (hasil pemrosesan)")
    col1, col2, col3 = st.columns(3)
    col1.metric("Jumlah Toko (unik)", agg['ID Toko'].nunique())
    col2.metric("Jumlah transaksi (baris)", len(df))
    col3.metric("Jumlah cluster", agg['Cluster'].nunique())
    
    # --- PENAMBAHAN FITUR ANGGARAN ---
    # 1. Input untuk Anggaran Maksimal
    st.subheader("💰 Pengaturan Anggaran")
    max_budget = st.number_input(
        "Masukkan Anggaran Maksimal (Rp)", 
        min_value=0, 
        value=1_000_000_000, 
        step=50_000_000,
        help="Total estimasi biaya dari toko terpilih tidak akan melebihi angka ini."
    )
    # --- AKHIR PENAMBAHAN ---

    st.subheader("⚙️ Pengaturan Seleksi & Skor")
    # 1) N_max input
    total_available = agg.shape[0]
    N_max = st.number_input("1) Jumlah Toko Maksimal (N_max)", min_value=1, max_value=total_available, value=min(500, total_available), step=1)

    # 2) Persentase cluster
    clusters_list = sorted(agg['Cluster'].unique())
    st.write("2) Atur Persentase Maksimum per Cluster")
    cols = st.columns(len(clusters_list))
    cluster_pct_inputs = {}
    default_vals = [round(100.0 / len(clusters_list), 2)] * len(clusters_list)
    for i, c in enumerate(clusters_list):
        with cols[i]:
            v = st.number_input(f"{c} (%)", min_value=0.0, max_value=100.0, value=float(default_vals[i]), step=0.5, key=f"clpct_{c}")
            cluster_pct_inputs[c] = v

    total_cluster_pct = sum(cluster_pct_inputs.values())
    norm_cluster_pct = {c: (cluster_pct_inputs[c] / total_cluster_pct) if total_cluster_pct > 0 else 1.0/len(clusters_list) for c in clusters_list}

    # 3) Bobot Skor
    st.write("3) Atur Bobot Skor")
    w_col1, w_col2, w_col3 = st.columns(3)
    with w_col1:
        w_ratio = st.number_input("Bobot: Ratio_vs_Cluster (%)", min_value=0.0, value=50.0, step=1.0)
    with w_col2:
        w_trx = st.number_input("Bobot: Avg_Trx (%)", min_value=0.0, value=30.0, step=1.0)
    with w_col3:
        w_growth = st.number_input("Bobot: Ton_Growth (%)", min_value=0.0, value=20.0, step=1.0)

    total_w_pct = w_ratio + w_trx + w_growth
    if total_w_pct == 0:
        w1, w2, w3 = 0.5, 0.3, 0.2 # fallback
    else:
        w1, w2, w3 = w_ratio / total_w_pct, w_trx / total_w_pct, w_growth / total_w_pct

    st.markdown("---")

    run_opt = st.button("▶️ Jalankan Optimasi", type="primary")
    if run_opt:
        agg_final = agg.copy()
        
        # --- PENAMBAHAN FITUR ANGGARAN ---
        # 2. Definisikan nilai konversi dan hitung estimasi biaya per toko
        poin_to_rupiah = {
            'BRONZE': 10000,
            'SILVER': 12500,
            'GOLD': 15000,
            'PLATINUM': 17500,
            'SUPER PLATINUM': 20000, # Pastikan nama cluster sesuai data Anda
        }
        # Gunakan .str.upper() agar tidak case-sensitive, .fillna(0) untuk cluster lain
        agg_final['Rupiah_per_Poin'] = agg_final['Cluster'].str.upper().map(poin_to_rupiah).fillna(0)
        agg_final['Estimated_Cost'] = agg_final['Avg_Ton'] * agg_final['Rupiah_per_Poin']
        # --- AKHIR PENAMBAHAN ---

        agg_final['Score'] = (w1*agg_final['Ratio_vs_Cluster'] + w2*normalize(agg_final['Avg_Trx']) + w3*normalize(agg_final['Ton_Growth']))
        agg_final.sort_values('Score', ascending=False, inplace=True, ignore_index=True)
        
        st.subheader("📋 Preview Top 20 (termasuk estimasi biaya)")
        st.dataframe(agg_final[['ID Toko', 'Nama Toko', 'Cluster', 'Avg_Ton', 'Score', 'Estimated_Cost']].head(20))
        
        try:
            import pulp
        except ImportError:
            st.error("Library 'pulp' tidak ditemukan. Install dengan `pip install pulp`.")
            st.stop()
        
        with st.spinner("Menjalankan optimasi dengan batasan anggaran..."):
            prob = pulp.LpProblem("Loyalty_Selection", pulp.LpMaximize)
            x_vars = {row['ID Toko']: pulp.LpVariable(f"x_{row['ID Toko']}", cat='Binary') for _, row in agg_final.iterrows()}
            prob += pulp.lpSum([row['Score'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()])
            prob += pulp.lpSum(x_vars.values()) <= int(N_max)
            for c in clusters_list:
                members = agg_final[agg_final['Cluster'] == c]['ID Toko'].tolist()
                cap = int(math.floor(norm_cluster_pct[c] * float(N_max) + 1e-9))
                if members: prob += pulp.lpSum([x_vars[sid] for sid in members]) <= cap
            
            # --- PENAMBAHAN FITUR ANGGARAN ---
            # 3. Tambahkan constraint anggaran ke model PuLP
            prob += pulp.lpSum([row['Estimated_Cost'] * x_vars[row['ID Toko']] for _, row in agg_final.iterrows()]) <= max_budget
            # --- AKHIR PENAMBAHAN ---

            prob.solve(pulp.PULP_CBC_CMD(msg=False))
            
            selected_ids = [sid for sid, var in x_vars.items() if pulp.value(var) == 1]
            selected_df = agg_final[agg_final['ID Toko'].isin(selected_ids)].sort_values('Score', ascending=False).reset_index(drop=True)

            st.subheader("✅ Hasil Seleksi")
            
            # --- PENAMBAHAN FITUR ANGGARAN ---
            # 4. Hitung dan tampilkan total estimasi budget
            total_estimated_budget = selected_df['Estimated_Cost'].sum()
            
            res_col1, res_col2 = st.columns(2)
            res_col1.metric("Total Toko Terpilih", f"{len(selected_df)}", f"dari target maks. {N_max}")
            res_col2.metric("Estimasi Budget Bulanan", f"Rp {total_estimated_budget:,.0f}", f"dari maks. Rp {max_budget:,.0f}")
            # --- AKHIR PENAMBAHAN ---

            st.write("Distribusi cluster dari toko terpilih:")
            st.dataframe(selected_df['Cluster'].value_counts().rename_axis('Cluster').reset_index(name='Count'))
            st.subheader("Daftar Toko Terpilih")
            st.dataframe(selected_df)
            
            excel_bytes = to_excel_bytes(selected_df)
            st.download_button("⬇️ Download Hasil (Excel)", data=excel_bytes, file_name="selected_stores.xlsx")
            st.balloons()
