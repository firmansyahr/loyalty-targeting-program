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
    .block-container { padding-top: 1.5rem; }
    div[data-testid="stSidebar"] { background: #1a1a2e; color: white; }
    div[data-testid="stSidebar"] .stMarkdown { color: #ccc; }
    div[data-testid="stSidebar"] label { color: #ccc !important; }
    .section-header {
        background: linear-gradient(90deg,#1a1a2e,#16213e);
        color:white; padding:12px 20px; border-radius:10px;
        margin:1rem 0 0.5rem 0; font-weight:600;
    }
    .info-box { background:#e8f4fd; padding:12px; border-left:4px solid #2196F3; border-radius:5px; margin-bottom:8px; }
    .ok-box   { background:#e8f5e9; padding:12px; border-left:4px solid #4CAF50; border-radius:5px; margin-bottom:8px; }
    .warn-box { background:#fff8e1; padding:12px; border-left:4px solid #FF9800; border-radius:5px; margin-bottom:8px; }
</style>
""", unsafe_allow_html=True)

st.title("🎯 Loyalty Program Optimizer")
st.markdown("Seleksi toko terbaik menggunakan **Multi-Criteria Scoring + ILP-A Mirror**.")

# ──────────────────────────────────────────────────────────────────
# KONSTANTA
# ──────────────────────────────────────────────────────────────────
JAWA_BALI = ['Jawa Barat','Jawa Tengah','Jawa Timur',
             'DKI Jakarta','Banten','DI Yogyakarta','Bali']
MIN_BULAN_AKTIF = 3

# UPPERCASE agar cocok dengan data aktual
FB_PROVINCES = ['KALIMANTAN TIMUR','KALIMANTAN UTARA',
                'SULAWESI TENGAH','SULAWESI SELATAN']

REWARD_RATES = {
    'Platinum'      : {'Main Brand':3750,'Companion Brand':1875,'Fighting Brand':1875},
    'Super Platinum': {'Main Brand':3750,'Companion Brand':1875,'Fighting Brand':1875},
    'Gold'          : {'Main Brand':2500,'Companion Brand':1250,'Fighting Brand':1250},
    'Silver'        : {'Main Brand':2500,'Companion Brand':1250,'Fighting Brand':1250},
    'Bronze'        : {'Main Brand':2500,'Companion Brand':1250,'Fighting Brand':1250},
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

# ──────────────────────────────────────────────────────────────────
# FUNGSI INTI
# ──────────────────────────────────────────────────────────────────
def normalize(s):
    return (s - s.min()) / (s.max() - s.min() + 1e-9)

def get_brand_category(area, brand, prov):
    au = str(area).strip().upper()
    pu = str(prov).strip().upper()
    bu = str(brand).strip().upper()
    am = BRAND_MAP_BY_PROV.get(au)
    if am is None: return 'Other'
    pm = am.get(pu)
    if pm is None:
        for k in am:
            if k in pu or pu in k: pm = am[k]; break
    if pm is None:
        fb = {'SP'  :{'main':['PADANG'],   'companion':['DYNAMIX','ANDALAS','BATURAJA']},
              'SMBR':{'main':['BATURAJA'], 'companion':['DYNAMIX','PADANG']},
              'ST'  :{'main':['TONASA'],   'companion':['GRESIK']}}
        pm = fb.get(au, {'main':[],'companion':[]})
    if any(k in bu for k in pm['main']): return 'Main Brand'
    if pm['companion'] and any(k in bu for k in pm['companion']): return 'Companion Brand'
    if au == 'ST' and 'MERDEKA' in bu and pu in FB_PROVINCES: return 'Fighting Brand'
    return 'Other'

def get_reward(cluster, brand_cat):
    return REWARD_RATES.get(cluster, REWARD_RATES['Bronze']).get(brand_cat, 0.0)

def compute_spearman_weights(df):
    vs = ['Ratio_vs_Cluster','Avg_Trx','Ton_Growth']
    rw = {v: abs(spearmanr(df[v], df['Avg_Ton'])[0]) for v in vs}
    t  = sum(rw.values()) or 1
    return {k: v/t for k,v in rw.items()}

def compute_scores(df, w1, w2, w3):
    d = df.copy()
    d['Score'] = w1*d['Ratio_vs_Cluster'] + w2*normalize(d['Avg_Trx']) + w3*normalize(d['Ton_Growth'])
    return d

def run_ilp(agg, n_max, budget, cluster_pcts=None):
    d    = agg.drop_duplicates(subset=['ID Toko']).copy()
    prob = pulp.LpProblem("ILP", pulp.LpMaximize)
    xv   = {r['ID Toko']: pulp.LpVariable(f"x_{i}", cat='Binary') for i,r in d.iterrows()}
    prob += pulp.lpSum(r['Score']*xv[r['ID Toko']] for _,r in d.iterrows())
    prob += pulp.lpSum(xv.values()) <= int(n_max)
    if budget > 0:
        prob += pulp.lpSum(r['Estimated_Cost']*xv[r['ID Toko']] for _,r in d.iterrows()) <= budget
    if cluster_pcts:
        for cl,pct in cluster_pcts.items():
            mem = d[d['Cluster Pareto']==cl]['ID Toko'].tolist()
            cap = int(math.ceil(pct/100*n_max))
            if mem and cap > 0:
                prob += pulp.lpSum(xv[s] for s in mem if s in xv) <= cap
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    return [s for s,v in xv.items() if pulp.value(v)==1]

def read_file(f):
    fn = f.name.lower()
    kw = {'dtype':{'ID Toko':str}}
    if fn.endswith('.csv'):            return pd.read_csv(f, **kw)
    if fn.endswith(('.xlsx','.xls')): return pd.read_excel(f, **kw)
    if fn.endswith('.parquet'):
        df = pd.read_parquet(f)
        if 'ID Toko' in df.columns: df['ID Toko'] = df['ID Toko'].astype(str)
        return df
    raise ValueError(f"Format tidak didukung: {f.name}")

def to_excel(sel_df, sum_df, trend_df=None):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as w:
        sel_df.to_excel(w, index=False, sheet_name='Toko Terpilih')
        sum_df.to_excel(w, index=False, sheet_name='Ringkasan Cluster')
        if trend_df is not None and not trend_df.empty:
            trend_df.to_excel(w, index=False, sheet_name='Tren Bulanan')
        pd.DataFrame({
            'Keterangan': ['Export','Toko Terpilih'],
            'Nilai': [datetime.now().strftime('%Y-%m-%d %H:%M'), len(sel_df)]
        }).to_excel(w, index=False, sheet_name='Metadata')
    return buf.getvalue()

# ──────────────────────────────────────────────────────────────────
# LANGKAH 1 — UPLOAD & PROSES
# ──────────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">📁 Langkah 1: Upload & Proses Data</div>',
            unsafe_allow_html=True)

uploaded_file = st.file_uploader("📤 Upload file transaksi",
                                  type=['csv','xlsx','xls','parquet'])

if uploaded_file:
    col1, col2 = st.columns([3,1])
    with col1:
        try:
            ck = uploaded_file.name
            if 'df_raw' not in st.session_state or st.session_state.get('_fn') != ck:
                st.session_state.df_raw = read_file(uploaded_file)
                st.session_state._fn = ck
            dr = st.session_state.df_raw
            st.markdown(
                f'<div class="info-box">📄 <b>{uploaded_file.name}</b> — '
                f'{dr.shape[0]:,} baris × {dr.shape[1]} kolom | '
                f'{uploaded_file.size/1024:.1f} KB<br>'
                f'🏷️ Brand dikategorikan otomatis (Main/Companion/Fighting) '
                f'berdasarkan Area AP & Provinsi.</div>',
                unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Gagal membaca file: {e}"); st.stop()

    with col2:
        st.write("👇 Klik untuk proses:")
        if st.button("⚙️ Proses Data", type="primary"):
            with st.spinner("Memproses..."):
                dr = st.session_state.df_raw
                req = ['Tanggal Transaksi','ID Toko','Nama Toko','Cluster Pareto',
                       'Area AP Toko','Provinsi Toko','Area Toko','Brands','TON Quantity']
                miss = [c for c in req if c not in dr.columns]
                if miss: st.error(f"Kolom hilang: {miss}"); st.stop()

                df = dr.copy()
                df['TON Quantity']       = df['TON Quantity'].fillna(0)
                df['Tanggal Transaksi']  = pd.to_datetime(df['Tanggal Transaksi'], errors='coerce')
                df.dropna(subset=['Tanggal Transaksi'], inplace=True)
                df.sort_values(['ID Toko','Tanggal Transaksi'], inplace=True)

                for c in ['Nama Toko','Cluster Pareto','Area AP Toko','Provinsi Toko','Area Toko','Brands']:
                    if c in df.columns:
                        df[c] = df.groupby('ID Toko')[c].transform(lambda x: x.ffill().bfill())
                df.dropna(subset=['Nama Toko','Cluster Pareto','Area AP Toko','Provinsi Toko','Area Toko'], inplace=True)

                # Filter Jawa-Bali
                n0 = len(df)
                df = df[~df['Provinsi Toko'].isin(JAWA_BALI)].copy()
                if n0 > len(df): st.info(f"ℹ️ {n0-len(df):,} baris Jawa-Bali difilter")

                if df.empty: st.warning("Tidak ada data valid."); st.stop()

                df['Bulan']          = df['Tanggal Transaksi'].dt.to_period('M').astype(str)
                # FIX 1: Brand category — vectorized lookup (bukan .apply row-by-row)
                # Buat lookup key: Area_upper + '|' + Prov_upper
                df['_au'] = df['Area AP Toko'].str.strip().str.upper()
                df['_pu'] = df['Provinsi Toko'].str.strip().str.upper()
                df['_bu'] = df['Brands'].str.strip().str.upper()

                def _cat_vec(au, pu, bu):
                    am = BRAND_MAP_BY_PROV.get(au)
                    if am is None: return 'Other'
                    pm = am.get(pu)
                    if pm is None:
                        for k in am:
                            if k in pu or pu in k: pm = am[k]; break
                    if pm is None:
                        fb = {'SP':{'main':['PADANG'],'companion':['DYNAMIX','ANDALAS','BATURAJA']},
                              'SMBR':{'main':['BATURAJA'],'companion':['DYNAMIX','PADANG']},
                              'ST':{'main':['TONASA'],'companion':['GRESIK']}}
                        pm = fb.get(au, {'main':[],'companion':[]})
                    if any(k in bu for k in pm['main']): return 'Main Brand'
                    if pm['companion'] and any(k in bu for k in pm['companion']): return 'Companion Brand'
                    if au == 'ST' and 'MERDEKA' in bu and pu in FB_PROVINCES: return 'Fighting Brand'
                    return 'Other'

                # Buat lookup key unik → hitung sekali per kombinasi, bukan per baris
                df['_key'] = df['_au'] + '|||' + df['_pu'] + '|||' + df['_bu']
                unique_keys = df[['_key','_au','_pu','_bu']].drop_duplicates('_key')
                unique_keys['Brand_Category'] = unique_keys.apply(
                    lambda r: _cat_vec(r['_au'], r['_pu'], r['_bu']), axis=1)
                df = df.merge(unique_keys[['_key','Brand_Category']], on='_key', how='left')

                # FIX 2: Reward — vectorized via map
                reward_map = {}
                for cl,br_map in REWARD_RATES.items():
                    for bc,rate in br_map.items():
                        reward_map[(cl,bc)] = rate
                df['Reward_per_Ton'] = [reward_map.get((cl,bc),0.0)
                                        for cl,bc in zip(df['Cluster Pareto'],df['Brand_Category'])]
                df.drop(columns=['_au','_pu','_bu','_key'], inplace=True)

                dv = df[df['Brand_Category']!='Other'].copy()
                if dv.empty: st.warning("Tidak ada transaksi brand valid."); st.stop()
                st.info(f"ℹ️ {len(df)-len(dv):,} transaksi brand kompetitor (Other) difilter")

                # Agregasi — konsisten 2 tahap
                grp = dv.groupby(
                    ['ID Toko','Nama Toko','Cluster Pareto','Area AP Toko','Provinsi Toko','Area Toko','Bulan']
                ).agg(Total_Ton=('TON Quantity','sum'),Jumlah_Trx=('Tanggal Transaksi','count')).reset_index()

                agg = grp.groupby(
                    ['ID Toko','Nama Toko','Cluster Pareto','Area AP Toko','Provinsi Toko','Area Toko']
                ).agg(Avg_Ton=('Total_Ton','mean'),Avg_Trx=('Jumlah_Trx','mean'),
                      Total_Bulan=('Bulan','nunique')).reset_index()

                n1 = len(agg)
                agg = agg[agg['Total_Bulan'] >= MIN_BULAN_AKTIF].copy()
                agg.reset_index(drop=True, inplace=True)
                st.info(f"ℹ️ {n1-len(agg):,} toko difilter (< {MIN_BULAN_AKTIF} bulan aktif)")

                # FIX 3: Ton_Growth — vectorized pivot (bukan loop per toko)
                tlast = grp['Bulan'].max()
                valid_ids = set(agg['ID Toko'])
                grp_f = grp[grp['ID Toko'].isin(valid_ids)].copy()

                # Ton bulan terakhir per toko
                last_df = (grp_f[grp_f['Bulan']==tlast]
                           .groupby('ID Toko')['Total_Ton'].sum().reset_index()
                           .rename(columns={'Total_Ton':'Last_Ton'}))
                # Mean bulan sebelumnya per toko
                prev_df = (grp_f[grp_f['Bulan']<tlast]
                           .groupby('ID Toko')['Total_Ton'].mean().reset_index()
                           .rename(columns={'Total_Ton':'Prev_Mean'}))

                growth_df = agg[['ID Toko']].merge(last_df, on='ID Toko', how='left')
                growth_df = growth_df.merge(prev_df, on='ID Toko', how='left')
                growth_df['Last_Ton']  = growth_df['Last_Ton'].fillna(0)
                growth_df['Prev_Mean'] = growth_df['Prev_Mean'].fillna(0)
                growth_df['Ton_Growth'] = np.where(
                    growth_df['Prev_Mean'] > 0,
                    (growth_df['Last_Ton'] - growth_df['Prev_Mean']) / growth_df['Prev_Mean'],
                    0.0)
                agg = agg.merge(growth_df[['ID Toko','Ton_Growth']], on='ID Toko', how='left')
                agg['Ton_Growth'] = agg['Ton_Growth'].fillna(0)

                # FIX 4: Ratio_vs_Cluster — vectorized map
                ca = agg.groupby('Cluster Pareto')['Avg_Ton'].mean().to_dict()
                agg['Ratio_vs_Cluster'] = agg['Avg_Ton'] / agg['Cluster Pareto'].map(ca).fillna(1.0)

                # Estimated_Cost brand-mix weighted — sudah vectorized
                tb = dv.groupby(['ID Toko','Brand_Category','Reward_per_Ton'])['TON Quantity'].sum().reset_index()
                tb = tb.merge(agg[['ID Toko','Total_Bulan']], on='ID Toko', how='left')
                tb['Cost_Brand'] = (tb['TON Quantity']/tb['Total_Bulan'].replace(0,1)) * tb['Reward_per_Ton']
                ct = (tb.groupby('ID Toko')['Cost_Brand'].sum().reset_index()
                      .rename(columns={'Cost_Brand':'Estimated_Cost'}))
                agg = agg.merge(ct, on='ID Toko', how='left')
                agg['Estimated_Cost'] = agg['Estimated_Cost'].fillna(0)

                W = compute_spearman_weights(agg)
                agg = compute_scores(agg, W['Ratio_vs_Cluster'], W['Avg_Trx'], W['Ton_Growth'])

                st.session_state.agg     = agg
                st.session_state.grouped = grp
                st.session_state.weights = W
                st.success(
                    f"✅ {agg.shape[0]:,} toko | "
                    f"w1={W['Ratio_vs_Cluster']:.3f} w2={W['Avg_Trx']:.3f} w3={W['Ton_Growth']:.3f}")

st.markdown("---")

# ──────────────────────────────────────────────────────────────────
# LANGKAH 2 — SIDEBAR: FILTER & PARAMETER
# ──────────────────────────────────────────────────────────────────
if 'agg' not in st.session_state:
    st.stop()

base = st.session_state.agg
W    = st.session_state.weights

with st.sidebar:
    st.markdown("## 🛠️ Panel Kontrol")
    st.markdown("---")

    # Filter Geografis
    st.markdown("### 📍 Filter Geografis")
    avail_ap = sorted(base['Area AP Toko'].unique())
    sel_ap   = st.multiselect("Area AP Toko", avail_ap, default=avail_ap)
    if not sel_ap: st.warning("Pilih minimal satu Area AP."); st.stop()

    a1 = base[base['Area AP Toko'].isin(sel_ap)].copy()
    avail_pv = sorted(a1['Provinsi Toko'].unique())
    sel_pv   = st.multiselect("Provinsi (opsional)", avail_pv, default=[])
    a2 = a1[a1['Provinsi Toko'].isin(sel_pv)].copy() if sel_pv else a1.copy()

    avail_at = sorted(a2['Area Toko'].unique())
    sel_at   = st.multiselect("Area Toko (opsional)", avail_at, default=[])
    agg      = a2[a2['Area Toko'].isin(sel_at)].copy() if sel_at else a2.copy()

    st.markdown("---")

    # Exclude
    st.markdown("### ❌ Kecualikan ID Toko")
    excl_str = st.text_area("ID Toko (satu per baris)", height=70)
    if excl_str:
        excl = [x.strip() for x in excl_str.splitlines() if x.strip()]
        agg  = agg[~agg['ID Toko'].astype(str).isin(excl)].copy()

    st.markdown("---")

    # Budget & N_max — langsung input, tidak ada toggle
    st.markdown("### 💰 Anggaran & Kuota")
    max_budget = st.number_input(
        "Budget Maks (Rp/bulan)", min_value=0,
        value=1_000_000_000, step=50_000_000,
        help="Total estimasi biaya reward untuk semua toko terpilih")
    N_max = st.number_input(
        "N_max (maks jumlah toko)", 1, max(1,len(agg)),
        value=min(500, len(agg)), step=10,
        help="Batas atas jumlah toko yang boleh dipilih ILP")

    st.markdown("---")

    # Bobot
    st.markdown("### ⚖️ Bobot Skor")
    use_sp = st.toggle("Spearman otomatis (recommended)", value=True)
    if use_sp:
        w1,w2,w3 = W['Ratio_vs_Cluster'], W['Avg_Trx'], W['Ton_Growth']
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

    # Cluster Mirror
    st.markdown("### 🎯 Batas Cluster")
    st.markdown('<div style="color:#aaa;font-size:11px;">% maks per cluster dari N_max. 0 = tanpa batas.</div>',
                unsafe_allow_html=True)
    pool_pcts = agg['Cluster Pareto'].value_counts(normalize=True).mul(100).round(1).to_dict()
    cl_pcts   = {}
    for c in sorted(agg['Cluster Pareto'].unique()):
        v = st.number_input(f"Maks {c} (%)", 0.0, 100.0,
                             value=round(pool_pcts.get(c,0.0),1),
                             step=1.0, key=f"cp_{c}",
                             help=f"Proporsi pool: {pool_pcts.get(c,0):.1f}%")
        cl_pcts[c] = v

    st.markdown("---")
    run_btn = st.button("▶️ Jalankan Optimasi ILP-A", type="primary", use_container_width=True)

# Status bar
st.markdown(
    f'<div class="info-box">🗂️ <b>{agg.shape[0]:,} toko</b> siap | '
    f'N_max = <b>{N_max:,}</b> · Budget = <b>Rp {max_budget:,.0f}</b></div>',
    unsafe_allow_html=True)

# What-If
with st.expander("🔮 What-If: Preview Distribusi Skor", expanded=False):
    c1,c2,c3 = st.columns(3)
    wr2 = c1.slider("Ratio (%)",0,100,int(w1*100),key="wi_r")
    wt2 = c2.slider("Trx (%)",  0,100,int(w2*100),key="wi_t")
    wg2 = c3.slider("Growth (%)",0,100,int(w3*100),key="wi_g")
    ws  = wr2+wt2+wg2
    if ws > 0:
        pv = compute_scores(agg, wr2/ws, wt2/ws, wg2/ws)
        m1,m2,m3 = st.columns(3)
        m1.metric("Max",  f"{pv['Score'].max():.4f}")
        m2.metric("Mean", f"{pv['Score'].mean():.4f}")
        m3.metric("Min",  f"{pv['Score'].min():.4f}")
        st.altair_chart(
            alt.Chart(pv).mark_bar(opacity=0.8)
            .encode(x=alt.X('Score:Q',bin=alt.Bin(maxbins=30)),
                    y='count()', color='Cluster Pareto:N')
            .properties(height=180), use_container_width=True)

st.markdown("---")

# ──────────────────────────────────────────────────────────────────
# OPTIMASI
# ──────────────────────────────────────────────────────────────────
if run_btn:
    af = agg.drop_duplicates(subset=['ID Toko']).copy()
    af.sort_values('Score', ascending=False, inplace=True, ignore_index=True)
    st.session_state.agg_scored  = af
    st.session_state.N_max_run   = N_max
    st.session_state.budget_run  = max_budget
    st.session_state.cl_pcts_run = {c:p for c,p in cl_pcts.items() if p>0}
    st.session_state.w_run       = (w1,w2,w3)

    with st.spinner("Menjalankan ILP-A Mirror..."):
        sel_ids = run_ilp(af, N_max, max_budget, st.session_state.cl_pcts_run or None)

    sel = af[af['ID Toko'].isin(sel_ids)].sort_values('Score', ascending=False, ignore_index=True)
    st.session_state.selected_df = sel
    st.success(f"✅ {len(sel):,} toko terpilih.")
    st.balloons()

if 'selected_df' not in st.session_state:
    st.stop()

sel_df     = st.session_state.selected_df
n_elig     = st.session_state.get('N_max_run',1)
bgt_run    = st.session_state.get('budget_run',1)
bgt_used   = sel_df['Estimated_Cost'].sum()
bgt_util   = bgt_used/bgt_run*100 if bgt_run else 0

# ──────────────────────────────────────────────────────────────────
# TABS
# ──────────────────────────────────────────────────────────────────
t1,t2,t3,t4,t5,t6 = st.tabs([
    "📊 Ringkasan","📈 Kontribusi","🔍 Perbandingan",
    "📅 Tren Bulanan","📋 Data & Export","🔬 Skenario"])

# ════ TAB 1 ════
with t1:
    st.markdown('<div class="section-header">✅ Ringkasan ILP-A Mirror</div>',
                unsafe_allow_html=True)
    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Toko Terpilih",  f"{len(sel_df):,}")
    m2.metric("Total Score",    f"{sel_df['Score'].sum():,.2f}")
    m3.metric("Est. Budget",    f"Rp {bgt_used:,.0f}")
    m4.metric("Utilisasi",      f"{bgt_util:.1f}%",
               "✅ OK" if bgt_util<=103 else "⚠️ Over")

    st.markdown("---")
    c1,c2 = st.columns(2)
    with c1:
        cs = sel_df['Cluster Pareto'].value_counts().reset_index()
        cs.columns = ['Cluster','Jumlah']
        cs['%']      = (cs['Jumlah']/len(sel_df)*100).round(1)
        cs['Budget'] = cs['Cluster'].map(
            sel_df.groupby('Cluster Pareto')['Estimated_Cost'].sum().to_dict())
        st.subheader("Komposisi Cluster")
        st.dataframe(cs.style.format({'%':'{:.1f}%','Budget':'Rp {:,.0f}'}),
                     use_container_width=True, hide_index=True)
    with c2:
        st.altair_chart(
            alt.Chart(cs).mark_arc(innerRadius=50)
            .encode(theta='Jumlah:Q', color='Cluster:N',
                    tooltip=['Cluster','Jumlah','%'])
            .properties(height=260, title="Distribusi Cluster"),
            use_container_width=True)

    g1,g2 = st.columns(2)
    with g1:
        pv = sel_df['Provinsi Toko'].value_counts().reset_index()
        pv.columns = ['Provinsi','N']
        st.altair_chart(
            alt.Chart(pv.head(15)).mark_bar()
            .encode(x='N:Q', y=alt.Y('Provinsi:N',sort='-x'),
                    color=alt.Color('N:Q',scale=alt.Scale(scheme='blues')))
            .properties(height=320, title="Per Provinsi"),
            use_container_width=True)
    with g2:
        av = sel_df['Area AP Toko'].value_counts().reset_index()
        av.columns = ['Area AP','N']
        st.altair_chart(
            alt.Chart(av).mark_bar(color='#FF6B6B')
            .encode(x='N:Q', y=alt.Y('Area AP:N',sort='-x'),tooltip=['Area AP','N'])
            .properties(height=320, title="Per Area AP"),
            use_container_width=True)

# ════ TAB 2 ════
with t2:
    st.markdown('<div class="section-header">📈 Kontribusi & Efisiensi</div>',
                unsafe_allow_html=True)
    d2 = sel_df.copy()
    ts = d2['Score'].sum(); tb2 = d2['Estimated_Cost'].sum()
    d2['Skor_%']   = d2['Score']/(ts+1e-9)*100
    d2['Budget_%'] = d2['Estimated_Cost']/(tb2+1e-9)*100
    d2['Efisiensi']= d2['Score']/(d2['Estimated_Cost']+1e-9)*1_000_000
    d2['Label']    = d2['ID Toko'].astype(str)+' — '+d2['Nama Toko']

    c1,c2 = st.columns(2)
    with c1:
        st.write("**Top 10 Kontributor Skor**")
        st.altair_chart(
            alt.Chart(d2.nlargest(10,'Skor_%')).mark_bar(color='#4CAF50')
            .encode(x='Skor_%:Q', y=alt.Y('Label:N',sort='-x'),
                    tooltip=['ID Toko','Nama Toko','Cluster Pareto','Skor_%']),
            use_container_width=True)
    with c2:
        st.write("**Top 10 Kontributor Budget**")
        st.altair_chart(
            alt.Chart(d2.nlargest(10,'Budget_%')).mark_bar(color='#FF9800')
            .encode(x='Budget_%:Q', y=alt.Y('Label:N',sort='-x'),
                    tooltip=['ID Toko','Nama Toko','Cluster Pareto','Budget_%']),
            use_container_width=True)

    st.altair_chart(
        alt.Chart(d2).mark_circle()
        .encode(x='Estimated_Cost:Q', y='Score:Q',
                color='Cluster Pareto:N', size=alt.Size('Avg_Ton:Q'),
                tooltip=['ID Toko','Nama Toko','Cluster Pareto','Score','Estimated_Cost','Efisiensi'])
        .interactive().properties(height=320, title="Scatter: Skor vs Biaya"),
        use_container_width=True)

    st.subheader("Top 20 Toko Paling Efisien")
    te = d2.nlargest(20,'Efisiensi')[['ID Toko','Nama Toko','Cluster Pareto',
                                       'Score','Estimated_Cost','Efisiensi']]
    st.dataframe(te.style.format({'Score':'{:.4f}','Estimated_Cost':'Rp {:,.0f}',
                                   'Efisiensi':'{:,.2f}'}),
                 use_container_width=True, hide_index=True)

# ════ TAB 3 ════
with t3:
    st.markdown('<div class="section-header">🔍 Perbandingan Toko</div>',
                unsafe_allow_html=True)
    opts   = (sel_df['ID Toko']+' — '+sel_df['Nama Toko']).tolist()
    chosen = st.multiselect("Pilih 2–4 toko:", opts,
                             default=opts[:min(3,len(opts))], max_selections=4)
    if chosen:
        ids_c  = [t.split(' — ')[0] for t in chosen]
        cdf    = sel_df[sel_df['ID Toko'].isin(ids_c)].copy()
        cols_c = st.columns(len(cdf))
        mets   = [('Score','Skor','{:.4f}'),('Avg_Ton','Avg Ton/Bulan','{:.2f}'),
                  ('Avg_Trx','Avg Trx/Bulan','{:.1f}'),('Ton_Growth','Growth','{:.2%}'),
                  ('Ratio_vs_Cluster','Ratio vs Cluster','{:.2f}x'),
                  ('Estimated_Cost','Est. Biaya','Rp {:,.0f}')]
        for col_ui,(_,row) in zip(cols_c, cdf.iterrows()):
            with col_ui:
                st.markdown(f"### 🏪 {row['Nama Toko']}")
                st.markdown(f"**ID:** {row['ID Toko']}  \n**Cluster:** {row['Cluster Pareto']}  \n"
                            f"**Provinsi:** {row['Provinsi Toko']}")
                st.markdown("---")
                for f,l,fmt in mets:
                    v = row.get(f,'N/A')
                    st.metric(l, fmt.format(v) if isinstance(v,(int,float)) else str(v))
        if 'grouped' in st.session_state:
            g  = st.session_state.grouped.copy()
            g['ID Toko'] = g['ID Toko'].astype(str)
            tc = g[g['ID Toko'].isin(ids_c)]
            if not tc.empty:
                st.altair_chart(
                    alt.Chart(tc).mark_line(point=True)
                    .encode(x=alt.X('Bulan:N',sort=None), y='Total_Ton:Q',
                            color='Nama Toko:N',
                            tooltip=['ID Toko','Nama Toko','Bulan','Total_Ton'])
                    .interactive().properties(height=280, title="Tren Tonase"),
                    use_container_width=True)

# ════ TAB 4 ════
with t4:
    st.markdown('<div class="section-header">📅 Tren Bulanan</div>',
                unsafe_allow_html=True)
    if 'grouped' in st.session_state:
        g  = st.session_state.grouped.copy()
        g['ID Toko'] = g['ID Toko'].astype(str)
        tr = g[g['ID Toko'].isin(sel_df['ID Toko'])]

        st.subheader("Tren Agregat")
        ag2 = tr.groupby('Bulan').agg(
            Total_Ton=('Total_Ton','sum'),N_Aktif=('ID Toko','nunique')).reset_index()
        st.altair_chart(
            alt.Chart(ag2).mark_line(point=True, color='#1976D2')
            .encode(x=alt.X('Bulan:N',sort=None), y='Total_Ton:Q',
                    tooltip=['Bulan','Total_Ton','N_Aktif'])
            .properties(height=240),
            use_container_width=True)

        st.subheader("Per Cluster")
        sc = sel_df[['ID Toko','Cluster Pareto']].drop_duplicates()
        tm = tr.drop(columns=['Cluster Pareto'],errors='ignore').merge(sc,on='ID Toko',how='left')
        tm = tm.dropna(subset=['Cluster Pareto'])
        ct = tm.groupby(['Bulan','Cluster Pareto'])['Total_Ton'].sum().reset_index()
        st.altair_chart(
            alt.Chart(ct).mark_line(point=True)
            .encode(x=alt.X('Bulan:N',sort=None), y='Total_Ton:Q',
                    color='Cluster Pareto:N',
                    tooltip=['Bulan','Cluster Pareto','Total_Ton'])
            .interactive().properties(height=280),
            use_container_width=True)

        st.subheader("Per Toko")
        t_opts = sel_df['Nama Toko'].unique().tolist()
        sel_t  = st.multiselect("Pilih toko (maks 10):", t_opts,
                                 default=t_opts[:5], max_selections=10)
        if sel_t:
            st.altair_chart(
                alt.Chart(tr[tr['Nama Toko'].isin(sel_t)]).mark_line(point=True)
                .encode(x=alt.X('Bulan:N',sort=None), y='Total_Ton:Q',
                        color='Nama Toko:N',
                        tooltip=['ID Toko','Nama Toko','Bulan','Total_Ton'])
                .interactive().properties(height=300),
                use_container_width=True)

# ════ TAB 5 ════
with t5:
    st.markdown('<div class="section-header">📋 Data & Export</div>',
                unsafe_allow_html=True)
    srch = st.text_input("🔎 Cari ID / Nama / Provinsi","")
    disp = sel_df.copy()
    if srch:
        msk = (disp['ID Toko'].str.contains(srch,case=False,na=False) |
               disp['Nama Toko'].str.contains(srch,case=False,na=False) |
               disp['Provinsi Toko'].str.contains(srch,case=False,na=False))
        disp = disp[msk]
        st.info(f"{len(disp):,} hasil")

    sc2  = ['ID Toko','Nama Toko','Cluster Pareto','Area AP Toko','Provinsi Toko',
            'Area Toko','Avg_Ton','Avg_Trx','Ton_Growth','Score','Estimated_Cost']
    av2  = [c for c in sc2 if c in disp.columns]
    st.dataframe(
        disp[av2].style.format(
            {k:v for k,v in {'Avg_Ton':'{:.2f}','Avg_Trx':'{:.1f}','Ton_Growth':'{:.2%}',
                              'Score':'{:.4f}','Estimated_Cost':'Rp {:,.0f}'}.items()
             if k in av2}),
        use_container_width=True, height=400, hide_index=True)

    st.markdown("---")
    cs3 = sel_df['Cluster Pareto'].value_counts().reset_index()
    cs3.columns = ['Cluster Pareto','Jumlah']
    tr_exp = None
    if 'grouped' in st.session_state:
        g2 = st.session_state.grouped.copy()
        g2['ID Toko'] = g2['ID Toko'].astype(str)
        tr_exp = g2[g2['ID Toko'].isin(sel_df['ID Toko'])]

    e1,e2,e3 = st.columns(3)
    with e1:
        st.download_button("📊 Excel",
            data=to_excel(disp[av2], cs3, tr_exp),
            file_name=f"loyalty_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            use_container_width=True)
    with e2:
        st.download_button("📄 CSV",
            data=sel_df[av2].to_csv(index=False).encode('utf-8-sig'),
            file_name=f"loyalty_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime='text/csv', use_container_width=True)
    with e3:
        buf = BytesIO()
        sel_df[av2].to_parquet(buf, index=False)
        st.download_button("🗜️ Parquet",
            data=buf.getvalue(),
            file_name=f"loyalty_{datetime.now().strftime('%Y%m%d_%H%M')}.parquet",
            mime='application/octet-stream', use_container_width=True)

# ════ TAB 6: SKENARIO ════
with t6:
    st.markdown('<div class="section-header">🔬 Skenario — Temukan Budget Optimal</div>',
                unsafe_allow_html=True)

    if 'agg_scored' not in st.session_state:
        st.warning("Jalankan Optimasi terlebih dahulu.")
    else:
        af2   = st.session_state.agg_scored
        bref  = st.session_state.get('budget_run', max_budget)
        nref  = st.session_state.get('N_max_run', N_max)
        clref = st.session_state.get('cl_pcts_run', {})

        st.markdown(
            f'<div class="info-box">📌 <b>Acuan:</b> N_max = {nref:,} | '
            f'Budget = Rp {bref:,.0f} | '
            f'Cluster: {"Mirror (" + str(len(clref)) + " cluster)" if clref else "Bebas"}<br>'
            f'N_max dan cluster constraint mengikuti setting sidebar. '
            f'Hanya budget yang divariasikan.</div>',
            unsafe_allow_html=True)

        pcts = st.multiselect(
            "Pilih variasi budget (% dari budget acuan di sidebar):",
            [40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 150],
            default=[60, 70, 80, 90, 100, 110, 120],
            help="100% = budget yang diset di sidebar")

        run_sc = st.button("▶️ Jalankan Semua Skenario",
                            type="primary", use_container_width=True,
                            disabled=not pcts)

        if run_sc:
            rows = []
            prog = st.progress(0)
            for i,bp in enumerate(sorted(pcts)):
                bgt = bref * bp / 100
                ids = run_ilp(af2, nref, bgt, clref or None)
                s   = af2[af2['ID Toko'].isin(ids)]
                ac  = s['Estimated_Cost'].sum()
                ts2 = s['Score'].sum()
                rows.append({
                    'Budget_%'       : bp,
                    'Budget_Ceiling' : round(bgt,0),
                    'Budget_Terpakai': round(ac,0),
                    'Budget_Util_%'  : round(ac/bgt*100,1) if bgt>0 else 0,
                    'N_Terpilih'     : len(ids),
                    'Total_Score'    : round(ts2,2),
                    'Avg_Score/Toko' : round(s['Score'].mean(),4) if len(s)>0 else 0,
                    'Avg_Ton/Toko'   : round(s['Avg_Ton'].mean(),2) if len(s)>0 else 0,
                    'Score/Juta_Rp'  : round(ts2/(ac/1e6),4) if ac>0 else 0,
                    'Feasible'       : '✅' if ac<=bgt*1.03 else '❌',
                })
                prog.progress((i+1)/len(pcts),
                               text=f"Budget {bp}%: {len(ids):,} toko")
            prog.empty()
            st.session_state.sc_df = pd.DataFrame(rows)

        if 'sc_df' in st.session_state:
            df6 = st.session_state.sc_df
            fe6 = df6[df6['Feasible']=='✅']

            if not fe6.empty:
                br  = fe6.loc[fe6['Total_Score'].idxmax()]
                efr = fe6.loc[fe6['Score/Juta_Rp'].idxmax()]
                cb1,cb2 = st.columns(2)
                cb1.markdown(
                    f'<div class="ok-box">🏆 <b>Skor Tertinggi: {br["Budget_%"]}%</b><br>'
                    f'Score={br["Total_Score"]:,.2f} | N={br["N_Terpilih"]:,} | '
                    f'AvgTon={br["Avg_Ton/Toko"]:.2f}</div>', unsafe_allow_html=True)
                cb2.markdown(
                    f'<div class="info-box">💰 <b>Paling Efisien: {efr["Budget_%"]}%</b><br>'
                    f'Score/Juta={efr["Score/Juta_Rp"]:.4f} | '
                    f'Util={efr["Budget_Util_%"]:.1f}%</div>', unsafe_allow_html=True)

                def hl6(row):
                    if row['Budget_%'] == br['Budget_%']: return ['background:#e8f5e9']*len(row)
                    if row['Feasible'] == '❌':           return ['background:#fff3e0']*len(row)
                    return ['']*len(row)
            else:
                def hl6(row): return ['']*len(row)

            st.dataframe(
                df6.style
                .format({'Budget_Ceiling':'Rp {:,.0f}','Budget_Terpakai':'Rp {:,.0f}',
                         'Budget_Util_%':'{:.1f}%','Total_Score':'{:,.2f}',
                         'Avg_Score/Toko':'{:.4f}','Avg_Ton/Toko':'{:.2f}',
                         'Score/Juta_Rp':'{:.4f}'})
                .apply(hl6, axis=1),
                use_container_width=True, hide_index=True)

            cv1,cv2 = st.columns(2)
            with cv1:
                st.altair_chart(
                    alt.Chart(df6).mark_line(point=True)
                    .encode(x=alt.X('Budget_%:O',title='Budget (%)'),
                            y=alt.Y('Total_Score:Q',title='Total Score'),
                            color=alt.Color('Feasible:N',
                                scale=alt.Scale(domain=['✅','❌'],range=['#4CAF50','#FF9800'])),
                            tooltip=['Budget_%','N_Terpilih','Total_Score','Budget_Util_%'])
                    .properties(height=260, title='Score vs Budget %'),
                    use_container_width=True)
            with cv2:
                st.altair_chart(
                    alt.Chart(fe6).mark_line(point=True, color='#1976D2')
                    .encode(x=alt.X('Budget_%:O',title='Budget (%)'),
                            y=alt.Y('Score/Juta_Rp:Q',title='Score / Juta Rp'),
                            tooltip=['Budget_%','Score/Juta_Rp','N_Terpilih'])
                    .properties(height=260, title='Efisiensi vs Budget %'),
                    use_container_width=True)

            st.altair_chart(
                alt.Chart(df6).mark_bar()
                .encode(x=alt.X('Budget_%:O',title='Budget (%)'),
                        y=alt.Y('N_Terpilih:Q',title='Toko Terpilih'),
                        color=alt.Color('Feasible:N',
                            scale=alt.Scale(domain=['✅','❌'],range=['#2196F3','#FF9800'])),
                        tooltip=['Budget_%','N_Terpilih','Total_Score'])
                .properties(height=220, title='N Toko Terpilih per Budget %'),
                use_container_width=True)

            st.markdown("""
            <div class="info-box">
            📖 <b>Membaca hasil:</b><br>
            • <b>Score naik</b> seiring budget naik — wajar, lebih banyak toko bisa masuk<br>
            • <b>Score/Juta Rp</b> — cari titik <i>diminishing return</i>: titik di mana
              tambah budget tidak lagi menambah efisiensi signifikan → itulah budget optimal<br>
            • <b>N_Terpilih = N_max stabil</b> meski budget naik → budget bukan constraint,
              N_max yang membatasi — pertimbangkan naikkan N_max di sidebar
            </div>""", unsafe_allow_html=True)

            st.download_button("📊 Download Skenario (CSV)",
                data=df6.to_csv(index=False).encode('utf-8-sig'),
                file_name=f"skenario_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime='text/csv', use_container_width=True)
