"""Conditional onboarding metadata and encrypted secret references."""
import base64
import json
import time
import uuid

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def new_id(prefix="op"):
    number = uuid.uuid4().int
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = ALPHABET[remainder] + encoded
    return prefix + "_" + encoded.rjust(22, "1")


def digest(value):
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()


def account_parameter(prefix, email):
    return prefix.rstrip("/") + "/google/accounts/" + base64.urlsafe_b64encode(email.encode()).decode().rstrip("=")


class WorkflowError(Exception):
    def __init__(self, status, code, detail):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


class Store:
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

    def transition(self, key, expected, changes, conditions=None):
        names = {"#phase": "phase"}
        values = {":expected": expected}
        updates = []
        checks = ["#phase = :expected"]
        for index, (name, value) in enumerate((conditions or {}).items()):
            names[f"#c{index}"] = name
            values[f":c{index}"] = value
            checks.append(f"#c{index} = :c{index}")
        for index, (name, value) in enumerate(changes.items()):
            names[f"#n{index}"] = name
            values[f":v{index}"] = value
            updates.append(f"#n{index} = :v{index}")
        try:
            return self.table.update_item(Key={"pk": key}, ConditionExpression=" AND ".join(checks),
                UpdateExpression="SET " + ", ".join(updates), ExpressionAttributeNames=names,
                ExpressionAttributeValues=values, ReturnValues="ALL_NEW")["Attributes"]
        except Exception as exc:
            self._conditional(exc)

    def lock_account(self, email, operation_id, now):
        try:
            self.table.update_item(Key={"pk": "ACCOUNT#" + email},
                ConditionExpression="attribute_not_exists(active_operation) OR lock_expires < :now",
                UpdateExpression="SET active_operation = :op, operation_id = :op, lock_expires = :expires",
                ExpressionAttributeValues={":op": operation_id, ":now": now, ":expires": now + 1800})
        except Exception as exc:
            self._conditional(exc)

    def release_account(self, email, operation_id):
        operation = self.get("OP#" + operation_id) or {}
        kwargs = {"Key": {"pk": "ACCOUNT#" + email}, "ConditionExpression": "active_operation = :op",
            "UpdateExpression": "REMOVE active_operation, lock_expires", "ExpressionAttributeValues": {":op": operation_id}}
        if operation.get("phase") in {"ready", "verification_failed", "failed"}:
            snapshot = {key: operation[key] for key in ("phase", "status", "verified_at", "promoted_at", "error", "error_code", "recovery") if key in operation}
            snapshot["terminal_operation_id"] = operation_id
            names, updates = {}, []
            for index, (name, value) in enumerate(snapshot.items()):
                names[f"#s{index}"] = name
                kwargs["ExpressionAttributeValues"][f":s{index}"] = value
                updates.append(f"#s{index} = :s{index}")
            kwargs["ExpressionAttributeNames"] = names
            kwargs["UpdateExpression"] = "SET " + ", ".join(updates) + " REMOVE active_operation, lock_expires"
        try:
            self.table.update_item(**kwargs)
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

    def accounts(self):
        from boto3.dynamodb.conditions import Attr
        items, cursor = [], None
        while True:
            kwargs = {"FilterExpression": Attr("pk").begins_with("ACCOUNT#")}
            if cursor:
                kwargs["ExclusiveStartKey"] = cursor
            response = self.table.scan(**kwargs)
            items.extend(response.get("Items", []))
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                return items

    def secret_put(self, suffix, value):
        name = self.prefix + "/onboarding/" + suffix
        self.ssm.put_parameter(Name=name, Type="SecureString", KeyId="alias/aws/ssm",
            Value=json.dumps(value), Overwrite=True)
        return name

    def secret_get(self, name):
        return json.loads(self.ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"])

    def secret_delete(self, name):
        if name:
            try:
                self.ssm.delete_parameter(Name=name)
            except Exception as exc:
                if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ParameterNotFound":
                    raise

    @staticmethod
    def _conditional(exc):
        if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise WorkflowError(409, "operation_conflict", "This operation already changed; reload its status.") from None
        raise exc
