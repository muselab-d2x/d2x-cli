import asyncio
import json
import logging
import os
import shutil
import uuid
import websockets
from base64 import b64decode
from collections import defaultdict
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, List, Optional, Text
from zipfile import BadZipFile, ZipFile
import rich_click as click
from nacl.signing import SigningKey
from pydantic import BaseModel
from websockets.http import Headers
from github3 import login
from rich.console import Console
from rich.logging import RichHandler
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress
from rich.status import Status
from rich.table import Table
from cumulusci.core.config import FlowConfig, TaskConfig
from cumulusci.core.config.org_config import OrgConfig
from cumulusci.core.config.scratch_org_config import SfdxOrgConfig, ScratchOrgConfig
from cumulusci.core.config.project_config import BaseProjectConfig
from cumulusci.core.exceptions import (
    CumulusCIFailure,
    CumulusCIUsageError,
    CumulusCIException,
    OrgNotFound,
    SfdxOrgException,
)
from cumulusci.core.flowrunner import (
    FlowCallback,
    FlowCoordinator,
    StepResult,
    StepSpec,
)
from cumulusci.core.github import get_github_api_for_repo
from cumulusci.core.sfdx import sfdx
from cumulusci.core.utils import import_global, merge_config
from cumulusci.tasks.salesforce import UpdateDependencies
from cumulusci.utils import cd, temporary_dir
from d2x_cli.runtime import pass_runtime, CliRuntime
from d2x_cli.api import get_d2x_api_client, get_d2x_worker_api_client, D2XApiObjects
from d2x_cli.github import local_github_checkout, get_github_api_for_repo
from d2x_cli.utils import api_list_to_table

nl = "\n"  # fstrings can't contain backslashes


class StepProgress(BaseModel):
    progress: Progress
    step: StepSpec
    result: Optional[StepResult] = None
    # progress_task: Any
    log: Optional[str] = None
    exception: Optional[Exception] = None
    is_started: bool = False
    is_finished: bool = False
    is_failed: bool = False
    is_skipped: bool = False
    is_aborted: bool = False
    percent_complete: int = 0

    class Config:
        arbitrary_types_allowed = True

    def start(self):
        # self.progress.update(self.progress_task, progress=10)
        self.is_started = True
        # self.progress.refresh()

    def finish(self, result):
        self.result = result
        self.is_finished = True
        # self.progress.update(self.progress_task, progress=100)
        # self.progress.refresh()
        if result.exception:
            self.is_failed = True
            self.log = f"Task {self.step.task_name} failed: {result.exception}"
            self.exception = result.exception


def display_job_summary(steps: List[StepSpec], max_option_length: int = 30):
    table = Table(title="Job Summary")

    table.add_column("Step", justify="right")
    table.add_column("Task Name")
    table.add_column("Options")

    for step in steps:
        options_text = ""
        if isinstance(step.task_config, dict) and "options" in step.task_config:
            options = step.task_config["options"]
            if isinstance(options, dict):
                options_text = ", ".join(
                    f"{key}: {Text(str(value)).crop(max_option_length)}"
                    for key, value in options.items()
                )
        table.add_row(
            str(step.step_num),
            step.task_name,
            options_text,
        )
    return table


class RichHandler(logging.Handler):
    def __init__(self, console, rich_tracebacks=True):
        super().__init__()
        self.console = console
        self.rich_tracebacks = rich_tracebacks
        self.queue = []  # Buffer for log messages
        self.current_task = None
        self.task_logs = defaultdict(list)

    def set_current_task(self, task: StepSpec):
        self.current_task = task

    def emit(self, record):
        try:
            msg = self.format(record)
            if self.current_task:
                self.task_logs[self.current_task].append(msg)
            else:
                self.queue.append(msg)  # Buffer log messages when no task is set
        except Exception:
            self.handleError(record)


