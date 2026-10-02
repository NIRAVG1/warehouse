#!/usr/bin/env python3
"""
Synthetic weekly pharma sales generator for the sales pipeline + SQL agent project.

Dimensions (HCPs) come from the CMS Medicare Part D "Prescribers by Provider" file
if you pass --cms-file; otherwise fully synthetic HCPs are generated so the script
runs out of the box. Weekly sales are always simulated.

Outputs (under --out, default ./data):
  dimensions/   hcps.csv, products.csv, territories.csv, reps.csv
  quotas/       territory_quotas.csv
  incoming/     sales_<ISOYEAR>_w<NN>.csv   (one file per week, some deliberately bad)
  ground_truth/ anomalies.csv, dq_issues.csv   (answer keys for your eval set and QA checks)

Usage:
  pip install numpy pandas faker
  python generate_data.py                                  # fully synthetic
  python generate_data.py --cms-file MUP_DPR_...csv        # real HCPs from CMS
  python generate_data.py --weeks 52 --hcps 5000 --seed 42 --no-bad-files
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from faker import Faker
except ImportError:
    Faker = None

# ---------------------------------------------------------------- reference data
# Fictional brand names, so nothing here claims to be a real product's sales.
PRODUCTS = [
    # id, name, therapeutic area, unit price, seasonal amplitude, peak ISO week, launch week
    ("CARDIVEX", "Cardivex", "Cardiovascular", 42.0, 0.05, 4, None),
    ("GLUCORA", "Glucora", "Diabetes", 58.0, 0.03, 50, None),
    ("RESPIRA", "Respira", "Respiratory", 75.0, 0.25, 4, None),
    ("NEUROLIN", "Neurolin", "Neurology", 120.0, 0.04, 30, None),
    ("ONCORA", "Oncora", "Oncology", 480.0, 0.02, 20, None),
    ("DERMACLEAR", "Dermaclear", "Dermatology", 95.0, 0.10, 28, 20),
]
PROD = {p[0]: p for p in PRODUCTS}

SPECIALTY_PRODUCTS = {
    "Cardiology": ["CARDIVEX", "GLUCORA"],
    "Endocrinology": ["GLUCORA"],
    "Pulmonary Disease": ["RESPIRA"],
    "Neurology": ["NEUROLIN"],
    "Hematology/Oncology": ["ONCORA"],
    "Medical Oncology": ["ONCORA"],
    "Dermatology": ["DERMACLEAR"],
}
DEFAULT_PRODUCTS = ["CARDIVEX", "GLUCORA", "RESPIRA"]  # primary care style prescribers

KEEP_SPECIALTIES = [
    "Internal Medicine", "Family Practice", "General Practice", "Cardiology",
    "Endocrinology", "Pulmonary Disease", "Neurology", "Hematology/Oncology",
    "Medical Oncology", "Dermatology", "Nurse Practitioner", "Physician Assistant",
]
SYNTH_SPECIALTY_WEIGHTS = [0.22, 0.20, 0.03, 0.10, 0.05, 0.05, 0.06, 0.04, 0.02, 0.05, 0.12, 0.06]
SYNTH_STATES = ["CA", "TX", "FL", "NY", "PA", "IL", "OH", "GA", "NC", "MI", "NJ", "VA"]


# ---------------------------------------------------------------- HCP dimension
def synth_hcps(n, rng):
    fake = Faker("en_US") if Faker else None
    if fake:
        Faker.seed(int(rng.integers(1e9)))
    first = [fake.first_name() if fake else f"First{i}" for i in range(n)]
    last = [fake.last_name() if fake else f"Last{i}" for i in range(n)]
    return pd.DataFrame({
        "hcp_id": 1_000_000_000 + np.arange(n),
        "first_name": first,
        "last_name": last,
        "specialty": rng.choice(KEEP_SPECIALTIES, size=n, p=np.array(SYNTH_SPECIALTY_WEIGHTS) / sum(SYNTH_SPECIALTY_WEIGHTS)),
        "state": rng.choice(SYNTH_STATES, size=n),
        "zip5": rng.integers(10000, 99999, size=n).astype(str),
        "annual_claims": np.clip(rng.lognormal(5.0, 1.0, size=n), 11, 6000).round(),
    })


def load_cms(path, n, rng):
    """Read the CMS Part D Prescribers by Provider CSV (column names per CMS data dictionary)."""
    cols = {
        "Prscrbr_NPI": "hcp_id", "Prscrbr_Last_Org_Name": "last_name",
        "Prscrbr_First_Name": "first_name", "Prscrbr_Type": "specialty",
        "Prscrbr_State_Abrvtn": "state", "Prscrbr_zip5": "zip5", "Tot_Clms": "annual_claims",
    }
    df = pd.read_csv(path, usecols=lambda c: c in cols, dtype={"Prscrbr_zip5": str})
    missing = set(cols) - set(df.columns)
    if missing:
        raise SystemExit(f"CMS file is missing expected columns {sorted(missing)}. "
                         "Check the column names against the CMS data dictionary.")
    df = df.rename(columns=cols).dropna()
    df = df[df["specialty"].isin(KEEP_SPECIALTIES) & (df["annual_claims"] > 0)]
    df = df.sample(n=min(n, len(df)), random_state=int(rng.integers(1e9))).reset_index(drop=True)
    df["hcp_id"] = df["hcp_id"].astype("int64")
    return df


def build_territories(hcps, n_states, per_state=3):
    top = hcps["state"].value_counts().head(n_states).index
    hcps = hcps[hcps["state"].isin(top)].copy()
    zip3 = pd.to_numeric(hcps["zip5"].astype(str).str[:3], errors="coerce").fillna(0).astype(int)
    hcps["territory_id"] = hcps["state"] + "-T" + ((zip3 % per_state) + 1).astype(str)
    pct = hcps["annual_claims"].rank(pct=True, ascending=False)
    hcps["segment"] = np.where(pct <= 0.2, "A", np.where(pct <= 0.5, "B", "C"))
    return hcps.reset_index(drop=True)


def assign_products(hcps, rng):
    rows = []
    for r in hcps.itertuples():
        prods = SPECIALTY_PRODUCTS.get(r.specialty)
        if prods is None:
            k = int(rng.integers(1, 3))
            prods = list(rng.choice(DEFAULT_PRODUCTS, size=k, replace=False))
        rows.extend((r.hcp_id, p) for p in prods)
    return pd.DataFrame(rows, columns=["hcp_id", "product_id"])


# ---------------------------------------------------------------- main generator
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--hcps", type=int, default=5000)
    ap.add_argument("--states", type=int, default=12, help="number of top states to keep (3 territories each)")
    ap.add_argument("--start", default="2024-12-30", help="first Monday (default = ISO week 1 of 2025)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cms-file", default=None)
    ap.add_argument("--no-bad-files", action="store_true", help="skip deliberate data quality problems")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    out = Path(args.out)
    for sub in ("dimensions", "quotas", "incoming", "ground_truth"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    hcps = load_cms(args.cms_file, args.hcps, rng) if args.cms_file else synth_hcps(args.hcps, rng)
    hcps = build_territories(hcps, args.states)

    territories = sorted(hcps["territory_id"].unique())
    reps = pd.DataFrame({"rep_id": [f"R{i + 1:03d}" for i in range(len(territories))], "territory_id": territories})
    fake = Faker("en_US") if Faker else None
    reps["rep_name"] = [fake.name() if fake else f"Rep {i + 1}" for i in range(len(reps))]
    terr_dim = hcps.groupby("territory_id")["state"].first().reset_index().merge(reps[["territory_id", "rep_id"]])

    products = pd.DataFrame(PRODUCTS, columns=["product_id", "product_name", "therapeutic_area",
                                               "unit_price", "season_amp", "season_peak_week", "launch_week"])
    hcps.to_csv(out / "dimensions/hcps.csv", index=False)
    products[["product_id", "product_name", "therapeutic_area", "unit_price"]].to_csv(out / "dimensions/products.csv", index=False)
    terr_dim.to_csv(out / "dimensions/territories.csv", index=False)
    reps.to_csv(out / "dimensions/reps.csv", index=False)

    # HCP x product pairs with a baseline weekly volume (avg ~3 units/week)
    pairs = assign_products(hcps, rng).merge(hcps[["hcp_id", "territory_id", "state", "annual_claims"]], on="hcp_id")
    n_prod = pairs.groupby("hcp_id")["product_id"].transform("size")
    terr_mult = {t: rng.uniform(0.85, 1.15) for t in territories}
    pairs["base"] = pairs["annual_claims"] / n_prod
    pairs["base"] = pairs["base"] / pairs["base"].mean() * 3.0 * pairs["territory_id"].map(terr_mult)

    # Anomaly targets
    drop_terr = territories[int(rng.integers(len(territories)))]
    spike_terr = territories[int(rng.integers(len(territories)))]
    stock_state = hcps["state"].value_counts().index[0]

    anomalies, dq_log, baseline_parts = [], [], []
    amp = pairs["product_id"].map({p[0]: p[4] for p in PRODUCTS}).to_numpy()
    peak = pairs["product_id"].map({p[0]: p[5] for p in PRODUCTS}).to_numpy()
    price = pairs["product_id"].map({p[0]: p[3] for p in PRODUCTS}).to_numpy()
    launch = pairs["product_id"].map({p[0]: (p[6] or 0) for p in PRODUCTS}).to_numpy()
    terr = pairs["territory_id"].to_numpy()
    prod = pairs["product_id"].to_numpy()
    state = pairs["state"].to_numpy()
    base = pairs["base"].to_numpy()

    start = pd.Timestamp(args.start)
    for w in range(1, args.weeks + 1):
        ws = start + pd.Timedelta(weeks=w - 1)
        iso = ws.isocalendar()
        fname = f"sales_{iso.year}_w{iso.week:02d}.csv"

        season = 1 + amp * np.cos(2 * np.pi * (iso.week - peak) / 52)
        trend = 1 + 0.002 * (w - 1)
        ramp = np.where(launch == 0, 1.0, np.where(w < launch, 0.0, np.minimum(1.0, (w - launch + 1) / 12)))
        mu_base = base * season * trend * ramp
        mult = np.ones(len(pairs))

        if w == 20:
            anomalies.append((w, ws.date(), "product_launch", "DERMACLEAR", "launch with 12-week ramp to full volume"))
        if w in (30, 31):
            mult[terr == drop_terr] *= 0.6
            anomalies.append((w, ws.date(), "territory_drop", drop_terr, "units about 40% below baseline"))
        if w == 26:
            mult[(terr == spike_terr) & (prod == "GLUCORA")] *= 1.8
            anomalies.append((w, ws.date(), "product_spike", f"{spike_terr}/GLUCORA", "units about 80% above baseline for one week"))
        if w == 40:
            mult[(state == stock_state) & (prod == "RESPIRA")] = 0
            anomalies.append((w, ws.date(), "stock_out", f"{stock_state}/RESPIRA", "zero units for one week"))

        units = rng.poisson(mu_base * mult)
        rev = (units * price * rng.uniform(0.85, 0.95, len(units))).round(2)
        df = pd.DataFrame({
            "week_start_date": ws.date(), "hcp_id": pairs["hcp_id"].to_numpy(),
            "product_id": prod, "territory_id": terr, "units": units, "net_revenue": rev,
        })
        df = df[df["units"] > 0].reset_index(drop=True)
        df["hcp_id"] = df["hcp_id"].astype("Int64")

        q = f"{(ws + pd.Timedelta(days=3)).year}Q{(ws + pd.Timedelta(days=3)).quarter}"
        part = pd.DataFrame({"territory_id": terr, "product_id": prod, "mu": mu_base}).groupby(["territory_id", "product_id"], as_index=False)["mu"].sum()
        part["quarter"] = q
        baseline_parts.append(part)

        if not args.no_bad_files:
            seed = int(rng.integers(1e9))
            if w == 10:
                df = pd.concat([df, df.sample(frac=0.02, random_state=seed)])
                dq_log.append((w, fname, "duplicate_rows", "about 2% of rows duplicated", "critical"))
            elif w == 15:
                df = df.drop(columns=["net_revenue"])
                dq_log.append((w, fname, "missing_column", "net_revenue column absent", "critical"))
            elif w == 22:
                idx = df.sample(frac=0.01, random_state=seed).index
                df.loc[idx, "hcp_id"] = pd.NA
                dq_log.append((w, fname, "null_keys", "about 1% of hcp_id values null", "critical"))
            elif w == 33:
                idx = df.sample(frac=0.005, random_state=seed).index
                df.loc[idx, "territory_id"] = "ZZ-T9"
                dq_log.append((w, fname, "unknown_territory", "about 0.5% rows with territory ZZ-T9", "critical"))
            elif w == 45:
                df = df.sample(frac=0.5, random_state=seed)
                dq_log.append((w, fname, "truncated_file", "row count about 50% of normal", "critical"))
            elif w == 48:
                idx = df.sample(frac=0.005, random_state=seed).index
                df.loc[idx, "units"] = -df.loc[idx, "units"]
                dq_log.append((w, fname, "negative_units", "about 0.5% rows with negative units", "warning"))

        df.to_csv(out / "incoming" / fname, index=False)

    # Quarterly quotas: expected baseline volume x 1.05
    q_df = pd.concat(baseline_parts).groupby(["territory_id", "product_id", "quarter"], as_index=False)["mu"].sum()
    q_df["quota_units"] = (q_df.pop("mu") * 1.05).round().astype(int)
    q_df.to_csv(out / "quotas/territory_quotas.csv", index=False)

    pd.DataFrame(anomalies, columns=["week", "week_start_date", "type", "entity", "detail"]).to_csv(out / "ground_truth/anomalies.csv", index=False)
    pd.DataFrame(dq_log, columns=["week", "file", "issue_type", "detail", "severity"]).to_csv(out / "ground_truth/dq_issues.csv", index=False)

    print(f"HCPs: {len(hcps):,} | territories: {len(territories)} | HCP-product pairs: {len(pairs):,}")
    print(f"Wrote {args.weeks} weekly files to {out / 'incoming'}")
    print(f"Anomaly targets: drop={drop_terr}, spike={spike_terr}, stock-out state={stock_state}")


if __name__ == "__main__":
    main()
