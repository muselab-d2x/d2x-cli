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
    print(orgs)
    api_list_to_table(orgs)