class RichFlowCallback(FlowCallback):
    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.console = Console()
        # self.progress = Progress()
        # self.live = Live(self.progress, console=self.console)
        self.org = org
        self.loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("cumulusci")
        self.handler = RichHandler(console=self.console, rich_tracebacks=True)
        self.logger.addHandler(self.handler)
        self.log_task = None
        self.task_progress = {}
        self.task_panels = {}
        self.steps = []
        self.log_capture_string = StringIO()

    async def tail_logs(self):
        while True:
            if self.handler.queue:
                with self.console:
                    for msg in self.handler.queue:
                        self.console.print(msg)
                self.handler.queue.clear()
            await asyncio.sleep(1)  # Adjust the sleep time as needed

    def _init_job_panel(self):
        print("Initializing job panel")
        return
        org_info = f"Org User: {self.org.username} ({self.org.org_id})"
        self.job_panel = Panel(
            f"[bold green]Flow Execution Started\n{org_info}",
            title="Job Execution",
            expand=False,
        )
        print(f"Job panel: {self.job_panel}")

    def update_overall_progress(self, step, total_steps, message):
        print(f"Updating overall progress: {step}/{total_steps} - {message}")
        return
        if not hasattr(self, "overall_progress_task"):
            self.overall_progress_task = self.progress.add_task(
                "[green]Overall Progress:", total=total_steps
            )
        self.progress.update(
            self.overall_progress_task, advance=1, description=f"[green]{message}"
        )
        print(f"Overall progress: {self.progress}")

    def pre_flow(self, coordinator):
        self.steps = coordinator.steps
        # self.live.start()
        # self._init_job_panel()
        # self.log_task = asyncio.create_task(
        #    self.tail_logs()
        # )  # Start as a background task
        return coordinator

    def pre_task(self, step: StepSpec):
        print(f"Starting task {step.task_name}...")
        # self.update_overall_progress(
        # step.step_num, len(self.steps), f"Starting {step.task_name}"
        # )

        print(f"Overall progress updated")
        # Set the current task for the handler
        # self.handler.set_current_task(step)

        # Initialize task-specific progress
        print(f"Creating task progress")
        # task_progress = Progress(console=self.console)
        # self.task_progress[step] = StepProgress(progress=task_progress, step=step)
        # self.task_progress[step].start()

        print(f"Creating task panel")
        # Initialize task-specific panel
        # panel = Panel(
        # f"[bold blue]Task: {step.task_name}\n\n",
        # title="Task Execution",
        # expand=False,
        # )
        # self.task_panels[step.path] = panel
        # self.console.print(panel)
        return step

    def post_task(self, step, result):
        super().post_task(step, result)
        print(f"Task {step.task_name} completed.")
        # Stop capturing to this task's log
        # self.logger.removeHandler(self.handler)
        print(f"Stopped task log capture")
        # self.update_overall_progress(
        # step.step_num, len(self.steps), f"Completed {step.task_name}"
        # )
        print(f"Updated overall progress")

        # Update task panel with captured log and result
        # panel_content = self.log_capture_string.getvalue()
        # new_panel = Panel(
        #     f"[bold blue]Task: {step.task_name}\n{panel_content}\n\n[bold green]Status: Completed",
        #     title="Task Execution",
        #     expand=False,
        # )
        # self.task_panels[step.path] = (
        #     new_panel  # Replace the old panel with the new one
        # )
        # self.console.print(new_panel)

    def post_flow(self, coordinator):
        super().post_flow(coordinator)
        # self.cleanup()
        # self.console.print(
        #     Panel(
        #         "[bold green]Flow execution completed.",
        #         title="Job Completed",
        #         expand=False,
        #     )
        # )

    def cleanup(self):
        # self.live.stop()  # Stop the Live instance
        if self.log_task and not self.log_task.done():
            self.log_task.cancel()  # Cancel the log tailing task
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.handler.close()
        self.logger.removeHandler(self.handler)


class D2XFlowCallback(RichFlowCallback):
    def __init__(self, worker_api, signing_key, job_id, org, *args, **kwargs):
        super().__init__(org, *args, **kwargs)
        self.worker_api = worker_api
        self.signing_key = signing_key
        self.job_id = job_id
        self.status = None

    def pre_flow(self, coordinator: FlowCoordinator):
        super().pre_flow(coordinator)
        message = f"Job {self.job_id} started"
        self.log(message)

    def pre_task(self, step: StepSpec):
        super().pre_task(step)
        message = f"Task {step.path}/{step.task_name} started"
        self.log(message)

    def post_task(self, step: StepSpec, result: StepResult):
        super().post_task(step, result)
        message = f"Task {step.path}/{step.task_name} completed"
        self.log(message)

    def post_flow(self, coordinator: FlowCoordinator):
        super().post_flow(coordinator)
        message = f"Job {self.job_id} completed"
        self.log(message)

    def log(self, message, status=None, exception=None):
        result = self.worker_api.job_status_update(
            job_id=self.job_id,
            signing_key=self.signing_key,
            log=f"{message}\n",
            exception=str(exception),
            status=status if status else "in_progress",
        )
        return result


async def listen_to_socket(job_id, tenant, websocket_uri, token):
    headers = Headers({"Authorization": f"Bearer {token}"})
    async with websockets.connect(
        f"{websocket_uri}/d2x/{tenant}/jobs/{job_id}/log",
        extra_headers=headers,
    ) as websocket:
        while True:
            message = await websocket.recv()
            print(message)


