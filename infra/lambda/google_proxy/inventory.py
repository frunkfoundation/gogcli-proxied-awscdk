"""Caller-filtered metadata inventory; never fetch Google credentials."""
import os
from errors import ProxyError, remaining_seconds

_TABLE = None


def metadata_table():
    global _TABLE
    if _TABLE is None:
        name = os.getenv('ONBOARDING_TABLE', '')
        if not name:
            raise ProxyError(500, 'account inventory unavailable')
        import boto3
        from botocore.config import Config
        _TABLE = boto3.resource('dynamodb', config=Config(connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 1})).Table(name)
    return _TABLE


def caller_accounts(allow, is_allowed, *, deadline):
    from onboarding_services import account_projection
    table = metadata_table()
    emails = [value.strip().lower() for value in allow.get('emails', [])]
    broad = '*' in emails or bool(allow.get('domains'))
    rows = []
    if broad:
        kwargs = {'FilterExpression': 'begins_with(pk, :prefix)', 'ExpressionAttributeValues': {':prefix': 'ACCOUNT#'}}
        while True:
            remaining_seconds(deadline)
            result = table.scan(**kwargs)
            rows.extend(result.get('Items', []))
            if not result.get('LastEvaluatedKey'):
                break
            kwargs['ExclusiveStartKey'] = result['LastEvaluatedKey']
    else:
        for email in sorted({str(value).strip().lower() for value in emails}):
            remaining_seconds(deadline)
            row = table.get_item(Key={'pk': 'ACCOUNT#' + email}, ConsistentRead=True).get('Item')
            if row:
                rows.append(row)
    accounts = []
    for row in rows:
        email = row.get('email', '')
        if not email or not is_allowed(allow, email) or not row.get('credential_revision'):
            continue
        projection = account_projection(row)
        accounts.append({key: projection[key] for key in ('email', 'connection_status', 'services')})
    remaining_seconds(deadline)
    return {'accounts': sorted(accounts, key=lambda item: item['email']), 'setup_url': os.getenv('ONBOARDING_SETUP_URL', '')}
