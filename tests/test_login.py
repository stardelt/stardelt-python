"""Tests for `stardelt login` (stardelt.login and the login/logout commands). No network: a fake session is the IdP."""

import io
import json
import os
import stat

import pytest
import requests

from stardelt import cli, login
from tests.test_api import CANARY, FakeResponse, FakeSession

ISSUER = "https://id.example/realms/stardelt"
META = {
    "device_authorization_endpoint": ISSUER + "/protocol/openid-connect/auth/device",
    "token_endpoint": ISSUER + "/protocol/openid-connect/token",
}
START = {"device_code": "dc-1", "user_code": "ABCD-EFGH", "verification_uri": ISSUER + "/device", "interval": 0, "expires_in": 600}
PENDING = FakeResponse(400, {"error": "authorization_pending"})
GRANTED = FakeResponse(200, {"access_token": CANARY, "refresh_token": "rt-1", "expires_in": 300})


def idp(*token_answers):
    return FakeSession(FakeResponse(200, META), FakeResponse(200, START), *token_answers)


def stored(home, **fields):
    creds = {
        "issuer": ISSUER, "client_id": "stardelt-dev", "token_endpoint": META["token_endpoint"],
        "access_token": "stored-token", "refresh_token": "rt-old", "expires_at": 4_000_000_000, "host": "https://nova.example",
    }
    creds.update(fields)
    return login.save({"HOME": str(home)}, creds)


def run(argv, session, environ):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, environ=environ, stdout=out, stderr=err, session=session)
    return code, out.getvalue(), err.getvalue()


def test_the_device_flow_shows_the_code_polls_and_returns_the_tokens():
    session, said, slept = idp(PENDING, PENDING, GRANTED), [], []
    creds = login.device_login(session, ISSUER, "stardelt-dev", said.append, sleep=slept.append, clock=lambda: 1000)
    assert creds["access_token"] == CANARY and creds["refresh_token"] == "rt-1" and creds["expires_at"] == 1300
    assert len(said) == 1 and "ABCD-EFGH" in said[0] and ISSUER + "/device" in said[0]
    assert slept == [0, 0, 0]
    method, url, kwargs = session.calls[-1]
    assert (method, url) == ("POST", META["token_endpoint"])
    assert kwargs["data"] == {
        "grant_type": login.GRANT_DEVICE,
        "device_code": "dc-1",
        "client_id": "stardelt-dev",
        "code_verifier": kwargs["data"]["code_verifier"],
    }


def challenge_of(verifier):
    import base64
    import hashlib

    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_the_request_carries_a_pkce_challenge_and_every_poll_the_matching_verifier():
    session = idp(PENDING, GRANTED)
    login.device_login(session, ISSUER, "stardelt-dev", print, sleep=lambda s: None, clock=lambda: 0)
    start = session.calls[1][2]["data"]
    assert start["client_id"] == "stardelt-dev" and start["code_challenge_method"] == "S256"
    verifiers = [call[2]["data"]["code_verifier"] for call in session.calls[2:]]
    assert len(verifiers) == 2 and len(set(verifiers)) == 1
    assert 43 <= len(verifiers[0]) <= 128
    assert start["code_challenge"] == challenge_of(verifiers[0])
    assert verifiers[0] not in str(start), "the verifier never goes to the device endpoint"


def test_each_login_makes_a_new_verifier():
    first, second = login.pkce(), login.pkce()
    assert first[0] != second[0] and first[1] != second[1]
    assert first[1] == challenge_of(first[0]) and "=" not in first[1] and "+" not in first[1] and "/" not in first[1]


def test_slow_down_makes_the_next_wait_five_seconds_longer():
    slept = []
    login.device_login(idp(FakeResponse(400, {"error": "slow_down"}), GRANTED), ISSUER, "c", print, sleep=slept.append, clock=lambda: 0)
    assert slept == [0, 5]


@pytest.mark.parametrize(
    "answer, message",
    [
        (FakeResponse(400, {"error": "access_denied"}), "refused"),
        (FakeResponse(400, {"error": "expired_token"}), "expired"),
        (FakeResponse(500, {}), "HTTP 500"),
    ],
)
def test_a_refusal_an_expired_code_and_a_broken_provider_are_login_errors(answer, message):
    with pytest.raises(login.LoginError, match=message):
        login.device_login(idp(answer), ISSUER, "c", print, sleep=lambda s: None, clock=lambda: 0)


