import click
import functools
import json
import inspect
import os
from cumulusci.cli.runtime import CliRuntime as BaseCliRuntime
from cumulusci.core.config import BaseProjectConfig

D2X_SERVICE_CONFIG = {
    "description": "D2X Cloud API",
    "attributes": {
        "base_url": {
            "description": "The base URL for the D2X Cloud API",
            "required": True,
        },
        "tenant": {
            "description": "The tenant for the D2X Cloud API. For example, acme-corp",
            "required": True,
        },
        "token": {
            "description": "The token for the D2X Cloud API",
            "required": True,
            "default_factory": "d2x_cli.auth.get_d2x_token",
            "senstive": True,
        },
    },
    "validator": "d2x_cli.auth.validate_d2x_service",
}

D2X_WORKER_SERVICE_CONFIG = {
    "description": "D2X Cloud Worker API",
    "attributes": {
        "base_url": {
            "description": "The base URL for the D2X Cloud API (only the base url, no path)",
            "required": True,
        },
        "tenant": {
            "description": "The tenant for the D2X Cloud API. For example, acme-corp",
            "required": True,
        },
        "token": {
            "description": "The token for the D2X Cloud Worker API",
            "required": True,
            "default_factory": "d2x_cli.auth.get_d2x_worker_token",
            "senstive": True,
        },
    },
    "validator": "d2x_cli.auth.validate_d2x_worker_service",
}


class CliRuntime(BaseCliRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.check_d2x_version()

    def check_d2x_version(self):
        pass


def pass_runtime(func=None, require_project=True, require_keychain=False):
    """Decorator which passes the D2X CLI runtime object as the first arg to a click command."""

    def decorate(func):
        @click.pass_context
        def new_func(ctx, *args, **kw):
            runtime = CliRuntime(load_keychain=False)

            if not runtime.project_config:
                # Construct a dummy project config
                runtime.project_config = BaseProjectConfig(
                    runtime.universal_config,
                    runtime.universal_config.config,
                )
            runtime.project_config.config["services"]["d2x"] = D2X_SERVICE_CONFIG
            runtime.project_config.config["services"][
                "d2x_worker"
            ] = D2X_WORKER_SERVICE_CONFIG

            if require_project and runtime.project_config is None:
                raise runtime.project_config_error
            if require_keychain:
                runtime._load_keychain()

            # Pass runtime as the second argument
            args = list(args)
            args.insert(1, runtime)
            return func(*args, **kw)

        return functools.update_wrapper(new_func, func)

    if func is None:
        return decorate
    else:
        if isinstance(func, click.core.MultiCommand):
            for command in func.list_commands(ctx):
                func.add_command(decorate(func.get_command(ctx, command)))
        else:
            return decorate(func)
