"""Client for the Pixeltable cloud deploy API."""

from __future__ import annotations

import json
from typing import Any

import requests

from pixeltable import exceptions as excs
from pixeltable.env import Env
from pixeltable.serving._config import lookup_environment_config
from pixeltable.serving.deploy import build_deploy_bundle
from pixeltable.share.publish import PIXELTABLE_API_URL, _api_headers, _upload_to_presigned_url


def deploy(environment_name: str, json_output: bool = False) -> None:
    """Build the deploy bundle and start a cloud deployment for each service in the environment."""
    cfg = lookup_environment_config(environment_name)
    bundle_path = build_deploy_bundle(environment_name)

    for service_name in cfg.services:
        deploy_resp = _post({
            'operation_type': 'DEPLOY_REQUEST',
            'env_name': cfg.name,
            'service_name': service_name,
            'bundle_size_bytes': bundle_path.stat().st_size,
        })
        upload_id = deploy_resp['upload_id']
        upload_url = deploy_resp['upload_url']

        _upload_to_presigned_url(bundle_path, upload_url)

        finalize_resp = _post({
            'operation_type': 'FINALIZE_DEPLOY',
            'upload_id': upload_id,
        })
        run = finalize_resp['run']

        if json_output:
            print(json.dumps({
                'status': 'deploying',
                'service': service_name,
                'run_id': run['run_id'],
                'version': run['version'],
                'state': run['state'],
            }))
        else:
            Env.get().console_logger.info(
                f"Service '{service_name}': deployment started"
                f" (run {run['version']}, state: {run['state']})"
            )


def environment_create(
    env_name: str,
    cpus: float,
    memory_gb: float,
    disk_gb: float,
    json_output: bool = False,
) -> None:
    resp = _post({
        'operation_type': 'CREATE_ENVIRONMENT',
        'env_name': env_name,
        'cpus': cpus,
        'memory_gb': memory_gb,
        'disk_gb': disk_gb,
    })
    env = resp['environment']
    if json_output:
        print(json.dumps(env))
    else:
        _print_env(env)


def environment_list(json_output: bool = False) -> None:
    resp = _post({'operation_type': 'LIST_ENVIRONMENTS'})
    envs = resp.get('environments', [])
    if json_output:
        print(json.dumps(envs))
    else:
        if not envs:
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
    json_output: bool = False,
) -> None:
    env = _find_env_by_name(env_name)
    body: dict[str, Any] = {'operation_type': 'UPDATE_ENVIRONMENT', 'env_id': env['env_id']}
    if new_name is not None:
        body['env_name'] = new_name
    if cpus is not None:
        body['cpus'] = cpus
    if memory_gb is not None:
        body['memory_gb'] = memory_gb
    if disk_gb is not None:
        body['disk_gb'] = disk_gb
    resp = _post(body)
    updated_env = resp['environment']
    if json_output:
        print(json.dumps(updated_env))
    else:
        _print_env(updated_env)


def environment_delete(env_name: str, json_output: bool = False) -> None:
    env = _find_env_by_name(env_name)
    _post({'operation_type': 'DELETE_ENVIRONMENT', 'env_id': env['env_id']})
    if json_output:
        print(json.dumps({'deleted': env_name}))
    else:
        print(f"Deleted environment '{env_name}'.")


def _find_env_by_name(env_name: str) -> dict[str, Any]:
    resp = _post({'operation_type': 'LIST_ENVIRONMENTS'})
    for env in resp.get('environments', []):
        if env['env_name'] == env_name:
            return env
    raise excs.NotFoundError(excs.ErrorCode.SERVICE_NOT_FOUND, f"Environment '{env_name}' not found")


def _post(body: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(PIXELTABLE_API_URL, data=json.dumps(body), headers=_api_headers())
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
