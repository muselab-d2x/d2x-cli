import json
import requests
from enum import Enum
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

    def _get_obj_base_url(self, obj: D2XApiObjects, parents: Dict[str, UUID] = None):
        parents_path = ""
        if obj in D2X_NON_TENANTED_OBJECTS:
            return f"{self.base_url}/{obj}"
        if obj == D2XApiObjects.PlanVersion:
            if not parents:
                raise D2XConfigError("PlanVersion requires a plan_id in parents")
            parents_path = f"/plans/{parents['plan_id']}"
        return f"{self.base_url}/d2x/{self.tenant}{parents_path}/{obj.value}"

    def _get_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _check_status_code(self, response: requests.Response):
        if response.status_code == 401:
            raise D2XUnauthorizedException("Unauthorized")
        if response.status_code == 404:
            raise D2XNotFoundException("Not found")
        if response.status_code == 400:
            raise D2XBadRequestException("Bad request")
        if response.status_code == 500:
            raise D2XServerError("Server error")

    def list(self, obj: D2XApiObjects, parents: Dict[str, UUID] = None, **kwargs):
        url = self._get_obj_base_url(obj)
        resp = requests.get(
            self._get_obj_base_url(obj, parents),
            headers=self._get_headers(),
            timeout=30,
            **kwargs,
        )
        self._check_status_code(resp)
        return resp.json()

    def read(
        self, obj: D2XApiObjects, id: UUID, parents: Dict[str, UUID] = None, **kwargs
    ):
        resp = requests.get(
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
        resp = requests.post(
            self._get_obj_base_url(obj, parents),
            headers=self._get_headers(),
            data=data,
            timeout=30,
            **kwargs,
        )
        self._check_status_code(resp)
        return resp

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
            data=data,
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


def get_d2x_api_client(runtime: CliRuntime):
    service = runtime.project_config.keychain.get_service("d2x")
    return D2XApiClient(
        base_url=service.base_url,
        token=service.token,
        tenant=service.tenant,
    )
