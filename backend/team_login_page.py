"""Render the Fincode team sign-in page without loading protected dashboard assets."""

from base64 import b64encode
from hashlib import sha256
from html import escape


_STYLE = """
* { box-sizing: border-box; }
body { margin: 0; color: #17243b; background: #f6f8fc;
  font: 1rem/1.6 'DM Sans', 'Noto Sans KR', system-ui, sans-serif; }
main { min-height: 100vh; min-height: 100svh; display: grid; place-items: center; padding: 1rem; }
section { background: white; border: 1px solid #e7ebf2; border-radius: 14px; padding: clamp(1.25rem, 5vw, 2.5rem);
  width: 100%; max-width: 27rem; box-shadow: 0 .5rem 2rem #17243b08; }
.brand { display: flex; align-items: center; gap: .65rem; margin-bottom: 2rem; }
.mark { display: grid; place-items: center; width: 2.5rem; height: 2.5rem; background: #265ce9;
  color: white; border-radius: .65rem; font-size: 1.5rem; font-weight: 800; }
.name { font-size: 1.4rem; font-weight: 750; line-height: 1.1; }
.name small { display: block; margin-top: .2rem; color: #52617a; font-size: .6rem; letter-spacing: .1em; }
h1 { margin: 0; font-size: 1.6rem; line-height: 1.4; letter-spacing: -.045em; }
p { margin: .6rem 0 0; color: #52617a; overflow-wrap: anywhere; }
#google-login { margin-top: 1.6rem; }
#status { font-size: .875rem; }
a { color: #1948c7; }
a:focus-visible { outline: 3px solid #739af7; outline-offset: 3px; }
"""
_SCRIPT = """
const mount = document.getElementById('google-login');
const statusText = document.getElementById('status');
const retry = document.getElementById('retry');
let submitting = false;

function reportError(message) {
  statusText.textContent = message;
  retry.hidden = false;
}

async function authenticate(result) {
  if (submitting) return;
  if (!result.credential) {
    reportError('Google 계정을 확인하지 못했습니다. 다시 로그인해 주세요.');
    return;
  }
  submitting = true;
  retry.hidden = true;
  statusText.textContent = '팀원 계정을 확인하고 있습니다.';
  try {
    const response = await fetch('/auth/google', {
      method: 'POST', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({credential: result.credential})
    });
    if (response.ok) {
      location.replace('/');
      return;
    }
    const messages = {
      401: '로그인 인증이 만료되었거나 유효하지 않습니다. 다시 로그인해 주세요.',
      403: '접근이 거부되었습니다. 등록된 팀원 계정인지 확인하고 다시 로그인해 주세요.',
      503: '지금은 로그인을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.'
    };
    reportError(messages[response.status] || '로그인을 완료하지 못했습니다. 다시 시도해 주세요.');
  } catch {
    reportError('서버에 연결하지 못했습니다. 연결을 확인하고 다시 시도해 주세요.');
  } finally {
    submitting = false;
  }
}

const provider = document.createElement('script');
provider.src = 'https://accounts.google.com/gsi/client?hl=ko';
provider.async = true;
provider.onload = () => {
  try {
    const identity = window.google.accounts.id;
    identity.initialize({
      client_id: mount.dataset.clientId, nonce: mount.dataset.nonce,
      callback: authenticate, auto_select: false
    });
    identity.renderButton(mount, {
      type: 'standard', theme: 'outline', size: 'large', text: 'signin_with',
      locale: 'ko', width: String(Math.min(360, Math.floor(mount.getBoundingClientRect().width)))
    });
    statusText.textContent = '';
  } catch {
    reportError('Google 로그인 버튼을 표시하지 못했습니다. 다시 시도해 주세요.');
  }
};
provider.onerror = () => reportError('Google에 연결하지 못했습니다. 네트워크 설정을 확인하고 다시 시도해 주세요.');
document.head.append(provider);
"""


def _csp_hash(content: str) -> str:
    digest = b64encode(sha256(content.encode("utf-8")).digest()).decode("ascii")
    return f"'sha256-{digest}'"


LOGIN_CSP = "; ".join([
    "default-src 'none'",
    f"script-src {_csp_hash(_SCRIPT)} https://accounts.google.com/gsi/client",
    f"style-src {_csp_hash(_STYLE)} https://accounts.google.com/gsi/style",
    "style-src-attr 'unsafe-inline'",  # GIS sets dimensions on its embedded button.
    "frame-src https://accounts.google.com/gsi/",
    "connect-src 'self' https://accounts.google.com/gsi/",
    "img-src data: https://accounts.google.com https://*.googleusercontent.com",
    "base-uri 'none'", "object-src 'none'", "form-action 'self'", "frame-ancestors 'none'",
])


def render_login(client_id: str, nonce: str) -> str:
    if client_id and nonce:
        box = f"""<p>등록된 팀원 Google 계정으로 로그인해 주세요.</p>
<div id="google-login" data-client-id="{escape(client_id, quote=True)}" data-nonce="{escape(nonce, quote=True)}"></div>
<p id="status" role="status" aria-live="polite">Google 로그인 버튼을 불러오는 중입니다.</p>
<a id="retry" href="/login" hidden>로그인 다시 시도</a>
<noscript><p>Google 로그인을 사용하려면 JavaScript를 켜 주세요.</p></noscript>
<script>{_SCRIPT}</script>"""
    else:
        box = '<p role="status">팀 로그인을 준비 중입니다. 팀 관리자에게 설정 상태를 문의해 주세요.</p>'
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#265ce9"><title>팀원 로그인 · 핀코드</title>
<style>{_STYLE}</style></head><body><main><section aria-labelledby="title">
<div class="brand"><span class="mark" aria-hidden="true">F</span>
<div class="name" lang="en">fincode<small>LLM SECURITY LAB</small></div></div>
<h1 id="title">팀원 로그인</h1>{box}</section></main></body></html>"""
