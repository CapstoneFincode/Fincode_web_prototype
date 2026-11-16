-- Keep category IDs and historical scenario/run references intact.
ALTER TABLE fincode.attack_categories ADD COLUMN archived_at timestamptz;

UPDATE fincode.attack_categories
SET name='시스템 프롬포트 재정의', normalized_name='시스템 프롬포트 재정의'
WHERE id='role-spoofing';

-- Recoverable retirement: do not delete scenarios, attachments or run snapshots.
UPDATE fincode.attack_categories SET archived_at=now()
WHERE id IN ('uncategorized','attachment-injection');

-- New scenarios must explicitly select an active category.
ALTER TABLE fincode.attack_scenarios ALTER COLUMN category_id DROP DEFAULT;
