-- Uploads may precede a saved email or scenario. Keep the original versioned
-- attachment table and all existing rows; a successful extraction is immutable.
ALTER TABLE fincode.attachments ALTER COLUMN email_case_id DROP NOT NULL;
ALTER TABLE fincode.attachments ADD COLUMN storage_backend text NOT NULL DEFAULT 'legacy'
    CHECK (storage_backend IN ('legacy', 'local', 's3'));
ALTER TABLE fincode.attachments ADD COLUMN extraction_metadata jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(extraction_metadata) = 'object');

CREATE TABLE fincode.scenario_attachments (
    scenario_id uuid NOT NULL REFERENCES fincode.attack_scenarios(id),
    attachment_id uuid NOT NULL REFERENCES fincode.attachments(id),
    position integer NOT NULL CHECK (position BETWEEN 0 AND 4),
    PRIMARY KEY (scenario_id, attachment_id),
    UNIQUE (scenario_id, position)
);
CREATE INDEX scenario_attachments_attachment_idx ON fincode.scenario_attachments(attachment_id);
