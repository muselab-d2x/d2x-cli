import httpx
import json
import requests
from enum import Enum
from urllib.parse import urlencode
from typing import Dict
from uuid import UUID
from cumulusci.core.config import BaseProjectConfig
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


class D2XApiClient:
    def __init__(self, base_url: str, token: str, tenant: str):
        self.base_url = base_url
        self.token = token
        self.tenant = tenant

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

    def _get_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _check_status_code(self, response: requests.Response):
        if response.status_code == 400:
            raise D2XBadRequestException("Bad request")
        if response.status_code == 401:
            raise D2XUnauthorizedException("Unauthorized")
        if response.status_code == 403:
            raise D2XUnauthorizedException(
                "Token expired or invalid, use d2x service connect d2x to re-authenticate. Message: {response.json()['message']}"
            )
        if response.status_code == 404:
            raise D2XNotFoundException("Not found")
        if response.status_code == 500:
            raise D2XServerError("Server error")

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


def get_d2x_api_client(runtime: CliRuntime):
    service = runtime.project_config.keychain.get_service("d2x")
    token = json.loads(service.token)
    return D2XApiClient(
        base_url=service.base_url,
        token=token["access_token"],
        tenant=service.tenant,
    )
