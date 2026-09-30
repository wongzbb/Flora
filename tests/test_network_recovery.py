# SPDX-License-Identifier: Apache-2.0
import io
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from flora.general.network import HttpClient, NetworkPolicy, _resolve_doh, _TunnelHTTP
from flora.general.storage import ObservationStore
from flora.general.web import WebTools
from flora.support.errors import InterruptedEffect, ValidationError
from tests.test_network_policy import addresses


class NetworkRecoveryTests(unittest.TestCase):
    def test_doh_public_answer_is_checked_and_pinned(self):
        policy = NetworkPolicy(dns_over_https="https://dns.test/resolve", dns_bootstrap=["8.8.8.8"])
        with patch("flora.general.network._resolve_doh", return_value=["93.184.216.34"]) as dns:
            self.assertEqual(policy.resolve("https://page.test")[3], "93.184.216.34")
            dns.assert_called_once_with(policy, "page.test")
        for invalid in ("127.0.0.1", "169.254.169.254", "198.18.0.1"):
            with patch("flora.general.network._resolve_doh", return_value=[invalid]):
                with self.assertRaises(ValidationError):
                    policy.resolve("https://page.test")

    def test_doh_json_response_tls_host_and_bootstrap(self):
        conn = Mock()
        body = io.BytesIO(
            json.dumps({"Status": 0, "Answer": [{"type": 1, "data": "93.184.216.34"}]}).encode()
        )
        conn.getresponse.return_value.status = 200
        conn.getresponse.return_value.getheader.return_value = "identity"
        conn.getresponse.return_value.read1.side_effect = body.read1
        policy = NetworkPolicy(dns_over_https="https://dns.test/resolve", dns_bootstrap=["8.8.8.8"])
        with patch("flora.general.network._connection", return_value=conn) as factory:
            self.assertEqual(_resolve_doh(policy, "page.test"), ["93.184.216.34"])
            self.assertEqual(factory.call_args.args[1:4], ("dns.test", 443, "8.8.8.8"))
            self.assertTrue(factory.call_args.kwargs["tls"])
            self.assertIn("name=page.test&type=A", conn.request.call_args.args[1])
            conn.close.assert_called_once()

    def test_doh_cannot_use_private_bootstrap_or_embedded_credentials(self):
        for options in (
            {"dns_over_https": "http://dns.test/resolve", "dns_bootstrap": ["8.8.8.8"]},
            {"dns_over_https": "https://dns.test/resolve", "dns_bootstrap": ["127.0.0.1"]},
            {"dns_over_https": "https://key:secret@dns.test/resolve", "dns_bootstrap": ["8.8.8.8"]},
            {"dns_bootstrap": ["8.8.8.8"]},
            {"proxy_url": "http://secret:token@localhost:8888"},
        ):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                NetworkPolicy(**options)

    def test_proxy_tunnels_to_checked_ip_and_keeps_tls_hostname(self):
        transport = _TunnelHTTP("page.test", 443, "93.184.216.34", 5, ("127.0.0.1", 8888), tls=True)
        sock = Mock()
        context = Mock()
        with (
            patch("flora.general.network.socket.create_connection", return_value=sock) as connect,
            patch.object(transport, "_tunnel") as tunnel,
            patch("flora.general.network.ssl.create_default_context", return_value=context),
        ):
            transport.connect()
            connect.assert_called_once_with(("127.0.0.1", 8888), 5)
            tunnel.assert_called_once()
            self.assertEqual(transport._tunnel_host, "93.184.216.34")
            context.wrap_socket.assert_called_once_with(sock, server_hostname="page.test")

    def test_proxy_does_not_waive_private_destination_check(self):
        with (
            patch("flora.general.network._resolve_bounded", return_value=addresses("127.0.0.1")),
            patch("flora.general.network._TunnelHTTP") as tunnel,
        ):
            with self.assertRaises(ValidationError):
                HttpClient(NetworkPolicy(proxy_url="http://localhost:8888")).request(
                    "https://page.test"
                )
            tunnel.assert_not_called()


class SearchFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ObservationStore(self.tmp.name)
        self.web = WebTools(
            self.store,
            search={
                "provider": "duckduckgo",
                "fallbacks": [{"provider": "searxng", "base_url": "https://search.test"}],
            },
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_blocked_primary_can_use_only_configured_alternative(self):
        primary, secondary = self.web, self.web.fallbacks[0]
        with (
            patch.object(
                primary, "_search_once", side_effect=ValidationError("Search returned HTTP 403")
            ),
            patch.object(
                secondary, "_search_once", return_value={"results": [{"title": "actual"}]}
            ) as fallback,
        ):
            result = self.web.web_search("query")
            fallback.assert_called_once_with("query", 5)
            self.assertEqual(
                result["provider_failures"],
                [{"provider": "duckduckgo", "code": "provider_unavailable"}],
            )

    def test_unknown_post_search_is_not_retried_or_sent_to_another_provider(self):
        with (
            patch.object(
                self.web, "_search_once", side_effect=InterruptedEffect("Unknown POST outcome")
            ),
            patch.object(self.web.fallbacks[0], "_search_once") as fallback,
        ):
            with self.assertRaises(InterruptedEffect):
                self.web.web_search("query")
            fallback.assert_not_called()

    def test_private_or_synthetic_dns_deny_never_falls_through(self):
        for reason in ("Private network blocked", "network_policy_benchmark_address: 198.18.0.1"):
            with (
                self.subTest(reason=reason),
                patch.object(self.web, "_search_once", side_effect=ValidationError(reason)),
                patch.object(self.web.fallbacks[0], "_search_once") as fallback,
            ):
                with self.assertRaises(ValidationError):
                    self.web.web_search("query")
                fallback.assert_not_called()

    def test_empty_successful_search_is_not_treated_as_a_transport_failure(self):
        with (
            patch.object(self.web, "_search_once", return_value={"results": []}),
            patch.object(self.web.fallbacks[0], "_search_once") as fallback,
        ):
            self.assertEqual(self.web.web_search("query"), {"results": []})
            fallback.assert_not_called()
