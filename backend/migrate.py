"""Versioned, transactional PostgreSQL migrations; never drops existing data."""
import hashlib
import os
from pathlib import Path
import psycopg

ROOT = Path(__file__).resolve().parent.parent

def migrate(url=None):
    url = url or os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL is required')
    with psycopg.connect(url, connect_timeout=5) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(20261014, 67)')
        conn.execute('CREATE TABLE IF NOT EXISTS public.fincode_schema_migrations (version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
        for path in sorted((ROOT / 'database' / 'migrations').glob('*.sql')):
            source = path.read_text('utf-8')
            digest = hashlib.sha256(source.encode()).hexdigest()
            old = conn.execute('SELECT sha256 FROM public.fincode_schema_migrations WHERE version=%s', (path.name,)).fetchone()
            if old:
                if old[0] != digest:
                    raise RuntimeError(f'Applied migration changed: {path.name}')
                continue
            # The reviewed initial artifact has its own transaction wrapper.
            source = source.replace('\nBEGIN;\n', '\n', 1)
            if source.rstrip().endswith('COMMIT;'):
                source = source.rstrip()[:-7]
            conn.execute(source, prepare=False)
            conn.execute('INSERT INTO public.fincode_schema_migrations(version,sha256) VALUES (%s,%s)', (path.name,digest))

if __name__ == '__main__':
    migrate()
    from backend.database import Database
    Database(os.environ['DATABASE_URL']).seed()
    print('Database migrations and sample seed completed.')
