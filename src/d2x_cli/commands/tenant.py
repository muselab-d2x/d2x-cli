import rich_click as click
import rich
from d2x_cli.runtime import pass_runtime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


@click.group("tenant", help="")
def tenant():
    """Top-level `click` command group for interacting with D2X Cloud tenants."""
    pass


@tenant.command(name="list", help="List all tenants")
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime):
    d2x = get_d2x_api_client(runtime)
    api_list_to_table(d2x.list(D2XApiObjects.Tenant))
