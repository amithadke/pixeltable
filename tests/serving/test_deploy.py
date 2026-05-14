"""Tests for deployment bundling (pixeltable/serving/deploy.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import textwrap
import time
import uuid
from pathlib import Path
from uuid import UUID
from unittest.mock import MagicMock

import pytest
import requests
import toml

import pixeltable as pxt
import pixeltable.functions as pxtf
from pixeltable import exceptions as excs, metadata
from pixeltable.config import Config
from pixeltable.runtime import get_runtime
from pixeltable.serving._config import create_service_from_config, lookup_service_config
from pixeltable.serving.bootstrap import _create_tables_from_md
from pixeltable.serving.deploy import build_deploy_bundle
from pixeltable.share.deploy_client import deploy, service_delete
from tests.utils import (
    capture_console_output,
    pxt_raises,
    reload_catalog,
    skip_test_if_no_pxt_credentials,
    skip_test_if_not_installed,
)

from ..utils import pxt_raises, skip_test_if_not_installed


class TestDeploy:
    def test_deploy_bundle(self, uses_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Build a deploy bundle from a TOML config and verify its contents."""
        skip_test_if_not_installed('fastapi')
        from fastapi import FastAPI

        from pixeltable.serving import FastAPIRouter

        _ = pxt.create_table('table1', {'id': pxt.Int, 'name': pxt.String})
        pxt.create_dir('dir1')
        tbl2 = pxt.create_table('dir1.table2', {'id': pxt.Int, 'value': pxt.Float})
        tbl2.add_computed_column(computed=(tbl2.value + 2.7))
        tbl3 = pxt.create_table('dir1.table3', {'id': pxt.Int, 'value': pxt.Float})
        tbl3.add_computed_column(computed=(tbl3.value + 2.9))

        app = FastAPI(title='Pixeltable Test Service', version='0.42')
        router = FastAPIRouter()
        router.add_compute_route(tbl3, path='/compute3')
        app.include_router(router)

        # Monkeypatch a new module with the test service so that config can find it
        test_service_module = MagicMock()
        test_service_module.test_service = app
        monkeypatch.setitem(sys.modules, 'pxttest', test_service_module)

        config_path = tmp_path / 'pixeltable.toml'
        config_contents = textwrap.dedent(
            """\
            [[deployment]]
            name = "deploy-svc1"
            service = "myservice1"
            env = "prod"
            include = ["*.toml", "a*.txt"]
            exclude = ["a_exclude.txt"]

            [[deployment]]
            name = "deploy-svc2"
            service = "myservice2"
            env = "prod"

            [[deployment]]
            name = "deploy-code"
            service = "pxttest:test_service"
            env = "prod"

            [[service]]
            name = "myservice1"

            [[service.routes]]
            type = "compute"
            table = "table1"
            path = "/compute1"

            [[service]]
            name = "myservice2"

            [[service.routes]]
            type = "compute"
            table = "dir1.table2"
            path = "/compute2"
            """
        )
        config_path.write_text(config_contents)
        (tmp_path / 'a_include.txt').write_text("I'm a random artifact in the project folder.")
        (tmp_path / 'a_exclude.txt').write_text("I'm explicitly excluded.")
        (tmp_path / 'b_exclude.txt').write_text("I'm excluded because I'm not in the includes.")

        monkeypatch.chdir(tmp_path)  # cwd until end of test

        Config.init({}, reinit=True)  # pick up the new configuration

        # deploy-svc1: TOML-defined service with file include/exclude
        bundle_path = build_deploy_bundle('deploy-svc1')
        with tarfile.open(bundle_path, 'r:bz2') as tar:
            members = tar.getnames()
            assert 'config.toml' in members
            assert 'metadata.json' in members
            assert 'conda-env.yml' in members
            assert 'project/pixeltable.toml' in members
            assert 'project/a_include.txt' in members
            assert 'project/a_exclude.txt' not in members
            assert 'project/b_exclude.txt' not in members

            # Verify the contents of a_include.txt
            a_include_member = tar.getmember('project/a_include.txt')
            with tar.extractfile(a_include_member) as f:
                text = f.read().decode('utf-8')
                assert text == "I'm a random artifact in the project folder.", text

            # Verify the contents of config.toml
            config_member = tar.getmember('config.toml')
            with tar.extractfile(config_member) as f:
                content = toml.loads(f.read().decode('utf-8'))
                assert content['deployment'][0]['name'] == 'deploy-svc1'
                assert len(content['service']) == 1
                assert content['service'][0]['name'] == 'myservice1'
                assert content['service'][0]['routes'][0]['table'] == 'table1'

            # Verify the contents of metadata.json
            metadata_member = tar.getmember('metadata.json')
            with tar.extractfile(metadata_member) as f:
                content = json.loads(f.read().decode('utf-8'))
                assert content['pxt_version'] == pxt.__version__
                assert content['pxt_md_version'] == metadata.VERSION
                assert len(content['tables_md']) == 2  # 2 tables referenced in services
                assert len(content['tables_md'][0]) == 4  # TableVersionMd + _path

            # Verify the contents of conda-env.yml
            env_member = tar.getmember('conda-env.yml')
            with tar.extractfile(env_member) as f:
                text = f.read().decode('utf-8')
                assert 'pixeltable==' in text, text

            with tar.extractfile(tar.getmember('metadata.json')) as f:
                tables_md = json.loads(f.read().decode('utf-8'))['tables_md']

        # Drop the tables and recreate from the exported metadata to verify round-trip.
        pxt.drop_table('table1')
        pxt.drop_table('table2')
        _create_tables_from_md(tables_md)
        reload_catalog()  # sync in-memory state after direct metadata write

        t1 = pxt.get_table('table1')
        assert set(t1.columns()) == {'id', 'name'}

        t2 = pxt.get_table('table2')
        assert set(t2.columns()) == {'id', 'value'}

        catalog = get_runtime().catalog
        for record in tables_md:
            tbl_id = UUID(record['tbl_md']['tbl_id'])
            with catalog.begin_xact(for_write=False):
                assert catalog.get_table_by_id(tbl_id) is not None, f'table {tbl_id} not found by UUID'

    def test_bootstrap_then_serve(self, uses_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bootstrap recreates tables from metadata.json and pxt serve routes work on top of them."""
        skip_test_if_not_installed('fastapi')
        from fastapi.testclient import TestClient

        _ = pxt.create_table('table1', {'id': pxt.Int, 'name': pxt.String})

        config_path = tmp_path / 'pixeltable.toml'
        config_path.write_text(
            textwrap.dedent("""\
            [[environment]]
            name = "test-serve"
            services = ["mysvc"]

            [[service]]
            name = "mysvc"

            [[service.routes]]
            type = "insert"
            table = "table1"
            path = "/insert"
            """)
        )
        monkeypatch.chdir(tmp_path)
        Config.init({}, reinit=True)

        bundle_path = build_deploy_bundle('test-serve')

        with tarfile.open(bundle_path, 'r:bz2') as tar, tar.extractfile(tar.getmember('metadata.json')) as f:
            tables_md = json.loads(f.read().decode('utf-8'))['tables_md']

        # Simulate container bootstrap: drop the original table, recreate from metadata.
        pxt.drop_table('table1')
        _create_tables_from_md(tables_md)
        reload_catalog()

        # pxt serve: build the FastAPI app from the config bundled with the deploy.
        with tarfile.open(bundle_path, 'r:bz2') as tar, tar.extractfile(tar.getmember('config.toml')) as f:
            (tmp_path / 'pixeltable.toml').write_bytes(f.read())
        Config.init({}, reinit=True)

        svc_cfg = lookup_service_config('mysvc')
        app = create_service_from_config(svc_cfg)
        client = TestClient(app)

        resp = client.post('/insert', json={'id': 1, 'name': 'hello'})
        assert resp.status_code == 200, resp.text

        t = pxt.get_table('table1')
        rows = t.collect().to_pandas()
        assert len(rows) == 1
        assert rows['id'][0] == 1
        assert rows['name'][0] == 'hello'
        
	# deploy-svc2: second TOML-defined service
        bundle_path = build_deploy_bundle('deploy-svc2')
        with tarfile.open(bundle_path, 'r:bz2') as tar:
            config_member = tar.getmember('config.toml')
            with tar.extractfile(config_member) as f:
                content = toml.loads(f.read().decode('utf-8'))
                assert content['deployment'][0]['name'] == 'deploy-svc2'
                assert len(content['service']) == 1
                assert content['service'][0]['name'] == 'myservice2'
                assert content['service'][0]['routes'][0]['table'] == 'dir1.table2'
            metadata_member = tar.getmember('metadata.json')
            with tar.extractfile(metadata_member) as f:
                content = json.loads(f.read().decode('utf-8'))
                assert len(content['tables_md']) == 1

        # deploy-code: code-defined service (pxttest:test_service)
        bundle_path = build_deploy_bundle('deploy-code')
        with tarfile.open(bundle_path, 'r:bz2') as tar:
            config_member = tar.getmember('config.toml')
            with tar.extractfile(config_member) as f:
                content = toml.loads(f.read().decode('utf-8'))
                assert content['deployment'][0]['name'] == 'deploy-code'
                assert 'service' not in content or len(content.get('service', [])) == 0
            metadata_member = tar.getmember('metadata.json')
            with tar.extractfile(metadata_member) as f:
                content = json.loads(f.read().decode('utf-8'))
                assert len(content['tables_md']) == 1

    def test_deploy_bundle_errors(self, uses_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test error paths in build_deploy_bundle()."""
        skip_test_if_not_installed('fastapi')
        from fastapi import FastAPI

        from pixeltable.serving import FastAPIRouter

        config_path = tmp_path / 'pixeltable.toml'
        monkeypatch.chdir(tmp_path)

        # Invalid deployment name
        config_path.write_text(
            textwrap.dedent("""\
            [[deployment]]
            name = "123bad"
            service = "x"
            env = "prod"
            """)
        )
        with pxt_raises(excs.ErrorCode.INVALID_CONFIGURATION, match='not a valid Pixeltable identifier'):
            Config.init({}, reinit=True)

        # No deployments configured
        config_path.write_text('')
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.DEPLOYMENT_NOT_FOUND, match='No deployments found'):
            build_deploy_bundle('nonexistent')

        # Deployment name not found among configured deployments
        config_path.write_text(
            textwrap.dedent("""\
            [[deployment]]
            name = "other-env"
            service = "x"
            env = "prod"
            """)
        )
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.DEPLOYMENT_NOT_FOUND, match="Deployment 'nonexistent' not found"):
            build_deploy_bundle('nonexistent')

        # Service referenced by deployment not found (no services configured)
        config_path.write_text(
            textwrap.dedent("""\
            [[deployment]]
            name = "my-env"
            service = "missing-service"
            env = "prod"
            """)
        )
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.SERVICE_NOT_FOUND, match='No services found'):
            build_deploy_bundle('my-env')

        # Service referenced by deployment not found (different service configured)
        config_path.write_text(
            textwrap.dedent("""\
            [[deployment]]
            name = "my-env"
            service = "missing-service"
            env = "prod"

            [[service]]
            name = "other-service"
            """)
        )
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.SERVICE_NOT_FOUND, match="Service 'missing-service' not found"):
            build_deploy_bundle('my-env')

        # TOML-defined service: route type is not 'compute' (e.g. 'insert')
        _ = pxt.create_table('deploy_err_toml_tbl', {'id': pxt.Int})
        for route_type in ('insert', 'update', 'delete'):
            config_path.write_text(
                textwrap.dedent(f"""\
                [[deployment]]
                name = "my-env"
                service = "my-service"
                env = "prod"

                [[service]]
                name = "my-service"

                [[service.routes]]
                type = "{route_type}"
                table = "deploy_err_toml_tbl"
                path = "/invalid"
                """)
            )
            Config.init({}, reinit=True)
            with pxt_raises(excs.ErrorCode.INVALID_CONFIGURATION, match="only 'compute' routes are supported"):
                build_deploy_bundle('my-env')

        # Table referenced in route does not exist
        config_path.write_text(
            textwrap.dedent("""\
            [[deployment]]
            name = "my-env"
            service = "my-service"
            env = "prod"

            [[service]]
            name = "my-service"

            [[service.routes]]
            type = "compute"
            table = "no_such_table"
            path = "/compute"
            """)
        )
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.PATH_NOT_FOUND, match='no_such_table'):
            build_deploy_bundle('my-env')


