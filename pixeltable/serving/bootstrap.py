"""
Container startup bootstrap for a pxt-deploy service.

Creates all tables from /app/metadata.json, then hands off to `pxt serve`.
The Dockerfile CMD should be:
    python -m pixeltable.serving.bootstrap <service_name> [--host HOST] [--port PORT]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from pixeltable.catalog.globals import IfExistsParam
from pixeltable.catalog.path import Path as PxtPath
from pixeltable.catalog.table_version import TableVersion, TableVersionKey, TableVersionMd
from pixeltable.runtime import get_runtime
from pixeltable.metadata import schema

_METADATA_PATH = Path('/app/metadata.json')


def _create_tables_from_md(tables_md: list[dict[str, Any]]) -> None:
    catalog = get_runtime().catalog

    for record in tables_md:
        path_str = record.get('_path')
        assert path_str, f'table record missing _path: {record}'

        md = schema.md_from_dict(TableVersionMd, {k: v for k, v in record.items() if k != '_path'})
        tbl_id = UUID(md.tbl_md.tbl_id)

        pxt_path = PxtPath.parse(path_str)
        parent_path = pxt_path.parent

        # create_dir asserts no active transaction; call it before opening one
        if not parent_path.is_root:
            catalog.create_dir(parent_path, if_exists=IfExistsParam.IGNORE, parents=True)

        with catalog.begin_xact(for_write=True):
            if catalog.get_table_by_id(tbl_id) is not None:
                continue  # idempotent

            dir_id = catalog._get_dir(parent_path).id

            catalog.write_tbl_md(tbl_id, dir_id, md.tbl_md, md.version_md, md.schema_version_md)

            key = TableVersionKey(tbl_id, md.version_md.version, None)
            tbl_version = TableVersion(key, md.tbl_md, md.version_md, md.schema_version_md, [])
            catalog._tbl_versions[key] = tbl_version
            with catalog._allow_tbl_md_read():
                tbl_version.init()
            tbl_version.store_tbl.create()


def run(service_name: str, host: str | None = None, port: int | None = None) -> None:
    data = json.loads(_METADATA_PATH.read_text())
    _create_tables_from_md(data['tables_md'])

    args = ['pxt', 'serve', service_name]
    if host is not None:
        args += ['--host', host]
    if port is not None:
        args += ['--port', str(port)]
    os.execvp('pxt', args)


if __name__ == '__main__':
    argv = sys.argv[1:]
    if not argv or argv[0].startswith('-'):
        print('Usage: python -m pixeltable.serving.bootstrap <service_name> [--host HOST] [--port PORT]', file=sys.stderr)
        sys.exit(1)
    service_name = argv[0]
    host = None
    port = None
    i = 1
    while i < len(argv):
        if argv[i] == '--host' and i + 1 < len(argv):
            host = argv[i + 1]; i += 2
        elif argv[i] == '--port' and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 2
        else:
            i += 1
    run(service_name, host, port)
