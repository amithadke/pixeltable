"""Client for the Pixeltable cloud deploy API."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from pixeltable import exceptions as excs
from pixeltable.env import Env
from pixeltable.serving._config import lookup_environment_config
from pixeltable.serving.deploy import build_deploy_bundle
from pixeltable.share.protocol.service import (
    CreateEnvironmentRequest,
    DeleteEnvironmentRequest,
    DeleteServiceRequest,
    DeployRequest,
    FinalizeDeployRequest,
    GetServiceRequest,
    ListEnvironmentsRequest,
    UpdateEnvironmentRequest,
)
from pixeltable.share.publish import PIXELTABLE_API_URL, _api_headers, _upload_to_presigned_url


def deploy(
    environment_name: str, json_output: bool = False, watch: bool = True, org_slug: str | None = None
) -> dict[str, str]:
    """Build the deploy bundle and start a cloud deployment for each service in the environment.

    Returns a mapping of service_name → service_id for all deployed services.
    """
    cfg = lookup_environment_config(environment_name)
    default_org_slug = org_slug or cfg.org
    bundle_path = build_deploy_bundle(environment_name)
    service_ids: dict[str, str] = {}

    for service_name_raw in cfg.services:
        # Support "org_slug/service_name" format in the services list
        if '/' in service_name_raw:
            svc_org_slug, service_name = service_name_raw.split('/', 1)
        else:
            svc_org_slug = default_org_slug
            service_name = service_name_raw

        deploy_resp = _post(
            DeployRequest(
                org_slug=svc_org_slug,
                env_name=cfg.name,
                service_name=service_name,
                bundle_size_bytes=bundle_path.stat().st_size,
            )
        )
        upload_id = deploy_resp['upload_id']
        upload_url = deploy_resp['upload_url']
        service_id = deploy_resp['service_id']
        service_ids[service_name] = service_id

        _upload_to_presigned_url(bundle_path, upload_url)

        finalize_resp = _post(FinalizeDeployRequest(org_slug=svc_org_slug, upload_id=upload_id))
        run = finalize_resp['run']

        if json_output:
            print(
                json.dumps(
                    {
                        'status': 'deploying',
                        'service': service_name,
                        'run_id': run['run_id'],
                        'version': run['version'],
                        'state': run['state'],
                    }
                )
            )
        else:
            Env.get().console_logger.info(
                f"Service '{service_name}': deployment started (run {run['version']}, state: {run['state']})"
            )

        if watch:
            endpoint = _poll_until_running(service_id, org_slug=svc_org_slug, json_output=json_output)
            if endpoint and not json_output:
                Env.get().console_logger.info(f"Service '{service_name}' is live at: {endpoint}")

    return service_ids


def _poll_until_running(
    service_id: str, timeout: int = 600, interval: int = 10, json_output: bool = False, org_slug: str | None = None
) -> str | None:
    """Poll GET_SERVICE until the current run reaches RUNNING or FAILED; return endpoint or None."""
    deadline = time.monotonic() + timeout
    last_state: str | None = None

    while time.monotonic() < deadline:
        resp = _post(GetServiceRequest(org_slug=org_slug, service_id=service_id))
        svc = resp.get('service', {})
        current_run = svc.get('current_run')
        if current_run:
            state = current_run.get('state', '')
            if state != last_state:
                last_state = state
                if json_output:
                    print(json.dumps({'state': state}))
                else:
                    Env.get().console_logger.info(f'  deployment state: {state}')
            if state == 'RUNNING':
                return current_run.get('endpoint')
            if state == 'FAILED':
                error = current_run.get('error') or 'unknown error'
                raise excs.ExternalServiceError(
                    excs.ErrorCode.PROVIDER_ERROR, f'Deployment failed: {error}', provider='pixeltable_cloud'
                )
        time.sleep(interval)

    raise excs.ExternalServiceError(
        excs.ErrorCode.PROVIDER_ERROR,
        f'Deployment did not reach RUNNING within {timeout}s',
        provider='pixeltable_cloud',
    )


def environment_create(
    env_name: str, cpus: float, memory_gb: float, disk_gb: float, org_slug: str | None = None, json_output: bool = False
) -> None:
    resp = _post(
        CreateEnvironmentRequest(org_slug=org_slug, env_name=env_name, cpus=cpus, memory_gb=memory_gb, disk_gb=disk_gb)
    )
    env = resp['environment']
    if json_output:
        print(json.dumps(env))
    else:
        _print_env(env)


def environment_list(org_slug: str | None = None, json_output: bool = False) -> None:
    resp = _post(ListEnvironmentsRequest(org_slug=org_slug))
    envs = resp.get('environments', [])
    if json_output:
        print(json.dumps(envs))
    elif not envs:
        print('No environments.')
    else:
        for env in envs:
            _print_env(env)


def environment_update(
    env_name: str,
    new_name: str | None,
    cpus: float | None,
    memory_gb: float | None,
    disk_gb: float | None,
    org_slug: str | None = None,
    json_output: bool = False,
) -> None:
    env = _find_env_by_name(env_name, org_slug=org_slug)
    resp = _post(
        UpdateEnvironmentRequest(
            org_slug=org_slug, env_id=env['env_id'], env_name=new_name, cpus=cpus, memory_gb=memory_gb, disk_gb=disk_gb
        )
    )
    updated_env = resp['environment']
    if json_output:
        print(json.dumps(updated_env))
    else:
        _print_env(updated_env)


def environment_delete(env_name: str, org_slug: str | None = None, json_output: bool = False) -> None:
    env = _find_env_by_name(env_name, org_slug=org_slug)
    _post(DeleteEnvironmentRequest(org_slug=org_slug, env_id=env['env_id']))
    if json_output:
        print(json.dumps({'deleted': env_name}))
    else:
        print(f"Deleted environment '{env_name}'.")


def service_delete(service_id: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(DeleteServiceRequest(org_slug=org_slug, service_id=service_id))
    if json_output:
        print(json.dumps({'deleted': service_id}))
    else:
        print(f"Deleted service '{service_id}'.")


def _find_env_by_name(env_name: str, org_slug: str | None = None) -> dict[str, Any]:
    resp = _post(ListEnvironmentsRequest(org_slug=org_slug))
    for env in resp.get('environments', []):
        if env['env_name'] == env_name:
            return env
    raise excs.NotFoundError(excs.ErrorCode.SERVICE_NOT_FOUND, f"Environment '{env_name}' not found")


def _post(request: Any) -> dict[str, Any]:
    body = request.model_dump_json()
    resp = requests.post(PIXELTABLE_API_URL, data=body, headers=_api_headers())
    if resp.status_code != 200:
        raise excs.ExternalServiceError(
            excs.ErrorCode.PROVIDER_ERROR,
            f'Deploy API error ({resp.status_code}): {resp.text}',
            provider='pixeltable_cloud',
            status_code=resp.status_code,
        )
    return resp.json()


def _print_env(env: dict[str, Any]) -> None:
    print(f'  {env["env_name"]}  (id: {env["env_id"]})')
    print(f'    CPUs: {env["cpus"]},  Memory: {env["memory_gb"]} GB,  Disk: {env["disk_gb"]} GB')
    print(f'    Version: {env["version"]}')
