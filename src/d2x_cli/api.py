import httpx
import json
import requests
from enum import Enum
from urllib.parse import urlencode
from typing import Dict
from uuid import UUID
from nacl.encoding import Base64Encoder
from nacl.signing import SigningKey
from cumulusci.core.config import BaseProjectConfig
from d2x_cli.auth import (
    _validate_service,
    D2X_OAUTH_APP,
    D2X_WORKER_OAUTH_APP,
)
from d2x_cli.runtime import CliRuntime


class D2XApiObjects(Enum):
    Application = "applications"
    GithubRepo = "github-repos"
    GithubOrg = "github-orgs"
    Job = "jobs"
    Plan = "plans"
    PlanVersion = "versions"
    Org = "orgs"
    OrgConnectRequest = "org-connect-requests"
    OrgUser = "org-users"
    OrgUserCredential = "org-user-credentials"
    OrgUserGrant = "org-user-grants"
    ScratchCreateRequest = "scratch-create-requests"
    ScratchDeleteRequest = "scratch-delete-requests"
    Tenant = "tenants"
    TenantUserRole = "tenant-user-roles"
    User = "users"


D2X_NON_TENANTED_OBJECTS = (
    D2XApiObjects.Application,
    D2XApiObjects.Tenant,
    D2XApiObjects.TenantUserRole,
    D2XApiObjects.User,
)


def fk_field_to_model(field_name):
    # Remove the trailing '_id' if it exists
    if field_name.endswith("_id"):
        field_name = field_name[:-3]
    # Convert to camel case
    parts = field_name.split("_")
    model_name = "".join(part.capitalize() for part in parts)
    if model_name == "Repo":
        model_name = "GithubRepo"
    return model_name


class BaseD2XException(Exception):
    pass


class D2XUnauthorizedException(BaseD2XException):
    pass


class D2XNotFoundException(BaseD2XException):
    pass


class D2XBadRequestException(BaseD2XException):
    pass


class D2XConfigError(BaseD2XException):
    pass


class D2XServerError(BaseD2XException):
    pass


class BaseD2XApiClient:
    def __init__(self, base_url: str, token: str, tenant: str):
        self.base_url = base_url
        self.token = token
        self.tenant = tenant

    def _get_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _check_status_code(self, response: requests.Response):
        if response.status_code == 400:
            raise D2XBadRequestException(f"Bad request: {response.json()}")
        if response.status_code == 401:
            raise D2XUnauthorizedException(f"Unauthorized: {response.json()}")
        if response.status_code == 403:
            raise D2XUnauthorizedException(
                f"Token expired or invalid, use d2x service connect d2x to re-authenticate. Message: {response.json()['message']}"
            )
        if response.status_code == 404:
            raise D2XNotFoundException(f"Not found: {response.json()}")
        if response.status_code == 500:
            raise D2XServerError(f"Server error: {response.json()}")


