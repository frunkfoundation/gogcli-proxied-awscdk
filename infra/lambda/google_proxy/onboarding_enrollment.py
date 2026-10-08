"""Shared enrollment prerequisites for owner commands and frozen connection links."""
from errors import WorkflowError
from onboarding_services import selected_services


def enrollment_services(account, body, removal_detail):
    services = selected_services(body.get("services", account.get("installed_services") or account["requested_services"]))
    if not set(account.get("installed_services", [])).issubset(services):
        raise WorkflowError(409, "scope_removal_requires_policy", removal_detail)
    return services


def enrollment_client(store, project, services, setup_detail):
    client = store.get("CLIENT#" + project)
    if not client or not client.get("secret_ref") or not client.get("revision") or any(not client.get("api_confirmed", {}).get(name) for name in services):
        raise WorkflowError(409, "application_setup_required", setup_detail)
    return client
