import datetime
import io
import json
import logging
import os
import subprocess
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import requests
import toml
from pathspec import PathSpec

import pixeltable as pxt
from pixeltable import config, exceptions as excs, metadata
from pixeltable.env import Env
from pixeltable.runtime import get_runtime
from pixeltable.serving._config import lookup_environment_config, lookup_service_config
from pixeltable.share.publish import PIXELTABLE_API_URL, _api_headers, _upload_to_presigned_url

_logger = logging.getLogger('pixeltable')


def build_deploy_bundle(environment_name: str) -> Path:
    """
    Packages the current Pixeltable projecti into a tarball, along with details of the environment:
    - conda environment, if present
    - catalog metadata for any relevant tables
    - environment and service configuration
    """

    cfg = lookup_environment_config(environment_name)
    Env.get().console_logger.info(f'Deploying {environment_name!r} ...')
    services_cfg = [lookup_service_config(name) for name in cfg.services]
    if len(services_cfg) == 0:
        Env.get().console_logger.warning('That environment contains no services.')
    else:
        Env.get().console_logger.info(
            f'The following service(s) will be deployed: {", ".join(service.name for service in services_cfg)}'
        )

    config_export = {
        'environment': [cfg.model_dump(mode='json')],
        'service': [service.model_dump(mode='json') for service in services_cfg],
    }
    md_export = _export_tables_md(services_cfg)
    table_md_export = _export_table_metadata(services_cfg)
    conda_export = _export_conda_env()
    lockfile = _find_lockfile()
    if conda_export is None and len(cfg.env_dependencies) == 0:
        Env.get().console_logger.warning(
            'No conda environment was detected and no environment dependencies are specified in config.\n'
            'The deployment may not have the necessary dependencies to run correctly.'
        )
    if lockfile is None and len(cfg.python_dependencies) == 0:
        Env.get().console_logger.warning(
            'No dependency lockfile was found and no Python dependencies are specified in config.\n'
            'The deployment may not have the necessary dependencies to run correctly.'
        )
    bundle_path = package(
        cfg,
        config_export=config_export,
        md_export=md_export,
        table_md_export=table_md_export,
        conda_export=conda_export,
    )
    Env.get().console_logger.info(f'Built project bundle: {bundle_path}')
    return bundle_path


def _resolve_patterns(project_dir: Path, patterns: list[str]) -> set[Path]:
    """Resolve gitignore-style patterns against a project directory, returning matching file paths."""
    spec = PathSpec.from_lines('gitignore', patterns)
    return {p for p in project_dir.rglob('*') if p.is_file() and spec.match_file(p.relative_to(project_dir))}


def _read_gitignore(project_dir: Path) -> list[str]:
    gitignore = project_dir / '.gitignore'
    if not gitignore.is_file():
        return []
    return gitignore.read_text().splitlines()


def _collect_project_files(project_dir: Path, include: list[str] | None, exclude: list[str] | None) -> list[Path]:
    if include is not None:
        files = _resolve_patterns(project_dir, include)
    else:
        files = {p for p in project_dir.rglob('*') if p.is_file()}

    # Automatically exclude everything in the project's .gitignore
    files -= _resolve_patterns(project_dir, _read_gitignore(project_dir))
    if exclude is not None:
        files -= _resolve_patterns(project_dir, exclude)
    return sorted(files)


def _export_tables_md(services_cfg: list[config.ServiceConfig]) -> dict[str, Any]:
    # Get all tables mentioned by any route contained in this environment.
    table_paths: set[str] = set()
    for service in services_cfg:
        for route in service.routes:
            # Query routes are only supported for cloud-hosted tables (not implemented yet)
            # TODO: we should catch this upstream to prevent users from trying to deploy unsupported configurations
            assert not isinstance(route, config.QueryRouteConfig)
            table_paths.add(route.table)
    sorted_paths = sorted(table_paths)
    tables = [pxt.get_table(path) for path in sorted_paths]
    # Map tbl_id → user-facing path for primary tables (ancestors get None).
    tbl_id_to_path: dict[str, str] = {str(tbl._id): path for tbl, path in zip(tables, sorted_paths)}

    # Get the md for all ancestors of all such tables.
    catalog = get_runtime().catalog
    with catalog.begin_xact(for_write=False):
        tables_md = [catalog.load_md_for_export(tbl, as_replica=False) for tbl in tables]

    # The ancestor md is returned as: primary table first, followed by ancestors in descending order.
    # Reverse so that ancestors come first, then flatten and de-duplicate (since some tables might have common
    # ancestors). Use a dict for deduplicating, so that we preserve ancestor order to get a topologically
    # sorted list at the end.
    flattened_md = {md.tbl_md.tbl_id: md for md_list in tables_md for md in reversed(md_list)}

    # Add '_path' so the bootstrap can recreate the table at the right location.
    # User-facing tables get their full path; ancestor/system tables get None.
    tables_md_records = []
    for md in flattened_md.values():
        record = md.as_dict()
        record['_path'] = tbl_id_to_path.get(md.tbl_md.tbl_id)
        tables_md_records.append(record)

    bundle_md = {
        'pxt_version': pxt.__version__,
        'pxt_md_version': metadata.VERSION,
        'tables_md': tables_md_records,
    }
    return bundle_md


class _MetadataEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)


def _export_table_metadata(services_cfg: list[config.ServiceConfig]) -> dict[str, Any]:
    """Build table_metadata.json: HTTP route→[table_path] mapping and per-table get_metadata() results."""
    route_to_table: dict[str, list[str]] = {}
    table_paths: set[str] = set()
    for service in services_cfg:
        for route in service.routes:
            if not isinstance(route, config.QueryRouteConfig):
                route_to_table[route.path] = [route.table]
                table_paths.add(route.table)
    table_metadata = {path: pxt.get_table(path).get_metadata() for path in table_paths}
    return {'route_to_table': route_to_table, 'table_metadata': table_metadata}


