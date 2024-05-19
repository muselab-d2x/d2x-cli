import json
import textwrap
from typing import List, Optional
from pydantic import BaseModel
import rich_click as click
from rich.console import Console
from cumulusci.cli.ui import CliTable
from cumulusci.utils import get_task_option_info, get_command_syntax
from cumulusci.cli.utils import group_items
from d2x_cli.runtime import pass_runtime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects
from d2x_cli.utils import api_list_to_table


class TaskOptionInfo(BaseModel):
    name: str
    usage: Optional[str]
    description: Optional[str]
    default: Optional[str]
    required: Optional[bool]
    option_type: Optional[str]


class TaskInfo(BaseModel):
    task_name: str
    description: Optional[str]
    class_path: str
    task_docs: Optional[str]
    command_syntax: str
    options: List[TaskOptionInfo]


@click.group("doc", help="")
def doc():
    """Top-level `click` command group for dynamically generating and interacting with project documentation."""
    pass


@doc.command(name="tasks", help="Create documentation for the tasks in this project.")
@click.option("--group", multiple=True, help="The group(s) to which the task belongs")
@click.option(
    "--json",
    "print_json",
    is_flag=True,
    help="Output the task documentation data as JSON",
)
@pass_runtime(require_project=True, require_keychain=True)
def doc_tasks(runtime, group, print_json):
    tasks_info = {}
    for task_name, task_config in runtime.project_config.lookup("tasks").items():
        tasks_info.setdefault(task_config.get("group", "No Group"), {})[
            task_name
        ] = task_config

    console = Console()
    # Sort the tasks by group and then by name
    tasks_info = {
        k: dict(sorted((k, v) for k, v in v.items() if k is not None))
        for k, v in sorted(tasks_info.items())
        if k is not None
    }

    for group, tasks in tasks_info.items():
        task_models = [
            get_task_info(task_name, task) for task_name, task in tasks.items()
        ]
        tasks_info[group] = [task_model.dict() for task_model in task_models]

    if print_json:
        console.print_json(data=tasks_info)
        return None


def get_task_options_info(task_options):
    """Generate the 'Options' section for a given tasks documentation"""
    options_info = []
    for option, info in task_options.items():
        option_info = TaskOptionInfo(
            name=option,
            usage=info.get("usage"),
            description=info.get("description"),
            default=info.get("default"),
            required=info.get("required"),
            option_type=info.get("option_type"),
        )
        options_info.append(option_info)
    return options_info


def get_task_info(task_name, task_config, project_config=None, org_config=None):
    """Document a (project specific) task configuration in JSON format."""
    from cumulusci.core.utils import import_global

    task_class = import_global(task_config["class_path"])

    task_docs = None
    if "task_docs" in task_class.__dict__:
        task_docs = textwrap.dedent(task_class.task_docs.strip("\n"))

    task_option_info = get_task_options_info(task_class.task_options)

    command_syntax = get_command_syntax(task_name)

    task_info = TaskInfo(
        task_name=task_name,
        description=task_config.get("description"),
        class_path=task_config.get("class_path"),
        task_docs=task_docs,
        command_syntax=command_syntax,
        options=task_option_info,
    )

    return task_info
