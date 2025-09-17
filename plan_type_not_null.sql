BEGIN;
UPDATE subscriptions SET plan_type='unknown' WHERE plan_type IS NULL;
ALTER TABLE subscriptions ALTER COLUMN plan_type SET DEFAULT 'unknown';
ALTER TABLE subscriptions ALTER COLUMN plan_type SET NOT NULL;
COMMIT;