class D2XApiClient(BaseD2XApiClient):
    def _get_obj_base_url(
        self,
        obj: D2XApiObjects,
        parents: Dict[str, UUID] = None,
        extra_path: str = None,
    ):
        parents_path = ""
        url = ""
        if obj in D2X_NON_TENANTED_OBJECTS:
            url = f"{self.base_url}/{obj}"
        elif obj == D2XApiObjects.PlanVersion:
            if not parents:
                raise D2XConfigError("PlanVersion requires a plan_id in parents")
            parents_path = f"/plans/{parents['plan_id']}"
        url = f"{self.base_url}/d2x/{self.tenant}{parents_path}/{obj.value}"
        if extra_path:
            url = f"{url}/{extra_path}"
        return url

    def list(self, obj: D2XApiObjects, parents: Dict[str, UUID] = None, **kwargs):
        url = self._get_obj_base_url(obj)
        kwargs.update(
            {
                "url": self._get_obj_base_url(obj, parents),
                "headers": self._get_headers(),
                "timeout": 30,
            }
        )
        resp = requests.get(**kwargs)
        self._check_status_code(resp)
        return resp.json()

    async def list_async(
        self, obj: D2XApiObjects, parents: Dict[str, UUID] = None, **kwargs
    ):
        url = self._get_obj_base_url(obj)
        kwargs.update(
            {
                "url": self._get_obj_base_url(obj, parents),
                "headers": self._get_headers(),
                "timeout": 30,
            }
        )
        async with httpx.AsyncClient() as client:
            resp = await client.get(**kwargs)
        self._check_status_code(resp)
        return resp.json()

    def read(
        self, obj: D2XApiObjects, id: UUID, parents: Dict[str, UUID] = None, **kwargs
    ):
        extra_path = kwargs.pop("extra_path", "")
        if extra_path:
            extra_path = f"/{extra_path}"
        resp = requests.get(
            self._get_obj_base_url(obj, parents) + f"/{id}{extra_path}",
            headers=self._get_headers(),
            timeout=30,
            **kwargs,
        )
        self._check_status_code(resp)
        return resp.json()

    async def read_async(
        self, obj: D2XApiObjects, id: UUID, parents: Dict[str, UUID] = None, **kwargs
    ):
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self._get_obj_base_url(obj, parents) + f"/{id}",
                headers=self._get_headers(),
                timeout=30,
                **kwargs,
            )
        self._check_status_code(resp)
        return resp.json()

    def create(
        self, obj: D2XApiObjects, data, parents: Dict[str, UUID] = None, **kwargs
    ):
        extra_path = kwargs.pop("extra_path", "")
        resp = requests.post(
            self._get_obj_base_url(obj, parents, extra_path=extra_path),
            headers=self._get_headers(),
            json=data,
            timeout=30,
            **kwargs,
        )
        self._check_status_code(resp)
        return resp.json()

    async def create_async(
        self, obj: D2XApiObjects, data, parents: Dict[str, UUID] = None, **kwargs
    ):
        extra_path = kwargs.pop("extra_path", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._get_obj_base_url(obj, parents, extra_path),
                headers=self._get_headers(),
                json=data,
                timeout=30,
                **kwargs,
            )
        self._check_status_code(resp)
        return resp.json()

    def update(
        self,
        obj: D2XApiObjects,
        id: UUID,
        data,
        parents: Dict[str, UUID] = None,
        **kwargs,
    ):
        resp = requests.put(
            self._get_obj_base_url(obj, parents) + f"/{id}",
            headers=self._get_headers(),
            json=data,
            timeout=30,
            **kwargs,
        )
        self._check_status_code(resp)
        return resp.json()

    async def update_async(
        self,
        obj: D2XApiObjects,
        id: UUID,
        data,
        parents: Dict[str, UUID] = None,
        **kwargs,
    ):
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                self._get_obj_base_url(obj, parents) + f"/{id}",
                headers=self._get_headers(),
                json=data,
                timeout=30,
                **kwargs,
            )
        self._check_status_code(resp)
        return resp.json()

    def delete(self, obj: D2XApiObjects, parents: Dict[str, UUID] = None, **kwargs):
        resp = requests.delete(
            self._get_obj_base_url(obj, parents),
            headers=self._get_headers(),
            **kwargs,
            timeout=30,
        )
        self._check_status_code(resp)
        return resp.json()

    async def delete_async(
        self, obj: D2XApiObjects, parents: Dict[str, UUID] = None, **kwargs
    ):
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                self._get_obj_base_url(obj, parents),
                headers=self._get_headers(),
                **kwargs,
                timeout=30,
            )
        self._check_status_code(resp)
        return resp.json()

    def org_login(self, id: UUID, path: str = None, **kwargs):
        if path:
            ret_url = urlencode({"redirect_path": path})
            target = f"{target}&{ret_url}"
        resp = requests.get(
            f"{self.base_url}/d2x/{self.tenant}/org-login/{id}",
            headers=self._get_headers(),
            timeout=30,
            **kwargs,
        )
        self._check_status_code(resp)
        return resp.json().get("login_url")


