-- Smoke test for schema.sql. Run as a superuser against a fresh database after schema.sql.
-- Verifies: seeds shape, RLS isolation, posting immutability, balance check, off_report root rule,
-- money-unit trigger, partition routing. Exits non-zero on the first failure (psql -v ON_ERROR_STOP=1).

\set ON_ERROR_STOP on
BEGIN;

-- minimal reference rows (the real seed loader does this from seeds/*.json)
INSERT INTO units(code, measure, factor_to_base, decimals, symbol, is_system) VALUES
  ('VND','money',NULL,0,'₫',true), ('USD','money',NULL,2,'$',true),
  ('g','mass',1,3,'g',true), ('chi','mass',3.75,3,'chỉ',true), ('unit','count',1,0,'',true), ('m2','area',1,2,'m²',true)
ON CONFLICT DO NOTHING;

INSERT INTO plans(id, name, limits, is_default) VALUES ('free','Free','{"raw.item":{"month":500}}',true) ON CONFLICT DO NOTHING;

-- system tags: two roots + children
INSERT INTO tags(id, system_key, kind, is_custom) VALUES
  ('11111111-1111-1111-1111-111111111111','food','expense',false),
  ('22222222-2222-2222-2222-222222222222','off_report','off_report',false),
  ('33333333-3333-3333-3333-333333333333','salary','income',false);
INSERT INTO tags(id, system_key, root_id, is_custom, default_state) VALUES
  ('11111111-1111-1111-1111-111111111112','coffee_drinks','11111111-1111-1111-1111-111111111111',false,'on'),
  ('22222222-2222-2222-2222-222222222223','self_transfer','22222222-2222-2222-2222-222222222222',false,'on');

-- two users
INSERT INTO users(id, default_currency, locale) VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','VND','vi'),
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','USD','en');
INSERT INTO accounts(id, user_id, name, type, currency) VALUES
  ('a1a1a1a1-0000-0000-0000-000000000001','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','TCB main','bank','VND'),
  ('a1a1a1a1-0000-0000-0000-000000000002','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','Cash','cash','VND'),
  ('b1b1b1b1-0000-0000-0000-000000000001','bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','Chase','bank','USD');

-- 1. money-unit trigger: an account in 'g' must fail
DO $$ BEGIN
  BEGIN
    INSERT INTO accounts(user_id, name, type, currency) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','bad','cash','g');
    RAISE EXCEPTION 'expected failure did not happen: non-money currency accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;

-- 2. simple expense as user A: account leg + tag leg, balanced
SET LOCAL ROLE finassis_app;
SET LOCAL app.user_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
INSERT INTO transactions(id, user_id, occurred_at, description) VALUES
  ('c0ffee00-0000-0000-0000-000000000001','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', now(), 'Highlands cà phê');
INSERT INTO postings(user_id, transaction_id, account_id, amount, currency, occurred_at) VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','c0ffee00-0000-0000-0000-000000000001','a1a1a1a1-0000-0000-0000-000000000001',-45000,'VND',now());
INSERT INTO postings(user_id, transaction_id, tag_id, amount, currency, occurred_at, tag_source, tag_confidence) VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','c0ffee00-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111112',45000,'VND',now(),'keyword','high');

-- 3. RLS: user A sees own rows, none of B's
DO $$ DECLARE n int; BEGIN
  SELECT count(*) INTO n FROM accounts; IF n <> 2 THEN RAISE EXCEPTION 'RLS leak: A sees % accounts', n; END IF;
  SELECT count(*) INTO n FROM tags;     IF n <> 5 THEN RAISE EXCEPTION 'A should see 5 system tags, saw %', n; END IF;
END $$;

-- 4. off_report root cannot be used directly
DO $$ BEGIN
  BEGIN
    INSERT INTO postings(user_id, transaction_id, tag_id, amount, currency, occurred_at) VALUES
      ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','c0ffee00-0000-0000-0000-000000000001','22222222-2222-2222-2222-222222222222',0,'VND',now());
    RAISE EXCEPTION 'expected failure did not happen: off_report root accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;

-- 5. postings are immutable for the app role
DO $$ BEGIN
  BEGIN
    UPDATE postings SET amount = amount + 1 WHERE transaction_id = 'c0ffee00-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'expected failure did not happen: posting updated';
  EXCEPTION WHEN integrity_constraint_violation THEN NULL; END;
END $$;

-- 5b. but re-tagging a leg is allowed for the app role
UPDATE postings SET tag_id = '11111111-1111-1111-1111-111111111111', tag_source = 'caller', tag_confidence = 'exact'
 WHERE transaction_id = 'c0ffee00-0000-0000-0000-000000000001' AND account_id IS NULL;

-- 6. dirty_periods marked
DO $$ DECLARE n int; BEGIN
  SELECT count(*) INTO n FROM dirty_periods; IF n < 2 THEN RAISE EXCEPTION 'dirty_periods not marked (%)', n; END IF;
END $$;

-- 7. cross-tenant write is rejected
DO $$ BEGIN
  BEGIN
    INSERT INTO accounts(user_id, name, type, currency) VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','sneaky','cash','USD');
    RAISE EXCEPTION 'expected failure did not happen: cross-tenant insert accepted';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;

RESET ROLE;

-- 8. unbalanced transaction is rejected at commit (deferred trigger) — test in a savepoint
SAVEPOINT unbalanced;
INSERT INTO transactions(id, user_id, occurred_at) VALUES ('c0ffee00-0000-0000-0000-000000000002','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', now());
INSERT INTO postings(user_id, transaction_id, account_id, amount, currency, occurred_at) VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','c0ffee00-0000-0000-0000-000000000002','a1a1a1a1-0000-0000-0000-000000000001',-100,'VND',now());
DO $$ BEGIN
  BEGIN
    SET CONSTRAINTS ALL IMMEDIATE;
    RAISE EXCEPTION 'expected failure did not happen: unbalanced transaction accepted';
  EXCEPTION WHEN integrity_constraint_violation THEN NULL; END;
END $$;
ROLLBACK TO SAVEPOINT unbalanced;

-- 9. partition routing: the posting landed in a monthly partition, not the default
DO $$ DECLARE n int; BEGIN
  SELECT count(*) INTO n FROM postings_default; IF n <> 0 THEN RAISE EXCEPTION 'postings fell into default partition'; END IF;
END $$;

SELECT 'smoke ok' AS result;
ROLLBACK;
