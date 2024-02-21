import contextlib
import os
import re
import sys
import time
from collections import defaultdict

import click
import pkg_resources
import requests
from rich.console import Console
from rich.table import Table

from d2x_cli.__about__ import __version__
from cumulusci.core.config import UniversalConfig
from cumulusci.cli.utils import timestamp_file, FINAL_VERSION_RE
from cumulusci.utils import get_cci_upgrade_command
from cumulusci.utils.http.requests_utils import safe_json_from_response

LOWEST_SUPPORTED_VERSION = (0, 0, 1)
WIN_LONG_PATH_WARNING = """
WARNING: Long path support is not enabled. This can lead to errors with some
tasks. Your administrator will need to activate the "Enable Win32 long paths"
group policy, or set LongPathsEnabled to 1 in the registry key
HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\FileSystem.
"""


def is_final_release(version: str) -> bool:
    """Returns bool whether version string should be considered a final release.

    d2x-cli versions are considered final if they contain only digits and periods.
    e.g. 1.0.1 is final but 2.0b1 and 2.0.dev0 are not.
    """
    return bool(FINAL_VERSION_RE.match(version))


def get_latest_final_version():
    """return the latest version of d2x_cli in pypi, be defensive"""
    # use the pypi json api https://wiki.python.org/moin/PyPIJSON
    return pkg_resources.parse_version("0.0.1")
    res = safe_json_from_response(
        requests.get("https://pypi.org/pypi/d2x_cli/json", timeout=5)
    )
    with timestamp_file() as f:
        f.write(str(time.time()))
    versions = []
    for versionstring in res["releases"].keys():
        if not is_final_release(versionstring):
            continue
        versions.append(pkg_resources.parse_version(versionstring))
    versions.sort(reverse=True)
    return versions[0]


def check_latest_version():
    """checks for the latest version of d2x-api from pypi, max once per hour"""
    check = True

    with timestamp_file() as f:
        timestamp = float(f.read() or 0)
    delta = time.time() - timestamp
    check = delta > 3600

    if check:
        try:
            latest_version = get_latest_final_version()
        except requests.exceptions.RequestException as e:
            click.echo("Error checking cci version:", err=True)
            click.echo(str(e), err=True)
            return

        result = latest_version > get_installed_version()
        if result:
            click.echo(
                f"""An update to D2X CLI is available. To install the update, run this command: {get_cci_upgrade_command()}""",
                err=True,
            )

        if sys.version_info < LOWEST_SUPPORTED_VERSION:
            click.echo(
                "Sorry! Your Python version is not supported. Please upgrade to Python 3.9.",
                err=True,
            )


def get_installed_version():
    """returns the version name (e.g. 2.0.0b58) that is installed"""
    return pkg_resources.parse_version(__version__)


def api_list_to_table(items):
    """Convert a list of dictionaries to a rich Table."""
    if not items:
        return None
    table = Table()
    main_columns = {}
    extra_columns = {}
    if not items:
        return None
    for key, value in items[0].items():
        if key in ["id", "name"]:
            main_columns[key] = value
        else:
            extra_columns[key] = value

    columns = main_columns.copy()
    columns.update(extra_columns)

    for key in main_columns.keys():
        table.add_column(key, no_wrap=True, header_style="bold")
    for key in extra_columns.keys():
        table.add_column(key)
    for item in items:
        table.add_row(*[str(item[key]) for key in columns.keys()])
    console = Console()
    console.print(table)
    return table