def create_scratch_org(
    keychain,
    project_config,
    org_name: str,
    config_name: str,
    days: Optional[int] = None,
    set_password: Optional[bool] = None,
    prerelease: bool = None,
    namespaced: bool = None,
    devhub: Optional[str] = None,
):
    """Adds/Updates a scratch org config to the keychain from a named config"""
    scratch_config = project_config.lookup(f"orgs__scratch__{config_name}")
    if scratch_config is None:
        raise OrgNotFound(f"No such org configured: `{config_name}`")
    if days is not None:
        # Allow override of scratch config's default days
        scratch_config["days"] = days
    else:
        # Use scratch config days or default of 1 day
        scratch_config.setdefault("days", 1)
    if prerelease is not None:
        scratch_config["release"] = "preview"
    scratch_config["scratch"] = True
    if set_password is not None:
        scratch_config["set_password"] = set_password
    if namespaced is not None:
        scratch_config["namespaced"] = namespaced
    scratch_config["config_name"] = config_name

    scratch_config["sfdx_alias"] = f"{project_config.project__name}__{org_name}"
    scratch_config["devhub"] = f"D2X-WORKER-{devhub}"
    org_config = ScratchOrgConfig(
        scratch_config, org_name, keychain=keychain, global_org=False
    )
    org_config.create_org()

    org_config.save()
    return org_config


def _freeze_steps(project_config: BaseProjectConfig, flow_config: FlowConfig) -> list:
    # flow_config.project_config = project_config
    flow = FlowCoordinator(project_config, flow_config)
    steps = []
    for step in flow.steps:
        if step.skip:
            continue
        with cd(step.project_config.repo_root):
            task = step.task_class(
                step.project_config,
                TaskConfig(step.task_config),
                name=step.task_name,
            )
            steps.extend(task.freeze(step))
    # click.echo(f"Prepared steps:\n  {json.dumps(steps, indent=4)}")

    return steps


@click.group("job", help="")
def job():
    """Top-level `click` command group for interacting with D2X jobs."""
    pass


@job.command(name="create", help="Create new job(s) in D2X Cloud")
@click.option("--plan", "-p", help="The slug of the plan to use for the job")
@click.option(
    "--plan-version",
    "-n",
    help="The plan version id of a specific plan version to use for the job. Otherwise, the latest plan version will be used.",
)
@click.option(
    "--flow",
    "-f",
    help="The CumulusCI flow to use to determine the steps to run in the job",
)
@click.option(
    "--task",
    "-t",
    help="The CumulusCI task to use to determine the steps to run in the job",
)
# @click.option(
# "--orgs", "-o", help="The list of orgs to run the job against, separated by commas"
# )
@click.option(
    "--org-user", "-u", help="The D2X Cloud org user id to run the job against"
)
@click.option(
    "--scratch-org",
    "-s",
    help="The name of the CumulusCI scratch org profiles to create and use for the job",
)
@click.option(
    "--local",
    "-l",
    is_flag=True,
    help="Run the job locally instead of remotely, overriding the default setting",
)
@click.option(
    "--remote",
    "-r",
    is_flag=True,
    help="Run the job remotely instead of locally, overriding the default setting",
)
@pass_runtime(require_project=True, require_keychain=True)
def create(
    runtime, plan, plan_version, flow, task, org_user, scratch_org, local, remote
):
    # Ensure that either a plan or a flow/task is specified
    if not plan and not (flow or task):
        raise click.UsageError(
            "You must specify either a plan, flow, or task to create a job"
        )

    # Ensure that either a plan or a flow/task is specified, but not both
    if plan and (flow or task):
        raise click.UsageError(
            "You cannot specify both a plan, flow, or task to create a job"
        )

    # Ensure that plan_version is only specified if a plan is specified
    if plan and not plan_version:
        raise click.UsageError("You must specify a plan version if you specify a plan")

    # Ensure that org_user or scratch_org is specified
    if not org_user and not scratch_org:
        raise click.UsageError(
            "You must specify either an org user or a scratch org to create a job"
        )

    # Ensure that org_user and scratch_org are not both specified
    if org_user and scratch_org:
        raise click.UsageError(
            "You cannot specify both org_user and a scratch org to create a job"
        )

    # Ensure that local and remote are not both specified
    if local and remote:
        raise click.UsageError(
            "You cannot specify both local and remote to create a job"
        )

    steps = None
    # If flow or task are specified, resolve the steps by freezing the flow
    if flow or task:
        # Create a flow config for a single task if task
        if task:
            runtime.project_config.config["flows"]["d2x_single_task"] = {
                "description": "A flow with a single task for use with D2X Cloud jobs",
                "steps": {1: {"task": task}},
            }
            flow = "d2x_single_task"

        # Get the flow config
        flow_config = runtime.project_config.get_flow(flow)

        steps = _freeze_steps(runtime.project_config, flow_config)

    d2x_api = get_d2x_api_client(runtime)

    repo_id = None
    for repo in d2x_api.list(D2XApiObjects.GithubRepo):
        if (
            repo["org"]["name"] == runtime.project_config.repo_owner
            and repo["name"] == runtime.project_config.repo_name
        ):
            repo_id = repo["id"]
            break

    if not repo_id:
        raise click.UsageError(
            f"GitHub repo '{runtime.project_config.repo_owner}/{runtime.project_config.repo_name}' not found in D2X Cloud. Please make sure you have installed the D2X Cloud GitHub Application to the repo."
        )

    # Look up org user
    org_user_id = None
    if org_user:
        try:
            d2x_org_user = d2x_api.read(D2XApiObjects.OrgUser, org_user)
        except Exception as exc:
            raise click.UsageError(f"Org user '{org_user}' not found in D2X Cloud")

    # Prepare scratch create request
    scratch_org_request = None
    if scratch_org:
        scratch_config = runtime.project_config.lookup(f"orgs__scratch__{scratch_org}")
        if not scratch_config:
            raise click.UsageError(
                f"Scratch org '{scratch_org}' not found in CumulusCI project config"
            )
        scratch_org_request = {
            "org_name": "job-{JOB_ID}",  # FIXME: Make this dynamic?
            # "scratchdef_path": scratch_config["config_file"],
            "cumulusci_config_name": scratch_org,
        }

    # Look up plan
    plan_version_id = None
    if plan:
        plan = next(
            (p for p in d2x_api.list(D2XApiObjects.Plan) if p["slug"] == plan), None
        )
        if not plan:
            raise click.UsageError(f"Plan '{plan}' not found in D2X Cloud")

        # Look up plan version
        if plan_version:
            result = d2x_api.read(D2XApiObjects.PlanVersion, plan_version)
            if not result:
                raise click.UsageError(
                    f"Plan version '{plan_version}' not found in D2X Cloud"
                )
            plan_version_id = result["id"]
        else:
            plan_versions = d2x_api.list(
                D2XApiObjects.PlanVersion, parents={"plan_id": plan["id"]}
            )
            plan_version_id = plan_versions[0]["id"]  # FIXME: Sorting?

    # Create the Scratch Create Request if needed
    scratch_create_request_id = None
    if scratch_org_request:
        response = d2x_api.create(
            D2XApiObjects.ScratchCreateRequest, scratch_org_request
        )
        scratch_create_request_id = response["id"]

    # Create the job
    job_data = {
        "plan_version_id": plan_version_id,
        "org_user_id": org_user,
        "ref": runtime.project_config.repo_commit,
        "repo_id": repo_id,
        "steps": json.dumps(steps),
        "scratch_create_request_id": scratch_create_request_id,
    }
    job = d2x_api.create(
        D2XApiObjects.Job,
        job_data,
    )

    click.echo(f"Job {job['id']} created")

    if local:
        run_job(runtime, job["id"])


