-- Pharma Commercial Data Warehouse Schema Initialization
-- Version: 1.0.0

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 1. DIMENSION TABLES
-- ============================================================================

-- Reps Dimension
CREATE TABLE IF NOT EXISTS dim_rep (
    rep_id VARCHAR(20) PRIMARY KEY,
    territory_id VARCHAR(20) NOT NULL,
    rep_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Territories Dimension
CREATE TABLE IF NOT EXISTS dim_territory (
    territory_id VARCHAR(20) PRIMARY KEY,
    state VARCHAR(10) NOT NULL,
    rep_id VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Products Dimension
CREATE TABLE IF NOT EXISTS dim_product (
    product_id VARCHAR(50) PRIMARY KEY,
    product_name VARCHAR(100) NOT NULL,
    therapeutic_area VARCHAR(100) NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Healthcare Professionals (HCP) Dimension
CREATE TABLE IF NOT EXISTS dim_hcp (
    hcp_id BIGINT PRIMARY KEY,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    specialty VARCHAR(100) NOT NULL,
    state VARCHAR(10) NOT NULL,
    zip5 VARCHAR(10) NOT NULL,
    annual_claims DOUBLE PRECISION NOT NULL,
    territory_id VARCHAR(20) NOT NULL,
    segment VARCHAR(5) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Territory Quotas Table (Quarterly targets)
CREATE TABLE IF NOT EXISTS territory_quotas (
    territory_id VARCHAR(20) NOT NULL,
    product_id VARCHAR(50) NOT NULL,
    quarter VARCHAR(10) NOT NULL,
    quota_units BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (territory_id, product_id, quarter)
);

-- ============================================================================
-- 2. STAGING & FACT TABLES
-- ============================================================================

-- Staging Table for Ingestion Batches
CREATE TABLE IF NOT EXISTS stg_weekly_sales (
    week_start_date DATE,
    hcp_id BIGINT,
    product_id VARCHAR(50),
    territory_id VARCHAR(20),
    units BIGINT,
    net_revenue NUMERIC(12, 2),
    batch_id VARCHAR(100),
    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Fact Weekly Sales Table (Core Warehouse Fact)
CREATE TABLE IF NOT EXISTS fact_weekly_sales (
    week_start_date DATE NOT NULL,
    hcp_id BIGINT NOT NULL,
    product_id VARCHAR(50) NOT NULL,
    territory_id VARCHAR(20) NOT NULL,
    units BIGINT NOT NULL,
    net_revenue NUMERIC(12, 2) NOT NULL,
    loaded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (week_start_date, hcp_id, product_id, territory_id)
);

-- ============================================================================
-- 3. PIPELINE RUNS & AUDIT LOGGING
-- ============================================================================

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id VARCHAR(100) PRIMARY KEY,
    run_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    file_name VARCHAR(255) NOT NULL,
    week_number INT,
    year INT,
    status VARCHAR(50) NOT NULL, -- 'SUCCESS', 'CRITICAL_FAILURE', 'LOADED_WITH_WARNINGS'
    rows_staged INT DEFAULT 0,
    rows_loaded INT DEFAULT 0,
    dq_critical_count INT DEFAULT 0,
    dq_warning_count INT DEFAULT 0,
    execution_time_ms DOUBLE PRECISION DEFAULT 0.0,
    logs JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 4. PERFORMANCE INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_fact_sales_date ON fact_weekly_sales (week_start_date);
CREATE INDEX IF NOT EXISTS idx_fact_sales_product ON fact_weekly_sales (product_id);
CREATE INDEX IF NOT EXISTS idx_fact_sales_territory ON fact_weekly_sales (territory_id);
CREATE INDEX IF NOT EXISTS idx_fact_sales_hcp ON fact_weekly_sales (hcp_id);
CREATE INDEX IF NOT EXISTS idx_hcp_territory ON dim_hcp (territory_id);
CREATE INDEX IF NOT EXISTS idx_hcp_specialty ON dim_hcp (specialty);
CREATE INDEX IF NOT EXISTS idx_quotas_lookup ON territory_quotas (territory_id, quarter);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_ts ON pipeline_runs (run_timestamp DESC);
