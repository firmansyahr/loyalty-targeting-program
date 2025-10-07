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
Aplikasi: Upload transaksi harian (Juli–Sep 2025). Pilih brand, hitung skor per toko, lalu jalankan optimasi
untuk memilih toko peserta program loyalty berdasarkan batasan jumlah dan proporsi cluster.
""")

# ------------- Helper functions -------------
def normalize(series):
    return (series - series.min()) / (series.max() - series.min() + 1e-9)

def to_excel_bytes(df):
    output = BytesIO()
    # use openpyxl engine for compatibility
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Hasil')
    return output.getvalue()

# ------------- File Upload & Preprocessing -------------
uploaded_file = st.file_uploader("📤 Upload file transaksi harian (CSV / XLSX)", type=["csv", "xlsx"])
if not uploaded_file:
    st.info("Silakan upload file transaksi terlebih dahulu. Format kolom wajib: "
            "`Tanggal Transaksi`, `ID Toko`, `Nama Toko`, `Cluster`, `Area`, `Brands`, `Nama Produk`, `Total Ton`.")
    st.stop()

# read file
try:
    if uploaded_file.name.lower().endswith(".csv"):
        df_raw = pd.read_csv(uploaded_file)
    else:
        df_raw = pd.read_excel(uploaded_file)
except Exception as e:
    st.error(f"Gagal membaca file: {e}")
    st.stop()

required_cols = [
    'Tanggal Transaksi', 'ID Toko', 'Nama Toko',
    'Cluster', 'Area', 'Brands', 'Nama Produk', 'Total Ton'
]
missing = [c for c in required_cols if c not in df_raw.columns]
if missing:
    st.error(f"Kolom wajib hilang: {missing}. Pastikan file sesuai template.")
    st.stop()

st.subheader("🔍 Preview data awal (5 baris)")
st.dataframe(df_raw.head())

# Brand filter
available_brands = sorted(df_raw['Brands'].dropna().unique())
selected_brands = st.multiselect("🏷️ Pilih Brand yang dihitung", available_brands,
                                 default=[b for b in ["SEMEN GRESIK","DYNAMIX","MERDEKA"] if b in available_brands])
if not selected_brands:
    st.warning("Pilih minimal 1 brand.")
    st.stop()

df = df_raw[df_raw['Brands'].isin(selected_brands)].copy()
df['Tanggal Transaksi'] = pd.to_datetime(df['Tanggal Transaksi'], errors='coerce')
df = df.dropna(subset=['Tanggal Transaksi'])
df['Bulan'] = df['Tanggal Transaksi'].dt.to_period('M')

# Filter periode: Juli - September 2025 (user requested)
start_period = pd.Period('2025-07', freq='M')
end_period = pd.Period('2025-09', freq='M')
valid_months = pd.period_range(start_period, end_period, freq='M')
df = df[df['Bulan'].isin(valid_months)]

if df.empty:
    st.warning("Tidak ada transaksi pada periode Juli-September 2025 untuk brand terpilih.")
    st.stop()

st.info(f"Data setelah filter brand & periode: {len(df)} baris, periode: {valid_months[0]} - {valid_months[-1]}")

# Group per toko per bulan
grouped = df.groupby(['ID Toko', 'Nama Toko', 'Cluster', 'Area', 'Bulan']).agg(
    Total_Ton=('Total Ton', 'sum'),
    Jumlah_Transaksi=('Tanggal Transaksi', 'count')
).reset_index()

# Aggregate per toko (3 bulan)
agg = grouped.groupby(['ID Toko', 'Nama Toko', 'Cluster', 'Area']).agg(
    Avg_Ton=('Total_Ton', 'mean'),
    Avg_Trx=('Jumlah_Transaksi', 'mean'),
    Ton_Last=('Total_Ton', 'last')
).reset_index()

# Compute growth (last month vs mean of previous months)
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

# Cluster average and ratio
cluster_avg = agg.groupby('Cluster')['Avg_Ton'].mean().to_dict()
agg['Ratio_vs_Cluster'] = agg.apply(lambda x: x['Avg_Ton'] / cluster_avg.get(x['Cluster'], 1.0), axis=1)

# Scoring (default weights, adjustable later)
default_w1, default_w2, default_w3 = 0.5, 0.3, 0.2
agg['Score'] = (
    default_w1 * agg['Ratio_vs_Cluster'] +
    default_w2 * normalize(agg['Avg_Trx']) +
    default_w3 * normalize(agg['Ton_Growth'])
)

agg = agg.sort_values('Score', ascending=False).reset_index(drop=True)

# ---------------- Show preprocessing results ----------------
st.subheader("📈 Statistik ringkas (setelah preprocessing & scoring awal)")
col1, col2, col3 = st.columns(3)
col1.metric("Jumlah Toko (unik)", agg['ID Toko'].nunique())
col2.metric("Jumlah transaksi (baris)", len(df))
col3.metric("Jumlah cluster", agg['Cluster'].nunique())

st.subheader("📊 Distribusi brand & cluster (setelah filter)")
c1, c2 = st.columns(2)
with c1:
    st.write("Brand counts")
    st.dataframe(df['Brands'].value_counts())
with c2:
    st.write("Cluster (toko level)")
    st.dataframe(agg['Cluster'].value_counts())

st.markdown("---")

# ---------------- Settings (placed AFTER preprocessing as requested) ----------------
st.subheader("⚙️ Pengaturan Seleksi & Proporsi Cluster (letak di bawah hasil preprocessing)")

# 1) N_max input (jumlah toko terpilih)
total_available = agg.shape[0]
N_max = st.number_input("1) Jumlah Toko Maksimal (N_max) — jumlah toko yang ingin dipilih untuk program loyalty",
                        min_value=1, max_value=total_available, value=min(500, total_available), step=1)

st.write(f"Total toko tersedia untuk dipilih: **{total_available}** — N_max diset ke **{N_max}**")

# 2) Persentase maksimal per cluster (user input but auto-normalize to 100%)
clusters_list = sorted(agg['Cluster'].unique())
st.write("2) Atur Persentase Maksimum per Cluster (total harus = 100%). Jika total != 100%, sistem akan menormalisasi otomatis.")
cols = st.columns(len(clusters_list))
cluster_pct_inputs = {}
# default equal split
default_vals = [round(100.0 / len(clusters_list), 2)] * len(clusters_list)
for i, c in enumerate(clusters_list):
    with cols[i]:
        v = st.number_input(f"{c} (%)", min_value=0.0, max_value=100.0, value=float(default_vals[i]), step=0.5, key=f"clpct_{c}")
        cluster_pct_inputs[c] = v

# Validate & normalize cluster percents to sum 100%
total_cluster_pct = sum(cluster_pct_inputs.values())
if total_cluster_pct == 0:
    # fallback to equal distribution
    norm_cluster_pct = {c: 1.0/len(clusters_list) for c in clusters_list}
    st.warning("Total persentase cluster = 0%. Diganti ke distribusi merata.")
else:
    # normalize to 100% and convert to fraction
    norm_cluster_pct = {c: (cluster_pct_inputs[c] / total_cluster_pct) for c in clusters_list}
    # show message if user inputs didn't sum to 100
    if abs(total_cluster_pct - 100.0) > 1e-6:
        st.info(f"Persentase cluster yang dimasukkan = {total_cluster_pct:.2f}%. Dinormalisasi ke total 100% secara proporsional.")
        # display normalized %
        norm_display = {c: round(norm_cluster_pct[c]*100.0,2) for c in clusters_list}
        st.write("Distribusi cluster setelah normalisasi (%) :", norm_display)
    else:
        st.success("Total persentase cluster = 100%.")

# 3) Bobot Skor (user sets weights; auto-normalize to sum=1)
st.write("3) Atur Bobot Skor untuk komponen penilaian (total bobot = 100%).")
w_col1, w_col2, w_col3 = st.columns(3)
w_inputs = {}
with w_col1:
    w_inputs['ratio'] = st.number_input("Bobot: Ratio_vs_Cluster (%)", min_value=0.0, max_value=100.0, value=default_w1*100, step=1.0)
with w_col2:
    w_inputs['trx'] = st.number_input("Bobot: Avg_Trx (%)", min_value=0.0, max_value=100.0, value=default_w2*100, step=1.0)
with w_col3:
    w_inputs['growth'] = st.number_input("Bobot: Ton_Growth (%)", min_value=0.0, max_value=100.0, value=default_w3*100, step=1.0)

total_w_pct = sum(w_inputs.values())
if total_w_pct == 0:
    w1, w2, w3 = default_w1, default_w2, default_w3
    st.warning("Total bobot = 0%. Dipakai bobot default.")
else:
    # normalize to sum 1.0
    w1 = w_inputs['ratio'] / total_w_pct
    w2 = w_inputs['trx'] / total_w_pct
    w3 = w_inputs['growth'] / total_w_pct
    if abs(total_w_pct - 100.0) > 1e-6:
        st.info(f"Total bobot yang dimasukkan = {total_w_pct:.2f}%. Dinormalisasi ke 100%. (Ratio: {w1:.2f}, Trx: {w2:.2f}, Growth: {w3:.2f})")
    else:
        st.success("Total bobot = 100%.")

# Recompute Score with (normalized) weights and show top table preview
agg['Score'] = (
    w1 * agg['Ratio_vs_Cluster'] +
    w2 * normalize(agg['Avg_Trx']) +
    w3 * normalize(agg['Ton_Growth'])
)
agg = agg.sort_values('Score', ascending=False).reset_index(drop=True)
st.subheader("📋 Preview Top 20 setelah normalisasi bobot")
st.dataframe(agg.head(20))

st.markdown("---")

# ------------- Run Optimization button -------------
st.subheader("▶️ Jalankan Optimasi")
run_opt = st.button("Jalankan Optimasi dengan constraint di atas")

if run_opt:
    # Try import pulp
    try:
        import pulp
    except Exception as e:
        st.error("Library 'pulp' tidak ditemukan. Install dengan `pip install pulp` lalu jalankan ulang.")
        st.stop()

    st.info("Menyiapkan model optimasi (Integer Programming)...")

    # create LP problem
    prob = pulp.LpProblem("Loyalty_Selection", pulp.LpMaximize)

    # decision variables: x_store (binary)
    x_vars = {}
    for idx, row in agg.iterrows():
        var_name = f"x_{row['ID Toko']}"
        x_vars[row['ID Toko']] = pulp.LpVariable(var_name, cat='Binary')

    # objective: maximize total score
    prob += pulp.lpSum([row['Score'] * x_vars[row['ID Toko']] for _, row in agg.iterrows()])

    # constraint: total selected <= N_max
    prob += pulp.lpSum([x_vars[sid] for sid in x_vars.keys()]) <= int(N_max)

    # cluster constraints: for each cluster, selected_in_cluster <= p_k * N_max
    for c in clusters_list:
        members = agg[agg['Cluster'] == c]['ID Toko'].tolist()
        cap = int(math.floor(norm_cluster_pct[c] * float(N_max) + 1e-9))
        if len(members) > 0:
            prob += pulp.lpSum([x_vars[sid] for sid in members]) <= cap

    st.info("Menjalankan solver PuLP (CBC default)...")
    solve_status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    status = pulp.LpStatus[prob.status]
    st.success(f"Solver completed. Status: {status}")

    # Collect selected stores
    selected_ids = [sid for sid, var in x_vars.items() if pulp.value(var) == 1]
    selected_df = agg[agg['ID Toko'].isin(selected_ids)].copy()
    selected_df = selected_df.sort_values('Score', ascending=False).reset_index(drop=True)

    # Summary
    st.subheader("✅ Hasil Seleksi")
    st.write(f"Total toko terpilih: **{len(selected_df)}** (Batas N_max = {N_max})")
    st.write(f"Total Score (objective): **{sum(selected_df['Score']):.4f}**")
    # show cluster distribution
    st.write("Distribusi cluster dari toko terpilih:")
    st.dataframe(selected_df['Cluster'].value_counts().rename_axis('Cluster').reset_index(name='Count'))

    st.subheader("Daftar Toko Terpilih (Top 200)")
    st.dataframe(selected_df[['ID Toko', 'Nama Toko', 'Cluster', 'Area', 'Avg_Ton', 'Avg_Trx', 'Ton_Growth', 'Score']].head(200))

    # KPIs: total avg ton of selected stores
    total_avg_ton = selected_df['Avg_Ton'].sum()
    st.write(f"Total avg ton (sum Avg_Ton) of selected stores: **{total_avg_ton:.2f}**")

    # Download buttons
    csv_bytes = selected_df.to_csv(index=False).encode('utf-8')
    st.download_button("⬇️ Download selected (CSV)", data=csv_bytes, file_name="selected_stores.csv", mime="text/csv")

    excel_bytes = to_excel_bytes(selected_df)
    st.download_button("⬇️ Download selected (Excel)", data=excel_bytes, file_name="selected_stores.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # Show top rejected stores (optional)
    rejected = agg[~agg['ID Toko'].isin(selected_ids)].copy().sort_values('Score', ascending=False)
    st.subheader("Top 20 Rejected (berdasarkan Score)")
    st.dataframe(rejected[['ID Toko', 'Nama Toko', 'Cluster', 'Area', 'Avg_Ton', 'Score']].head(20))

    st.balloons()
else:
    st.info("Sesuaikan parameter di atas lalu tekan 'Jalankan Optimasi'.")

# ---------------- End ----------------