def _export_conda_env() -> bytes | None:
    """Export the active conda environment as YAML, without platform-specific build strings.

    Returns the conda-env.yml content as bytes, or None if not running in a conda environment.
    """
    conda_exe = os.environ.get('CONDA_EXE')
    if conda_exe is None or 'CONDA_DEFAULT_ENV' not in os.environ:
        return None

    Env.get().console_logger.info(f'Found a conda environment: {os.environ["CONDA_DEFAULT_ENV"]}')
    try:
        result = subprocess.run((conda_exe, 'env', 'export', '--no-builds'), capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        Env.get().console_logger.warning(f'Failed to export conda environment: {exc}')
        return None
    return result.stdout


def _find_lockfile() -> Path | None:
    cwd = Path.cwd()
    for name in ('uv.lock', 'poetry.lock', 'requirements.txt'):
        path = cwd / name
        if path.is_file():
            Env.get().console_logger.info(f'Found a dependency lockfile: {path}')
            return path
    return None


def package(
    env_config: config.EnvironmentConfig,
    config_export: dict[str, Any],
    md_export: dict[str, Any],
    table_md_export: dict[str, Any] | None = None,
    conda_export: bytes | None = None,
    project_dir: Path | None = None,
) -> Path:
    """Bundle the contents of a Pixeltable project directory into a tarball.

    Args:
        env_config: Environment configuration.
        config_export: The environment and service configuration to include in the bundle, as a dict.
        md_export: The table metadata to include in the bundle, as a dict.
        table_md_export: ``{'route_to_table': {path: tbl_id}, 'table_metadata': {tbl_id: TableMetadata}}``.
            Written as ``table_metadata.json`` when provided.
        conda_export: Output of `conda env export --no-builds`, included as `conda-env.yml`
            in the bundle when provided.
        project_dir: Path to the project directory. Defaults to the current working directory.

    Returns:
        Path to the generated tarball.
    """
    if project_dir is None:
        project_dir = Path.cwd()
    project_dir = project_dir.resolve()

    if not project_dir.is_dir():
        raise FileNotFoundError(f'Project directory does not exist: {project_dir}')

    _logger.info(f'Packaging project directory: {project_dir}')
    fd, name = tempfile.mkstemp(suffix='.tar.bz2', prefix='pxt_deploy_')
    os.close(fd)
    bundle_path = Path(name)

    files = _collect_project_files(project_dir, env_config.include, env_config.exclude)
    with tarfile.open(bundle_path, 'w:bz2') as tf:
        __add_tarfile(tf, 'config.toml', toml.dumps(config_export).encode('utf-8'))
        __add_tarfile(tf, 'metadata.json', json.dumps(md_export).encode('utf-8'))
        if table_md_export is not None:
            __add_tarfile(
                tf,
                'table_metadata.json',
                json.dumps(table_md_export, cls=_MetadataEncoder).encode('utf-8'),
            )
        if conda_export is not None:
            __add_tarfile(tf, 'conda-env.yml', conda_export)
        for f in files:
            relpath = f.relative_to(project_dir)
            tf.add(f, arcname=f'project/{relpath}')

    _logger.info(f'Packaging complete: {bundle_path}')
    return bundle_path


def __add_tarfile(tf: tarfile.TarFile, name: str, content: bytes) -> None:
    """Helper function to add a file with the given content to the tarfile being built in `package()`."""
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    tf.addfile(info, fileobj=io.BytesIO(content))


# ---------------------------------------------------------------------------
# Cloud deploy client
# ---------------------------------------------------------------------------

def deploy(environment_name: str, json_output: bool = False, watch: bool = True) -> None:
    """Build the deploy bundle and start a cloud deployment for each service in the environment."""
    cfg = lookup_environment_config(environment_name)
    bundle_path = build_deploy_bundle(environment_name)

    for service_name in cfg.services:
        deploy_resp = _post({
            'operation_type': 'deploy_request',
            'env_name': environment_name,
            'service_name': service_name,
            'bundle_size_bytes': bundle_path.stat().st_size,
        })
        upload_id = deploy_resp['upload_id']
        upload_url = deploy_resp['upload_url']
        service_id = deploy_resp['service_id']

        _upload_to_presigned_url(bundle_path, upload_url)

        finalize_resp = _post({
            'operation_type': 'finalize_deploy',
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

        if watch:
            endpoint = _poll_until_running(service_id, json_output=json_output)
            if endpoint and not json_output:
                Env.get().console_logger.info(f"Service '{service_name}' is live at: {endpoint}")


def _poll_until_running(
    service_id: str,
    timeout: int = 600,
    interval: int = 10,
    json_output: bool = False,
) -> str | None:
    """Poll GET_SERVICE until the current run reaches RUNNING or FAILED; return endpoint or None."""
    deadline = time.monotonic() + timeout
    last_state: str | None = None

    while time.monotonic() < deadline:
        resp = _post({'operation_type': 'get_service', 'service_id': service_id})
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
                    excs.ErrorCode.PROVIDER_ERROR,
                    f'Deployment failed: {error}',
                    provider='pixeltable_cloud',
                )
        time.sleep(interval)

    raise excs.ExternalServiceError(
        excs.ErrorCode.PROVIDER_ERROR,
        f'Deployment did not reach RUNNING within {timeout}s',
        provider='pixeltable_cloud',
    )


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
