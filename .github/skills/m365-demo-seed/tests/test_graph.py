import unittest

from support import TenantCase
from fake_tenant import Reply, claims_challenge

from m365demo import auth
from m365demo.common import Blocked
from m365demo.graph import CLAIMS_WAITS, Graph, GraphError, quote

MEGAN = "megan@contoso-demo.example"


class GraphTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.fake.add_user(MEGAN, "pw")
        self.session, _ = auth.password_sign_in(self.fake.receipt(), MEGAN, "pw")
        self.sleeps = []
        self.graph = Graph(self.session, sleep=self.sleeps.append)

    def test_pages_follow_next_links(self):
        url = self.fake.url

        def items(request):
            page = int(request["query"].get("page", "1"))
            body = {"value": [{"n": page * 10 + i} for i in range(2)]}
            if page < 3:
                body["@odata.nextLink"] = f"{url}/v1.0/things?page={page + 1}"
            return Reply(200, body)

        self.fake.route("GET", r"^/v1\.0/things$", items)
        self.assertEqual([i["n"] for i in self.graph.pages("things")], [10, 11, 20, 21, 30, 31])
        self.assertEqual(len(list(self.graph.pages("things", limit=3))), 3)

    def test_a_next_link_to_another_host_is_not_followed(self):
        self.fake.route("GET", r"^/v1\.0/things$",
                        lambda request: Reply(200, {"value": [], "@odata.nextLink": "https://evil.example/v1.0/x"}))
        with self.assertRaises(Blocked) as caught:
            list(self.graph.pages("things"))
        self.assertEqual(caught.exception.reason, "unexpected-link")
        self.assertEqual(len(self.fake.calls("GET", "things$")), 1)

    def test_throttling_is_retried_with_retry_after(self):
        answers = [Reply(429, {"error": {"code": "TooManyRequests"}}, {"Retry-After": "7"}),
                   Reply(503, {"error": {"code": "ServiceUnavailable"}}), Reply(200, {"ok": True})]
        self.fake.route("GET", r"^/v1\.0/busy$", lambda request: answers.pop(0))
        self.assertEqual(self.graph.get("busy"), {"ok": True})
        self.assertEqual(self.sleeps, [7.0, 4.0])

    def test_retries_end_with_the_error(self):
        self.fake.route("GET", r"^/v1\.0/busy$", lambda request: Reply(429, {"error": {"code": "TooManyRequests"}},
                                                                         {"Retry-After": "500"}))
        with self.assertRaises(GraphError) as caught:
            self.graph.get("busy")
        self.assertEqual((caught.exception.status, caught.exception.reason), (429, "graph-error"))
        self.assertEqual(self.sleeps, [60.0] * 5)
        self.assertEqual(caught.exception.report()["code"], "TooManyRequests")

    def test_errors_carry_status_code_and_message_without_the_query(self):
        self.fake.route("GET", r"^/v1\.0/secret$", lambda request: Reply(
            403, {"error": {"code": "Forbidden", "message": "Insufficient privileges.\nmore"}}))
        with self.assertRaises(GraphError) as caught:
            self.graph.get("secret?$filter=x")
        error = caught.exception
        self.assertEqual((error.status, error.code, error.detail), (403, "Forbidden", "Insufficient privileges."))
        self.assertEqual(error.message, "GET secret: 403 Forbidden Insufficient privileges.")

    def test_delete_reports_whether_something_was_deleted(self):
        self.fake.route("DELETE", r"^/v1\.0/items/gone$", lambda request: Reply(404, {"error": {"code": "NotFound"}}))
        self.fake.route("DELETE", r"^/v1\.0/items/here$", lambda request: Reply(204))
        self.assertTrue(self.graph.delete("items/here"))
        self.assertFalse(self.graph.delete("items/gone"))
        with self.assertRaises(GraphError):
            self.graph.delete("items/gone", missing_ok=False)

    def test_bodies_and_the_bearer_token(self):
        self.fake.route("POST", r"^/v1\.0/echo$", lambda request: Reply(201, {"got": request["json"]}))
        self.fake.route("PUT", r"^/v1\.0/upload$", lambda request: Reply(200, {"size": len(request["raw"]),
                                                                                "type": request["headers"]["content-type"]}))
        self.assertEqual(self.graph.post("echo", {"a": 1}), {"got": {"a": 1}})
        self.assertEqual(self.graph.put("upload", b"\x00\x01\x02", "application/zip"), {"size": 3, "type": "application/zip"})
        self.assertTrue(all(r["caller"] == MEGAN for r in self.fake.calls(pattern="echo|upload")))

    def test_a_post_is_never_repeated_after_a_gateway_timeout(self):
        self.fake.route("POST", r"^/v1\.0/act$", lambda request: Reply(504, {"error": {"code": "GatewayTimeout"}}))
        self.fake.route("GET", r"^/v1\.0/read$", lambda request: Reply(504, {"error": {"code": "GatewayTimeout"}}))
        with self.assertRaises(GraphError):
            self.graph.post("act", {})
        self.assertEqual(len(self.fake.calls("POST", "act$")), 1)
        with self.assertRaises(GraphError):
            self.graph.get("read")
        self.assertEqual(len(self.fake.calls("GET", "read$")), 6)

    def refreshes(self):
        return [r for r in self.fake.calls("POST", "token$") if r["form"].get("grant_type") == "refresh_token"]

    def test_a_claims_challenge_waits_for_conditional_access_without_renewing_the_token(self):
        logs = []
        graph = Graph(self.session, sleep=self.sleeps.append, log=logs.append)
        answers = [claims_challenge(header=False), claims_challenge(), Reply(201, {"id": "chat-1"})]
        self.fake.route("POST", r"^/v1\.0/chats$", lambda request: answers.pop(0))
        self.assertEqual(graph.post("chats", {}), {"id": "chat-1"})
        self.assertEqual(self.sleeps, list(CLAIMS_WAITS[:2]))
        self.assertFalse(self.refreshes(), "a renewed token cannot satisfy the challenge, and renewing can be refused")
        self.assertIn("Conditional Access changes have not reached the service yet", logs[0])

    def test_claims_challenges_stop_once_the_account_has_waited_its_share(self):
        self.fake.route("POST", r"^/v1\.0/chats$", lambda request: claims_challenge())
        with self.assertRaises(GraphError) as caught:
            self.graph.post("chats", {})
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(self.sleeps, list(CLAIMS_WAITS))
        with self.assertRaises(GraphError):
            Graph(self.session, sleep=self.sleeps.append).post("chats", {})
        self.assertEqual(self.sleeps, list(CLAIMS_WAITS), "the waiting budget belongs to the account, not one client")

    def test_an_expired_token_is_still_renewed_once(self):
        answers = [Reply(401, {"error": {"code": "InvalidAuthenticationToken", "message": "Lifetime validation failed."}}),
                   Reply(200, {"ok": True})]
        self.fake.route("GET", r"^/v1\.0/thing$", lambda request: answers.pop(0))
        self.assertEqual(self.graph.get("thing"), {"ok": True})
        self.assertEqual(len(self.refreshes()), 1)
        self.assertEqual(self.sleeps, [])

    def test_a_post_is_never_repeated_after_the_connection_drops(self):
        self.fake.route("POST", r"^/v1\.0/act$", lambda request: Reply(drop=True))
        with self.assertRaises(Blocked) as caught:
            self.graph.post("act", {})
        self.assertEqual(caught.exception.reason, "network")
        self.assertEqual(len(self.fake.calls("POST", "act$")), 1)

    def test_quote(self):
        self.assertEqual(quote("O'Brien/x y"), "O%27%27Brien%2Fx%20y")


if __name__ == "__main__":
    unittest.main()
