import unittest
from unittest.mock import patch

import proxy_authorization as authorization
import proxy_config
import handler


class CallerIdentityTests(unittest.TestCase):
    def _event(self, **identity):
        return {"requestContext": {"identity": identity}}

    def test_role_identity_is_stable_across_sessions(self):
        for session in ("agent-one", "agent-two"):
            event = self._event(userArn=f"arn:aws:sts::123456789012:assumed-role/proxy-agent/{session}")
            self.assertEqual("arn:aws:iam::123456789012:role/proxy-agent", authorization._caller_id(event))

    def test_session_name_cannot_collide_across_roles_or_accounts(self):
        identities = [authorization._caller_id(self._event(userArn=arn)) for arn in (
            "arn:aws:sts::123456789012:assumed-role/agent-one/shared-session",
            "arn:aws:sts::123456789012:assumed-role/agent-two/shared-session",
            "arn:aws:sts::987654321098:assumed-role/agent-one/shared-session",
        )]
        self.assertEqual(3, len(set(identities)))

    def test_caller_arn_when_user_arn_missing(self):
        event = self._event(caller="arn:aws-us-gov:sts::123456789012:assumed-role/agent/session")
        self.assertEqual("arn:aws-us-gov:iam::123456789012:role/agent", authorization._caller_id(event))

    def test_iam_user_arn_preserved(self):
        arn = "arn:aws:iam::123456789012:user/agent"
        self.assertEqual(arn, authorization._caller_id(self._event(userArn=arn)))

    def test_cognito_identity_still_wins(self):
        event = self._event(cognitoIdentityId="us-east-2:1234-5678", caller="AROA:session")
        self.assertEqual("us-east-2:1234-5678", authorization._caller_id(event))

    def test_missing_or_untrusted_identity_fails_before_allowlist_lookup(self):
        for identity in ({}, {"caller": "AROA2DVGUO577DSQZOPJD:agent"}, {"userArn": "unknown"}):
            event = self._event(**identity)
            event["headers"] = {"X-GOG-Account": "agent@example.com"}
            with self.subTest(identity=identity), patch.object(proxy_config, "_ssm_get_json") as lookup:
                response = handler.handler(event, None)
                self.assertEqual(403, response["statusCode"])
                lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