def test_a_provider_without_the_device_endpoint_is_a_login_error():
    with pytest.raises(login.LoginError, match="device"):
        login.device_login(FakeSession(FakeResponse(200, {"token_endpoint": "x"})), ISSUER, "c", print)


def test_the_credentials_file_is_private_and_forgotten_on_logout(tmp_path):
    environ = {"HOME": str(tmp_path)}
    path = stored(tmp_path)
    assert path == tmp_path / ".config" / "stardelt" / "credentials.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert login.load(environ)["access_token"] == "stored-token"
    assert login.forget(environ) is True and login.load(environ) is None and login.forget(environ) is False


def test_without_home_or_xdg_nothing_is_read_or_written():
    assert login.credentials_path({}) is None and login.load({}) is None and login.forget({}) is False
    with pytest.raises(login.LoginError):
        login.save({}, {"access_token": "x"})


def test_xdg_config_home_wins_over_home(tmp_path):
    assert login.credentials_path({"XDG_CONFIG_HOME": str(tmp_path / "x"), "HOME": "/h"}) == tmp_path / "x" / "stardelt" / "credentials.json"


def test_a_token_with_time_left_is_returned_without_a_request(tmp_path):
    stored(tmp_path, expires_at=1000 + login.SKEW + 1)
    session = FakeSession()
    assert login.fresh_token({"HOME": str(tmp_path)}, session, clock=lambda: 1000) == "stored-token"
    assert session.calls == []


def test_a_token_about_to_expire_is_renewed_and_the_new_one_is_kept(tmp_path):
    environ = {"HOME": str(tmp_path)}
    stored(tmp_path, expires_at=1000 + login.SKEW - 1)
    session = FakeSession(FakeResponse(200, {"access_token": "renewed", "expires_in": 300}))
    assert login.fresh_token(environ, session, clock=lambda: 1000) == "renewed"
    assert session.calls[0][2]["data"] == {"grant_type": "refresh_token", "refresh_token": "rt-old", "client_id": "stardelt-dev"}
    kept = login.load(environ)
    assert (kept["access_token"], kept["refresh_token"], kept["expires_at"], kept["host"]) == ("renewed", "rt-old", 1300, "https://nova.example")


def test_a_refused_renewal_and_a_missing_refresh_token_ask_for_a_new_login(tmp_path):
    stored(tmp_path, expires_at=0)
    with pytest.raises(login.LoginError, match="stardelt login"):
        login.fresh_token({"HOME": str(tmp_path)}, FakeSession(FakeResponse(400, {"error": "invalid_grant"})), clock=lambda: 1000)
    stored(tmp_path, expires_at=0, refresh_token=None)
    with pytest.raises(login.LoginError, match="expired"):
        login.fresh_token({"HOME": str(tmp_path)}, FakeSession(), clock=lambda: 1000)


def test_the_login_command_keeps_the_sign_in_and_never_prints_the_token(tmp_path):
    environ = {"HOME": str(tmp_path)}
    code, out, err = run(["--host", "https://nova.example", "login", "--issuer", ISSUER], idp(PENDING, GRANTED), environ)
    assert code == 0 and out == ""
    assert "ABCD-EFGH" in err and "signed in" in err and CANARY not in err
    kept = login.load(environ)
    assert (kept["access_token"], kept["client_id"], kept["host"]) == (CANARY, "stardelt-dev", "https://nova.example")


def test_after_login_commands_use_the_stored_token_and_host(tmp_path):
    stored(tmp_path)
    session = FakeSession(FakeResponse(200, {"user": "analyst"}))
    code, out, err = run(["whoami"], session, {"HOME": str(tmp_path)})
    assert (code, json.loads(out), err) == (0, {"user": "analyst"}, "")
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "https://nova.example/api/me")
    assert kwargs["headers"] == {"Authorization": "Bearer stored-token"}


def test_the_environment_token_wins_over_the_stored_one(tmp_path):
    stored(tmp_path)
    session = FakeSession(FakeResponse(200, {}))
    environ = {"HOME": str(tmp_path), "STARDELT_TOKEN": CANARY}
    assert run(["whoami"], session, environ)[0] == 0
    assert session.calls[0][2]["headers"] == {"Authorization": f"Bearer {CANARY}"}


