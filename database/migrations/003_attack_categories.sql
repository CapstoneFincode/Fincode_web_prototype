-- Add a category catalog without changing existing attack revisions or runs.
-- API names are NFKC-normalized, whitespace-folded and casefolded before insert.
CREATE TABLE fincode.attack_categories (
    id text PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 80),
    name varchar(60) NOT NULL CHECK (length(btrim(name)) > 0),
    normalized_name text NOT NULL UNIQUE CHECK (length(normalized_name) > 0),
    sort_order integer NOT NULL DEFAULT 1000,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX attack_category_name_ci ON fincode.attack_categories(lower(name));

INSERT INTO fincode.attack_categories (id,name,normalized_name,sort_order,created_by) VALUES
    ('system-prompt-leak','시스템 프롬프트 유출','시스템 프롬프트 유출',10,'seed'),
    ('instruction-override','지시 덮어쓰기','지시 덮어쓰기',20,'seed'),
    ('role-spoofing','역할·권한 사칭','역할·권한 사칭',30,'seed'),
    ('special-token','특수 토큰 인젝션','특수 토큰 인젝션',40,'seed'),
    ('hidden-text','비가시 문자 은닉','비가시 문자 은닉',50,'seed'),
    ('attachment-injection','첨부 콘텐츠 주입','첨부 콘텐츠 주입',60,'seed'),
    ('uncategorized','미분류','미분류',9999,'seed');

ALTER TABLE fincode.attack_scenarios ADD COLUMN category_id text NOT NULL
    DEFAULT 'uncategorized' REFERENCES fincode.attack_categories(id);
CREATE INDEX attack_scenario_category_idx ON fincode.attack_scenarios(category_id);

-- Only known synthetic samples have a reviewed mapping. Team data stays unclassified.
UPDATE fincode.attack_scenarios SET category_id = CASE
    WHEN scenario_key IN ('ATK-S01','ATK-S03') THEN 'instruction-override'
    WHEN scenario_key IN ('ATK-S02','ATK-S04') THEN 'role-spoofing'
    WHEN scenario_key IN ('ATK-S05','ATK-S06') THEN 'attachment-injection'
    WHEN scenario_key = 'ATK-S07' THEN 'special-token'
    ELSE 'uncategorized' END
WHERE provenance = 'sample' AND scenario_key IN
    ('ATK-S01','ATK-S02','ATK-S03','ATK-S04','ATK-S05','ATK-S06','ATK-S07');
