import asyncio
import json
import logging
import websockets
import rich_click as click
from websockets.http import Headers
from cumulusci.utils import cd
from cumulusci.core.config import FlowConfig, TaskConfig
from cumulusci.core.sfdx import sfdx
from cumulusci.core.config.project_config import BaseProjectConfig
from cumulusci.core.github import get_github_api_for_repo
from cumulusci.core.exceptions import OrgNotFound
from cumulusci.core.flowrunner import FlowCoordinator, StepSpec
from cumulusci.core.utils import import_global
from d2x_cli.runtime import pass_runtime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


class WebSocketLogHandler(logging.Handler):
    def __init__(self, websocket_uri, token):
        super().__init__()
        self.websocket_uri = websocket_uri
        self.loop = asyncio.get_event_loop()
        self.token = token
        self.queue = asyncio.Queue()

        # Start the worker task
        self.loop.create_task(self.worker())

        # Send an initial log
        self.emit(
            logging.LogRecord(
                name="WebSocketLogHandler",
                level=logging.INFO,
                pathname=__file__,
                lineno=0,
                msg="WebSocketLogHandler initialized",
                args=None,
                exc_info=None,
            )
        )

    async def send_log(self, record):
        headers = Headers({"Authorization": f"Bearer {self.token}"})
        async with websockets.connect(
            self.websocket_uri, extra_headers=headers
        ) as websocket:
            await websocket.send(self.format(record))

    def emit(self, record):
        self.loop.create_task(self.queue.put(record))

    async def worker(self):
        while True:
            # Wait for a log record to be added to the queue
            record = await self.queue.get()

            # Send the log record
            await self.send_log(record)

            # Mark the task as done
            self.queue.task_done()


def setup_logging(job_id, tenant, websocket_uri, token):
    logger = logging.getLogger("cumulusci")
    logger.setLevel(logging.INFO)

    ws_handler = WebSocketLogHandler(
        f"{websocket_uri}/d2x/{tenant}/jobs/{job_id}/log?is_cli=true",
        token,
    )
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    ws_handler.setFormatter(formatter)

    logger.addHandler(ws_handler)
    return logger


async def listen_to_socket(job_id, tenant, websocket_uri, token):
    headers = Headers({"Authorization": f"Bearer {token}"})
    async with websockets.connect(
        f"{websocket_uri}/d2x/{tenant}/jobs/{job_id}/log",
        extra_headers=headers,
    ) as websocket:
        while True:
            message = await websocket.recv()
            print(message)


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
    click.echo(f"Prepared steps:\n  {json.dumps(steps, indent=4)}")

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
        print(response)
        scratch_create_request_id = response["id"]

    # Create the job
    job_data = {
        "plan_version_id": plan_version_id,
        "orgs": orgs_map,
        "steps": json.dumps(steps),
        "scratch_create_request_id": scratch_create_request_id,
    }
    print(job_data)
    job = d2x.create(
        D2XApiObjects.Job,
        job_data,
    )
    print(job)

    if local:
        run_job(runtime, job["id"])


@job.command(name="run", help="Run a queued job locally")
@click.argument("job_id")
@click.option(
    "--retry-scratch",
    is_flag=True,
    help="Retry the scratch org creation if it previously failed",
)
@pass_runtime(require_project=False, require_keychain=True)
def run_job(runtime, job_id, retry_scratch=False):
    d2x = get_d2x_api_client(runtime)
    job = d2x.read(D2XApiObjects.Job, job_id)
    org = None

    try:
        org = asyncio.run(run_job_async(d2x, runtime, job, retry_scratch))
    finally:
        # Store the org if it was created
        if org:
            runtime.keychain.set_org(org)