def import_org_from_d2x(
    d2x_api,
    keychain,
    logger,
    org_name,
    org_salesforce_id,
    org_user_id,
    username,
    org_alias,
):

    print(f"Importing org {org_name} from D2X Cloud")
    try:
        org = keychain.get_org(org_name)
        if org.org_id[:15] != org_salesforce_id[:15]:
            raise ValueError(
                f"Org named {org_name} already exists in the local keychain but is pointing to a different org ({org.org_id}) than the D2X Cloud org {org_salesforce_id}."
            )
        if org.username != username:
            raise ValueError(
                f"Org named {org_name} already exists in the local keychain but is pointing to a different user ({org.username}) than the D2X Cloud org {username}."
            )
        logger.info(
            f"Found existing org in keychain named {org_name} with matching org id and username. Using it."
        )
        print(
            f"Found existing org in keychain named {org_name} with matching org id and username. Using it."
        )
    except OrgNotFound:
        print(f"Org {org_name} not found in local keychain, attempting to import it...")
        logger.info(
            f"Org {org_name} not found in local keychain, attempting to import it..."
        )
        # Get the org user credential
        org_user_credential = d2x_api.read(
            D2XApiObjects.OrgUser, org_user_id, extra_path="credential"
        )
        print(f"Org user credential: {org_user_credential}")

        # Import the credential into the sfdx keychain
        sfdx_auth_url = org_user_credential["sfdx_auth_url"]

        with NamedTemporaryFile(delete=False) as f:
            f.write(sfdx_auth_url.encode("utf-8"))
            temp_file_name = f.name

        print(f"{open(temp_file_name).read()}")
        try:
            command = (
                f"force:auth:sfdxurl:store -f {temp_file_name} -a {org_alias} --json"
            )
            print(f"Importing to sfdx keychain with command: sfdx {command}")
            p = sfdx(
                f"force:auth:sfdxurl:store -f {temp_file_name} -a {org_alias} --json"
            )
            org_info = None
            stderr_list = [line.strip() for line in p.stderr_text]
            stdout_list = [line.strip() for line in p.stdout_text]

            if p.returncode:
                logger.error(f"Return code: {p.returncode}")
                for line in stderr_list:
                    logger.error(line)
                for line in stdout_list:
                    logger.error(line)
                message = f"\nstderr:\n{nl.join(stderr_list)}"
                message += f"\nstdout:\n{nl.join(stdout_list)}"
                raise SfdxOrgException(message)
            else:
                try:
                    org_info = json.loads("".join(stdout_list))
                except Exception as exc:
                    raise SfdxOrgException(
                        "Failed to parse json from output.\n  "
                        f"Exception: {exc.__class__.__name__}\n  Output: {''.join(stdout_list)}"
                    )
                    exception = exc

            # Import the sfdx org into the CumulusCI keychain
            org_config = SfdxOrgConfig(
                {"username": username, "sfdx": True},
                org_name,
                keychain,
                global_org=False,
            )
            org_config.save()

            logger.info(f"Org {org_name} imported into local keychain successfully.")
            print(f"Org {org_name} imported into local keychain successfully.")
            print(org_config.config)

        finally:
            os.remove(temp_file_name)

        return org_config


