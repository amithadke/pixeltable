"""
Protocol models for pxt service deployment operations.

Defines request/response types for environment, service, deploy, and run operations.
These models are shared between the pixeltable CLI and the cloud server.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator

from .operation_types import ServiceOperationType

# ── Environment ───────────────────────────────────────────────────────────────


class EnvironmentRecord(BaseModel):
    env_id: str
    org_id: str
    env_name: str
    version: int
    cpus: float
    memory_gb: float
    disk_gb: float
    gpu: Optional[str]
    created_at: float
    updated_at: float


class CreateEnvironmentRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.CREATE_ENVIRONMENT] = ServiceOperationType.CREATE_ENVIRONMENT
    org_slug: Optional[str] = None
    env_name: str
    cpus: float = 0.5
    memory_gb: float = 1.0
    disk_gb: float = 50.0
    gpu: Optional[str] = None

    @field_validator('gpu')
    @classmethod
    def no_gpu_v1(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            raise ValueError('GPU is not yet supported')
        return v


class CreateEnvironmentResponse(BaseModel):
    environment: EnvironmentRecord


class GetEnvironmentRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.GET_ENVIRONMENT] = ServiceOperationType.GET_ENVIRONMENT
    org_slug: Optional[str] = None
    env_id: str


class GetEnvironmentResponse(BaseModel):
    environment: EnvironmentRecord


class ListEnvironmentsRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.LIST_ENVIRONMENTS] = ServiceOperationType.LIST_ENVIRONMENTS
    org_slug: Optional[str] = None


class ListEnvironmentsResponse(BaseModel):
    environments: list[EnvironmentRecord]


class UpdateEnvironmentRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.UPDATE_ENVIRONMENT] = ServiceOperationType.UPDATE_ENVIRONMENT
    org_slug: Optional[str] = None
    env_id: str
    env_name: Optional[str] = None
    cpus: Optional[float] = None
    memory_gb: Optional[float] = None
    disk_gb: Optional[float] = None
    gpu: Optional[str] = None

    @field_validator('gpu')
    @classmethod
    def no_gpu_v1(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            raise ValueError('GPU is not yet supported')
        return v


class UpdateEnvironmentResponse(BaseModel):
    environment: EnvironmentRecord


class DeleteEnvironmentRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.DELETE_ENVIRONMENT] = ServiceOperationType.DELETE_ENVIRONMENT
    org_slug: Optional[str] = None
    env_id: str


class DeleteEnvironmentResponse(BaseModel):
    env_id: str


# ── Environment secrets ───────────────────────────────────────────────────────


class AddEnvSecretRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.ADD_ENV_SECRET] = ServiceOperationType.ADD_ENV_SECRET
    org_slug: Optional[str] = None
    env_id: str
    secret_name: str
    secret_value: str


class AddEnvSecretResponse(BaseModel):
    env_id: str
    secret_name: str


class RemoveEnvSecretRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.REMOVE_ENV_SECRET] = ServiceOperationType.REMOVE_ENV_SECRET
    org_slug: Optional[str] = None
    env_id: str
    secret_name: str


class RemoveEnvSecretResponse(BaseModel):
    env_id: str
    secret_name: str


class ListEnvSecretsRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.LIST_ENV_SECRETS] = ServiceOperationType.LIST_ENV_SECRETS
    org_slug: Optional[str] = None
    env_id: str


class ListEnvSecretsResponse(BaseModel):
    env_id: str
    secret_names: list[str]


# ── Run (shared record, referenced by ServiceRecord) ─────────────────────────


class ServiceRunRecord(BaseModel):
    run_id: str
    service_id: str
    org_id: str
    env_id: str
    env_name: str
    env_version: int
    env_config: dict[str, Any]
    bundle_id: str
    version: str
    state: str  # PENDING | BUILDING | DEPLOYING | STARTING | RUNNING | STOPPED | FAILED
    endpoint: Optional[str]
    public_endpoint: Optional[str] = None
    error: Optional[str]
    started_at: float


# ── Service ───────────────────────────────────────────────────────────────────


class ServiceRecord(BaseModel):
    service_id: str
    env_id: str
    org_id: str
    service_name: str
    state: str  # STOPPED | DEPLOYING | UPDATING | RUNNING | FAILED
    workers_min: int
    workers_max: int
    scale_to_zero: bool
    description: Optional[str]
    created_at: float
    current_run: Optional[ServiceRunRecord] = None


class CreateServiceRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.CREATE_SERVICE] = ServiceOperationType.CREATE_SERVICE
    org_slug: Optional[str] = None
    env_id: str
    service_name: str
    workers_min: int = 1
    workers_max: int = 1
    scale_to_zero: bool = True
    description: Optional[str] = None


class CreateServiceResponse(BaseModel):
    service: ServiceRecord


class GetServiceRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.GET_SERVICE] = ServiceOperationType.GET_SERVICE
    org_slug: Optional[str] = None
    service_id: str


class GetServiceResponse(BaseModel):
    service: ServiceRecord


class ListServicesRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.LIST_SERVICES] = ServiceOperationType.LIST_SERVICES
    org_slug: Optional[str] = None
    env_id: str


class ListServicesResponse(BaseModel):
    services: list[ServiceRecord]


class StopServiceRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.STOP_SERVICE] = ServiceOperationType.STOP_SERVICE
    org_slug: Optional[str] = None
    service_id: str


class StopServiceResponse(BaseModel):
    service: ServiceRecord


class StartServiceRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.START_SERVICE] = ServiceOperationType.START_SERVICE
    org_slug: Optional[str] = None
    service_id: str


class StartServiceResponse(BaseModel):
    service: ServiceRecord


class DeleteServiceRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.DELETE_SERVICE] = ServiceOperationType.DELETE_SERVICE
    org_slug: Optional[str] = None
    service_id: str


class DeleteServiceResponse(BaseModel):
    service_id: str


# ── Deploy (two-step: request presigned URL, then finalize) ──────────────────


class DeployRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.DEPLOY_REQUEST] = ServiceOperationType.DEPLOY_REQUEST
    org_slug: Optional[str] = None
    env_name: str
    service_name: str
    bundle_size_bytes: int


class DeployRequestResponse(BaseModel):
    upload_id: str
    upload_url: str  # presigned PUT URL for tar.bz2, TTL 1h
    service_id: str  # resolved service ID, returned for use in polling


class FinalizeDeployRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.FINALIZE_DEPLOY] = ServiceOperationType.FINALIZE_DEPLOY
    org_slug: Optional[str] = None
    upload_id: str


class BundleRecord(BaseModel):
    bundle_id: str
    org_id: str
    bundle_hash: str
    routes: list[str]  # HTTP paths exposed (e.g. ["/insert", "/documents"])
    route_to_table: dict[str, list[str]]  # HTTP path → [table_path, ...]
    table_metadata: dict[str, Any]  # table_path → TableMetadata
    tables_md: list[dict[str, Any]]  # list of TableVersionMd dicts
    pxt_version: str
    pxt_md_version: int
    file_manifest: list[str]
    created_at: float
    browse_url: str


class FinalizeDeployResponse(BaseModel):
    bundle: BundleRecord
    run: ServiceRunRecord


# ── Service run history ───────────────────────────────────────────────────────


class RouteTableRecord(BaseModel):
    table_path: str
    metadata: dict[str, Any]


class RouteRecord(BaseModel):
    path: str
    tables: list[RouteTableRecord]


class GetServiceRunRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.GET_SERVICE_RUN] = ServiceOperationType.GET_SERVICE_RUN
    org_slug: Optional[str] = None
    run_id: str


class GetServiceRunResponse(BaseModel):
    run: ServiceRunRecord
    routes: list[RouteRecord]


class ListServiceRunsRequest(BaseModel):
    operation_type: Literal[ServiceOperationType.LIST_SERVICE_RUNS] = ServiceOperationType.LIST_SERVICE_RUNS
    org_slug: Optional[str] = None
    service_id: str


class ListServiceRunsResponse(BaseModel):
    runs: list[ServiceRunRecord]
