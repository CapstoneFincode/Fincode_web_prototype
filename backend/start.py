"""Migrate once, then replace the launcher with a single persistent API worker."""
import os
import sys

from backend.database import Database
from backend.deployment import server_port, validate_hosted
from backend.migrate import migrate


def main():
    url = os.environ.get('DATABASE_URL', '')
    try:
        validate_hosted(url)
        port = server_port()
        if not url:
            raise RuntimeError('DATABASE_URL is required by the container launcher')
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    try:
        migrate(url)
        Database(url).seed()
    except Exception:
        # Database errors can contain a connection string; never log credentials.
        print('Database initialization failed. Check connectivity and migration compatibility.', file=sys.stderr)
        return 1
    print('Database initialized; starting Fincode API.', flush=True)
    os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'backend.app:app',
        '--host', '0.0.0.0', '--port', str(port), '--workers', '1',
        '--timeout-graceful-shutdown', '135', '--no-access-log'])


if __name__ == '__main__':
    sys.exit(main())
