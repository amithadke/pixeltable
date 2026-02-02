"""
Remote functionality for Pixeltable.

This package contains all the remote client, server, and utility components
for handling remote function calls in Pixeltable.
"""

from .client import RemoteClient
from .remote_schema_objects import RemoteDir, RemoteTable
from .utils import ModelCache, convert_local_path_to_remote, convert_remote_path_to_local, is_remote_path

# Lazy import to avoid requiring FastAPI when only utils (e.g. create_pydantic_model_from_function) is used
def __getattr__(name: str):
    if name in ('RemoteServer', 'app'):
        from .server import RemoteServer, app
        return app if name == 'app' else RemoteServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    'ModelCache',
    'RemoteClient',
    'RemoteDir',
    'RemoteServer',
    'RemoteTable',
    'app',
    'convert_local_path_to_remote',
    'convert_remote_path_to_local',
    'is_remote_path',
]
