import asyncio
import json
import logging
import os
import websockets
import rich_click as click
from tempfile import NamedTemporaryFile
from typing import Any, List, Optional, Text
from pydantic import BaseModel
from websockets.http import Headers
from rich.console import Console
from rich.live import Live
from rich.progress import Progress
from rich.status import Status
from cumulusci.core.config import FlowConfig, TaskConfig
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
from cumulusci.core.utils import import_global
from cumulusci.utils import cd
from d2x_cli.runtime import pass_runtime, CliRuntime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
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
        self.progress.refresh()

    def finish(self, result):
        self.result = result
        self.is_finished = True
        # self.progress.update(self.progress_task, progress=100)
        self.progress.refresh()
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


class D2XFlowCallback(FlowCallback):
    def __init__(self, d2x, job_id, logger):
        self.progress = Progress()
        # self.live = Live(auto_refresh=False)
        self.console = Console()
        self.logger = logger
        self.d2x = d2x
        self.job_id = job_id
        self.status = None
        self.steps = {}

    def pre_flow(self, coordinator: FlowCoordinator):
        for step in coordinator.steps:
            self.steps[step.step_num] = StepProgress(
                progress=self.progress,
                step=step,
                result=None,
                # progress_task=self.progress.add_task(
                #    step.task_name, total=None, start=False
                # ),
            )
        self.log(f"Starting job {self.job_id}\nSteps:\n{nl.join(self.steps)}")
        self.progress.refresh()
        self.progress.start()
        self.progress.refresh()

    def pre_task(self, step: StepSpec):
        self.log(f"Starting task {step.task_name}")
        step_progress: StepProgress = self.steps[step.step_num]
        step_progress.start()

    def post_task(self, step: StepSpec, result: StepResult):
        self.log(f"Task {step.task_name} completed")
        step_progress: StepProgress = self.steps[step.step_num]
        step_progress.finish(result)

    def log(self, message, status=None, exception=None):
        result = self.d2x.create(
            D2XApiObjects.Job,
            data={
                "log": str(message),
                "exception": str(exception),
                "status": status if status else "in_progress",
            },
            extra_path=f"{self.job_id}/status",
        )
        self.logger.info(message)
        # self.live.update(message)


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
    runtime: CliRuntime,
    org_name: str,
    config_name: str,
    days: Optional[int] = None,
    set_password: Optional[bool] = None,
    prerelease: bool = None,
    namespaced: bool = None,
):
    """Adds/Updates a scratch org config to the keychain from a named config"""
    scratch_config = runtime.project_config.lookup(f"orgs__scratch__{config_name}")
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

    scratch_config["sfdx_alias"] = f"{runtime.project_config.project__name}__{org_name}"
    org_config = ScratchOrgConfig(
        scratch_config, org_name, keychain=runtime.keychain, global_org=False
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
@click.option(
    "--orgs", "-o", help="The list of orgs to run the job against, separated by commas"
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
def create(runtime, plan, plan_version, flow, task, orgs, scratch_org, local, remote):
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

    # Ensure that orgs or scratch_org is specified
    if not orgs and not scratch_org:
        raise click.UsageError(
            "You must specify either orgs or a scratch org to create a job"
        )

    # Ensure that orgs and scratch_org are not both specified
    if orgs and scratch_org:
        raise click.UsageError(
            "You cannot specify both orgs and a scratch org to create a job"
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
                "steps": [{1: {"task": task}}],
            }
            flow = "d2x_single_task"

        # Get the flow config
        flow_config = runtime.project_config.get_flow(flow)

        steps = _freeze_steps(runtime.project_config, flow_config)

    d2x = get_d2x_api_client(runtime)

    # Look up orgs by name
    orgs = [org.strip() for org in orgs.split(",")] if orgs else []
    orgs_map = {}
    d2x_orgs = d2x.list(D2XApiObjects.Org)
    for org in orgs:
        orgs_map[org] = next((o for o in d2x_orgs if o["name"] == org), None)
        if not orgs_map[org]:
            raise click.UsageError(f"Org '{org}' not found in D2X Cloud")

    # Prepare scratch create request
    scratch_org_request = None
    if scratch_org:
        scratch_config = runtime.project_config.lookup(f"orgs__scratch__{scratch_org}")
        if not scratch_config:
            raise click.UsageError(
                f"Scratch org '{scratch_org}' not found in CumulusCI project config"
            )
        scratch_org_request = {
            "org_name": "job-org",  # FIXME: Make this dynamic?
            "scratchdef_path": scratch_config["config_file"],
            "cumulusci_config_name": scratch_org,
        }

    # Look up plan
    plan_version_id = None
    if plan:
        plan = next(
            (p for p in d2x.list(D2XApiObjects.Plan) if p["slug"] == plan), None
        )
        if not plan:
            raise click.UsageError(f"Plan '{plan}' not found in D2X Cloud")

        # Look up plan version
        if plan_version:
            result = d2x.read(D2XApiObjects.PlanVersion, plan_version)
            if not result:
                raise click.UsageError(
                    f"Plan version '{plan_version}' not found in D2X Cloud"
                )
            plan_version_id = result["id"]
        else:
            plan_versions = d2x.list(
                D2XApiObjects.PlanVersion, parents={"plan_id": plan["id"]}
            )
            plan_version_id = plan_versions[0]["id"]  # FIXME: Sorting?

    # Create the Scratch Create Request if needed
    scratch_create_request_id = None
    if scratch_org_request:
        response = d2x.create(D2XApiObjects.ScratchCreateRequest, scratch_org_request)
        scratch_create_request_id = response["id"]

    # Create the job
    job_data = {
        "plan_version_id": plan_version_id,
        "orgs": orgs_map,
        "steps": json.dumps(steps),
        "scratch_create_request_id": scratch_create_request_id,
    }
    job = d2x.create(
        D2XApiObjects.Job,
        job_data,
    )

    click.echo(f"Job {job['id']} created")

    if local:
        run_job(runtime, job["id"])


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
    d2x = get_d2x_api_client(runtime)
    d2x_job = d2x.read(D2XApiObjects.Job, job_id)
    org = None
    exception = None
    flow_callback: D2XFlowCallback = None
    logger = logging.getLogger("d2x")

    try:
        # Handle Scratch Create Request
        if d2x_job["scratch_create_request_id"]:
            scratch_create_request = d2x.read(
                D2XApiObjects.ScratchCreateRequest, d2x_job["scratch_create_request_id"]
            )
            org_name = scratch_create_request["org_name"] + "-" + d2x_job["id"]
            scratch_alias = (
                f"{runtime.project_config.lookup('project__name')}__{org_name}"
            )
            scratch_created = False

            # If the scratch create request is completed, use its org
            if scratch_create_request["status"] == "success":
                d2x_org = d2x.read(
                    D2XApiObjects.Org,
                    scratch_create_request["org_id"],
                )
                d2x_org_user = d2x.read(
                    D2XApiObjects.OrgUser,
                    scratch_create_request["org_user_id"],
                )
                try:
                    org = runtime.keychain.get_org(org_name)
                    if org.org_id != d2x_org["salesforce_id"]:
                        raise ValueError(
                            f"Org named {org_name} already exists in the local keychain but is pointing to a different org ({org.org_id}) than the D2X Cloud org {d2x_org['org_id']}."
                        )
                    if org.username != d2x_org_user["username"]:
                        raise ValueError(
                            f"Org named {org_name} already exists in the local keychain but is pointing to a different user ({org.username}) than the D2X Cloud org {d2x_org_user['username']}."
                        )
                    logger.info(
                        f"Found existing org in keychain named {org_name} with matching org id and username. Using it."
                    )
                except OrgNotFound:
                    logger.info(
                        f"Org not found in local keychain, attempting to import it..."
                    )
                    # Get the org user credential
                    org_user_credentials = d2x.list(
                        D2XApiObjects.OrgUserCredential, d2x_org_user["id"]
                    )

                    # Import the credential into the sfdx keychain
                    sfdx_auth_url = None
                    for cred in org_user_credentials:
                        sfdx_auth_url = cred["sfdx_auth_url"]
                        if sfdx_auth_url is not None:
                            break
                    with NamedTemporaryFile(delete=False) as f:
                        f.write(sfdx_auth_url.encode("utf-8"))
                        temp_file_name = f.name
                    try:
                        p = sfdx(
                            f"force:auth:sfdxurl:store -f {temp_file_name} -a {scratch_alias} --json"
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
                            pass

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
                            {"username": d2x_org_user["username"], "sfdx": True},
                            org_name,
                            runtime.keychain,
                            global_org=False,
                        )
                        org_config.save()

                        logger.info(
                            f"Org {org_name} imported into local keychain successfully."
                        )

                    finally:
                        os.remove(temp_file_name)

            # If the scratch create request is pending or retry_scratch is set, create the scratch org
            elif scratch_create_request["status"] == "pending" or retry_scratch:
                scratch_config = {
                    "config_file": scratch_create_request["scratchdef_path"],
                }
                try:
                    logger.info(
                        "Found existing org in keychain named {org_name}. Using it."
                    )
                    org = runtime.keychain.get_org(org_name)
                except OrgNotFound:
                    logger.info(f"Creating scratch org {org_name}...")
                    org = create_scratch_org(
                        runtime,
                        org_name,
                        scratch_create_request["cumulusci_config_name"],
                    )
                    scratch_created = True
                    logger.info(f"Scratch org {org_name} created successfully.")
            else:
                raise ValueError(
                    f"Scratch create request is already completed with status {scratch_create_request['status']}. Use --retry-scratch to retry creating the scratch org."
                )

            # If the scratch org was created, complete the scratch create request to record the org in D2X Cloud
            if scratch_create_request["status"] == "pending" or scratch_created:
                logger.info(
                    f"Completing scratch create request {scratch_create_request['id']}"
                )
                p = sfdx(
                    f"force:org:display --json -u {scratch_alias} --verbose --json"
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
                    org_id = org_info["result"]["accessToken"].split("!")[0]

                sfdx_auth_url = org_info["result"]["sfdxAuthUrl"]
                complete_data = {
                    "sfdx_auth_url": sfdx_auth_url,
                    "org_id": org.org_id,
                    "user_id": org.user_id,
                    "username": org.username,
                    "instance_url": org.instance_url,
                }

                # Complete the scratch create request
                scratch_create_request = d2x.create(
                    D2XApiObjects.ScratchCreateRequest,
                    data=complete_data,
                    extra_path=f"{scratch_create_request['id']}/complete",
                )
                logger.info(
                    f"Scratch create request {scratch_create_request['id']} completed successfully."
                )
            try:
                org = runtime.keychain.get_org(org_name)
            except OrgNotFound:
                raise OrgNotFound(f"Org {org_name} not found in the local keychain")

        # We have an org!

        # Get the steps
        if d2x_job["plan_version_id"]:
            raise NotImplementedError(
                "Running a job with a plan version is not supported"
            )
        else:
            steps = json.loads(d2x_job["steps"])

        step_specs = []
        for step in steps:
            if (
                step["name"] == "dx_convert_from"
            ):  # FIXME: This is a hack to skip the dx_convert_from step, remove it
                print("%%%% SKIPPING dx_convert_from step %%%%")
                continue
            step_specs.append(
                StepSpec(
                    step_num=step["step_num"],
                    task_name=step["name"],
                    task_config=step.get("task_config", {}),
                    task_class=import_global(step["task_class"]),
                    project_config=runtime.project_config,
                )
            )

        flow_callback = D2XFlowCallback(d2x, job_id, logger)
        # Initialize a FlowCoordinator
        flow = FlowCoordinator.from_steps(
            runtime.project_config,
            step_specs,
            callbacks=flow_callback,
        )
        # flow_logger = setup_logging(flow.logger, job["id"], tenant, websocket_uri, token)

        try:
            # Run the flow
            flow.run(org)
        except Exception as exc:
            logger.error(f"Job {job_id} failed: {exception}")
            exception = exc
    finally:
        if exception:
            if flow_callback:
                flow_callback.log(
                    message=f"Job {job_id} failed",
                    status="failed",
                    exception=exception,
                )
            else:
                d2x.create(
                    D2XApiObjects.Job,
                    job_id,
                    {"status": "failed", "log": None, "exception": str(exception)},
                    extra_path=f"{job_id}/status",
                )
        else:
            if flow_callback:
                flow_callback.log(
                    message=f"Job {job_id} completed",
                    status="success",
                )
            else:
                d2x.create(
                    D2XApiObjects.Job,
                    job_id,
                    {"status": "success", "log": None, "exception": None},
                    extra_path=f"{job_id}/status",
                )


async def convert_url_to_websocket(url):
    return url.replace("http://", "ws://").replace("https://", "wss://")


@job.command(name="log", help="Stream logs from a job")
@click.argument("job_id")
@pass_runtime(require_project=False, require_keychain=True)
def log(runtime, job_id):
    d2x = get_d2x_api_client(runtime)
    job = d2x.read(D2XApiObjects.Job, job_id)
    if not job:
        raise click.UsageError(f"Job {job_id} not found in D2X Cloud")
    asyncio.run(log_async(runtime, job_id))


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
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime):
    d2x = get_d2x_api_client(runtime)
    api_list_to_table(
        exclude_keys_from_dicts(d2x.list(D2XApiObjects.Job), ["steps", "log"])
    )
