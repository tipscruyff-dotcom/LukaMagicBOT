-- Fix plan_type to allow NULL values temporarily
-- The actual plan_type comes from invoice.paid event after checkout.session.completed
-- This fixes the PostgreSQL constraint issue

BEGIN;

-- Remove NOT NULL constraint from plan_type
ALTER TABLE subscriptions ALTER COLUMN plan_type DROP NOT NULL;

-- Optional: Set default to 'pending' for safety
ALTER TABLE subscriptions ALTER COLUMN plan_type SET DEFAULT 'pending';

-- Update any existing NULL values to 'pending' (if any)
UPDATE subscriptions SET plan_type = 'pending' WHERE plan_type IS NULL;

COMMIT;

-- To apply this:
-- psql postgresql://USER:PASSWORD@HOST:PORT/DATABASE -f fix_plan_type_nullable.sql

