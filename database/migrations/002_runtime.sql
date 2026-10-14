-- Runtime metadata. Unknown classification is used for arbitrary ad-hoc input.
ALTER TABLE fincode.runs ADD COLUMN response_payload jsonb;
ALTER TABLE fincode.runs ADD COLUMN lease_expires_at timestamptz;
ALTER TABLE fincode.experiments ADD COLUMN submission_key text UNIQUE;
ALTER TABLE fincode.experiments ADD COLUMN submission_sha256 text;
CREATE INDEX queued_runs_idx ON fincode.runs(created_at) WHERE status = 'queued';
ALTER TABLE fincode.email_cases ADD COLUMN required_facts jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(required_facts) = 'array');
