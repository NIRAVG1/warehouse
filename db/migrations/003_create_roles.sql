-- Role-Based Access Control (RBAC) Setup
-- Version: 1.0.0

-- Create read-only role for the Text-to-SQL semantic agent
DO
$do$
BEGIN
   IF NOT EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE  rolname = 'pharma_analyst_ro') THEN

      CREATE ROLE pharma_analyst_ro WITH LOGIN PASSWORD 'ro_secure_analyst_2025';
   END IF;
END
$do$;

-- Revoke all default table permissions on public schema
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM pharma_analyst_ro;
REVOKE ALL ON SCHEMA public FROM pharma_analyst_ro;

-- Grant USAGE on public schema
GRANT USAGE ON SCHEMA public TO pharma_analyst_ro;

-- Grant SELECT only on semantic views
GRANT SELECT ON v_weekly_sales_summary TO pharma_analyst_ro;
GRANT SELECT ON v_territory_quota_attainment TO pharma_analyst_ro;
GRANT SELECT ON v_hcp_prescribing_trends TO pharma_analyst_ro;
GRANT SELECT ON v_product_performance TO pharma_analyst_ro;

-- Prevent direct table access to staging and write tables
REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON stg_weekly_sales FROM pharma_analyst_ro;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON fact_weekly_sales FROM pharma_analyst_ro;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON pipeline_runs FROM pharma_analyst_ro;

-- Ensure read-only users cannot create or alter objects
REVOKE CREATE ON SCHEMA public FROM pharma_analyst_ro;