def complete_scratch_create_request(
    worker_api, signing_key, logger, scratch_create_request, org
):
    logger.info(f"Completing scratch create request {scratch_create_request['id']}")
    p = sfdx(f"force:org:display --json -u {org.sfdx_alias} --verbose --json")

    org_info = None
    stderr_list = [line.strip() for line in p.stderr_text]
    stdout_list = [line.strip() for line in p.stdout_text]

    if p.returncode:
        logger.error(f"Return code: {p.returncode}")
        for line in stderr_list:
            logger.error(line)
        for line in stdout_list:
            logger.error(line)
        message = f"\nstderr:\n{nl.join(stderr_list)}"
        message += f"\nstdout:\n{nl.join(stdout_list)}"
        raise SfdxOrgException(message)

    else:
        try:
            org_info = json.loads("".join(stdout_list))
        except Exception as exc:
            raise SfdxOrgException(
                "Failed to parse json from output.\n  "
                f"Exception: {exc.__class__.__name__}\n  Output: {''.join(stdout_list)}"
            )
            exception = exc
        org_id = org_info["result"]["accessToken"].split("!")[0]

    sfdx_auth_url = org_info["result"]["sfdxAuthUrl"]
    worker_api.scratch_create_request_complete(
        signing_key,
        scratch_create_request["id"],
        org_id=org.org_id,
        instance_url=org.instance_url,
        username=org.username,
        user_id=org.user_id,
        sfdx_auth_url=sfdx_auth_url,
    )

    logger.info(
        f"Scratch create request {scratch_create_request['id']} completed successfully."
    )


class WorkerOrgConfig(OrgConfig):
    def __init__(
        self,
        worker_api,
        refresh_token,
        config: dict,
        name: str,
        keychain=None,
        global_org=False,
    ):
        self.worker_api = worker_api
        self.refresh_token = refresh_token
        self.last_refresh = datetime.now()
        super().__init__(config, name, keychain, global_org)

    @property
    def instance_name(self):
        return self.config.get("instance_name")

    @property
    def scratch(self):
        return self.config.get("scratch")

    def refresh_oauth_token(self, keychain, connected_app=None, is_sandbox=False):
        if datetime.now() - self.last_refresh < timedelta(minutes=15):
            return
        resp = self.worker_api.refresh_org_token(self.refresh_token)
        self.config["access_token"] = resp["access_token"]
        self.refresh_token = resp["refresh_token"]


def import_worker_org(
    worker_api,
    keychain,
    org_name: str,
    access_token: str,
    instance_url: str,
    add_to_sf: bool = True,
):
    sfdx_alias = f"D2X-WORKER-{org_name}"
    org_config = WorkerOrgConfig(
        worker_api=worker_api,
        config={
            "instance_url": instance_url,
            "access_token": access_token,
            "sfdx_alias": "sfdx_alias",
        },
        name=org_name,
        keychain=keychain,
        global_org=False,
    )

    if add_to_sf:
        p = sfdx(
            f"org login access-token --instance-url {instance_url} --alias {sfdx_alias}",
            env={"SF_INSTANCE_URL": instance_url, "SF_ACCESS_TOKEN": access_token},
        )

        stdout = [line.strip() for line in p.stdout_text]
        stderr = [line.strip() for line in p.stderr_text]

        if p.returncode:
            raise SfdxOrgException(
                f"Failed to import org {org_name} into sfdx keychain.\n  "
                f"Return code: {p.returncode}\n  "
                f"stderr: {nl.join(stderr)}\n  "
                f"stdout: {nl.join(stdout)}"
            )
    return org_config


def remove_worker_org(org_name, keychain):
    try:
        org = keychain.get_org(org_name)
        keychain.remove_org(org_name)
    except OrgNotFound:
        pass
    sfdx_alias = f"D2X-WORKER-{org_name}"

    p = sfdx(f"org logout --target-org {sfdx_alias} --noprompt")


