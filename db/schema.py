"""Database schema initialization, migration, and dimensional seed loader."""
import logging
from pathlib import Path
import pandas as pd

from config.settings import settings
from db.connection import get_db

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = settings.BASE_DIR / "db" / "migrations"


def apply_migrations():
    """Runs all SQL migration scripts in order."""
    db = get_db(read_only=False)
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    for mfile in migration_files:
        logger.info(f"Applying migration: {mfile.name}")
        sql_content = mfile.read_text(encoding="utf-8")
        db.execute_raw_sql(sql_content)

    logger.info("All migrations successfully applied.")


def seed_dimensions_and_quotas():
    """Seeds dimension tables and quarterly quotas from data/ directory."""
    db = get_db(read_only=False)
    data_dir = settings.DATA_DIR

    # 1. Reps Dimension
    reps_file = data_dir / "dimensions" / "reps.csv"
    if reps_file.exists():
        df_reps = pd.read_csv(reps_file)
        if db.is_postgres_available():
            # Seed PostgreSQL
            for _, row in df_reps.iterrows():
                db.execute_query(
                    """
                    INSERT INTO dim_rep (rep_id, territory_id, rep_name)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (rep_id) DO UPDATE SET
                        territory_id = EXCLUDED.territory_id,
                        rep_name = EXCLUDED.rep_name;
                    """,
                    (str(row["rep_id"]), str(row["territory_id"]), str(row["rep_name"]))
                )
        else:
            conn = db._get_duckdb_connection()
            conn.register("tmp_reps", df_reps)
            conn.execute("INSERT OR REPLACE INTO dim_rep SELECT rep_id, territory_id, rep_name, CURRENT_TIMESTAMP FROM tmp_reps;")
            conn.unregister("tmp_reps")
        logger.info(f"Seeded {len(df_reps)} sales reps.")

    # 2. Territories Dimension
    terr_file = data_dir / "dimensions" / "territories.csv"
    if terr_file.exists():
        df_terr = pd.read_csv(terr_file)
        if db.is_postgres_available():
            for _, row in df_terr.iterrows():
                db.execute_query(
                    """
                    INSERT INTO dim_territory (territory_id, state, rep_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (territory_id) DO UPDATE SET
                        state = EXCLUDED.state,
                        rep_id = EXCLUDED.rep_id;
                    """,
                    (str(row["territory_id"]), str(row["state"]), str(row["rep_id"]))
                )
        else:
            conn = db._get_duckdb_connection()
            conn.register("tmp_terr", df_terr)
            conn.execute("INSERT OR REPLACE INTO dim_territory SELECT territory_id, state, rep_id, CURRENT_TIMESTAMP FROM tmp_terr;")
            conn.unregister("tmp_terr")
        logger.info(f"Seeded {len(df_terr)} territories.")

    # 3. Products Dimension
    prod_file = data_dir / "dimensions" / "products.csv"
    if prod_file.exists():
        df_prod = pd.read_csv(prod_file)
        if db.is_postgres_available():
            for _, row in df_prod.iterrows():
                db.execute_query(
                    """
                    INSERT INTO dim_product (product_id, product_name, therapeutic_area, unit_price)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (product_id) DO UPDATE SET
                        product_name = EXCLUDED.product_name,
                        therapeutic_area = EXCLUDED.therapeutic_area,
                        unit_price = EXCLUDED.unit_price;
                    """,
                    (str(row["product_id"]), str(row["product_name"]), str(row["therapeutic_area"]), float(row["unit_price"]))
                )
        else:
            conn = db._get_duckdb_connection()
            conn.register("tmp_prod", df_prod)
            conn.execute("INSERT OR REPLACE INTO dim_product SELECT product_id, product_name, therapeutic_area, unit_price, CURRENT_TIMESTAMP FROM tmp_prod;")
            conn.unregister("tmp_prod")
        logger.info(f"Seeded {len(df_prod)} products.")

    # 4. HCP Dimension
    hcp_file = data_dir / "dimensions" / "hcps.csv"
    if hcp_file.exists():
        df_hcp = pd.read_csv(hcp_file)
        if db.is_postgres_available():
            import psycopg2.extras
            pg_conn = db._get_postgres_connection()
            with pg_conn.cursor() as cur:
                records = [
                    (
                        int(r["hcp_id"]),
                        str(r["first_name"]),
                        str(r["last_name"]),
                        str(r["specialty"]),
                        str(r["state"]),
                        str(r["zip5"]),
                        float(r["annual_claims"]),
                        str(r["territory_id"]),
                        str(r["segment"]),
                    )
                    for _, r in df_hcp.iterrows()
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO dim_hcp (hcp_id, first_name, last_name, specialty, state, zip5, annual_claims, territory_id, segment)
                    VALUES %s
                    ON CONFLICT (hcp_id) DO UPDATE SET
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        specialty = EXCLUDED.specialty,
                        state = EXCLUDED.state,
                        zip5 = EXCLUDED.zip5,
                        annual_claims = EXCLUDED.annual_claims,
                        territory_id = EXCLUDED.territory_id,
                        segment = EXCLUDED.segment;
                    """,
                    records,
                    page_size=1000
                )
                pg_conn.commit()
            pg_conn.close()
        else:
            conn = db._get_duckdb_connection()
            conn.register("tmp_hcp", df_hcp)
            conn.execute("""
                INSERT OR REPLACE INTO dim_hcp 
                SELECT hcp_id, first_name, last_name, specialty, state, zip5, annual_claims, territory_id, segment, CURRENT_TIMESTAMP 
                FROM tmp_hcp;
            """)
            conn.unregister("tmp_hcp")
        logger.info(f"Seeded {len(df_hcp)} HCPs.")

    # 5. Territory Quotas
    quota_file = data_dir / "quotas" / "territory_quotas.csv"
    if quota_file.exists():
        df_quota = pd.read_csv(quota_file)
        if db.is_postgres_available():
            import psycopg2.extras
            pg_conn = db._get_postgres_connection()
            with pg_conn.cursor() as cur:
                records = [
                    (
                        str(r["territory_id"]),
                        str(r["product_id"]),
                        str(r["quarter"]),
                        int(r["quota_units"]),
                    )
                    for _, r in df_quota.iterrows()
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO territory_quotas (territory_id, product_id, quarter, quota_units)
                    VALUES %s
                    ON CONFLICT (territory_id, product_id, quarter) DO UPDATE SET
                        quota_units = EXCLUDED.quota_units;
                    """,
                    records,
                    page_size=1000
                )
                pg_conn.commit()
            pg_conn.close()
        else:
            conn = db._get_duckdb_connection()
            conn.register("tmp_quota", df_quota)
            conn.execute("""
                INSERT OR REPLACE INTO territory_quotas 
                SELECT territory_id, product_id, quarter, quota_units, CURRENT_TIMESTAMP 
                FROM tmp_quota;
            """)
            conn.unregister("tmp_quota")
        logger.info(f"Seeded {len(df_quota)} quarterly territory quotas.")


def init_database():
    """Initializes schema migrations and seeds dimensional data."""
    apply_migrations()
    seed_dimensions_and_quotas()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_database()
