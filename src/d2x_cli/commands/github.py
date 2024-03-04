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
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


@click.group("github", help="")
def github():
    """Top-level `click` command group for interacting with GitHub configuration."""
    pass



@github.command(name="init", help="Initialize configuration for GitHub Actions to run D2X jobs as a remote runner.")
@pass_runtime(require_project=True, require_keychain=True)
def init(runtime):
    repo_api = runtime.project_config.get_github_api()
    repo_api = repo_api.repository(
        runtime.project_config.repo_owner, 
        runtime.project_config.repo_name,
    )
    base_url = repo_api.session.base_url
    repo_prefix = f"{base_url}/repos/{runtime.project_config.repo_owner}/{runtime.project_config.repo_name}"

    secret_name = "D2X_TOKEN"
    service = runtime.project_config.keychain.get_service("d2x")

    # Look for the D2X_TOKEN secret in the repo
    secrets = repo_api._get(f"{repo_prefix}/actions/secrets")
    for secret in secrets.json()["secrets"]:
        if secret["name"] == secret_name:
            break
    else:
        if Prompt.ask("Do you want to create D2X_TOKEN secret in the repository?", default="Y") == "Y":
            resp = repo_api._put(
                f"{repo_prefix}/actions/secrets/{secret_name}",
                json={"encrypted_value": service.config},
            )
            if resp.status_code == 201:
                print("D2X_TOKEN secret created in the repository.")
            else:
                raise Exception(f"Failed to create D2X_TOKEN secret: {resp.json()}")
        else:
            print("You didn't say Y")

    # If it doesn't exist, create it
    if not os.path.isfile(os.path.join(str(runtime.project_config.project_dir), ".github", "workflow", "d2x-job.yml")):
        if Prompt.ask("Do you want to create d2x-job.yml workflow file in the repository?", default="Y") == "Y":
            # Create a new branch
            try:
                branch = repo_api.branch('d2x-config')
            except NotFoundError:
                branch = repo_api.create_branch('d2x-config')

            # Create the workflow file in the new branch
            workflow_path = d2x_cli.__path__[0] + "/files/d2x-job.yml"
            with open("d2x-job.yml", "r") as file:
                content = file.read()
            repo_api.create_file("d2x-job.yml", "Create d2x-job.yml", content, branch=branch)

            print("d2x-job.yml workflow file created in the d2x-config branch.")

            # Create a PR to merge the new branch into the default branch
            title = "Add d2x-job.yml"
            body = "This PR adds the d2x-job.yml workflow file."
            base = repo_api.default_branch
            head = "d2x-config"
            pr = repo_api.create_pull(title, body, base, head)

            print(f"Pull request created: {pr.html_url}")

            # Prompt the user for approval to merge the PR
            if Prompt.ask("Do you want to merge the pull request now?", default="N") == "Y":
                pr.merge()
                print("Pull request merged.")