def prepare_dependencies(job_id, dependencies, project_config):
    output = []
    for dependency in dependencies:
        if "zip_file" not in dependency:
            output.append({"type": "dependency", "config": dependency})
            continue

        try:
            # Decode the base64-encoded ZIP file
            zip_file_bytes = b64decode(dependency["zip_file"])
            # Use BytesIO to create a binary stream
            with BytesIO(zip_file_bytes) as binary_stream:
                with ZipFile(binary_stream) as zip_ref:
                    job_dir = project_config.repo_root / ".d2x" / "jobs" / job_id
                    job_dir.mkdir(parents=True, exist_ok=True)
                    dependency_dir = job_dir / "dependencies" / "zip_files"
                    dependency_dir.mkdir(parents=True, exist_ok=True)
                    random_dir = str(uuid.uuid4())
                    zip_ref.extractall(dependency_dir / random_dir)
                    output.append(
                        {
                            "key": f"d2x_dependency_{random_dir}",
                            "name": f"D2X Unmanaged ZipFile Dependency: {random_dir}",
                            "description": f"Unmanaged ZIP file dependency for job {job_id} from D2X resolution.",
                            "type": "step",
                            "config": {
                                "type": "cumulusci_task_class",
                                "task_class": "cumulusci.tasks.salesforce.Deploy",
                                "options": {
                                    "path": dependency_dir / random_dir,
                                    "unmanaged": dependency["unmanaged"],
                                    "namespace_inject": dependency["namespace_inject"],
                                    "namespace_strip": dependency["namespace_strip"],
                                },
                            },
                        }
                    )
        except BadZipFile:
            print(
                f"Error: The provided ZIP file for dependency in job {job_id} is not a valid ZIP file."
            )
        # Convert to a list of steps using update_dependencies to group sequential dependencies into a single step
        steps = []
        current_dependencies = []
        group_number = 1

        for step in output:
            if step["type"] == "step":
                if current_dependencies:
                    steps.append(
                        {
                            "key": f"d2x_update_dependencies_{group_number}",
                            "name": "Update Dependencies from D2X Resolution",
                            "description": f"Update dependencies from D2X resolution for resolution group {group_number}",
                            "type": "cumulusci_task_class",
                            "config": {
                                "task_class": "cumulusci.tasks.salesforce.UpdateDependencies",
                                "options": {
                                    "dependencies": current_dependencies,
                                },
                            },
                        }
                    )
                    current_dependencies = []
                steps.append(step)
                current_dependencies = []
            elif step["type"] == "dependency":
                current_dependencies.append(step["config"])

        print(f"Steps: {steps}")
        return steps


