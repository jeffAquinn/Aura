"""Sprint 7: Connections. Credentials, browser sign-in and checks, against a fake Google.

The sign-in listener is real (a one-shot HTTP server on 127.0.0.1); the "browser" is httpx calling
it the way Google's redirect would. Google's endpoints are an httpx MockTransport.
"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, parse_qsl, urlparse

import httpx
import pytest
from test_governor import env, workshop_client  # noqa: F401 (fixtures)

from aura.audit import AuditLog
from aura.connections import ConnectionsError
from aura.connections.google import REVOKE_URL, SERVICES, TOKEN_URL, GoogleConnection
from aura.secrets import SecretError, Secrets

CLIENT_ID = "123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-" + "abcdefghijklmnopqrstuvwx"
REFRESH, ACCESS = "1//refresh-token-SECRET-value", "ya29.access-token-SECRET-value"
ENABLE = "https://console.developers.google.com/apis/api/drive.googleapis.com/overview?project=781294236515"
# Google's real answer when an API isn't enabled in the project (shape as of 2026-11).
SERVICE_DISABLED = {"error": {
    "code": 403, "status": "PERMISSION_DENIED",
    "message": f"Google Drive API has not been used in project 781294236515 before or it is disabled. Enable it by "
               f"visiting {ENABLE} then retry. If you enabled this API recently, wait a few minutes for the action "
               "to propagate to our systems and retry.",
    "details": [
        {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED", "domain": "googleapis.com",
         "metadata": {"service": "drive.googleapis.com", "consumer": "projects/781294236515",
                      "serviceTitle": "Google Drive API", "activationUrl": ENABLE}},
        {"@type": "type.googleapis.com/google.rpc.Help", "links": [{"description": "Google developers console API activation", "url": ENABLE}]},
    ],
}}


def id_token(email: str = "me@gmail.com", iss: str = "https://accounts.google.com") -> str:
    def part(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part({'iss': iss, 'email': email})}.signature"


class FakeGoogle:
    def __init__(self) -> None:
        self.challenge = ""
        self.grant: list[str] = []  # the scopes Google grants (None: all requested)
        self.refresh_ok = True
        self.check_status: dict[str, int] = {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        form = dict(parse_qsl(request.content.decode())) if request.method == "POST" else {}
        self.calls.append((url.split("?")[0], form))
        if url == TOKEN_URL and form["grant_type"] == "authorization_code":
            verifier = form["code_verifier"]
            s256 = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if s256 != self.challenge or form["code"] != "the-one-time-code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3599,
                                             "scope": " ".join(self.grant), "id_token": id_token()})  # fmt: skip
        if url == TOKEN_URL and form["grant_type"] == "refresh_token":
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return httpx.Response(200, json={"access_token": ACCESS + "-2", "expires_in": 3599})
        if url == REVOKE_URL:
            return httpx.Response(200)
        for s in SERVICES.values():
            if url == s.check_url:
                status = self.check_status.get(s.id, 200)
                return httpx.Response(status, json=SERVICE_DISABLED if status == 403 else {})
        return httpx.Response(404)


@pytest.fixture
def google(tmp_path):
    fake = FakeGoogle()
    secrets = Secrets()
    now = [1_000_000.0]
    conn = GoogleConnection(secrets, AuditLog(tmp_path / "audit.jsonl"), http=httpx.Client(transport=httpx.MockTransport(fake)),
                            clock=lambda: now[0], timeout=5)  # fmt: skip
    conn.set_credentials(CLIENT_ID, CLIENT_SECRET)
    return conn, fake, now


def begin(conn: GoogleConnection, fake: FakeGoogle, services: list[str], grant: list[str] | None = None) -> dict:
    url = conn.begin(services)
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    fake.challenge = query["code_challenge"]
    requested = query["scope"].split()
    fake.grant = requested if grant is None else grant
    return query


def browser(query: dict, **params: str) -> httpx.Response:
    """What Google's redirect does: the user's browser calls the loopback address."""
    return httpx.get(query["redirect_uri"], params=params, timeout=5)


def finish(conn: GoogleConnection) -> None:
    assert conn._signin is not None and conn._signin.done.wait(5)


# ── credentials ─────────────────────────────────────────────────────────────────────────────


def test_credentials_are_checked_and_kept_in_the_keychain(tmp_path):
    conn = GoogleConnection(Secrets(), AuditLog(tmp_path / "audit.jsonl"))
    with pytest.raises(SecretError, match="should end with '.apps.googleusercontent.com'"):
        conn.set_credentials("sk-ant-api03-not-a-google-client-id-at-all-0000", CLIENT_SECRET)
    with pytest.raises(SecretError, match="should start with 'GOCSPX-'"):
        conn.set_credentials(CLIENT_ID, "tvly-wrong-thing-pasted-here-000")
    conn.set_credentials(f"  {CLIENT_ID}\n", CLIENT_SECRET)  # pasted with whitespace: trimmed
    status = conn.status()
    assert status["state"] == "ready" and status["configured"]
    hints = {c["name"]: c["hint"] for c in status["credentials"]}
    assert hints == {"google_client_id": "123456…apps.googleusercontent.com", "google_client_secret": "GOCSPX-…uvwx"}
    assert CLIENT_SECRET not in json.dumps(status)
    with pytest.raises(SecretError, match="written only by Aura's sign-in"):
        conn.secrets.set("google_tokens", '{"refresh_token": "forged"}')  # the app can never plant a token
    assert [s["name"] for s in conn.secrets.listing()] == ["anthropic_api_key", "tavily_api_key"]  # Keys ≠ connections


# ── sign-in ─────────────────────────────────────────────────────────────────────────────────


def test_sign_in_asks_only_for_the_chosen_services_with_pkce(google):
    conn, fake, _ = google
    query = begin(conn, fake, ["gmail"])
    scopes = query["scope"].split()
    assert scopes == ["openid", "email", *SERVICES["gmail"].scopes]  # nothing for Calendar or Drive
    assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) == 43
    assert query["access_type"] == "offline" and query["client_id"] == CLIENT_ID
    redirect = urlparse(query["redirect_uri"])
    assert redirect.scheme == "http" and redirect.hostname == "127.0.0.1" and redirect.path == "/callback"
    assert conn.status()["state"] == "signing_in"
    with pytest.raises(ConnectionsError, match="Choose at least one"):
        conn.begin(["gmail", "photos"])
    conn.disconnect()


def test_a_completed_sign_in_keeps_only_tokens_in_the_keychain(google, tmp_path):
    conn, fake, _ = google
    query = begin(conn, fake, ["gmail", "calendar"])
    page = browser(query, code="the-one-time-code", state=query["state"], iss="https://accounts.google.com")
    finish(conn)
    assert page.status_code == 200 and "You can close this tab" in page.text
    status = conn.status()
    assert status["state"] == "connected" and status["account"] == "me@gmail.com" and status["problem"] is None
    assert {s["id"]: s["granted"] for s in status["services"]} == {"gmail": True, "calendar": True, "drive": False}
    stored = json.loads(conn.secrets.get("google_tokens"))
    assert stored["refresh_token"] == REFRESH  # in the Keychain only
    shown = json.dumps(status) + (tmp_path / "audit.jsonl").read_text()
    for secret in (REFRESH, ACCESS, "the-one-time-code", CLIENT_SECRET, query["state"]):
        assert secret not in shown  # never in what the app sees, nor in the audit log


def test_forged_and_replayed_answers_are_refused(google):
    conn, fake, _ = google
    query = begin(conn, fake, ["drive"])
    assert browser(query, code="the-one-time-code", state="forged").status_code == 400
    assert conn.status()["state"] == "signing_in"  # still waiting for the real answer
    assert httpx.get(query["redirect_uri"].replace("/callback", "/elsewhere"), timeout=5).status_code == 404
    assert browser(query, code="the-one-time-code", state=query["state"]).status_code == 200
    finish(conn)
    try:  # a replay is refused: "already used", or the listener is already gone
        assert browser(query, code="the-one-time-code", state=query["state"]).status_code == 410
    except httpx.HTTPError:
        pass
    assert sum(url == TOKEN_URL for url, _ in fake.calls) == 1  # the code was redeemed once
    assert conn.status()["state"] == "connected"


def test_an_answer_from_another_issuer_is_refused(google):
    conn, fake, _ = google
    query = begin(conn, fake, ["gmail"])
    page = browser(query, code="the-one-time-code", state=query["state"], iss="https://evil.example")
    finish(conn)
    assert page.status_code == 400 and conn.secrets.get("google_tokens") is None
    assert "unexpected issuer" in conn.status()["problem"]
    assert not any(url == TOKEN_URL for url, _ in fake.calls)  # the code was never even redeemed


def test_declining_on_googles_screen_connects_nothing(google):
    conn, fake, _ = google
    query = begin(conn, fake, ["gmail"])
    browser(query, error="access_denied", state=query["state"])
    finish(conn)
    status = conn.status()
    assert status["state"] == "ready" and status["problem"] == "You declined, so nothing was connected."


def test_permissions_unticked_on_googles_screen_are_reported(google):
    conn, fake, _ = google
    query = begin(conn, fake, ["gmail", "calendar"], grant=["openid", "email", *SERVICES["gmail"].scopes])
    browser(query, code="the-one-time-code", state=query["state"])
    finish(conn)
    status = conn.status()
    assert {s["id"]: s["granted"] for s in status["services"]}["calendar"] is False
    assert "didn't grant access to Calendar" in status["problem"]


def test_an_unanswered_sign_in_times_out(google):
    conn, fake, _ = google
    conn.timeout = 0.5
    begin(conn, fake, ["gmail"])
    finish(conn)
    assert "timed out" in conn.status()["problem"] and conn.status()["state"] == "ready"


# ── using it ────────────────────────────────────────────────────────────────────────────────


def connected(conn: GoogleConnection, fake: FakeGoogle, services: list[str]) -> None:
    query = begin(conn, fake, services)
    browser(query, code="the-one-time-code", state=query["state"])
    finish(conn)


def test_tokens_refresh_and_a_revoked_sign_in_is_cleared(google):
    conn, fake, now = google
    connected(conn, fake, ["gmail"])
    assert conn.access_token() == ACCESS
    now[0] += 3600  # expired
    assert conn.access_token() == ACCESS + "-2"
    assert json.loads(conn.secrets.get("google_tokens"))["access_token"] == ACCESS + "-2"
    now[0] += 3600
    fake.refresh_ok = False  # the user revoked Aura at myaccount.google.com
    with pytest.raises(ConnectionsError, match="Couldn't refresh"):
        conn.access_token()
    status = conn.status()
    assert status["state"] == "ready" and "Sign in again" in status["problem"]


def test_check_reads_each_granted_service(google):
    conn, fake, _ = google
    connected(conn, fake, ["gmail", "drive"])
    fake.check_status["drive"] = 403  # the Drive API isn't enabled in the user's project
    checks = {c["service"]: c for c in conn.check()["checks"]}
    assert set(checks) == {"gmail", "drive"}  # only what was granted
    assert checks["gmail"]["ok"] and not checks["drive"]["ok"]
    assert checks["drive"]["detail"].startswith("The Google Drive API isn't enabled in your Google Cloud project yet.")
    assert checks["drive"]["fix_url"] == ENABLE  # whole: the project number intact (a cut once broke it)
    assert checks["gmail"]["fix_url"] is None


def test_googles_explanations_are_never_cut_mid_link():
    from aura.connections.google import _diagnose, _error

    r = httpx.Response(403, json={"error": {"message": SERVICE_DISABLED["error"]["message"]}})  # no details
    assert ENABLE in _error(r) and _diagnose(r) == (_error(r), None)
    forged = {"error": {"details": [{"reason": "SERVICE_DISABLED", "metadata": {"activationUrl": "https://evil.example/x"}}]}}
    assert _diagnose(httpx.Response(403, json=forged))[1] is None  # only ever a Google Cloud console link


def test_disconnect_revokes_at_google_and_forgets(google):
    conn, fake, _ = google
    connected(conn, fake, ["calendar"])
    conn.disconnect()
    assert (REVOKE_URL, {"token": REFRESH}) in fake.calls
    assert conn.secrets.get("google_tokens") is None and conn.status()["state"] == "ready"
    conn.remove_credentials()
    assert conn.status()["state"] == "not_set_up" and conn.secrets.get("google_client_secret") is None


# ── the boundary: the app's API, and the agent ──────────────────────────────────────────────


def test_the_api_never_returns_a_secret_and_keys_stay_separate(env):  # noqa: F811
    client, auth, aura = workshop_client(env)
    fake = FakeGoogle()
    google = aura.toolbox.connections.get("google")
    google.http = httpx.Client(transport=httpx.MockTransport(fake))

    assert client.get("/v1/connections", headers=auth).json()[0]["state"] == "not_set_up"
    assert client.get("/v1/connections").status_code == 401
    bad = client.put("/v1/connections/google/credentials", json={"client_id": "nope", "client_secret": "nope"}, headers=auth)
    assert bad.status_code == 422 and "googleusercontent" in bad.json()["error"]
    assert client.post("/v1/connections/google/connect", json={"services": ["gmail"]}, headers=auth).status_code == 409
    ok = client.put("/v1/connections/google/credentials", json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
                    headers=auth)  # fmt: skip
    assert ok.status_code == 200 and ok.json()["state"] == "ready" and CLIENT_SECRET not in ok.text
    started = client.post("/v1/connections/google/connect", json={"services": ["gmail"]}, headers=auth).json()
    assert started["auth_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?") and started["state"] == "signing_in"
    query = {k: v[0] for k, v in parse_qs(urlparse(started["auth_url"]).query).items()}
    fake.challenge, fake.grant = query["code_challenge"], query["scope"].split()
    browser(query, code="the-one-time-code", state=query["state"])
    finish(google)
    everything = "".join(client.get(p, headers=auth).text for p in ("/v1/connections", "/v1/connections/google", "/v1/secrets"))
    assert "me@gmail.com" in everything
    for secret in (REFRESH, ACCESS, CLIENT_SECRET):
        assert secret not in everything
    # Connection secrets can't be read, set or deleted through Settings → Keys.
    assert client.put("/v1/secrets/google_tokens", json={"value": "x" * 30}, headers=auth).status_code == 404
    assert client.put("/v1/secrets/google_client_id", json={"value": CLIENT_ID}, headers=auth).status_code == 404
    assert client.delete("/v1/secrets/google_client_secret", headers=auth).status_code == 404
    assert client.post("/v1/connections/slack/check", json={}, headers=auth).status_code == 404
    assert client.post("/v1/connections/google/disconnect", json={}, headers=auth).json()["state"] == "ready"


def test_the_agent_has_no_way_to_reach_a_connection(env):  # noqa: F811
    _, _, aura = workshop_client(env)
    names = " ".join(t.name + " " + (t.description or "") for t in aura.toolbox.all_tools()).lower()
    for word in ("google", "gmail", "oauth", "token", "keychain", "secret", "connection"):
        assert word not in names, word  # no tool of Aura's mentions, let alone reaches, a connection
