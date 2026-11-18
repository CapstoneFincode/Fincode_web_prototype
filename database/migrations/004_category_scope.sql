-- Category scope describes where an empty category is displayed; scenario vectors
-- remain authoritative. Existing scenarios, revisions and run snapshots are untouched.
ALTER TABLE fincode.attack_categories ADD COLUMN scope text NOT NULL DEFAULT 'both'
    CHECK (scope IN ('direct', 'indirect', 'both'));
ALTER TABLE fincode.attack_categories ADD CONSTRAINT attack_category_uncategorized_scope
    CHECK (id <> 'uncategorized' OR scope = 'both');

-- Only the built-in attachment category has a single-path meaning. Other attack
-- techniques can be delivered through either path, including currently empty ones.
UPDATE fincode.attack_categories SET scope = 'indirect'
WHERE id = 'attachment-injection' AND created_by = 'seed';