@job.command(name="run", help="Run a queued job locally")
@click.argument("job_id")
# @click.option("--retry", is_flag=True, help="Retry the job if it previously failed")
@click.option(
    "--retry-scratch",
    is_flag=True,
    help="Retry the scratch org creation if it previously failed",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable verbose output showing all logs",
)
@pass_runtime(require_project=False, require_keychain=True)
def run_job(runtime, job_id, retry_scratch=False, verbose=False):
    worker_api = get_d2x_worker_api_client(runtime)
    signing_key, d2x_job = worker_api.job_start(job_id)
    org = None
    exception = None
    flow_callback: D2XFlowCallback = None
    logger = logging.getLogger("d2x")

    org_name = f"d2x-job-{job_id}"
    org_imported = False
    scratch_created = False
    devhub_org = None
    devhub_alias = f"job-{job_id}-devhub"
    try:

        ref = d2x_job["ref"]
        commit = d2x_job["resolved_ref"]["commit"]
        branch = None
        tag = None
        if "commit" in ref:
            ref = ref["commit"]
            commit = ref
        elif "tag" in ref:
            ref = ref["tag"]
            tag = ref
        elif "branch" in ref:
            ref = ref["branch"]
            branch = ref
        else:
            ref = runtime.project_config.project__git__default_branch

        zip_file = worker_api.job_repo_contents(
            job_id=job_id,
            repo={"id": d2x_job["repo"]["id"]},
            ref={"commit": commit["sha"]},
            signing_key=signing_key,
        )
        with temporary_dir() as temp_dir:
            temp_dir = Path(temp_dir)
            zip_file.extractall(temp_dir)
            # GitHub repo contents are extracted to a directory named after the repo
            # Since that's the only directory in the temp_dir, we can move its contents to the temp_dir root
            repo_dir = (
                Path(temp_dir) / Path(temp_dir).iterdir().__next__()
            )  # Get the only directory in temp_dir'

            # Move the contents of the repo directory to the temp_dir root
            for item in repo_dir.iterdir():
                item.rename(temp_dir / item.name)
            repo_dir.rmdir()

            # Set the repo info since we're not working from a local checkout
            repo_info = {
                "branch": branch,
                "commit": commit,
                "tag": tag,
                "root": temp_dir,
                "ci": "D2X CLI (d2x)",
            }

            # runtime._load_project_config()
            project_config = runtime.project_config_cls(
                runtime.universal_config, repo_info=repo_info
            )
            project_config.set_keychain(runtime.keychain)
            project_config._add_tasks_directory_to_python_path()

            # Create scratch org if needed
            if d2x_job["scratch_create_request"] and not (
                retry_scratch or d2x_job["access_token"]
            ):
                scratch_create_request = d2x_job["scratch_create_request"]
                org_name = scratch_create_request["org_name"] + "-" + job_id
                scratch_alias = f"{project_config.lookup('project__name')}__{org_name}"

                # If the scratch create request is pending or retry_scratch is set, create the scratch org
                if scratch_create_request["status"] == "pending" or retry_scratch:
                    scratch_config = {
                        "config_file": scratch_create_request["scratchdef_path"],
                    }
                    logger.info(f"Creating scratch org {org_name}...")

                    if d2x_job["devhub_access_token"]:
                        devhub_org = import_worker_org(
                            worker_api=worker_api,
                            keychain=runtime.keychain,
                            org_name=devhub_alias,
                            access_token=d2x_job["devhub_access_token"],
                            instance_url=d2x_job["devhub_instance_url"],
                        )
                    else:
                        devhub_alias = None
                    org = create_scratch_org(
                        runtime.keychain,
                        project_config,
                        org_name,
                        scratch_create_request["cumulusci_config_name"],
                        days=scratch_create_request["days"],
                        devhub=devhub_alias,
                    )
                    scratch_created = True
                    logger.info(f"Scratch org {org_name} created successfully.")
                else:
                    raise ValueError(
                        f"Scratch create request is already completed with status {scratch_create_request['status']}. Use --retry-scratch to retry creating the scratch org."
                    )

                # If the scratch org was created, complete the scratch create request to record the org in D2X Cloud
                if scratch_create_request["status"] == "pending" or scratch_created:
                    complete_scratch_create_request(
                        worker_api, signing_key, logger, scratch_create_request, org
                    )
                try:
                    org = runtime.keychain.get_org(org_name)
                except OrgNotFound:
                    raise OrgNotFound(f"Org {org_name} not found in the local keychain")
            # Org credentials available?
            elif d2x_job["access_token"]:
                org = import_worker_org(
                    worker_api=worker_api,
                    keychain=runtime.keychain,
                    org_name=org_name,
                    access_token=d2x_job["access_token"],
                    instance_url=d2x_job["instance_url"],
                )

                org_imported = True
                logger.info(f"Org {org_name} imported into local keychain.")
            else:
                raise ValueError(
                    "Job does not have a scratch create request or org user id"
                )
            # We have an org!

            # Get the steps
            steps = None
            if d2x_job["plan_version"]:
                raise NotImplementedError(
                    "Running a job with a plan version is not supported"
                )
            else:
                steps = d2x_job["run"]

            # Install resolved dependencies if provided
            dependency_steps = []
            if d2x_job["dependency_resolution_request"]:
                # FIXME: Wait until dependencies are resolved
                # while d2x_job["dependency_resolution_request"]["status"] in ["pending", "in_progress"]:
                #     sleep(5)
                #     d2x_job = worker_api.job_status(job_id)

                dependency_steps = prepare_dependencies(
                    job_id,
                    d2x_job["dependency_resolution_request"]["result"]["dependencies"],
                    project_config,
                )

            step_specs = []
            for i, step in enumerate(steps):
                step_config = step.get("config", {})
                step_type = step_config.get("type")
                if step_type == "cumulusci_task_class":
                    step_specs.append(
                        StepSpec(
                            step_num=str(i + 1),
                            task_name=step["name"],
                            task_class=import_global(step_config.get("task_path")),
                            task_config={
                                "options": step_config.get("options", {}),
                                "checks": [],
                            },
                            project_config=project_config,
                        )
                    )
                elif step_type == "cumulusci_flow":
                    coordinator = runtime.get_flow(step["config"]["flow"])
                    for flow_step in coordinator.steps:
                        flow_step.step_num = f"{i + 1}.{flow_step.step_num}"
                        # flow_step.source = step["key"]
                        print(f"Flow step: {flow_step}")
                        if (
                            flow_step.task_class == UpdateDependencies
                            and flow_step.task_config.get("options", {}).get(
                                "dependencies"
                            )
                            in [None, "$project__dependencies"]
                        ):
                            for dependency_step in dependency_steps:
                                dep_step_num = (
                                    f"{flow_step.step_num}.{dependency_step['key']}"
                                )
                                step = StepSpec(
                                    step_num=dep_step_num,
                                    task_name=dependency_step["name"],
                                    project_config=project_config,
                                    task_class=import_global(
                                        dependency_step["config"]["task_class"]
                                    ),
                                    task_config={
                                        "options": dependency_step["config"]["options"],
                                    },
                                )
                                step_specs.append(step)
                        else:
                            step_specs.append(flow_step)
                elif step_type == "cumulusci_task":
                    task_config = step.get("config", {})
                    task_name = task_config.get("task")
                    task_class = task_config.get("task_class")
                    task_options = task_config.get("options", {})
                    config_task_options = project_config.get_task(task_name)
                    if not config_task_options:
                        raise CumulusCIUsageError(
                            f"Task {task_name} not found in project config"
                        )
                    task_options = merge_config(
                        (config_task_options.get("options", {}), task_options)
                    )

                    step_spec = {
                        "step_num": str(i + 1),
                        "task_name": task_name,
                        "task_class": import_global(task_class),
                        "task_config": {
                            "options": task_options,
                            "checks": [],
                        },
                    }
                    step_specs.append(
                        StepSpec(
                            **step_spec,
                            project_config=project_config,
                        )
                    )
                elif step_type == "salesforce_cli":
                    step_spec = {
                        "step_num": str(i + 1),
                        "task_name": "dx",
                        "task_class": "cumulusci.tasks.sfdx.SFDXOrgTask",
                        "task_config": {
                            "options": {
                                "command": step["command"],
                                "description": step["description"],
                            },
                            "checks": [],
                        },
                    }
                    for option, value in step["options"].items():
                        if len(option["name"]) == 1:
                            flag_name = "-" + option["name"]
                        else:
                            flag_name = "--" + option["name"].replace("_", "-")
                        step_spec["task_config"]["options"][
                            "command"
                        ] += f' {flag_name} "{value}"'
                    step_specs.append(
                        StepSpec(
                            **step_spec,
                            project_config=project_config,
                        )
                    )
                else:
                    raise NotImplementedError(
                        f"Step type {step['type']} is not supported"
                    )

            flow_callback = D2XFlowCallback(worker_api, signing_key, job_id, org)
            # Initialize a FlowCoordinator
            flow = FlowCoordinator.from_steps(
                project_config,
                step_specs,
                callbacks=flow_callback,
            )

            try:
                # Disable CumulusCI's oauth token refresh
                os.environ["CUMULUSCI_DISABLE_REFRESH"] = "True"
                # Run the flow
                flow.run(org)
            except Exception as exc:
                logger.error(f"Job {job_id} failed with error:\n{exc}")
                exception = exc
                flow_callback.cleanup()
    finally:
        # Clean up
        if scratch_created:
            remove_worker_org(org_name, runtime.keychain)
        if devhub_org:
            remove_worker_org(devhub_alias, runtime.keychain)

        if exception:
            if flow_callback:
                flow_callback.cleanup()
                flow_callback.log(
                    message=f"Job {job_id} failed",
                    status="failed",
                    exception=exception,
                )
            else:
                worker_api.job_status_update(
                    signing_key,
                    job_id,
                    status="failed",
                    log=None,
                    exception=str(exception),
                )
        else:
            if flow_callback:
                flow_callback.cleanup()
                flow_callback.log(
                    message=f"Job {job_id} completed",
                    status="success",
                )
            else:
                worker_api.job_status_update(
                    signing_key,
                    job_id,
                    status="success",
                    log=None,
                    exception=None,
                )


