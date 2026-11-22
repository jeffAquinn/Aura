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
    assert client.post("/v1/connections/myspace/check", json={}, headers=auth).status_code == 404
    assert client.post("/v1/connections/google/disconnect", json={}, headers=auth).json()["state"] == "ready"


def test_the_agent_has_no_way_to_reach_a_connection(env):  # noqa: F811
    _, _, aura = workshop_client(env)
    names = " ".join(t.name + " " + (t.description or "") for t in aura.toolbox.all_tools()).lower()
    for word in ("google", "gmail", "oauth", "token", "keychain", "secret", "connection"):
        assert word not in names, word  # no tool of Aura's mentions, let alone reaches, a connection


# ── Slack ───────────────────────────────────────────────────────────────────────────────────

from aura.connections import slack as slack_mod  # noqa: E402
from aura.connections.slack import SlackConnection  # noqa: E402

SLACK_ID = "1234567890123.9876543210987"
XOXP = "xoxp-" + "1111-2222-3333-SECRETSECRETSECRET"


class FakeSlack:
    def __init__(self) -> None:
        self.challenge = ""
        self.grant = ""
        self.alive = True
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        form = dict(parse_qsl(request.content.decode())) if request.method == "POST" else {}
        self.calls.append((url, form, dict(request.headers)))
        if url == slack_mod.API + "oauth.v2.access":
            s256 = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if "client_secret" in form or s256 != self.challenge or form.get("client_id") != SLACK_ID:
                return httpx.Response(200, json={"ok": False, "error": "invalid_code"})
            return httpx.Response(200, json={"ok": True, "app_id": "A1", "team": {"id": "T1", "name": "Aura HQ"},
                                             "authed_user": {"id": "U1", "scope": self.grant, "access_token": XOXP,
                                                             "token_type": "user"}})  # fmt: skip
        authorized = request.headers.get("authorization") == f"Bearer {XOXP}"
        if url == slack_mod.API + "auth.test":
            ok = authorized and self.alive
            return httpx.Response(200, json={"ok": True, "user": "jeff", "team": "Aura HQ"} if ok else {"ok": False, "error": "invalid_auth"})
        if url == slack_mod.API + "conversations.list":
            return httpx.Response(200, json={"ok": authorized, "channels": []})
        if url == slack_mod.API + "auth.revoke":
            return httpx.Response(200, json={"ok": True, "revoked": True})
        return httpx.Response(404)


@pytest.fixture
def slack(tmp_path):
    fake = FakeSlack()
    conn = SlackConnection(Secrets(), AuditLog(tmp_path / "audit.jsonl"), http=httpx.Client(transport=httpx.MockTransport(fake)),
                           timeout=5, port=0)  # fmt: skip  # any free port in tests
    conn.set_credentials(SLACK_ID)
    return conn, fake


def slack_begin(conn: SlackConnection, fake: FakeSlack, services: list[str], grant: str | None = None) -> dict:
    query = {k: v[0] for k, v in parse_qs(urlparse(conn.begin(services)).query, keep_blank_values=True).items()}
    fake.challenge = query["code_challenge"]
    fake.grant = query["user_scope"] if grant is None else grant
    return query


def test_slacks_manifest_registers_aura_with_pkce_and_no_rotation():
    m = json.loads(slack_mod.manifest())
    assert m["oauth_config"]["redirect_urls"] == ["http://localhost:47615/slack/callback"] == [slack_mod.REDIRECT]
    assert m["oauth_config"]["pkce_enabled"] is True and m["settings"]["token_rotation_enabled"] is False
    assert set(m["oauth_config"]["scopes"]["user"]) == {sc for s in slack_mod.SERVICES.values() for sc in s.scopes}
    assert "bot" not in m["oauth_config"]["scopes"]  # Aura acts as the user, with no bot


def test_slack_needs_only_a_client_id(tmp_path):
    conn = SlackConnection(Secrets(), AuditLog(tmp_path / "audit.jsonl"))
    with pytest.raises(SecretError, match="should look like 1234567890123.1234567890123"):
        conn.set_credentials("xoxp-not-a-client-id-0000")
    conn.set_credentials(f" {SLACK_ID} ")
    status = conn.status()
    assert status["state"] == "ready" and [c["hint"] for c in status["credentials"]] == ["1234…0987"]
    assert status["setup"]["redirect_url"] == slack_mod.REDIRECT and "pkce_enabled" in status["setup"]["manifest"]


