-- Runtime scheduling metadata is separate from the frozen execution plan.
ALTER TABLE fincode.experiments ADD COLUMN last_dispatched_at timestamptz;
ALTER TABLE fincode.experiments ADD COLUMN stop_reason text;

-- A queued cancellation never started a provider call. Keep its start time NULL
-- while retaining its real cancellation time, rather than inventing a start.
DO $$
DECLARE constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT conname FROM pg_constraint
        WHERE conrelid='fincode.runs'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%completed_at%'
          AND pg_get_constraintdef(oid) LIKE '%started_at%'
    LOOP
        EXECUTE format('ALTER TABLE fincode.runs DROP CONSTRAINT %I',constraint_name);
    END LOOP;
END $$;
ALTER TABLE fincode.runs ADD CONSTRAINT runs_completion_time_check CHECK (
    completed_at IS NULL OR
    (started_at IS NOT NULL AND completed_at >= started_at) OR
    (status='cancelled' AND started_at IS NULL AND completed_at >= created_at)
);
CREATE INDEX performance_experiments_idx ON fincode.experiments(created_at DESC) WHERE purpose='performance';