def test_logout_forgets_and_a_later_command_needs_authentication(tmp_path):
    stored(tmp_path)
    environ = {"HOME": str(tmp_path), "STARDELT_NOVA_URL": "https://nova.example"}
    assert run(["logout"], FakeSession(), environ)[:1] == (0,)
    code, _, err = run(["whoami"], FakeSession(), environ)
    assert code == 3 and "stardelt login" in err


def test_login_without_an_issuer_is_a_usage_error(tmp_path):
    session = FakeSession()
    code, _, err = run(["login"], session, {"HOME": str(tmp_path)})
    assert (code, session.calls) == (2, []) and "--issuer" in err


def test_a_stored_sign_in_that_cannot_be_renewed_exits_3(tmp_path):
    stored(tmp_path, expires_at=0, refresh_token=None)
    code, out, err = run(["whoami"], FakeSession(), {"HOME": str(tmp_path)})
    assert (code, out) == (3, "") and "stardelt login" in err


NOVA = "https://nova.example"
NOVA_CONFIG = {"mode": "oidc", "issuer": ISSUER, "deviceClientId": "cli-1"}


def asking_nova(config, *token_answers):
    return FakeSession(FakeResponse(200, config), FakeResponse(200, META), FakeResponse(200, START), *token_answers)


def test_login_without_an_issuer_asks_nova_and_uses_the_client_it_names(tmp_path):
    environ = {"HOME": str(tmp_path)}
    session = asking_nova(NOVA_CONFIG, GRANTED)
    code, out, err = run(["--host", NOVA, "login"], session, environ)
    assert (code, out) == (0, "") and "ABCD-EFGH" in err and CANARY not in err
    assert session.calls[0][:2] == ("GET", NOVA + "/api/auth/config")
    assert session.calls[1][:2] == ("GET", ISSUER + "/.well-known/openid-configuration")
    assert session.calls[2][2]["data"]["client_id"] == "cli-1"
    kept = login.load(environ)
    assert (kept["issuer"], kept["client_id"], kept["host"]) == (ISSUER, "cli-1", NOVA)


def test_the_client_flag_wins_and_a_missing_client_falls_back_to_the_default(tmp_path):
    session = asking_nova(NOVA_CONFIG, GRANTED)
    run(["--host", NOVA, "login", "--client-id", "mine"], session, {"HOME": str(tmp_path)})
    assert session.calls[2][2]["data"]["client_id"] == "mine"
    session = asking_nova(dict(NOVA_CONFIG, deviceClientId=None), GRANTED)
    run(["--host", NOVA, "login"], session, {"HOME": str(tmp_path)})
    assert session.calls[2][2]["data"]["client_id"] == "stardelt-dev"


def test_an_issuer_given_means_nova_is_not_asked(tmp_path):
    session = idp(GRANTED)
    run(["--host", NOVA, "login", "--issuer", ISSUER], session, {"HOME": str(tmp_path)})
    assert session.calls[0][1] == ISSUER + "/.well-known/openid-configuration"
    assert all("/api/auth/config" not in call[1] for call in session.calls)


@pytest.mark.parametrize(
    "config",
    [
        {"mode": "dev", "issuer": None, "deviceClientId": None},
        {"mode": "dev", "issuer": ISSUER, "deviceClientId": None},
        {"mode": "oidc", "issuer": "", "deviceClientId": "cli-1"},
    ],
)
def test_a_nova_without_sign_in_is_an_authentication_error_and_nothing_is_kept(tmp_path, config):
    environ = {"HOME": str(tmp_path)}
    code, out, err = run(["--host", NOVA, "login"], FakeSession(FakeResponse(200, config)), environ)
    assert (code, out) == (3, "") and "does not name an identity provider" in err
    assert login.load(environ) is None


def test_a_nova_that_cannot_be_reached_is_named_in_the_error(tmp_path):
    class Down:
        def request(self, method, url, **kwargs):
            raise requests.ConnectionError("down")

    code, _, err = run(["--host", NOVA, "login"], Down(), {"HOME": str(tmp_path)})
    assert code == 1 and f"cannot reach {NOVA}" in err