class D2XWorkerApiClient(BaseD2XApiClient):

    def __init__(self, base_url: str, token: str, tenant: str):
        base_url = f"{base_url}/worker"
        self.tenant_url = f"{base_url}/{tenant}"
        super().__init__(base_url, token, tenant)

    def job_start(self, job_id: UUID):
        signing_key = SigningKey.generate()
        data = {
            "signing_key": signing_key.verify_key.encode(Base64Encoder).decode("utf-8"),
            "signature": signing_key.sign(
                job_id.encode(), encoder=Base64Encoder
            ).signature.decode("utf-8"),
        }
        resp = requests.post(
            f"{self.tenant_url}/jobs/{job_id}/start",
            headers=self._get_headers(),
            timeout=30,
            json=data,
        )
        self._check_status_code(resp)
        return signing_key, resp.json()

    def job_status_update(
        self,
        signing_key: SigningKey,
        job_id: UUID,
        status: str,
        log: str,
        exception: str = None,
    ):
        data = {
            "status": status,
            "log": log,
            "exception": exception,
        }
        data["signature"] = signing_key.sign(
            json.dumps(data).encode(), encoder=Base64Encoder
        ).signature.decode("utf-8")

        resp = requests.post(
            f"{self.tenant_url}/jobs/{job_id}/status",
            headers=self._get_headers(),
            timeout=30,
            json=data,
        )
        self._check_status_code(resp)
        return resp.json()

    def job_org_credentials(
        self,
        signing_key: SigningKey,
        job_id: UUID,
        org_user_id: str,
    ):
        data = {
            "signature": signing_key.sign(
                json.dumps(org_user_id).encode(), encoder=Base64Encoder
            ).signature.decode("utf-8")
        }

        resp = requests.post(
            f"{self.tenant_url}/jobs/{job_id}/org-credentials",
            headers=self._get_headers(),
            timeout=30,
            json=data,
        )
        self._check_status_code(resp)
        return resp.json()

    def scratch_create_request_complete(
        self,
        signing_key: SigningKey,
        request_id: UUID,
        org_id: str,
        instance_url: str,
        username: str,
        user_id: str,
        sfdx_auth_url: str,
    ):
        data = {
            "org_id": org_id,
            "instance_url": instance_url,
            "username": username,
            "user_id": user_id,
            "sfdx_auth_url": sfdx_auth_url,
        }
        # data["signature"] = signing_key.sign(
        #     json.dumps(data), encoder=Base64Encoder
        # ).signature.decode("utf-8")

        resp = requests.post(
            f"{self.tenant_url}/scratch-create-requests/{request_id}/complete",
            headers=self._get_headers(),
            timeout=30,
            json=data,
        )
        self._check_status_code(resp)
        return resp.json()


def get_d2x_api_client(runtime: CliRuntime):
    keychain = runtime.project_config.keychain
    service = runtime.project_config.keychain.get_service("d2x")
    changed, config = _validate_service(
        service.config,
        keychain,
        app=D2X_OAUTH_APP,
    )
    if changed:
        service.config.update(config)
        keychain.set_service("d2x", keychain.get_default_service_name("d2x"), service)

    token = json.loads(service.token)
    return D2XApiClient(
        base_url=service.base_url,
        token=token["access_token"],
        tenant=service.tenant,
    )


def get_d2x_worker_api_client(runtime: CliRuntime):
    keychain = runtime.keychain
    service = runtime.keychain.get_service("d2x_worker")
    changed, config = _validate_service(
        service.config,
        keychain,
        app=D2X_WORKER_OAUTH_APP,
    )
    if changed:
        service.config.update(config)
        keychain.set_service(
            "d2x_worker", keychain.get_default_service_name("d2x_worker"), service
        )

    token = json.loads(service.token)
    return D2XWorkerApiClient(
        base_url=service.base_url,
        token=token["access_token"],
        tenant=service.tenant,
    )
