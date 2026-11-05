"""AWS construction and cached dependencies for the onboarding entrypoints."""
import os
from functools import lru_cache


def runtime_store():
    import boto3
    from botocore.config import Config
    from onboarding_store import Store
    config = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1})
    table = boto3.resource("dynamodb", config=config).Table(os.environ["ONBOARDING_TABLE"])
    return Store(table, boto3.client("ssm", config=config), os.environ.get("GOG_SSM_PREFIX", "/gogcli-proxy"))


@lru_cache(maxsize=1)
def get_auth():
    from onboarding_auth import OwnerAuth
    return OwnerAuth()


@lru_cache(maxsize=1)
def get_service():
    from onboarding_operations import build_service
    return build_service()
