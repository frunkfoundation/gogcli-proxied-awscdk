"""Conditional account generations, transactional commands, and encrypted artifacts."""
import base64
import hashlib
import json
import uuid

from errors import WorkflowError
from onboarding_links_store import ConnectionStore

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
RETENTION = 604800


def new_id(prefix="op"):
    number, encoded = uuid.uuid4().int, ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = ALPHABET[remainder] + encoded
    return prefix + "_" + encoded.rjust(22, "1")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def encoded(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def account_parameter(prefix, email):
    return prefix.rstrip("/") + "/google/accounts/" + encoded(email)


class Store(ConnectionStore):
    def __init__(self, table, ssm, prefix):
        self.table, self.ssm, self.prefix = table, ssm, prefix.rstrip("/")

    def get(self, key):
        return self.table.get_item(Key={"pk": key}, ConsistentRead=True).get("Item")

    def put(self, item, absent=False):
        kwargs = {"Item": item}
        if absent:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
        try:
            self.table.put_item(**kwargs)
        except Exception as exc:
            self._conditional(exc)

    @staticmethod
    def update_parts(changes):
        names, values, updates = {}, {}, []
        for index, (name, value) in enumerate(changes.items()):
            names[f"#n{index}"], values[f":v{index}"] = name, value
            updates.append(f"#n{index} = :v{index}")
        return "SET " + ", ".join(updates), names, values

    def transition(self, key, expected, changes, conditions=None):
        expression, names, values = self.update_parts(changes)
        names["#phase"], values[":expected"] = "phase", expected
        checks = ["#phase = :expected"]
        for index, (name, value) in enumerate((conditions or {}).items()):
            names[f"#c{index}"], values[f":c{index}"] = name, value
            checks.append(f"#c{index} = :c{index}")
        try:
            return self.table.update_item(Key={"pk": key}, ConditionExpression=" AND ".join(checks),
                UpdateExpression=expression, ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW")["Attributes"]
        except Exception as exc:
            self._conditional(exc)

    def transaction(self, actions):
        # Table's resource client applies boto3's document serialization to transactions.
        try:
            self.table.meta.client.transact_write_items(TransactItems=actions)
        except Exception as exc:
            self._conditional(exc)

    def accept(self, account, operation, idem, changes, supersede=False):
        expression, names, values = self.update_parts({**changes, "generation": operation["generation"],
            "active_operation": operation["id"], "latest_operation_id": operation["id"]})
        names["#generation"], values[":old"] = "generation", account["generation"]
        condition = "#generation = :old"
        if account.get("active_operation"):
            values[":oldop"] = account["active_operation"]
            condition += " AND active_operation = :oldop"
            if not supersede:
                raise WorkflowError(409, "operation_conflict", "An account action is already active.")
        else:
            condition += " AND attribute_not_exists(active_operation)"
        actions = [{"Put": {"TableName": self.table.name, "Item": operation, "ConditionExpression": "attribute_not_exists(pk)"}},
            {"Update": {"TableName": self.table.name, "Key": {"pk": account["pk"]}, "ConditionExpression": condition,
                "UpdateExpression": expression, "ExpressionAttributeNames": names, "ExpressionAttributeValues": values}}]
        if idem:
            actions.append({"Put": {"TableName": self.table.name, "Item": idem, "ConditionExpression": "attribute_not_exists(pk)"}})
        if operation.get("connection_link_id"):
            actions.extend(self.connection_accept_actions(operation, idem))
        old_id = account.get("active_operation")
        if supersede and old_id:
            old = self.get("OP#" + old_id)
            if old:
                if operation.get("kind") == "enrollment" and old["phase"] not in {"awaiting_connection", "preparing_consent", "authorizing"}:
                    raise WorkflowError(409, "operation_conflict", "Authorization has advanced; wait or use restrictive account controls.")
                from onboarding_services import public_operation
                canceled = {**old, "phase": "canceled", "status": "canceled", "updated_at": operation["created_at"],
                    "expires_at": operation["created_at"] + RETENTION, "error_code": "operation_superseded",
                    "recovery": "A newer account command replaced this attempt."}
                actions.append({"Put": {"TableName": self.table.name, "Item": canceled,
                    "ConditionExpression": "#phase = :phase", "ExpressionAttributeNames": {"#phase": "phase"},
                    "ExpressionAttributeValues": {":phase": old["phase"]}}})
                if old.get("idempotency_pk"):
                    key = self.get(old["idempotency_pk"])
                    if key:
                        actions.append({"Put": {"TableName": self.table.name, "Item": {**key,
                            "result": public_operation(canceled), "expires_at": canceled["expires_at"]}}})
        self.transaction(actions)

    def update_account(self, operation, changes, lease=None):
        expression, names, values = self.update_parts(changes)
        names["#generation"], values[":generation"] = "generation", operation["generation"]
        values[":op"] = operation["id"]
        condition = "#generation = :generation AND active_operation = :op"
        if lease:
            condition += " AND effect_lease_owner = :lease"
            values[":lease"] = lease
        try:
            return self.table.update_item(Key={"pk": "ACCOUNT#" + operation["email"]}, ConditionExpression=condition,
                UpdateExpression=expression, ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW")["Attributes"]
        except Exception as exc:
            self._conditional(exc)

    def acquire_effect(self, operation, now):
        lease = new_id("ls")
        try:
            self.table.update_item(Key={"pk": "ACCOUNT#" + operation["email"]},
                ConditionExpression="#g = :g AND active_operation = :op AND (attribute_not_exists(effect_lease_until) OR effect_lease_until <= :now)",
                UpdateExpression="SET effect_lease_until = :until, effect_lease_owner = :lease",
                ExpressionAttributeNames={"#g": "generation"}, ExpressionAttributeValues={":g": operation["generation"],
                    ":op": operation["id"], ":now": now, ":until": now + 60, ":lease": lease})
            return lease
        except Exception as exc:
            self._conditional(exc)

    def release_effect(self, email, lease):
        try:
            self.table.update_item(Key={"pk": "ACCOUNT#" + email}, ConditionExpression="effect_lease_owner = :lease",
                UpdateExpression="REMOVE effect_lease_until, effect_lease_owner", ExpressionAttributeValues={":lease": lease})
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

    def terminal(self, operation, changes, now):
        from onboarding_services import public_operation
        updated = {**operation, **changes, "updated_at": now, "expires_at": now + RETENTION}
        updated.pop("lease_owner", None)
        updated.pop("lease_until", None)
        condition = "#phase = :phase"
        values = {":phase": operation["phase"]}
        if operation.get("lease_owner"):
            condition += " AND lease_owner = :lease"
            values[":lease"] = operation["lease_owner"]
        actions = [{"Put": {"TableName": self.table.name, "Item": updated,
            "ConditionExpression": condition, "ExpressionAttributeNames": {"#phase": "phase"},
            "ExpressionAttributeValues": values}}]
        if operation.get("idempotency_pk"):
            key = self.get(operation["idempotency_pk"])
            if key:
                actions.append({"Put": {"TableName": self.table.name, "Item": {**key,
                    "result": public_operation(updated), "expires_at": now + RETENTION}}})
        account = self.get("ACCOUNT#" + operation["email"]) or {}
        if account.get("active_operation") == operation["id"] and account.get("generation") == operation["generation"]:
            expression, names, values = self.update_parts({"latest_operation": public_operation(updated)})
            names["#g"], values[":g"], values[":op"] = "generation", operation["generation"], operation["id"]
            actions.append({"Update": {"TableName": self.table.name, "Key": {"pk": "ACCOUNT#" + operation["email"]},
                "ConditionExpression": "#g = :g AND active_operation = :op", "UpdateExpression": expression + " REMOVE active_operation",
                "ExpressionAttributeNames": names, "ExpressionAttributeValues": values}})
        self.transaction(actions)

    def confirm_project(self, client, changes):
        expression, names, values = self.update_parts(changes)
        names["#revision"], values[":revision"] = "revision", client["revision"]
        try:
            self.table.update_item(Key={"pk": client["pk"]}, ConditionExpression="#revision = :revision",
                UpdateExpression=expression, ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        except Exception as exc:
            self._conditional(exc)

    def save_account_metadata(self, original, updated):
        existing = self.get(original["pk"])
        kwargs = {"Item": updated}
        if existing:
            kwargs.update(ConditionExpression="#g = :g AND attribute_not_exists(active_operation)",
                ExpressionAttributeNames={"#g": "generation"}, ExpressionAttributeValues={":g": original["generation"]})
        else:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
        try:
            self.table.put_item(**kwargs)
        except Exception as exc:
            self._conditional(exc)

    def claim_operation(self, operation, lease, now):
        try:
            return self.table.update_item(Key={"pk": operation["pk"]},
                ConditionExpression="#phase = :phase AND (attribute_not_exists(lease_until) OR lease_until <= :now)",
                UpdateExpression="SET lease_owner = :lease, lease_until = :until",
                ExpressionAttributeNames={"#phase": "phase"}, ExpressionAttributeValues={":phase": operation["phase"],
                    ":now": now, ":lease": lease, ":until": now + 60}, ReturnValues="ALL_NEW")["Attributes"]
        except Exception as exc:
            self._conditional(exc)

    def finish_operation(self, operation, lease, changes):
        expression, names, values = self.update_parts(changes)
        names["#phase"], values[":phase"], values[":lease"] = "phase", operation["phase"], lease
        try:
            return self.table.update_item(Key={"pk": operation["pk"]},
                ConditionExpression="#phase = :phase AND lease_owner = :lease",
                UpdateExpression=expression + " REMOVE lease_until, lease_owner", ExpressionAttributeNames=names,
                ExpressionAttributeValues=values, ReturnValues="ALL_NEW")["Attributes"]
        except Exception as exc:
            self._conditional(exc)

    def allow_lock(self, key, lease, now):
        try:
            self.table.update_item(Key={"pk": key},
                ConditionExpression="attribute_not_exists(lease_until) OR lease_until <= :now",
                UpdateExpression="SET lease_until = :until, lease_owner = :lease, expires_at = :expires",
                ExpressionAttributeValues={":now": now, ":until": now + 60, ":lease": lease, ":expires": now + 300})
        except Exception as exc:
            self._conditional(exc)

    def allow_unlock(self, key, lease):
        try:
            self.table.update_item(Key={"pk": key}, ConditionExpression="lease_owner = :lease",
                UpdateExpression="REMOVE lease_until, lease_owner", ExpressionAttributeValues={":lease": lease})
        except Exception as exc:
            self._conditional(exc)

    def rows(self, prefix):
        from boto3.dynamodb.conditions import Attr
        items, cursor = [], None
        while True:
            kwargs = {"FilterExpression": Attr("pk").begins_with(prefix), "ConsistentRead": True}
            if cursor:
                kwargs["ExclusiveStartKey"] = cursor
            response = self.table.scan(**kwargs)
            items.extend(response.get("Items", []))
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                return items

    def accounts(self):
        return self.rows("ACCOUNT#")

    def secret_put(self, suffix, value):
        name = self.prefix + "/onboarding/" + suffix
        self.parameter_put(name, value)
        return name

    def parameter_put(self, name, value):
        self.ssm.put_parameter(Name=name, Type="SecureString", KeyId="alias/aws/ssm", Value=json.dumps(value), Overwrite=True)

    def secret_get(self, name):
        return json.loads(self.ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"])

    def optional_secret(self, name):
        try:
            return self.secret_get(name)
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ParameterNotFound":
                return None
            raise

    def secret_delete(self, name):
        if name:
            try:
                self.ssm.delete_parameter(Name=name)
            except Exception as exc:
                if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ParameterNotFound":
                    raise

    @staticmethod
    def _conditional(exc):
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in {"ConditionalCheckFailedException", "TransactionCanceledException"}:
            reasons = getattr(exc, "response", {}).get("CancellationReasons", [])
            if code == "TransactionCanceledException" and reasons and any(reason.get("Code") not in {"None", "ConditionalCheckFailed"} for reason in reasons):
                raise exc
            raise WorkflowError(409, "operation_conflict", "Account work changed; refresh its saved status.") from None
        raise exc
