import asyncio
import code
import contextlib
import pdb
import runpy
import sys
import traceback

import rich_click as click
import requests
import rich
from rich.console import Console
from rich.markup import escape

from cumulusci.core.debug import set_debug_mode
from cumulusci.core.exceptions import CumulusCIUsageError
from cumulusci.utils import get_cci_upgrade_command
from cumulusci.utils.http.requests_utils import init_requests_trust
from cumulusci.utils.logging import tee_stdout_stderr

from d2x_cli.logger import get_tempfile_logger, init_logger
from cumulusci.cli.utils import warn_if_no_long_paths

import d2x_cli
from .commands.service import service
from .commands.github import github
from .commands.job import job
from .commands.org import org
from .commands.plan import plan
from .commands.repo import repo
from .commands.scratch import scratch
from .commands.test import test
from .runtime import CliRuntime, pass_runtime
from .utils import (
    check_latest_version,
    get_installed_version,
    get_latest_final_version,
)

SUGGEST_ERROR_COMMAND = (
    """Run this command for more information about debugging errors: cci error --help"""
)

USAGE_ERRORS = (CumulusCIUsageError, click.UsageError)


#
# Root command
#
def main(args=None):
    """Main D2X CLI entry point.

    This runs as the first step in processing any CLI command.

    This wraps the `click` library in order to do some initialization and centralized error handling.
    """
    with contextlib.ExitStack() as stack:
        args = args or sys.argv

        # (If enabled) set up requests to validate certs using system CA certs instead of certifi
        init_requests_trust()

        # Check for updates _unless_ we've been asked to output JSON,
        # or if we're going to check anyway as part of the `version` command.
        is_version_command = len(args) > 1 and args[1] == "version"
        if "--json" not in args and not is_version_command:
            check_latest_version()

        # Only create logfiles for commands that are not `cci error`
        is_error_command = len(args) > 2 and args[1] == "error"
        tempfile_path = None
        if not is_error_command:
            logger, tempfile_path = get_tempfile_logger()
            stack.enter_context(tee_stdout_stderr(args, logger, tempfile_path))

        debug = "--debug" in args
        if debug:
            args.remove("--debug")

        with set_debug_mode(debug):
            try:
                runtime = CliRuntime(load_keychain=False)
            except Exception as e:
                handle_exception(e, is_error_command, tempfile_path, debug)
                sys.exit(1)

            runtime.check_d2x_version()
            should_show_stacktraces = runtime.universal_config.cli__show_stacktraces

            init_logger(debug=debug)
            # Hand CLI processing over to click, but handle exceptions
            try:
                cli(args[1:], standalone_mode=False, obj=runtime)
            except click.Abort:  # Keyboard interrupt
                console = Console()
                show_debug_info() if debug else console.print("\n[red bold]Aborted!")
                sys.exit(1)
            except Exception as e:
                if debug:
                    show_debug_info()
                else:
                    handle_exception(
                        e,
                        is_error_command,
                        tempfile_path,
                        should_show_stacktraces,
                    )
                sys.exit(1)


def handle_exception(
    error,
    is_error_cmd,
    logfile_path,
    should_show_stacktraces=False,
):
    """Displays error of appropriate message back to user, prompts user to investigate further
    with `cci error` commands, and writes the traceback to the latest logfile.
    """
    error_console = Console(stderr=True)
    if isinstance(error, requests.exceptions.ConnectionError):
        connection_error_message(error_console)
    elif isinstance(error, click.ClickException):
        error_console.print(f"[red bold]Error: {escape(error.format_message())}")
    else:
        # We call str ourselves to make Typeguard shut up.
        error_console.print(f"[red bold]Error: {escape(str(error))}")
    # Only suggest gist command if it wasn't run
    if not is_error_cmd:
        error_console.print(f"[yellow]{SUGGEST_ERROR_COMMAND}")

    # This is None if we're handling an exception for a `cci error` command.
    if logfile_path:
        with open(logfile_path, "a") as log_file:
            traceback.print_exc(file=log_file)  # log stacktrace silently

    if should_show_stacktraces and not isinstance(error, USAGE_ERRORS):
        error_console.print_exception()


def connection_error_message(console: Console):
    message = (
        "We encountered an error with your internet connection. "
        "Please check your connection and try the last cci command again."
    )
    console.print(f"[red bold]{message}")


def show_debug_info():
    """Displays the traceback and opens pdb"""
    traceback.print_exc()
    pdb.post_mortem()


def show_version_info():
    console = rich.get_console()
    console.print(f"D2X version: {d2x_cli.__version__} ({sys.argv[0]})")
    console.print(f"Python version: {sys.version.split()[0]} ({sys.executable})")
    console.print()
    warn_if_no_long_paths(console=console)

    current_version = get_installed_version()
    latest_version = get_latest_final_version()

    if not latest_version > current_version:
        console.print("You have the latest version of D2X CLI :sun_behind_cloud:\n")
        display_release_notes_link(str(latest_version))
        return

    console.print(
        f"[yellow]There is a newer version of D2X CLI available: {str(latest_version)}"
    )
    console.print(f"To upgrade, run `{get_cci_upgrade_command()}`")
    display_release_notes_link(str(latest_version))


def display_release_notes_link(latest_version: str) -> None:
    """Provide a link to the latest D2X CLI Release Notes"""
    release_notes_link = (
        f"https://github.com/muselab-d2x/d2x-cli/releases/tag/v{latest_version}"
    )
    console = rich.get_console()
    console.print(
        f"See the latest D2X CLI Release Notes: [link={release_notes_link}]{release_notes_link}[/link]"
    )


def version_info_wrapper(
    ctx: click.Context, param: click.Parameter, value: bool
) -> None:
    if not value:
        return
    show_version_info()
    ctx.exit()


@click.group("main", help="")
@click.option(  # based on https://click.palletsprojects.com/en/8.1.x/options/#callbacks-and-eager-options
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    help="Show the version and exit.",
    callback=version_info_wrapper,
)
def cli():
    """Top-level `click` command group."""


@cli.command(name="version", help="Print the current version of D2X CLI")
def version():
    show_version_info()


@cli.command(name="shell", help="Drop into a Python shell")
@click.option("--script", help="Path to a script to run", type=click.Path())
@click.option("--python", help="Python code to run directly")
@pass_runtime(require_project=False, require_keychain=True)
def shell(runtime, script=None, python=None):
    # alias for backwards-compatibility
    variables = {
        "config": runtime,
        "runtime": runtime,
        "project_config": runtime.project_config,
    }
    if script:
        if python:
            raise click.UsageError("Cannot specify both --script and --python")
        runpy.run_path(script, init_globals=variables)
    elif python:
        exec(python, variables)
    else:
        code.interact(local=variables)


# Top Level Groups

# cli.add_command(error)
# cli.add_command(project)
cli.add_command(github)
cli.add_command(job)
cli.add_command(org)
cli.add_command(plan)
cli.add_command(repo)
cli.add_command(scratch)
cli.add_command(service)
cli.add_command(test)
# cli.add_command(task)
# cli.add_command(flow)
# cli.add_command(robot)