def test_slack_sign_in_uses_pkce_without_a_secret_and_keeps_the_user_token(slack, tmp_path):
    conn, fake = slack
    query = slack_begin(conn, fake, ["read"])
    assert query["scope"] == "" and query["code_challenge_method"] == "S256"
    assert set(query["user_scope"].split(",")) == set(slack_mod.SERVICES["read"].scopes)  # not chat:write
    assert query["redirect_uri"].startswith("http://localhost:") and query["redirect_uri"].endswith("/slack/callback")
    page = browser(query, code="slack-code", state=query["state"])
    finish(conn)
    assert page.status_code == 200, page.text
    status = conn.status()
    assert status["state"] == "connected" and status["account"] == "jeff in Aura HQ"
    assert {s["id"]: s["granted"] for s in status["services"]} == {"read": True, "send": False}
    exchange = next(form for url, form, _ in fake.calls if url.endswith("oauth.v2.access"))
    assert "client_secret" not in exchange  # Slack, with PKCE: "should not include client_secret"
    shown = json.dumps(status) + (tmp_path / "audit.jsonl").read_text()
    assert XOXP not in shown and "slack-code" not in shown


def test_slack_checks_reading_and_clears_a_revoked_token(slack):
    conn, fake = slack
    query = slack_begin(conn, fake, ["read", "send"])
    browser(query, code="slack-code", state=query["state"])
    finish(conn)
    checks = {c["service"]: c for c in conn.check()["checks"]}
    assert checks["read"]["ok"] and checks["send"]["ok"] and "ask before sending" in checks["send"]["detail"]
    fake.alive = False  # the user removed the app from their workspace
    status = conn.check()
    assert status["state"] == "ready" and "Sign in again" in status["problem"]


def test_slack_disconnect_revokes_and_errors_are_explained(slack):
    conn, fake = slack
    query = slack_begin(conn, fake, ["read"])
    browser(query, code="slack-code", state=query["state"])
    finish(conn)
    conn.disconnect()
    revoke = [h for url, _, h in fake.calls if url.endswith("auth.revoke")]
    assert revoke and revoke[0]["authorization"] == f"Bearer {XOXP}" and conn.secrets.get("slack_tokens") is None
    assert "Redirect URL" in slack_mod._explain("bad_redirect_uri") and slack_mod._explain("weird") == "weird"


def test_slack_over_the_api_needs_no_secret_and_shows_its_manifest(env):  # noqa: F811
    client, auth, _ = workshop_client(env)
    slack_status = next(c for c in client.get("/v1/connections", headers=auth).json() if c["id"] == "slack")
    assert slack_status["state"] == "not_set_up" and json.loads(slack_status["setup"]["manifest"])["oauth_config"]["pkce_enabled"]
    saved = client.put("/v1/connections/slack/credentials", json={"client_id": SLACK_ID}, headers=auth)
    assert saved.status_code == 200 and saved.json()["state"] == "ready"
    google = client.put("/v1/connections/google/credentials", json={"client_id": CLIENT_ID}, headers=auth)
    assert google.status_code == 422  # Google's client does need its secret


# ── Discord ─────────────────────────────────────────────────────────────────────────────────

from aura.connections import discord as discord_mod  # noqa: E402
from aura.connections.discord import DiscordConnection  # noqa: E402

BOT_TOKEN = ".".join(("MTA1NTk5OTk5OTk5OTk5OTk5OQ", "GhSECRET", "kX9SECRETSECRETSECRETSECRETSECRETSECRE"))
USER_TOKEN = ".".join(("MTIzNDU2Nzg5MDEyMzQ1Njc4OQ", "GuSERX", "uSERTOKENuSERTOKENuSERTOKENuSERTOKENu"))