async def convert_url_to_websocket(url):
    return url.replace("http://", "ws://").replace("https://", "wss://")


@job.command(name="log", help="Stream logs from a job")
@click.argument("job_id")
@pass_runtime(require_project=False, require_keychain=True)
def log(runtime, job_id):
    d2x_api = get_d2x_api_client(runtime)
    job = d2x_api.read(D2XApiObjects.Job, job_id)
    if not job:
        raise click.UsageError(f"Job {job_id} not found in D2X Cloud")
    asyncio.run(log_async(runtime, job_id))


@job.command(name="steps", help="List the steps in a job")
@click.argument("job_id")
@pass_runtime(require_project=False, require_keychain=True)
def steps(runtime, job_id):
    d2x_api = get_d2x_api_client(runtime)
    job = d2x_api.read(D2XApiObjects.Job, job_id)
    if not job:
        raise click.UsageError(f"Job {job_id} not found in D2X Cloud")
    steps = json.loads(job["steps"])
    table = display_job_summary(steps)
    click.echo(table)


async def log_async(runtime, job_id):
    d2x_service = runtime.project_config.keychain.get_service("d2x")
    websocket_uri = await convert_url_to_websocket(
        runtime.keychain.get_service("d2x").config["base_url"]
        + f"/d2x/{d2x_service.tenant}/jobs/{job_id}/log"
    )
    await listen_to_socket(
        job_id,
        d2x_service.tenant,
        websocket_uri,
        d2x_service.token,
    )


def exclude_keys_from_dicts(list_of_dicts, keys_to_exclude):
    return [
        {k: v for k, v in d.items() if k not in keys_to_exclude} for d in list_of_dicts
    ]


@job.command(name="list", help="List all jobs")
@click.option(
    "--status",
    help="Filter jobs by status. Set multiple statuses using a comma-separated list. Valid statuses are 'queued', 'in_progress', 'failed', 'success', 'cancelled'",
)
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime, status):
    d2x_api = get_d2x_api_client(runtime)
    status = [s.strip() for s in status.split(",")] if status else None
    jobs = []
    for job in d2x_api.list(
        D2XApiObjects.Job,
        params={"status__in": status, "repo__name": runtime.project_config.repo_name},
    ):
        jobs.append(
            {
                "id": job["id"],
                "status": job["status"],
                "repo": job["repo"]["org"]["name"] + "/" + job["repo"]["name"],
            }
        )

    api_list_to_table(jobs)
