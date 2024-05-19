import json
import os
import webbrowser
import d2x_cli
import rich_click as click
from urllib.parse import urlencode
from github3.exceptions import NotFoundError
from rich import print
from rich.prompt import Prompt
from d2x_cli.runtime import pass_runtime
from d2x_cli.auth import _validate_service, D2X_OAUTH_APP, D2X_WORKER_OAUTH_APP
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


@click.group("token", help="Get API tokens for D2X Cloud for use in other systems")
def token():
    """Top-level `click` command group for interacting with D2X Cloud tokens."""
    pass


@token.command(name="access_token", help="Get an access token for the D2X Cloud API")
@click.option(
    "--worker",
    help="Get token for the D2X Worker API service",
)
@pass_runtime(require_project=False, require_keychain=True)
def access_token(runtime, worker):
    app = D2X_OAUTH_APP if not worker else D2X_WORKER_OAUTH_APP
    service_name = "d2x" if not worker else "d2x_worker"
    service = runtime.project_config.keychain.get_service(service_name)
    _, service = _validate_service(
        service.config, runtime.project_config.keychain, app=app
    )
    d2x_token = json.loads(service["token"])
    print(f"Access Token: {d2x_token['access_token']}")
