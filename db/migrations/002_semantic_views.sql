-- Semantic Layer Views & Analytics Aggregations
-- Version: 1.0.0

-- ============================================================================
-- 1. Weekly Sales Performance View
-- ============================================================================
CREATE OR REPLACE VIEW v_weekly_sales_summary AS
SELECT
    f.week_start_date,
    EXTRACT(ISODOW FROM f.week_start_date) AS iso_dow,
    EXTRACT(WEEK FROM f.week_start_date) AS iso_week,
    EXTRACT(YEAR FROM f.week_start_date) AS sales_year,
    CONCAT(EXTRACT(YEAR FROM (f.week_start_date + INTERVAL '3 days')), 'Q', EXTRACT(QUARTER FROM (f.week_start_date + INTERVAL '3 days'))) AS sales_quarter,
    f.territory_id,
    t.state,
    r.rep_id,
    r.rep_name,
    f.product_id,
    p.product_name,
    p.therapeutic_area,
    p.unit_price,
    COUNT(DISTINCT f.hcp_id) AS active_prescribers,
    SUM(f.units) AS total_units,
    SUM(f.net_revenue) AS total_revenue,
    ROUND(AVG(f.net_revenue / NULLIF(f.units, 0)), 2) AS effective_avg_unit_price
FROM fact_weekly_sales f
JOIN dim_product p ON f.product_id = p.product_id
JOIN dim_territory t ON f.territory_id = t.territory_id
LEFT JOIN dim_rep r ON t.rep_id = r.rep_id
GROUP BY
    f.week_start_date,
    EXTRACT(ISODOW FROM f.week_start_date),
    EXTRACT(WEEK FROM f.week_start_date),
    EXTRACT(YEAR FROM f.week_start_date),
    CONCAT(EXTRACT(YEAR FROM (f.week_start_date + INTERVAL '3 days')), 'Q', EXTRACT(QUARTER FROM (f.week_start_date + INTERVAL '3 days'))),
    f.territory_id,
    t.state,
    r.rep_id,
    r.rep_name,
    f.product_id,
    p.product_name,
    p.therapeutic_area,
    p.unit_price;

COMMENT ON VIEW v_weekly_sales_summary IS 'Denormalized weekly sales volume and revenue by territory, product, therapeutic area, and sales representative.';

-- ============================================================================
-- 2. Territory Quota Attainment View
-- ============================================================================
CREATE OR REPLACE VIEW v_territory_quota_attainment AS
WITH quarterly_actuals AS (
    SELECT
        CONCAT(EXTRACT(YEAR FROM (f.week_start_date + INTERVAL '3 days')), 'Q', EXTRACT(QUARTER FROM (f.week_start_date + INTERVAL '3 days'))) AS quarter,
        f.territory_id,
        f.product_id,
        SUM(f.units) AS actual_units,
        SUM(f.net_revenue) AS actual_revenue
    FROM fact_weekly_sales f
    GROUP BY 1, 2, 3
)
SELECT
    q.quarter,
    q.territory_id,
    t.state,
    r.rep_id,
    r.rep_name,
    q.product_id,
    p.product_name,
    p.therapeutic_area,
    COALESCE(qa.actual_units, 0) AS actual_units,
    q.quota_units AS quota_units,
    ROUND((COALESCE(qa.actual_units, 0)::NUMERIC / NULLIF(q.quota_units, 0)) * 100, 2) AS quota_attainment_pct,
    (COALESCE(qa.actual_units, 0) - q.quota_units) AS variance_units,
    COALESCE(qa.actual_revenue, 0) AS actual_revenue,
    CASE
        WHEN COALESCE(qa.actual_units, 0) >= q.quota_units THEN 'Met/Exceeded'
        WHEN COALESCE(qa.actual_units, 0) >= q.quota_units * 0.9 THEN 'Near Target (90-99%)'
        ELSE 'Underperforming (<90%)'
    END AS performance_status
FROM territory_quotas q
LEFT JOIN quarterly_actuals qa
    ON q.quarter = qa.quarter
    AND q.territory_id = qa.territory_id
    AND q.product_id = qa.product_id
JOIN dim_product p ON q.product_id = p.product_id
JOIN dim_territory t ON q.territory_id = t.territory_id
LEFT JOIN dim_rep r ON t.rep_id = r.rep_id;

COMMENT ON VIEW v_territory_quota_attainment IS 'Quarterly quota attainment metrics per territory and product, calculating attainment percentage, variance to quota, and performance tiers.';

-- ============================================================================
-- 3. HCP Prescribing Trends View
-- ============================================================================
CREATE OR REPLACE VIEW v_hcp_prescribing_trends AS
SELECT
    h.hcp_id,
    h.first_name,
    h.last_name,
    CONCAT(h.first_name, ' ', h.last_name) AS full_name,
    h.specialty,
    h.state,
    h.segment,
    h.territory_id,
    f.product_id,
    p.product_name,
    p.therapeutic_area,
    COUNT(DISTINCT f.week_start_date) AS active_weeks,
    SUM(f.units) AS total_prescribed_units,
    SUM(f.net_revenue) AS total_prescribed_revenue,
    ROUND(AVG(f.units), 2) AS avg_weekly_units,
    MIN(f.week_start_date) AS first_prescription_week,
    MAX(f.week_start_date) AS latest_prescription_week
FROM dim_hcp h
JOIN fact_weekly_sales f ON h.hcp_id = f.hcp_id
JOIN dim_product p ON f.product_id = p.product_id
GROUP BY
    h.hcp_id,
    h.first_name,
    h.last_name,
    h.specialty,
    h.state,
    h.segment,
    h.territory_id,
    f.product_id,
    p.product_name,
    p.therapeutic_area;

COMMENT ON VIEW v_hcp_prescribing_trends IS 'Aggregate HCP prescribing history, specialty segmentation, product mix, and total commercial volume.';

-- ============================================================================
-- 4. Product Performance & Market Share View
-- ============================================================================
CREATE OR REPLACE VIEW v_product_performance AS
SELECT
    p.product_id,
    p.product_name,
    p.therapeutic_area,
    p.unit_price,
    COUNT(DISTINCT f.week_start_date) AS total_active_weeks,
    COUNT(DISTINCT f.hcp_id) AS total_prescribing_hcps,
    COUNT(DISTINCT f.territory_id) AS total_active_territories,
    SUM(f.units) AS total_units_sold,
    SUM(f.net_revenue) AS total_gross_revenue,
    ROUND(AVG(f.units), 2) AS avg_units_per_sale_record
FROM dim_product p
LEFT JOIN fact_weekly_sales f ON p.product_id = f.product_id
GROUP BY
    p.product_id,
    p.product_name,
    p.therapeutic_area,
    p.unit_price;

COMMENT ON VIEW v_product_performance IS 'High-level product catalog performance across all therapeutic areas, sales volume, and prescriber adoption.';
