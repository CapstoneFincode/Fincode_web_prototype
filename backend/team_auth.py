"""Google sign-in and an exact team allowlist for the Railway web entry point."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from backend.team_login_page import LOGIN_CSP, render_login

GOOGLE_ISSUERS = ('https://accounts.google.com', 'accounts.google.com')
CHALLENGE_TTL = 5 * 60
SESSION_TTL = 8 * 60 * 60
MAX_CREDENTIAL_BODY = 16_384
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}
EMAIL = re.compile(r"[a-z0-9.!#$%&'+/=?^_`{|}~-]+@(?:[a-z0-9-]+\.)+[a-z0-9-]+", re.ASCII)


@dataclass(frozen=True)
class Settings:
    origin: str
    client_id: str
    allowed: frozenset[str]
    secret: bytes

    @property
    def secure(self):
        return self.origin.startswith('https://')

    def cookie_name(self, purpose):
        return ('__Host-' if self.secure else '') + 'fincode_' + purpose

    @classmethod
    def read(cls, env):
        origin = env.get('TEAM_AUTH_ORIGIN', '').strip().rstrip('/')
        client_id = env.get('GOOGLE_CLIENT_ID', '').strip()
        secret = env.get('TEAM_SESSION_SECRET', '').encode('utf-8')
        allowed = frozenset(e.strip().lower() for e in env.get('GOOGLE_ALLOWED_EMAILS', '').split(',') if e.strip())
        try:
            url = urlsplit(origin)
            port = url.port  # Reject a malformed or out-of-range port.
        except ValueError:
            return None
        loopback = url.hostname in {'localhost', '127.0.0.1', '::1'}
        if not (url.scheme == 'https' or (url.scheme == 'http' and loopback)):
            return None
        if (not url.hostname or not origin.isascii() or url.username is not None or url.password is not None
                or url.path or url.query or url.fragment or '%' in url.netloc or '\\' in url.netloc):
            return None
        if not re.fullmatch(r'[a-zA-Z0-9_-]+\.apps\.googleusercontent\.com', client_id):
            return None
        if len(secret) < 32 or not allowed or not all(EMAIL.fullmatch(e) for e in allowed):
            return None
        host = url.hostname if ':' not in url.hostname else '[' + url.hostname + ']'
        default_port = 443 if url.scheme == 'https' else 80
        authority = host if port is None or port == default_port else f'{host}:{port}'
        return cls(f'{url.scheme}://{authority}', client_id, allowed, secret)


class GoogleVerifier:
    """Only Google-published RSA keys can verify an ID token."""

    def __init__(self):
        self.keys = jwt.PyJWKClient('https://www.googleapis.com/oauth2/v3/certs', timeout=5)

    def verify(self, credential, client_id):
        header = jwt.get_unverified_header(credential)
        if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
            raise jwt.InvalidTokenError('Unsupported signing key')
        # Use the library's bounded JWK-set cache. An attacker-controlled unknown
        # key ID must not trigger repeated uncached requests to Google.
        key = next((key for key in self.keys.get_signing_keys() if key.key_id == header['kid']), None)
        if key is None:
            raise jwt.InvalidTokenError('Unknown signing key')
        return jwt.decode(credential, key.key, algorithms=['RS256'], audience=client_id, issuer=GOOGLE_ISSUERS,
                          options={'require': ['iss', 'aud', 'exp', 'sub', 'nonce']})

    async def __call__(self, credential, client_id):
        return await run_in_threadpool(self.verify, credential, client_id)


class TokenStore:
    """Single-process, expiring opaque tokens. Keys are HMAC digests, never the browser bearer."""

    def __init__(self, ttl, capacity):
        self.ttl = ttl
        self.capacity = capacity
        self.entries = OrderedDict()

    def prune(self, now):
        while self.entries and next(iter(self.entries.values()))[0] <= now:
            self.entries.popitem(last=False)

    @staticmethod
    def key(token, secret):
        if not isinstance(token, str) or not re.fullmatch('[A-Za-z0-9_-]{43}', token):
            return None
        return hmac.digest(secret, token.encode('ascii'), hashlib.sha256)

    def issue(self, value, settings, now):
        self.prune(now)
        if len(self.entries) >= self.capacity:
            self.entries.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self.entries[self.key(token, settings.secret)] = (now + self.ttl, value)
        return token

    def get(self, token, settings, now, *, consume=False):
        self.prune(now)
        key = self.key(token, settings.secret)
        item = self.entries.pop(key, None) if consume else self.entries.get(key)
        if item and item[0] > now:
            return item[1]
        return None


def error(status, code, message):
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


def allowed_email(claims, settings, nonce, now):
    if not isinstance(claims, dict) or not isinstance(claims.get('sub'), str) or not claims['sub'].strip():
        raise ValueError('Invalid subject')
    if claims.get('iss') not in GOOGLE_ISSUERS or claims.get('aud') != settings.client_id:
        raise ValueError('Invalid issuer or audience')
    if claims.get('azp', settings.client_id) != settings.client_id:
        raise ValueError('Invalid authorized party')
    if type(claims.get('exp')) is not int or claims['exp'] <= now:
        raise ValueError('Expired identity token')
    if claims.get('nonce') != nonce:
        raise ValueError('Invalid login challenge')
    email = claims.get('email')
    if not isinstance(email, str) or claims.get('email_verified') is not True:
        return None
    email = email.lower()
    # Outside Gmail a verified email on a consumer Google Account may be stale.
    # For exact-email invites, require a Google Workspace-managed identity there.
    hd = claims.get('hd')
    if not email.endswith('@gmail.com') and not (isinstance(hd, str) and hd.strip()):
        return None
    return email if email in settings.allowed else None


class TeamAccess:
    def __init__(self, app, env, verify_token, clock):
        self.app = app
        self.env = env
        self.verify_token = verify_token
        self.clock = clock
        self.challenges = TokenStore(CHALLENGE_TTL, capacity=4096)
        self.sessions = TokenStore(SESSION_TTL, capacity=4096)

    @staticmethod
    def set_cookie(response, settings, purpose, value, lifetime):
        response.set_cookie(settings.cookie_name(purpose), value, max_age=lifetime, path='/',
                            secure=settings.secure, httponly=True, samesite='lax')

    def login_page(self, request, settings):
        nonce = secrets.token_urlsafe(32)
        challenge = self.challenges.issue(nonce, settings, self.clock())
        response = HTMLResponse(render_login(settings.client_id, nonce))
        response.headers['Content-Security-Policy'] = LOGIN_CSP
        response.headers['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        previous = request.cookies.get(settings.cookie_name('login'))
        self.challenges.get(previous, settings, self.clock(), consume=True)
        self.set_cookie(response, settings, 'login', challenge, CHALLENGE_TTL)
        return response

    async def login(self, request, settings):
        challenge = request.cookies.get(settings.cookie_name('login'))
        nonce = self.challenges.get(challenge, settings, self.clock())
        if not nonce:
            return error(401, 'LOGIN_EXPIRED', '로그인 화면을 새로 열어 주세요.')
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            return error(415, 'INVALID_REQUEST', 'JSON 요청이 필요합니다.')
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_CREDENTIAL_BODY:
                return error(413, 'INVALID_REQUEST', '로그인 요청이 너무 큽니다.')
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            return error(400, 'INVALID_REQUEST', '로그인 요청을 확인해 주세요.')
        if not isinstance(payload, dict) or not isinstance(payload.get('credential'), str) or not payload['credential']:
            return error(400, 'INVALID_REQUEST', 'Google 로그인 정보가 필요합니다.')
        try:
            claims = await self.verify_token(payload['credential'], settings.client_id)
            email = allowed_email(claims, settings, nonce, self.clock())
        except jwt.PyJWKClientError:
            return error(503, 'GOOGLE_UNAVAILABLE', 'Google 계정을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.')
        except (jwt.InvalidTokenError, ValueError):
            return error(401, 'INVALID_CREDENTIAL', 'Google 계정을 확인할 수 없습니다. 다시 로그인해 주세요.')
        # Consume only after verification, and check again after awaiting Google so
        # concurrent uses of a valid credential cannot both mint a session.
        if self.challenges.get(challenge, settings, self.clock(), consume=True) != nonce:
            return error(401, 'LOGIN_EXPIRED', '로그인 화면을 새로 열어 주세요.')
        if not email:
            return error(403, 'NOT_INVITED', '이 계정에는 사이트 접근 권한이 없습니다.')
        current = request.cookies.get(settings.cookie_name('session'))
        self.sessions.get(current, settings, self.clock(), consume=True)
        session = self.sessions.issue((claims['sub'], email), settings, self.clock())
        response = JSONResponse({'email': email})
        self.set_cookie(response, settings, 'login', '', 0)
        self.set_cookie(response, settings, 'session', session, SESSION_TTL)
        return response

    async def authorize(self, request):
        # Keep invalid Host headers out of URL parsing before the authority check.
        path, method = request.scope['path'], request.method
        if path == '/healthz' and method in {'GET', 'HEAD'}:
            return Response(status_code=200)
        settings = Settings.read(self.env)
        # Railway probes with its own Host header. The existing readiness route
        # reports database availability only; data and diagnostics remain private.
        if path == '/api/ready' and method == 'GET':
            return None if settings else JSONResponse({'ready': False}, status_code=503)
        if not settings:
            if path == '/login' and method == 'GET':
                return HTMLResponse(render_login('', ''), status_code=503, headers={'Content-Security-Policy': LOGIN_CSP})
            return error(503, 'TEAM_AUTH_NOT_CONFIGURED', '팀 로그인 설정이 필요합니다.')
        expected_host = urlsplit(settings.origin).netloc
        hosts = request.headers.getlist('host')
        if len(hosts) != 1 or hosts[0].lower() != expected_host:
            return error(400, 'INVALID_HOST', '사이트 주소를 확인해 주세요.')
        if path == '/login' and method == 'GET':
            return self.login_page(request, settings)
        same_origin = request.headers.getlist('origin') == [settings.origin]
        if path == '/auth/google' and method == 'POST':
            if not same_origin:
                return error(403, 'INVALID_ORIGIN', '같은 사이트에서 로그인해 주세요.')
            return await self.login(request, settings)
        token = request.cookies.get(settings.cookie_name('session'))
        identity = self.sessions.get(token, settings, self.clock())
        if not identity or identity[1] not in settings.allowed:
            if identity:
                self.sessions.get(token, settings, self.clock(), consume=True)
            if method in {'GET', 'HEAD'} and not path.startswith(('/api/', '/auth/')) and 'text/html' in request.headers.get('accept', ''):
                return RedirectResponse('/login', status_code=303)
            return error(401, 'LOGIN_REQUIRED', '로그인이 필요합니다.')
        if method not in SAFE_METHODS and not same_origin:
            return error(403, 'INVALID_ORIGIN', '같은 사이트에서 요청해 주세요.')
        if path == '/auth/me' and method == 'GET':
            return JSONResponse({'email': identity[1]})
        if path == '/auth/logout' and method == 'POST':
            self.sessions.get(token, settings, self.clock(), consume=True)
            response = Response(status_code=204)
            self.set_cookie(response, settings, 'session', '', 0)
            return response
        if path.startswith('/auth/') or path == '/login':
            return Response(status_code=404)
        return None

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        async def private_send(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['Cache-Control'] = 'private, no-store'
                headers['X-Content-Type-Options'] = 'nosniff'
                headers['X-Frame-Options'] = 'DENY'
            await send(message)

        response = await self.authorize(Request(scope, receive))
        if response is None:
            await self.app(scope, receive, private_send)
        else:
            await response(scope, receive, private_send)


def wrap_team_access(app, *, env=None, verify_token=None, clock=time.time):
    return TeamAccess(app, os.environ if env is None else env,
                      GoogleVerifier() if verify_token is None else verify_token, clock)
