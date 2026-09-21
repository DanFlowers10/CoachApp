"""
Minimal Strava OAuth2 + API v3 client.

Docs: https://developers.strava.com/docs/authentication/
Create an app at https://www.strava.com/settings/api to get a client id/secret.
"""
import time
import requests

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
DEAUTHORIZE_URL = "https://www.strava.com/oauth/deauthorize"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


def get_authorize_url(client_id, redirect_uri, state=""):
    params = (
        f"client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&approval_prompt=auto"
        "&scope=activity:read_all"
        f"&state={state}"
    )
    return f"{AUTHORIZE_URL}?{params}"


def exchange_code_for_token(client_id, client_secret, code):
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(client_id, client_secret, refresh_token):
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_valid_access_token(token_row, client_id, client_secret, db):
    """Refreshes the token in-place (and saves to db) if it's expired or about to expire."""
    if token_row.expires_at and token_row.expires_at > time.time() + 60:
        return token_row.access_token

    data = refresh_access_token(client_id, client_secret, token_row.refresh_token)
    token_row.access_token = data["access_token"]
    token_row.refresh_token = data["refresh_token"]
    token_row.expires_at = data["expires_at"]
    db.session.commit()
    return token_row.access_token


def deauthorize(access_token):
    """Revokes the app's access on Strava's side, so it drops off strava.com/settings/apps."""
    resp = requests.post(
        DEAUTHORIZE_URL,
        data={"access_token": access_token},
        timeout=10,
    )
    resp.raise_for_status()


def fetch_recent_activities(access_token, per_page=15):
    resp = requests.get(
        ACTIVITIES_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"per_page": per_page},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_activities_since(access_token, after_epoch, per_page=200):
    """All activities after a given unix timestamp (used to cover a whole training block)."""
    resp = requests.get(
        ACTIVITIES_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"after": after_epoch, "per_page": per_page},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
