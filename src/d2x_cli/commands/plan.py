import rich_click as click
from d2x_cli.runtime import pass_runtime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


@click.group("plan", help="")
def plan():
    """Top-level `click` command group for interacting with D2X plans."""
    pass


@plan.command(name="create", help="Create a new plan")
@click.argument("name")
@pass_runtime(require_project=True, require_keychain=True)
def create(runtime, name):
    pass


@plan.command(name="list", help="List all plans")
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime):
    d2x = get_d2x_api_client(runtime)
    api_list_to_table(d2x.list(D2XApiObjects.Plan))
