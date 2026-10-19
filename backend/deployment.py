"""Runtime checks shared by the container launcher and ASGI application."""
import os


def is_hosted():
    return os.environ.get('FINCODE_ENV') == 'production' or bool(os.environ.get('RAILWAY_ENVIRONMENT_ID'))


def validate_hosted(database_url):
    if not is_hosted():
        return
    if not database_url:
        raise RuntimeError('DATABASE_URL is required for hosted deployment')
    token = os.environ.get('BACKEND_SHARED_TOKEN', '')
    if not (32 <= len(token) <= 256 and token.isascii() and all(33 <= ord(c) <= 126 for c in token)):
        raise RuntimeError('BACKEND_SHARED_TOKEN must contain 32-256 printable ASCII characters without spaces')


def server_port():
    value = os.environ.get('PORT', '8000')
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise RuntimeError('PORT must be an integer between 1 and 65535')
    return int(value)
