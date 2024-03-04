import json
import os
import requests
import time
import webbrowser
from datetime import datetime
from urllib.parse import urlencode
from rich.console import Console
from authlib.integrations.requests_client import OAuth2Session

AUTH0_DOMAIN = "dev-8isvmuklk1x3d5o5.us.auth0.com"

OAUTH_DEVICE_APP = {
    "client_id": "spCq5hYVUCjQocuVBLSsidcdduKlMf8r",
    "scope": "openid profile email offline_access",
    "audience": os.environ.get("D2X_AUDIENCE_URL", "https://d2xapi.onrender.com"),
}


def get_oauth_device_flow_token():
    """Interactive github authorization"""
    # Construct an HTTP GET query string from OAUTH_DEVICE_APP
    headers = {"content-type": "application/x-www-form-urlencoded"}

    oauth = OAuth2Session(OAUTH_DEVICE_APP["client_id"], token={})

    res = oauth.post(
        f"https://{ AUTH0_DOMAIN }/oauth/device/code",
        data=OAUTH_DEVICE_APP,
        headers=headers,
        timeout=30,
        withhold_token=True,
    )

    if res.status_code != 200:
        raise Exception("Failed to get device code")

    # Extract the device code and user code from the response
    device_code = res.json()

    console = Console()
    console.print(
        f"[bold] Enter this one-time code: [red]{device_code['user_code']}[/red][/bold]"
    )

    console.print(
        f"Opening {device_code['verification_uri']} in your default browser..."
    )
    webbrowser.open(device_code["verification_uri"])
    time.sleep(2)  # Give the user a second or two before we start polling

    started = time.time()
    with console.status("Polling server for authorization..."):
        while time.time() - started < device_code["expires_in"]:
            res = oauth.post(
                f"https://{ AUTH0_DOMAIN }/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code["device_code"],
                    "client_id": OAUTH_DEVICE_APP["client_id"],
                },
                headers=headers,
                timeout=30,
                withhold_token=True,
            )
            if res.status_code == 200:
                console.print(
                    "[bold green]Successfully authorized OAuth token[/bold green]"
                )
                device_token = res.json()
                break

            time.sleep(1)

    access_token = device_token.get("access_token")
    if access_token:
        console.print(
            f"[bold green]Successfully authorized OAuth token ({access_token[:7]}...)[/bold green]"
        )
    device_token["expires_at"] = int(datetime.now().timestamp()) + device_token.get("expires_in")
    return json.dumps(device_token)


def validate_service(options: dict, keychain) -> dict:
    base_url = options["base_url"]
    tenant = options["tenant"]
    token = json.loads(options["token"])

    # Refresh the token if it's expired or expires in the next 30 minutes
    if token.get("expires_at") <= datetime.now().timestamp() - 1800:
        oauth = OAuth2Session(OAUTH_DEVICE_APP["client_id"], token={})
        token_url = f"https://{ AUTH0_DOMAIN }/oauth/token"
        refresh_token = token.get("refresh_token")
        extra = {
            "grant_type": "refresh_token",
            "client_id": OAUTH_DEVICE_APP["client_id"],
            "refresh_token": refresh_token,
        }
        resp = requests.post(token_url, data=extra)
        if resp.status_code != 200:
            raise Exception(f"Failed to refresh token: {resp.json()}")
        new_token = resp.json()
        new_token["expires_at"] = datetime.now().timestamp() + new_token.get("expires_in")
        token = new_token

    resp = requests.get(
        f"https://{AUTH0_DOMAIN}/userinfo",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    if resp.status_code != 200:
        raise Exception("Invalid token")
    
    options["token"] = json.dumps(token)
    return options