class TestDeployCloud:
    """End-to-end test that calls the real Pixeltable cloud API.

    Skipped when PIXELTABLE_API_KEY is not set.
    """

    def test_deploy_e2e(self, uses_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """pxt deploy end-to-end: bundle → cloud API → CodeBuild → Northflank → RUNNING → /health 200."""
        skip_test_if_no_pxt_credentials()
        skip_test_if_not_installed('fastapi')

        uid = uuid.uuid4().hex[:8]
        # Use a pre-existing environment; create it once via:
        #   PIXELTABLE_API_KEY=<key> pxt environment create pytest --org <slug> --cpus 0.5 --memory-gb 1.0 --disk-gb 20
        env_name = os.environ.get('PXT_TEST_ENV_NAME', 'pytest')
        org_slug = os.environ['PXT_TEST_ORG_SLUG']  # required: org_slug is mandatory for all service ops
        svc_name = f'test-svc-{uid}'

        t1 = pxt.create_table('deploy_tbl', {'text': pxt.String})
        t1.add_computed_column(upper_text=t1.text.upper())
        t1.add_computed_column(text_len=t1.text.len())

        t2 = pxt.create_table('deploy_nums', {'value': pxt.Float})
        t2.add_computed_column(doubled=t2.value * 2)
        t2.add_computed_column(is_positive=t2.value > 0)

        t3 = pxt.create_table('deploy_imgs', {'image': pxt.Image})
        t3.add_computed_column(rotated=t3.image.rotate(angle=90))

        # Inject the current git branch as the pixeltable source so CodeBuild installs
        # the dev branch rather than the latest PyPI release.
        branch = (
            subprocess.check_output(['git', 'branch', '--show-current'], cwd=Path(__file__).parent, text=True).strip()
            or 'amit-pxt-deploy'
        )
        (tmp_path / 'pyproject.toml').write_text(
            textwrap.dedent(f"""\
            [project]
            name = "test-svc"
            version = "0.1.0"
            requires-python = ">=3.12"
            dependencies = ["pixeltable"]

            [tool.uv.sources]
            pixeltable = {{ git = "https://github.com/amithadke/pixeltable.git", branch = "{branch}" }}
            """)
        )
        subprocess.run(['uv', 'lock'], cwd=tmp_path, check=True)

        config_path = tmp_path / 'pixeltable.toml'
        config_path.write_text(
            textwrap.dedent(f"""\
            [[environment]]
            name = "{env_name}"
            services = ["{svc_name}"]

            [[service]]
            name = "{svc_name}"

            [[service.routes]]
            type = "insert"
            table = "deploy_tbl"
            path = "/insert-text"
            outputs = ["upper_text", "text_len"]

            [[service.routes]]
            type = "insert"
            table = "deploy_nums"
            path = "/insert-num"
            outputs = ["doubled", "is_positive"]
            background = true

            [[service.routes]]
            type = "insert"
            table = "deploy_imgs"
            path = "/insert-image"
            uploadfile_inputs = ["image"]
            outputs = ["rotated"]
            background = true
            """)
        )
        monkeypatch.chdir(tmp_path)
        Config.init({}, reinit=True)

        with capture_console_output() as out:
            service_ids = deploy(env_name, watch=True, org_slug=org_slug)

        output = out.getvalue()
        assert 'is live at:' in output.lower(), f'Expected live endpoint in output:\n{output}'

        endpoint = None
        for line in output.splitlines():
            if 'live at:' in line.lower():
                endpoint = line.split()[-1]
                break
        assert endpoint is not None

        api_key = os.environ['PIXELTABLE_API_KEY']
        auth_headers = {'X-api-key': api_key}

        try:
            resp = requests.get(f'{endpoint}/health', timeout=10)
            assert resp.status_code == 200, resp.text

            resp = requests.post(
                f'{endpoint}/insert-text', json={'text': 'hello world'}, headers=auth_headers, timeout=10
            )
            print(f'POST /insert-text → {resp.status_code}: {resp.text}')
            assert resp.status_code == 200, resp.text
            assert resp.json() == {'upper_text': 'HELLO WORLD', 'text_len': 11}, resp.text

            resp = requests.post(f'{endpoint}/insert-num', json={'value': 3.0}, headers=auth_headers, timeout=10)
            print(f'POST /insert-num → {resp.status_code}: {resp.text}')
            assert resp.status_code == 200, resp.text
            job = resp.json()
            assert 'job_url' in job, f'Expected BackgroundJobResponse, got: {job}'
            job_url = job['job_url']
            result = None
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                poll = requests.get(job_url, headers=auth_headers, timeout=10)
                assert poll.status_code == 200, poll.text
                body = poll.json()
                if body['status'] == 'done':
                    result = body['result']
                    break
                if body['status'] == 'error':
                    raise AssertionError(f'/insert-num job failed: {body.get("error")}')
                time.sleep(2)
            assert result is not None, f'Job did not complete within 60s, last body: {body}'
            assert result == {'doubled': 6.0, 'is_positive': True}, result

            image_path = Path(__file__).parent.parent / 'data' / 'images' / 'sewing-threads-smaller.jpg'
            with open(image_path, 'rb') as f:
                resp = requests.post(
                    f'{endpoint}/insert-image',
                    files={'image': ('image.jpg', f, 'image/jpeg')},
                    headers=auth_headers,
                    timeout=30,
                )
            print(f'POST /insert-image → {resp.status_code}: {resp.text}')
            assert resp.status_code == 200, resp.text
            job = resp.json()
            assert 'job_url' in job, f'Expected BackgroundJobResponse, got: {job}'
            result = None
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                poll = requests.get(job['job_url'], headers=auth_headers, timeout=10)
                assert poll.status_code == 200, poll.text
                body = poll.json()
                if body['status'] == 'done':
                    result = body['result']
                    break
                if body['status'] == 'error':
                    raise AssertionError(f'/insert-image job failed: {body.get("error")}')
                time.sleep(2)
            assert result is not None, f'Image job did not complete within 60s, last body: {body}'
            assert 'rotated' in result, f'Expected rotated URL in result: {result}'
            rotated_url = result['rotated']
            print(f'GET rotated image: {rotated_url}')
            assert rotated_url.startswith('pxtfs://'), f'Expected pxtfs:// URL, got: {rotated_url}'
            # t3 is a local empty table; resolve pxtfs:// → HTTP via a 1-row helper
            presign_tbl = pxt.create_table('presign_helper', {'uri': pxt.String}, if_exists='replace')
            presign_tbl.insert([{'uri': rotated_url}])
            http_url = presign_tbl.select(url=pxtf.net.presigned_url(presign_tbl.uri, 3600)).collect()['url'][0]
            pxt.drop_table('presign_helper')
            img_resp = requests.get(http_url, timeout=30)
            assert img_resp.status_code == 200, f'Could not fetch rotated image: {img_resp.status_code}'
            assert img_resp.headers.get('content-type', '').startswith('image/'), img_resp.headers
        finally:
            for sid in service_ids.values():
                try:
                    service_delete(sid, org_slug=org_slug)
                except Exception:
                    pass

