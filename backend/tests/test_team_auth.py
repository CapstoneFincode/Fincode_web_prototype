"""Offline tests for the browser login boundary, without DB or model access."""
import json
import os
import time
import unittest
from html.parser import HTMLParser
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.responses import JSONResponse

from backend.team_auth import GoogleVerifier, wrap_team_access


ORIGIN = 'https://fincode.example.test'
CLIENT_ID = 'demo-client.apps.googleusercontent.com'
MEMBER = 'approved.member@gmail.com'


class LoginPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nonce = None
        self.client_id = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get('id') == 'google-login':
            self.nonce = attributes.get('data-nonce')
            self.client_id = attributes.get('data-client-id')


class TeamAuthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1_800_000_000
        self.env = {
            'GOOGLE_CLIENT_ID': CLIENT_ID,
            'GOOGLE_ALLOWED_EMAILS': f'{MEMBER},staff@team.example',
            'TEAM_AUTH_ORIGIN': ORIGIN,
            'TEAM_SESSION_SECRET': 'offline-test-only-secret-value-of-32-or-more-bytes',
        }
        self.claims = {}
        self.requests = []
        self.verifications = []

        async def downstream(scope, receive, send):
            self.requests.append((scope['method'], scope['path']))
            await JSONResponse({'content': 'private test data'})(scope, receive, send)

        async def verify(credential, client_id):
            self.verifications.append((credential, client_id))
            if credential != 'locally-verified-credential':
                raise ValueError('Verification failed')
            return dict(self.claims)

        self.app = wrap_team_access(downstream, env=self.env, verify_token=verify, clock=lambda: self.now)
        self.client = self.new_client()

    def new_client(self):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=ORIGIN)
        self.addAsyncCleanup(client.aclose)
        return client

    async def challenge(self, **claims):
        response = await self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['cache-control'])
        page = LoginPage()
        page.feed(response.text)
        self.assertEqual(page.client_id, CLIENT_ID)
        self.assertTrue(page.nonce)
        self.claims = {
            'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'sub': 'test-subject-123',
            'email': MEMBER, 'email_verified': True, 'iat': self.now, 'exp': self.now + 3600,
            'nonce': page.nonce, **claims,
        }
        return self.client.cookies['__Host-fincode_login']

    async def submit(self, *, credential='locally-verified-credential', origin=ORIGIN, client=None):
        browser = self.client if client is None else client
        headers = {'origin': origin} if origin else {}
        return await browser.post('/auth/google', headers=headers, json={'credential': credential})

    async def sign_in(self, **claims):
        await self.challenge(**claims)
        response = await self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        return response

    async def test_anonymous_document_and_api_are_protected_even_by_direct_url(self):
        for path in ['/', '/index.html']:
            response = await self.client.get(path, headers={'accept': 'text/html'})
            self.assertTrue(response.is_redirect)
            self.assertEqual(urlsplit(response.headers['location']).path, '/login')
        asset = await self.client.get('/app.js')
        self.assertIn(asset.status_code, (302, 303, 307, 401))
        for method in ['GET', 'HEAD', 'POST', 'PUT', 'DELETE']:
            response = await self.client.request(method, '/api/private-test', headers={'origin': ORIGIN})
            self.assertEqual(response.status_code, 401)
        identity = await self.client.get('/auth/me', headers={'oai-authenticated-user-email': MEMBER})
        self.assertEqual(identity.status_code, 401)
        self.assertEqual((await self.client.get('/healthz')).status_code, 200)
        self.assertFalse(self.requests)

    async def test_session_granted_to_verified_member_and_revoked_on_logout(self):
        response = await self.sign_in()
        cookies = response.headers.get_list('set-cookie')
        session = next(value for value in cookies if value.startswith('__Host-fincode_session='))
        for flag in ('httponly', 'secure', 'samesite=', 'path=/'):
            self.assertIn(flag, session.lower())
        self.assertNotIn('domain=', session.lower())
        self.assertEqual(self.verifications, [('locally-verified-credential', CLIENT_ID)])
        self.assertEqual((await self.client.get('/auth/me')).json()['email'], MEMBER)
        self.assertEqual((await self.client.get('/')).status_code, 200)
        write = await self.client.post('/api/private-test', headers={'origin': ORIGIN})
        self.assertEqual(write.status_code, 200)
        self.assertEqual(self.requests, [('GET', '/'), ('POST', '/api/private-test')])

        copied_cookie = self.client.cookies['__Host-fincode_session']
        self.assertEqual((await self.client.post('/auth/logout', headers={'origin': ORIGIN})).status_code, 204)
        self.assertEqual((await self.client.get('/auth/me')).status_code, 401)
        replay = await self.new_client().get('/auth/me', headers={'cookie': f'__Host-fincode_session={copied_cookie}'})
        self.assertEqual(replay.status_code, 401)

    async def test_workspace_member_and_email_authority(self):
        response = await self.sign_in(email='staff@team.example', hd='team.example')
        self.assertEqual(response.json()['email'], 'staff@team.example')
        await self.client.post('/auth/logout', headers={'origin': ORIGIN})
        await self.challenge(email='staff@team.example')
        self.assertIn((await self.submit()).status_code, (401, 403))
        self.assertEqual((await self.client.get('/auth/me')).status_code, 401)

    async def test_failed_token_and_unauthorized_account_grant_no_session(self):
        await self.challenge()
        self.assertEqual((await self.submit(credential='unverified')).status_code, 401)
        await self.challenge(email='unregistered@gmail.com')
        self.assertEqual((await self.submit()).status_code, 403)
        self.assertEqual((await self.client.get('/auth/me')).status_code, 401)
        self.assertFalse(self.requests)

    async def test_claims_require_expected_provider_audience_expiry_nonce_and_verified_email(self):
        for claims in ({'iss': 'https://example.test'}, {'aud': 'a-different-app'},
                       {'exp': self.now - 600}, {'sub': ''}, {'nonce': 'not-this-login'},
                       {'email_verified': False}, {'email_verified': 'true'}):
            with self.subTest(claims=claims):
                await self.challenge(**claims)
                self.assertIn((await self.submit()).status_code, (401, 403))
                self.assertEqual((await self.client.get('/auth/me')).status_code, 401)

    async def test_login_challenge_is_bound_to_browser_single_use_and_expires(self):
        challenge_cookie = await self.challenge()
        self.assertIn((await self.submit(client=self.new_client())).status_code, (400, 401, 403))
        self.assertEqual((await self.submit()).status_code, 200)
        second = self.new_client()
        second.cookies.set('__Host-fincode_login', challenge_cookie, domain='fincode.example.test', path='/')
        self.assertIn((await self.submit(client=second)).status_code, (400, 401, 403))
        self.assertEqual((await second.get('/auth/me')).status_code, 401)
        await self.challenge()
        self.now += 600
        self.assertIn((await self.submit()).status_code, (400, 401, 403))

    async def test_state_changes_check_origin_and_host(self):
        await self.challenge()
        for origin in (None, 'https://foreign.example.test'):
            self.assertEqual((await self.submit(origin=origin)).status_code, 403)
        await self.sign_in()
        for path in ['/api/private-test', '/auth/logout']:
            for origin in (None, 'https://foreign.example.test'):
                headers = {'origin': origin} if origin else {}
                self.assertEqual((await self.client.post(path, headers=headers)).status_code, 403)
        wrong_host = await self.client.get('/auth/me', headers={'host': 'other.example.test'})
        self.assertNotEqual(wrong_host.status_code, 200)
        self.assertEqual((await self.client.get('/auth/me')).status_code, 200)
        self.assertFalse(self.requests)

    async def test_missing_configuration_and_removed_member_fail_closed(self):
        await self.sign_in()
        self.env['GOOGLE_ALLOWED_EMAILS'] = 'someoneelse@gmail.com'
        self.assertIn((await self.client.get('/api/private-test')).status_code, (401, 403))
        self.env.clear()
        self.assertEqual((await self.client.get('/api/private-test')).status_code, 503)
        self.assertEqual((await self.submit()).status_code, 503)
        self.assertFalse(self.requests)

    async def test_session_expires(self):
        await self.sign_in()
        self.now += 8 * 60 * 60 + 1
        self.assertEqual((await self.client.get('/auth/me')).status_code, 401)
        self.assertEqual((await self.client.get('/api/private-test')).status_code, 401)
        self.assertFalse(self.requests)


