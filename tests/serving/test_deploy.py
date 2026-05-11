"""Tests for deployment bundling (pixeltable/serving/deploy.py)."""

from __future__ import annotations

import json
import tarfile
import textwrap
from pathlib import Path
from uuid import UUID

import pytest
import toml

import pixeltable as pxt
from pixeltable import exceptions as excs, metadata
from pixeltable.config import Config
from pixeltable.runtime import get_runtime
from pixeltable.serving.bootstrap import _create_tables_from_md
from pixeltable.serving.deploy import build_deploy_bundle
from tests.utils import pxt_raises, reload_catalog


class TestDeploy:
    def test_deploy_bundle(self, uses_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Build a deploy bundle from a TOML config and verify its contents."""
        _ = pxt.create_table('table1', {'id': pxt.Int, 'name': pxt.String})
        _ = pxt.create_table('table2', {'id': pxt.Int, 'value': pxt.Float})

        config_path = tmp_path / 'pixeltable.toml'
        config_contents = textwrap.dedent(
            """\
            [[environment]]
            name = "test-deploy"
            include = ["*.toml", "a*.txt"]
            exclude = ["a_exclude.txt"]
            services = ["myservice1", "myservice2"]

            [[service]]
            name = "myservice1"

            [[service.routes]]
            type = "insert"
            table = "table1"
            path = "/insert"

            [[service]]
            name = "myservice2"

            [[service.routes]]
            type = "insert"
            table = "table2"
            path = "/insert"
            """
        )
        config_path.write_text(config_contents)
        (tmp_path / 'a_include.txt').write_text("I'm a random artifact in the project folder.")
        (tmp_path / 'a_exclude.txt').write_text("I'm explicitly excluded.")
        (tmp_path / 'b_exclude.txt').write_text("I'm excluded because I'm not in the includes.")

        monkeypatch.chdir(tmp_path)  # cwd until end of test

        Config.init({}, reinit=True)  # pick up the new configuration

        bundle_path = build_deploy_bundle('test-deploy')

        # Extract the bundle and verify contents
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
                assert content['environment'][0]['name'] == 'test-deploy'
                assert len(content['service']) == 2
                assert content['service'][0]['name'] == 'myservice1'
                assert content['service'][0]['routes'][0]['table'] == 'table1'
                assert content['service'][1]['name'] == 'myservice2'
                assert content['service'][1]['routes'][0]['table'] == 'table2'

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

    def test_deploy_bundle_errors(self, uses_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test error paths in build_deploy_bundle()."""
        config_path = tmp_path / 'pixeltable.toml'
        monkeypatch.chdir(tmp_path)

        # No environments configured
        config_path.write_text('')
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.ENVIRONMENT_NOT_FOUND, match='No environments found'):
            build_deploy_bundle('nonexistent')

        # Environment name not found among configured environments
        config_path.write_text(
            textwrap.dedent("""\
            [[environment]]
            name = "other-env"
            """)
        )
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.ENVIRONMENT_NOT_FOUND, match="Environment 'nonexistent' not found"):
            build_deploy_bundle('nonexistent')

        # Service referenced by environment not found (no services configured)
        config_path.write_text(
            textwrap.dedent("""\
            [[environment]]
            name = "my-env"
            services = ["missing-service"]
            """)
        )
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.SERVICE_NOT_FOUND, match='No services found'):
            build_deploy_bundle('my-env')

        # Service referenced by environment not found (different service configured)
        config_path.write_text(
            textwrap.dedent("""\
            [[environment]]
            name = "my-env"
            services = ["missing-service"]

            [[service]]
            name = "other-service"
            """)
        )
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.SERVICE_NOT_FOUND, match="Service 'missing-service' not found"):
            build_deploy_bundle('my-env')

        # Table referenced in route does not exist
        config_path.write_text(
            textwrap.dedent("""\
            [[environment]]
            name = "my-env"
            services = ["my-service"]

            [[service]]
            name = "my-service"

            [[service.routes]]
            type = "insert"
            table = "no_such_table"
            path = "/insert"
            """)
        )
        Config.init({}, reinit=True)
        with pxt_raises(excs.ErrorCode.PATH_NOT_FOUND, match='no_such_table'):
            build_deploy_bundle('my-env')
