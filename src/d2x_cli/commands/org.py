from urllib.parse import urlencode
import webbrowser
import rich_click as click
from d2x_cli.runtime import pass_runtime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


@click.group("org", help="")
def org():
    """Top-level `click` command group for interacting with D2X orgs."""
    pass


@org.command(name="create", help="Create a new org")
@click.argument("name")
@pass_runtime(require_project=False, require_keychain=True)
def run(runtime, name):
    pass


@org.command(name="list", help="List all orgs")
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime):
    d2x = get_d2x_api_client(runtime)
    orgs = d2x.list(D2XApiObjects.Org)
    api_list_to_table(orgs)


@click.group("user", help="")
def user():
    """Top-level `click` command group for interacting with D2X org users."""
    pass


@user.command(name="list", help="List all org users")
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime):
    d2x = get_d2x_api_client(runtime)
    org_users = d2x.list(D2XApiObjects.OrgUser)
    api_list_to_table(org_users)


@user.command(
    name="browser",
    help="Opens a browser window and logs into the org using the stored OAuth credentials",
)
@click.argument("org_user")
@click.option(
    "-p",
    "--path",
    required=False,
    help="Navigate to the specified page after logging in.",
)
@pass_runtime(require_project=False, require_keychain=True)
def org_browser(runtime, org_user, path):
    d2x = get_d2x_api_client(runtime)
    login_url = d2x.org_login(org_user, path=path)
    if not login_url:
        raise click.ClickException(f"Org user {org_user} not found")

    webbrowser.open(login_url)


org.add_command(user)
