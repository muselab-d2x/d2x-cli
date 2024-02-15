import rich_click as click
import rich
from d2x_cli.runtime import pass_runtime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


@click.group("repo", help="")
def repo():
    """Top-level `click` command group for interacting with D2X GitHub repos."""
    pass


@repo.command(name="create", help="Create a new repo")
@click.argument("repo_id")
@pass_runtime(require_project=False, require_keychain=True)
def run(runtime, repo_id):
    pass


@repo.command(name="list", help="List all repos")
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime):
    d2x = get_d2x_api_client(runtime)
    api_list_to_table(d2x.list(D2XApiObjects.GithubRepo))