async def run_job_async(d2x, runtime, job):
    # Set up logging and WebSocket listening
    base_url = runtime.keychain.get_service("d2x").config["base_url"]
    tenant = runtime.keychain.get_service("d2x").config["tenant"]
    token = runtime.keychain.get_service("d2x").config["token"]
    websocket_uri = base_url.replace("http://", "ws://").replace("https://", "wss://")
    logger = setup_logging(job["id"], tenant, websocket_uri, token)
    listen_task = asyncio.create_task(
        listen_to_socket(job["id"], tenant, websocket_uri, token)
    )

    # try:
    # Handle Scratch Create Request
    if job["scratch_create_request_id"]:
        scratch_create_request = await d2x.read_async(
            D2XApiObjects.ScratchCreateRequest, job["scratch_create_request_id"]
        )
        org_name = scratch_create_request["org_name"] + "-" + job["id"]
        scratch_alias = f"{runtime.project_config.lookup('project__name')}__{org_name}"
        # If the scratch create request is completed, use its org
        if scratch_create_request["status"] == "success":
            d2x_org = d2x.read_async(
                D2XApiObjects.Org, scratch_create_request["org_id"]
            )
            d2x_org_user = d2x.read_async(
                D2XApiObjects.OrgUser, scratch_create_request["org_user_id"]
            )
            try:
                org = runtime.keychain.get_org(org_name)
                if org.org_id != d2x_org["org_id"]:
                    raise ValueError(
                        f"Org named {org_name} already exists in the local keychain but is pointing to a different org ({org.org_id}) than the D2X Cloud org {d2x_org['org_id']}."
                    )
                if org.username != d2x_org_user["username"]:
                    raise ValueError(
                        f"Org named {org_name} already exists in the local keychain but is pointing to a different user ({org.username}) than the D2X Cloud org {d2x_org_user['username']}."
                    )
            except OrgNotFound:
                org_user_credentials = d2x.list_async(
                    D2XApiObjects.OrgUserCredential, d2x_org_user["id"]
                )
                sfdx_auth_url = None
                for cred in org_user_credentials:
                    if cred["credential_type"] == "sfdx_auth_url":
                        sfdx_auth_url = cred["credential"]
                        break
                with TemporaryFile() as f:
                    f.write(sfdx_auth_url)
                    sfdx_result = sfdx(
                        f"force:auth:sfdxurl:store -f {f.name} -a {scratch_alias} --json"
                    )
        elif scratch_create_request["status"] == "pending" or retry_scratch:
            scratch_config = {
                "config_file": scratch_create_request["scratchdef_path"],
            }
            try:
                org = runtime.keychain.get_org(org_name)
            except OrgNotFound:
                loop = asyncio.get_event_loop()
                future = loop.run_in_executor(
                    None,
                    runtime.keychain.create_scratch_org,
                    org_name,
                    "dev",
                    scratch_config,
                )
                org = await future
                # Get the sfdx auth url
            sfdx_info = sfdx(f"force:org:display --json -u {org_name} --verbose --json")
            sfdx_auth_url = sfdx_info["result"]["sfdxAuthUrl"]
            create_data = {
                "sfdx_auth_url": sfdx_auth_url,
                "org_id": org.org_id,
                "user_id": org.user_id,
                "username": org.username,
                "user_alias": org.user_alias,
                "instance_url": org.instance_url,
            }
            # Post the auth url to the scratch create request complete endpoint
            await d2x.create_async(
                D2XApiObjects.ScratchCreateRequest,
                data=create_data,
                extra_path=f"{scratch_create_request['id']}/complete",
            )
        else:
            raise ValueError(
                f"Scratch create request is already completed with status {scratch_create_request['status']}. Use --retry-scratch to retry creating the scratch org."
            )

    # Get the steps
    if job["plan_version_id"]:
        raise NotImplementedError("Running a job with a plan version is not supported")
    else:
        steps = json.loads(job["steps"])

    step_specs = []
    for step in steps:
        step_specs.append(
            StepSpec(
                step_num=step["step_num"],
                task_name=step["name"],
                task_config=step.get("task_config", {}),
                task_class=import_global(step["task_class"]),
                project_config=runtime.project_config,
            )
        )
    # Initialize a FlowCoordinator
    flow = FlowCoordinator.from_steps(
        runtime.project_config,
        step_specs,
    )

    # Run the flow
    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(None, flow.run, org)

    result = await future
    # finally:
    #    # Clean up logging and WebSocket listening
    #    logger.removeHandler(logger.handlers[0])
    #    return org


def exclude_keys_from_dicts(list_of_dicts, keys_to_exclude):
    return [
        {k: v for k, v in d.items() if k not in keys_to_exclude} for d in list_of_dicts
    ]


@job.command(name="list", help="List all jobs")
@pass_runtime(require_project=True, require_keychain=True)
def list(runtime):
    d2x = get_d2x_api_client(runtime)
    api_list_to_table(exclude_keys_from_dicts(d2x.list(D2XApiObjects.Job), ["steps"]))
