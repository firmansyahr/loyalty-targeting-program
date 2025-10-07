# app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="Loyalty Program Optimization", layout="wide")

st.title("🏪 Loyalty Program Optimization - Seleksi Toko Loyalty")

st.markdown("""
Aplikasi ini membantu menentukan **toko terbaik untuk masuk program loyalty**  
berdasarkan data transaksi harian 3 bulan terakhir dan brand semen pilihan.
""")

# === STEP 1: Upload File ===
uploaded_file = st.file_uploader("📤 Upload file transaksi harian (CSV/XLSX)", type=["csv", "xlsx"])

if uploaded_file:
    # === STEP 2: Baca dan validasi data ===
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Gagal membaca file: {e}")
        st.stop()

    st.subheader("🔍 Data Awal")
    st.dataframe(df.head())

    # Pastikan kolom utama tersedia
    required_cols = [
        'Tanggal Transaksi', 'ID Toko', 'Nama Toko',
        'Cluster', 'Area', 'Brands', 'Nama Produk', 'Total Ton'
    ]
    if not all(col in df.columns for col in required_cols):
        st.error(f"File harus memiliki kolom: {required_cols}")
        st.stop()

    # === STEP 3: Pilih Brand ===
    available_brands = sorted(df['Brands'].dropna().unique())
    selected_brands = st.multiselect("🏷️ Pilih Brand Semen yang Akan Dihitung", available_brands)

    if len(selected_brands) == 0:
        st.warning("Silakan pilih minimal satu brand untuk diproses.")
        st.stop()

    df = df[df['Brands'].isin(selected_brands)]
    st.success(f"✅ Data difilter untuk brand: {', '.join(selected_brands)}")

    # === STEP 4: Preprocessing ===
    df['Tanggal Transaksi'] = pd.to_datetime(df['Tanggal Transaksi'])
    df['Bulan'] = df['Tanggal Transaksi'].dt.to_period('M')

    # Ambil 3 bulan terakhir dari data
    latest_month = df['Bulan'].max()
    last_3_months = pd.period_range(latest_month - 2, latest_month, freq='M')
    df = df[df['Bulan'].isin(last_3_months)]

    st.info(f"📅 Menggunakan data dari 3 bulan terakhir: {', '.join(map(str, last_3_months))}")

    # Grouping per toko per bulan
    grouped = df.groupby(['ID Toko', 'Nama Toko', 'Cluster', 'Area', 'Bulan']).agg(
        Total_Ton=('Total Ton', 'sum'),
        Jumlah_Transaksi=('Tanggal Transaksi', 'count')
    ).reset_index()

    # Hitung agregat per toko
    agg = grouped.groupby(['ID Toko', 'Nama Toko', 'Cluster', 'Area']).agg(
        Avg_Ton=('Total_Ton', 'mean'),
        Avg_Trx=('Jumlah_Transaksi', 'mean'),
        Ton_Last=('Total_Ton', 'last')
    ).reset_index()

    # Hitung growth tonase (bulan terakhir vs dua bulan sebelumnya)
    growths = []
    for sid in agg['ID Toko']:
        toko_data = grouped[grouped['ID Toko'] == sid].sort_values('Bulan')
        if len(toko_data) >= 2:
            prev_mean = toko_data['Total_Ton'][:-1].mean()
            last_val = toko_data['Total_Ton'].iloc[-1]
            growth = (last_val - prev_mean) / prev_mean if prev_mean > 0 else 0
        else:
            growth = 0
        growths.append(growth)
    agg['Ton_Growth'] = growths

    # === STEP 5: Hitung Rata-Rata per Cluster ===
    cluster_avg = agg.groupby('Cluster')['Avg_Ton'].mean().to_dict()
    agg['Ratio_vs_Cluster'] = agg.apply(lambda x: x['Avg_Ton'] / cluster_avg.get(x['Cluster'], 1), axis=1)

    # === STEP 6: Hitung Skor ===
    def normalize(series):
        return (series - series.min()) / (series.max() - series.min() + 1e-6)

    agg['Score'] = (
        0.5 * agg['Ratio_vs_Cluster'] +
        0.3 * normalize(agg['Avg_Trx']) +
        0.2 * normalize(agg['Ton_Growth'])
    )

    agg = agg.sort_values('Score', ascending=False).reset_index(drop=True)

    # === STEP 7: Visualisasi ===
    st.subheader("📊 Hasil Scoring Toko (Top 20)")
    st.dataframe(agg.head(20))

    st.subheader("📈 Rata-rata Skor per Cluster")
    cluster_chart = agg.groupby('Cluster')['Score'].mean().sort_values(ascending=False)
    st.bar_chart(cluster_chart)

    # === STEP 8: Download Hasil ===
    st.subheader("📥 Download Hasil Seleksi")

    def to_excel(df):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Hasil')
        return output.getvalue()

    st.download_button(
        label="💾 Download hasil dalam Excel",
        data=to_excel(agg),
        file_name="hasil_loyalty_selection.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.success("✅ Selesai! Data berhasil diproses dan diberi skor berdasarkan brand terpilih.")

else:
    st.info("Silakan upload file transaksi untuk memulai analisis.")
