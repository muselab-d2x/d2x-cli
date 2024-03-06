import rich_click as click
from d2x_cli.runtime import pass_runtime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


@click.group("scratch", help="")
def scratch():
    """Top-level `click` command group for interacting with D2X scratch org create and delete requests."""
    pass


@scratch.command(name="list", help="List all requests for scratch orgs.")
@click.option(
    "--include-delete",
    is_flag=True,
    help="Include delete requests in the list. Default is to only include create requests.",
)
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime, include_delete):
    d2x = get_d2x_api_client(runtime)
    api_list_to_table(d2x.list(D2XApiObjects.ScratchCreateRequest))
    if include_delete:
        api_list_to_table(d2x.list(D2XApiObjects.ScratchDeleteRequest))

@scratch.command(name="info", help="Get information about a scratch org request.")
@click.argument("id", type=str)
@pass_runtime(require_project=True, require_keychain=True)
def info(runtime, id):
    d2x = get_d2x_api_client(runtime)
    print(d2x.read(D2XApiObjects.ScratchCreateRequest, id))