class FakeDiscord:
    def __init__(self) -> None:
        self.flags = 1 << 19  # Message Content intent (under 100 servers)
        self.guilds = [{"id": "1", "name": "Aura HQ"}]
        self.public = False
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = str(request.url).removeprefix(discord_mod.API)
        self.calls.append((path, dict(request.headers)))
        auth = request.headers.get("authorization", "")
        if auth not in (f"Bot {BOT_TOKEN}", f"Bot {USER_TOKEN}"):
            return httpx.Response(401, json={"message": "401: Unauthorized", "code": 0})
        if path == "users/@me":
            return httpx.Response(200, json={"id": "1055", "username": "Aura", "bot": True} if BOT_TOKEN in auth
                                  else {"id": "42", "username": "jeff"})  # fmt: skip
        if path == "oauth2/applications/@me":
            return httpx.Response(200, json={"id": "1055", "name": "Aura", "flags": self.flags, "bot_public": self.public})
        if path == "users/@me/guilds":
            return httpx.Response(200, json=self.guilds)
        return httpx.Response(404)


@pytest.fixture
def discord(tmp_path):
    fake = FakeDiscord()
    conn = DiscordConnection(Secrets(), AuditLog(tmp_path / "audit.jsonl"), http=httpx.Client(transport=httpx.MockTransport(fake)))
    return conn, fake


def test_a_discord_token_is_verified_before_it_is_saved(discord, tmp_path):
    conn, fake = discord
    with pytest.raises(SecretError, match="three parts joined by dots"):
        conn.set_credentials("not-a-token")
    assert fake.calls == []  # a malformed paste never reaches Discord
    with pytest.raises(ConnectionsError, match="isn't valid"):
        conn.set_credentials(BOT_TOKEN.replace("SECRET", "WRONGG"))
    with pytest.raises(ConnectionsError, match="personal account's token"):
        conn.set_credentials(USER_TOKEN)  # self-bots are against Discord's terms
    assert conn.secrets.get("discord_bot_token") is None and conn.status()["state"] == "not_set_up"
    conn.set_credentials(f"Bot {BOT_TOKEN}\n")  # pasted with the header's prefix: still fine
    status = conn.status()
    assert status["state"] == "connected" and status["account"] == "Aura"
    assert {s["id"]: s["granted"] for s in status["services"]} == {"read": True, "send": True}
    assert status["setup"]["invite_url"] == "https://discord.com/oauth2/authorize?client_id=1055&scope=bot&permissions=68608"
    sent = fake.calls[-1][1]
    assert sent["authorization"] == f"Bot {BOT_TOKEN}" and sent["user-agent"].startswith("DiscordBot (")
    shown = json.dumps(status) + (tmp_path / "audit.jsonl").read_text()
    assert "SECRET" not in shown and "kX9" not in shown


def test_discord_check_explains_the_intent_and_the_invite(discord):
    conn, fake = discord
    fake.flags, fake.guilds = 0, []
    conn.set_credentials(BOT_TOKEN)
    checks = {c["service"]: c for c in conn.check()["checks"]}
    assert not checks["read"]["ok"] and "Message Content intent is off" in checks["read"]["detail"]
    assert checks["read"]["fix_url"] == "https://discord.com/developers/applications/1055/bot"
    assert not checks["send"]["ok"] and checks["send"]["fix_url"].endswith("permissions=68608")
    fake.flags, fake.guilds, fake.public = 1 << 18, [{"name": "Aura HQ"}, {"name": "Friends"}], True
    status = conn.check()
    checks = {c["service"]: c for c in status["checks"]}
    assert checks["read"]["ok"] and checks["send"]["ok"] and "In 2 servers: Aura HQ, Friends" in checks["send"]["detail"]
    assert "Public" in status["problem"]  # anyone with the invite link could add it


def test_discord_disconnect_forgets_and_says_how_to_kill_the_token(discord):
    conn, _ = discord
    conn.set_credentials(BOT_TOKEN)
    conn.disconnect()
    status = conn.status()
    assert status["state"] == "not_set_up" and conn.secrets.get("discord_bot_token") is None
    assert "Reset Token" in status["problem"]
    with pytest.raises(ConnectionsError, match="no sign-in"):
        conn.begin(["read"])
