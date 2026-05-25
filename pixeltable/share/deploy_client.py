"""Client for the Pixeltable cloud deploy API."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from pixeltable import exceptions as excs
from pixeltable.env import Env
from pixeltable.serving._config import lookup_deployment_config
from pixeltable.serving.deploy import build_deploy_bundle
from pixeltable.share.protocol.service import (
    AddEnvSecretRequest,
    AddOrgSecretRequest,
    CreateEnvironmentRequest,
    CreateNodePoolRequest,
    DeleteEnvironmentRequest,
    DeleteNodePoolRequest,
    DeleteServiceRequest,
    DeployRequest,
    FinalizeDeployRequest,
    GetServiceRequest,
    ListEnvironmentsRequest,
    ListEnvSecretsRequest,
    ListNodePoolsRequest,
    ListOrgSecretsRequest,
    ListServiceRunsRequest,
    ListServicesRequest,
    RemoveEnvSecretRequest,
    RemoveOrgSecretRequest,
    StartServiceRequest,
    StopServiceRequest,
    UpdateEnvironmentRequest,
)
from pixeltable.share.publish import PIXELTABLE_API_URL, _api_headers, _upload_to_presigned_url


def deploy(
    deployment_name: str, json_output: bool = False, watch: bool = True, org_slug: str | None = None
) -> dict[str, str]:
    """Build the deploy bundle and start a cloud deployment for the deployment configuration.

    Returns a mapping of service_name → env_name for the deployed service.
    """
    cfg = lookup_deployment_config(deployment_name)
    bundle_path = build_deploy_bundle(deployment_name)

    # cfg.env can be "org/env-name"; extract org and env name
    if '/' in cfg.env:
        env_org_slug, env_name = cfg.env.split('/', 1)
    else:
        env_org_slug = org_slug
        env_name = cfg.env

    service_name = cfg.name

    deploy_resp = _post(
        DeployRequest(
            org_slug=env_org_slug,
            env_name=env_name,
            service_name=service_name,
            bundle_size_bytes=bundle_path.stat().st_size,
        )
    )
    upload_id = deploy_resp['upload_id']
    upload_url = deploy_resp['upload_url']

    _upload_to_presigned_url(bundle_path, upload_url)

    finalize_resp = _post(FinalizeDeployRequest(org_slug=env_org_slug, upload_id=upload_id))
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
        endpoint = _poll_until_running(service_name, env_name, org_slug=env_org_slug, json_output=json_output)
        if endpoint and not json_output:
            Env.get().console_logger.info(f"Service '{service_name}' is live at: {endpoint}")

    return {service_name: env_name}


def _poll_until_running(
    service_name: str,
    env_name: str | None,
    timeout: int = 600,
    interval: int = 10,
    json_output: bool = False,
    org_slug: str | None = None,
) -> str | None:
    """Poll GET_SERVICE until the current run reaches RUNNING or FAILED; return endpoint or None."""
    deadline = time.monotonic() + timeout
    last_state: str | None = None

    while time.monotonic() < deadline:
        resp = _post(GetServiceRequest(org_slug=org_slug, service_name=service_name, env_name=env_name))
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
                return current_run.get('public_endpoint') or current_run.get('endpoint')
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
    env_name: str,
    cpus: float | None = None,
    memory_gb: float | None = None,
    disk_gb: float | None = None,
    org_slug: str | None = None,
    json_output: bool = False,
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
    envs = _list_envs(org_slug)
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
    resp = _post(
        UpdateEnvironmentRequest(
            org_slug=org_slug,
            env_name=env_name,
            new_name=new_name,
            cpus=cpus,
            memory_gb=memory_gb,
            disk_gb=disk_gb,
        )
    )
    updated_env = resp['environment']
    if json_output:
        print(json.dumps(updated_env))
    else:
        _print_env(updated_env)


def environment_delete(env_name: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(DeleteEnvironmentRequest(org_slug=org_slug, env_name=env_name))
    if json_output:
        print(json.dumps({'deleted': env_name}))
    else:
        print(f"Deleted environment '{env_name}'.")


def environment_add_secret(
    env_name: str, key: str, value: str, org_slug: str | None = None, json_output: bool = False
) -> None:
    resp = _post(AddEnvSecretRequest(org_slug=org_slug, env_name=env_name, secret_name=key, secret_value=value))
    if json_output:
        print(json.dumps(resp))
    else:
        print(f"Secret '{key}' added to environment '{env_name}'.")


def environment_remove_secret(env_name: str, key: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(RemoveEnvSecretRequest(org_slug=org_slug, env_name=env_name, secret_name=key))
    if json_output:
        print(json.dumps({'removed': key}))
    else:
        print(f"Secret '{key}' removed from environment '{env_name}'.")


def environment_list_secrets(env_name: str, org_slug: str | None = None, json_output: bool = False) -> list[str]:
    resp = _post(ListEnvSecretsRequest(org_slug=org_slug, env_name=env_name))
    secret_names: list[str] = resp.get('secret_names', [])
    if json_output:
        print(json.dumps(secret_names))
    elif not secret_names:
        print(f"No secrets in environment '{env_name}'.")
    else:
        for name in secret_names:
            print(f'  {name}')
    return secret_names


def service_get(service_name: str, env_name: str | None = None, org_slug: str | None = None) -> dict[str, Any]:
    """Return the full service record including current_run with workers_min/workers_max."""
    return _post(GetServiceRequest(org_slug=org_slug, service_name=service_name, env_name=env_name))


def service_list_runs(
    service_name: str, env_name: str | None = None, org_slug: str | None = None
) -> list[dict[str, Any]]:
    """Return all run records for a service, each with workers_min/workers_max."""
    return _post(
        ListServiceRunsRequest(org_slug=org_slug, service_name=service_name, env_name=env_name)
    ).get('runs', [])


def service_delete(service_name: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(DeleteServiceRequest(org_slug=org_slug, service_name=service_name))
    if json_output:
        print(json.dumps({'deleted': service_name}))
    else:
        print(f"Deleted service '{service_name}'.")


def service_stop(service_name: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(StopServiceRequest(org_slug=org_slug, service_name=service_name))
    if json_output:
        print(json.dumps({'stopped': service_name}))
    else:
        print(f"Stopped service '{service_name}'.")


def service_start(service_name: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(StartServiceRequest(org_slug=org_slug, service_name=service_name))
    if json_output:
        print(json.dumps({'status': 'starting', 'service': service_name}))
    else:
        Env.get().console_logger.info(f"Service '{service_name}': starting...")
    endpoint = _poll_until_running(service_name, env_name=None, org_slug=org_slug, json_output=json_output)
    if endpoint and not json_output:
        Env.get().console_logger.info(f"Service '{service_name}' is live at: {endpoint}")


def service_list(
    org_slug: str | None = None, env_name: str | None = None, json_output: bool = False
) -> list[dict[str, Any]]:
    """List all cloud services for an org, optionally filtered by environment."""
    if env_name:
        resp = _post(ListServicesRequest(org_slug=org_slug, env_name=env_name))
        all_services = resp.get('services', [])
    else:
        resp = _post(ListServicesRequest(org_slug=org_slug))
        all_services = resp.get('services', [])

    if json_output:
        print(json.dumps(all_services, indent=2))
    else:
        if not all_services:
            print('No services found.')
        for svc in all_services:
            runs = svc.get('current_runs') or ([svc['current_run']] if svc.get('current_run') else [])
            if runs:
                for run in runs:
                    env_n = run.get('env_name', '?')
                    state = run.get('state', '?')
                    print(f'  [{env_n}] {svc["service_name"]}  state={state}')
            else:
                print(f'  {svc["service_name"]}  state=STOPPED')
    return all_services


def service_purge(
    org_slug: str | None = None, env_name: str | None = None, yes: bool = False, json_output: bool = False
) -> None:
    """List and delete all services for an org (with confirmation), from both DB and NF."""
    services = service_list(org_slug=org_slug, env_name=env_name, json_output=False)
    if not services:
        return

    if not yes:
        ans = input(f'Delete {len(services)} service(s)? This cannot be undone. [y/N] ').strip().lower()
        if ans != 'y':
            print('Aborted.')
            return

    results = []
    for svc in services:
        svc_name = svc['service_name']
        svc_env = svc['env_name']
        try:
            if svc.get('state') == 'RUNNING':
                _post(StopServiceRequest(org_slug=org_slug, service_name=svc_name, env_name=svc_env))
            _post(DeleteServiceRequest(org_slug=org_slug, service_name=svc_name, env_name=svc_env))
            results.append({'service_name': svc_name, 'deleted': True})
            if not json_output:
                print(f'  Deleted {svc_name}')
        except Exception as exc:
            results.append({'service_name': svc_name, 'deleted': False, 'error': str(exc)})
            if not json_output:
                print(f'  Failed to delete {svc_name}: {exc}')

    if json_output:
        print(json.dumps(results, indent=2))


def org_secret_add(key: str, value: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(AddOrgSecretRequest(org_slug=org_slug, secret_name=key, secret_value=value))
    if json_output:
        print(json.dumps({'added': key}))
    else:
        print(f"Secret '{key}' added to org.")


def org_secret_remove(key: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(RemoveOrgSecretRequest(org_slug=org_slug, secret_name=key))
    if json_output:
        print(json.dumps({'removed': key}))
    else:
        print(f"Secret '{key}' removed from org.")


def org_secret_list(org_slug: str | None = None, json_output: bool = False) -> list[str]:
    resp = _post(ListOrgSecretsRequest(org_slug=org_slug))
    secret_names: list[str] = resp.get('secret_names', [])
    if json_output:
        print(json.dumps(secret_names))
    elif not secret_names:
        print('No org secrets.')
    else:
        for name in secret_names:
            print(f'  {name}')
    return secret_names


def node_pool_create(
    name: str,
    instance_type: str,
    count: int,
    provider: str = 'northflank',
    region: str = 'nf-default',
    org_slug: str | None = None,
    json_output: bool = False,
) -> None:
    resp = _post(
        CreateNodePoolRequest(
            org_slug=org_slug,
            node_pool_name=name,
            provider=provider,
            region=region,
            instance_type=instance_type,
            count=count,
        )
    )
    if json_output:
        print(json.dumps(resp.get('node_pool', {})))
    else:
        pool = resp.get('node_pool', {})
        print(f"  {pool.get('name')}  ({pool.get('provider')}/{pool.get('region')}, {pool.get('count')}× {pool.get('instance_type')})")


def node_pool_delete(name: str, org_slug: str | None = None, json_output: bool = False) -> None:
    _post(DeleteNodePoolRequest(org_slug=org_slug, node_pool_name=name))
    if json_output:
        print(json.dumps({'deleted': name}))
    else:
        print(f"Deleted node pool '{name}'.")


def node_pool_list(org_slug: str | None = None, json_output: bool = False) -> list[dict]:
    resp = _post(ListNodePoolsRequest(org_slug=org_slug))
    pools = resp.get('node_pools', [])
    if json_output:
        print(json.dumps(pools))
    elif not pools:
        print('No node pools.')
    else:
        for pool in pools:
            print(f"  {pool['name']}  ({pool['provider']}/{pool['region']}, {pool['count']}× {pool['instance_type']})")
    return pools


def _list_envs(org_slug: str | None = None) -> list[dict[str, Any]]:
    return _post(ListEnvironmentsRequest(org_slug=org_slug)).get('environments', [])


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
