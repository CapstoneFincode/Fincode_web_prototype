-- Retire only the seven original UI attack examples. Archive instead of
-- deleting so existing experiment cases, snapshots and results remain valid.
-- User-created copies and normal-email examples are intentionally untouched.
SET search_path TO fincode, public;

UPDATE attack_scenarios
SET archived_at = now()
WHERE provenance = 'sample'
  AND scenario_key IN ('ATK-S01', 'ATK-S02', 'ATK-S03', 'ATK-S04',
                       'ATK-S05', 'ATK-S06', 'ATK-S07')
  AND archived_at IS NULL;
