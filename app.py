# app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime
import sys

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

st.subheader("📈 Statistik ringkas")
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

# ---------------- Optimization UI ----------------
st.sidebar.header("⚙️ Pengaturan Optimasi")
N_max = st.sidebar.number_input("Jumlah Toko Maksimal (N_max)", min_value=1, max_value=agg.shape[0], value=min(500, agg.shape[0]), step=1)

st.sidebar.markdown("**Atur persentase maksimal per cluster** (nilai antara 0 - 100). Nilai adalah persentase dari N_max.")
clusters_list = sorted(agg['Cluster'].unique())
cluster_caps = {}
default_pct = round(100.0 / len(clusters_list), 1)
for c in clusters_list:
    pct = st.sidebar.number_input(f"Max % untuk cluster {c}", min_value=0.0, max_value=100.0, value=float(default_pct), step=0.5, key=f"pct_{c}")
    cluster_caps[c] = pct / 100.0  # convert to fraction

# Optional: adjust weights
st.sidebar.markdown("---")
st.sidebar.markdown("**Bobot skor (opsional)**")
w1 = st.sidebar.slider("Bobot: Ratio vs Cluster", 0.0, 1.0, default_w1, 0.05)
w2 = st.sidebar.slider("Bobot: Avg Transactions", 0.0, 1.0, default_w2, 0.05)
w3 = st.sidebar.slider("Bobot: Ton Growth", 0.0, 1.0, default_w3, 0.05)
# normalize weights to sum 1 if user changed them
w_sum = w1 + w2 + w3
if w_sum == 0:
    w1, w2, w3 = default_w1, default_w2, default_w3
else:
    w1, w2, w3 = w1 / w_sum, w2 / w_sum, w3 / w_sum

# recompute score with chosen weights
agg['Score'] = (
    w1 * agg['Ratio_vs_Cluster'] +
    w2 * normalize(agg['Avg_Trx']) +
    w3 * normalize(agg['Ton_Growth'])
)
agg = agg.sort_values('Score', ascending=False).reset_index(drop=True)

st.sidebar.markdown("---")
run_opt = st.sidebar.button("▶️ Jalankan Optimasi")

# -------------- Optimization Execution --------------
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
        cap = int(np.floor(cluster_caps[c] * float(N_max) + 1e-9))
        # Add constraint (cap could be zero)
        if len(members) > 0:
            prob += pulp.lpSum([x_vars[sid] for sid in members]) <= cap

    # Additional optional constraint: ensure we don't select more stores than available per cluster (implicit)
    # Solve
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
    st.info("Atur parameter optimasi di sidebar lalu tekan 'Jalankan Optimasi'.")

# ---------------- End ----------------
