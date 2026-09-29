# SPDX-License-Identifier: Apache-2.0
import socket
import unittest
from unittest.mock import Mock, patch

from flora.general.network import HttpClient, NetworkPolicy
from flora.support.errors import ValidationError


def addresses(*values):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (v, 443)) for v in values]


class NetworkPolicyTests(unittest.TestCase):
    def test_fake_ip_has_actionable_error_without_dispatch(self):
        for address in ("198.18.0.32", "198.19.255.254", "::ffff:198.18.0.1"):
            with (
                self.subTest(address=address),
                patch("flora.general.network._resolve_bounded", return_value=addresses(address)),
                patch("flora.general.network._PinnedHTTPS") as connection,
            ):
                with self.assertRaisesRegex(ValidationError, "network_policy_benchmark_address"):
                    HttpClient().request("https://search.example.test/")
                connection.assert_not_called()

    def test_all_nonpublic_answers_still_denied(self):
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.2",
            "169.254.169.254",
            "224.0.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "2001:db8::1",
            "::ffff:127.0.0.1",
        ):
            with (
                self.subTest(address=address),
                patch(
                    "flora.general.network._resolve_bounded",
                    return_value=addresses("93.184.216.34", address),
                ),
            ):
                with self.assertRaises(ValidationError):
                    NetworkPolicy().resolve("https://example.test/")

    def test_public_resolved_address_is_pinned_and_opt_in_unchanged(self):
        with patch(
            "flora.general.network._resolve_bounded", return_value=addresses("93.184.216.34")
        ):
            self.assertEqual(NetworkPolicy().resolve("https://example.test/")[3], "93.184.216.34")
        with patch("flora.general.network._resolve_bounded", return_value=addresses("127.0.0.1")):
            self.assertEqual(
                NetworkPolicy(allow_private=True).resolve("http://localhost/")[3], "127.0.0.1"
            )

    def test_redirect_cannot_enter_private_network(self):
        connection = Mock()
        connection.getresponse.return_value.status = 302
        connection.getresponse.return_value.getheaders.return_value = [
            ("location", "https://localhost/")
        ]
        with (
            patch(
                "flora.general.network._resolve_bounded",
                side_effect=[addresses("93.184.216.34"), addresses("127.0.0.1")],
            ),
            patch("flora.general.network._PinnedHTTPS", return_value=connection) as factory,
        ):
            with self.assertRaises(ValidationError):
                HttpClient().request("https://example.test/")
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(factory.call_args.args[2], "93.184.216.34")
            self.assertEqual(connection.request.call_count, 1)