class OidcVerificationTest(unittest.IsolatedAsyncioTestCase):
    async def test_signature_checked_with_provider_public_key_without_network(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        public_jwk['kid'] = 'test-key'
        trusted_key = jwt.PyJWK.from_dict(public_jwk)
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {
            'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'exp': int(time.time()) + 600,
            'sub': 'offline-subject', 'nonce': 'offline-challenge', 'email': MEMBER,
        }
        verifier = GoogleVerifier()
        with patch.object(verifier.keys, 'get_signing_keys', return_value=[trusted_key]):
            verified = jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'test-key'})
            self.assertEqual(await verifier(verified, CLIENT_ID), claims)
            untrusted = jwt.encode(claims, other_key, algorithm='RS256', headers={'kid': 'test-key'})
            with self.assertRaises(jwt.InvalidTokenError):
                await verifier(untrusted, CLIENT_ID)
            with self.assertRaises(jwt.InvalidTokenError):
                await verifier(verified, 'another-client-id')


class RailwayBrowserAccessTest(unittest.IsolatedAsyncioTestCase):
    async def test_deployed_entrypoint_preserves_both_browser_and_backend_checks(self):
        service_token = 'test-only-backend-service-token-for-access-checks'
        config = {
            'GOOGLE_CLIENT_ID': CLIENT_ID, 'GOOGLE_ALLOWED_EMAILS': MEMBER,
            'TEAM_AUTH_ORIGIN': ORIGIN, 'TEAM_SESSION_SECRET': 'z' * 40,
            'BACKEND_SHARED_TOKEN': service_token,
        }
        with patch.dict(os.environ, config, clear=True):
            from backend.app import app

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                self.assertEqual((await client.get('/healthz')).status_code, 200)
                private_api = '/api/data'
                token_alone = await client.get(private_api, headers={'X-Fincode-Backend-Token': service_token})
                self.assertEqual(token_alone.status_code, 401)
                private_file = await client.get('/index.html', headers={'accept': 'text/html'})
                self.assertTrue(private_file.is_redirect)
                self.assertEqual(urlsplit(private_file.headers['location']).path, '/login')

                page = LoginPage()
                page.feed((await client.get('/login')).text)
                self.assertTrue(page.nonce)
                claims = {
                    'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'sub': 'test-subject',
                    'exp': int(time.time()) + 600, 'email_verified': True, 'email': MEMBER,
                    'nonce': page.nonce,
                }
                with patch.object(app, 'verify_token', new=AsyncMock(return_value=claims)):
                    login = await client.post('/auth/google', headers={'origin': ORIGIN},
                                              json={'credential': 'synthetic-id-token'})
                self.assertEqual(login.status_code, 200)
                self.assertEqual((await client.get('/auth/me')).json()['email'], MEMBER)
                dashboard = await client.get('/index.html')
                self.assertEqual(dashboard.status_code, 200)
                self.assertTrue(dashboard.headers['content-type'].startswith('text/html'))
                api = await client.get(private_api)
                self.assertEqual(api.status_code, 401)
                self.assertEqual(api.json()['error']['code'], 'UNAUTHORIZED')
