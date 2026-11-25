"""Regression: relogin 2026-10-25 reported LOGIN_FAILED on AppFolio's
"add a Passkey" interstitial (required-action webauthn-register-one-click)
even though the login itself had succeeded."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
ls = pytest.importorskip("lease_signer")


class _El:
    def __init__(self, page, visible):
        self.page, self.visible = page, visible
        self.first = self

    def is_visible(self, timeout=0):
        return self.visible

    def click(self):
        self.page.clicked = True
        self.page.url = "https://mckay.appfolio.com/"


class _Page:
    def __init__(self, url, nag, label="Remind Me Later"):
        self.url, self.nag, self.clicked, self.label = url, nag, False, label

    def locator(self, sel):
        return _El(self, self.nag and self.label in sel)

    def get_by_text(self, text):
        return _El(self, self.nag and text == self.label)

    def wait_for_load_state(self, *a, **k):
        pass


def test_skip_passkey_prompt_clicks_remind_me_later():
    page = _Page("https://account.appfolio.com/realms/property/login-actions/"
                 "required-action?execution=webauthn-register-one-click", nag=True)
    assert ls._skip_passkey_prompt(page) is True
    assert page.clicked and "login" not in page.url


def test_skip_passkey_prompt_noop_without_nag():
    page = _Page("https://mckay.appfolio.com/", nag=False)
    assert ls._skip_passkey_prompt(page) is False
    assert not page.clicked


def test_relogin_dismisses_passkey_before_success_check():
    src = (Path(__file__).parent / "relogin.py").read_text()
    assert src.index("ls._skip_passkey_prompt(page)") < src.index('ok = ("login" not in page.url')


def test_skip_passkey_prompt_clicks_not_now():
    """2026-11-25: AppFolio reworded the dismiss link to "Not now". Every auto-relogin then
    parked on the passkey page and raised the "do 2fa with Ix" card, with no 2FA involved."""
    page = _Page("https://account.appfolio.com/realms/property/login-actions/"
                 "required-action?execution=webauthn-register-one-click", nag=True, label="Not now")
    assert ls._skip_passkey_prompt(page) is True
    assert page.clicked and "login" not in page.url
    off_url = _Page("https://account.appfolio.com/somewhere-else", nag=True, label="Not now")
    assert ls._skip_passkey_prompt(off_url) is True


def test_session_probe_does_not_call_a_parked_login_step_ok():
    """2026-11-24 20:18: "Session probe OK" with the URL still on required-action."""
    src = (Path(__file__).parent / "lease_signer.py").read_text()
    body = src[src.index("def check_session"):src.index("def sign_lease")]
    assert body.index('"login-actions" in page.url.lower()') < body.index('return {"ok": True')
