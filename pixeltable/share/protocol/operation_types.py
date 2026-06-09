"""
Operation types for pixeltable shared protocol.

This module defines operation types that are shared between
pixeltable core and cloud implementations.
"""

from __future__ import annotations

from enum import Enum


class ReplicaOperationType(str, Enum):
    """Replica operation types for table replica operations."""

    PUBLISH_REPLICA = 'publish_replica'
    FINALIZE_REPLICA = 'finalize_replica'
    CLONE_REPLICA = 'clone_replica'
    DELETE_REPLICA = 'delete_replica'


class PixeltableStoreOperationType(str, Enum):
    """Operation types for Pixeltable-managed storage (home buckets)."""

    GET_BUCKET_CREDENTIALS = 'get_bucket_credentials'
    GET_PRESIGNED_URL = 'get_presigned_url'


REPLICA_OPERATIONS = frozenset(ReplicaOperationType)
PIXELTABLE_STORE_OPERATIONS = frozenset(PixeltableStoreOperationType)


class ServiceOperationType(str, Enum):
    """Operation types for pxt service deployment (environment, service, deploy, run)."""

    CREATE_ENVIRONMENT = 'create_environment'
    GET_ENVIRONMENT = 'get_environment'
    LIST_ENVIRONMENTS = 'list_environments'
    UPDATE_ENVIRONMENT = 'update_environment'
    DELETE_ENVIRONMENT = 'delete_environment'

    CREATE_SERVICE = 'create_service'
    GET_SERVICE = 'get_service'
    LIST_SERVICES = 'list_services'
    STOP_SERVICE = 'stop_service'
    START_SERVICE = 'start_service'
    DELETE_SERVICE = 'delete_service'

    DEPLOY_REQUEST = 'deploy_request'
    FINALIZE_DEPLOY = 'finalize_deploy'

    GET_SERVICE_RUN = 'get_service_run'
    LIST_SERVICE_RUNS = 'list_service_runs'

    ADD_ENV_SECRET = 'add_env_secret'
    REMOVE_ENV_SECRET = 'remove_env_secret'
    LIST_ENV_SECRETS = 'list_env_secrets'

    ADD_ORG_SECRET = 'add_org_secret'
    REMOVE_ORG_SECRET = 'remove_org_secret'
    LIST_ORG_SECRETS = 'list_org_secrets'

    CREATE_NODE_POOL = 'create_node_pool'
    DELETE_NODE_POOL = 'delete_node_pool'
    LIST_NODE_POOLS = 'list_node_pools'

    CREATE_CLUSTER = 'create_cluster'
    GET_CLUSTER = 'get_cluster'
    LIST_CLUSTERS = 'list_clusters'
    UPDATE_CLUSTER = 'update_cluster'
    DELETE_CLUSTER = 'delete_cluster'


SERVICE_OPERATIONS = frozenset(ServiceOperationType)
