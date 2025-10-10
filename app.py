# app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime
import math
import altair as alt

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
    # ... (Logika di bagian ini sama persis seperti sebelumnya, tidak ada perubahan) ...
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
    
    st.subheader("📍 Pengaturan Filter")
    # Filter Area
    available_areas = sorted(base_agg['Area'].unique())
    selected_areas = st.multiselect("Pilih Area", available_areas, default=available_areas)
    if not selected_areas: st.warning("Pilih minimal satu Area."); st.stop()
    agg = base_agg[base_agg['Area'].isin(selected_areas)].copy()
    
    # --- FITUR BARU: Pengecualian ID Toko ---
    excluded_ids_str = st.text_area(
        "❌ Kecualikan ID Toko (opsional)",
        placeholder="Salin dan tempel (copy-paste) kolom ID Toko dari Excel di sini. Setiap ID di baris baru.",
        height=150
    )
    
    if excluded_ids_str:
        # Proses input: pisahkan per baris, hapus spasi, dan buang baris kosong
        excluded_ids_list = [toko_id.strip() for toko_id in excluded_ids_str.splitlines() if toko_id.strip()]
        
        # Konversi ID Toko di DataFrame ke string untuk pencocokan yang aman
        agg['ID Toko'] = agg['ID Toko'].astype(str)
        
        # Filter DataFrame untuk membuang ID yang dikecualikan
        agg = agg[~agg['ID Toko'].isin(excluded_ids_list)]
        
        st.info(f"✅ Sebanyak **{len(excluded_ids_list)} ID Toko** telah ditandai untuk dikecualikan.")
    # --- AKHIR FITUR BARU ---
        
    st.subheader("💰 Pengaturan Anggaran & Seleksi")
    # ... (Sisa kode di bagian ini sama persis, tidak ada perubahan) ...
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
    w1, w2, w3 = (w_ratio/total_w_pct, w_trx/total_w_
