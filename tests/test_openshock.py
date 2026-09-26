import unittest

from chess_kills.openshock import TOKEN_HEADER, ControlType, OpenShockClient, OpenShockError
from tests.helpers import FakeResponse, FakeSession


class ClientTests(unittest.TestCase):
    def make(self, *responses, dry_run=False):
        session = FakeSession(list(responses))
        return OpenShockClient("https://api.example.test/", "secret", dry_run=dry_run, session=session), session

    def test_header(self):
        _, session = self.make()
        self.assertEqual(session.headers[TOKEN_HEADER], "secret")

    def test_control_body(self):
        client, session = self.make()
        client.control("abc", ControlType.VIBRATE, 42, 800, custom_name="hi", exclusive=False)
        call = session.calls[0]
        self.assertEqual(call["url"], "https://api.example.test/2/shockers/control")
        self.assertEqual(call["json"], {
            "shocks": [{"id": "abc", "type": "Vibrate", "intensity": 42, "duration": 800, "exclusive": False}],
            "customName": "hi",
        })

    def test_dry_run(self):
        client, session = self.make(dry_run=True)
        client.control("abc", ControlType.SHOCK, 10, 300)
        self.assertEqual(session.calls, [])

    def test_range_checks(self):
        client, _ = self.make()
        for args in [("abc", 101, 300), ("abc", 10, 299), ("abc", 10, 65536), ("", 10, 300)]:
            with self.assertRaises(ValueError):
                client.control(args[0], ControlType.SHOCK, args[1], args[2])

    def test_http_error(self):
        client, _ = self.make(FakeResponse(412, {"title": "Shocker is paused"}))
        with self.assertRaisesRegex(OpenShockError, "412.*paused"):
            client.control("abc", ControlType.SHOCK, 10, 300)

    def test_list_shockers(self):
        payload = {"message": "", "data": [{
            "id": "hub-1", "name": "Living room hub",
            "shockers": [
                {"id": "s-1", "name": "Left", "isPaused": False},
                {"id": "s-2", "name": "Right", "isPaused": True},
            ],
        }]}
        client, session = self.make(FakeResponse(200, payload))
        shockers = client.list_shockers()
        self.assertEqual(session.calls[0]["url"], "https://api.example.test/1/shockers/own")
        self.assertEqual([s.id for s in shockers], ["s-1", "s-2"])
        self.assertEqual(shockers[0].hub_name, "Living room hub")
        self.assertTrue(shockers[1].is_paused)


if __name__ == "__main__":
    unittest.main()
