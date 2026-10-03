# SPDX-License-Identifier: Apache-2.0
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flora.general.agent import GeneralAgent, PauseRequested, _new_session_defaults, _normalize
from flora.integrations.providers import ModelResponse
from flora.support.errors import ValidationError
from tests.helpers import bundle
from tests.test_frontend import observe


class WorkspaceProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, max_tokens):
        self.calls += 1
        c = json.loads(messages[1]["content"])
        assert "workspace_context" in {t["name"] for t in c["tools"]}
        return ModelResponse(
            json.dumps(bundle(observe("workspace_context"), c["epoch"], c["trace_digest"])), 10, 5
        )


class GeneralTests(unittest.TestCase):
    def test_repeated_program_and_observation_pauses_without_information_gain(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root, "workspace")
            workspace.mkdir()
            with GeneralAgent(
                session_dir=Path(root, "session"),
                workspace=workspace,
                provider=WorkspaceProvider(),
            ) as app:
                event = {
                    "kind": "consumer_check",
                    "candidate": "main",
                    "result": {
                        "verdict": "PASS",
                        "relation": "DEFINED_PREFIX",
                        "witness": {
                            "program_digest": "program-1",
                            "candidate": {"kind": "return", "value": {"x": 1}},
                        },
                    },
                }
                app._event(event)
                app._event(event)
                with self.assertRaises(PauseRequested):
                    app._event(event)
                self.assertIn("same program", app._pause_reason)

    def test_changed_observation_resets_stagnation_counter(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root, "workspace")
            workspace.mkdir()
            with GeneralAgent(
                session_dir=Path(root, "session"),
                workspace=workspace,
                provider=WorkspaceProvider(),
            ) as app:
                def event(value):
                    return {
                        "kind": "consumer_check",
                        "candidate": "main",
                        "result": {
                            "verdict": "PASS",
                            "relation": "DEFINED_PREFIX",
                            "witness": {
                                "program_digest": "program-1",
                                "candidate": {"kind": "return", "value": {"x": value}},
                            },
                        },
                    }
                app._event(event(1))
                app._event(event(1))
                app._event(event(2))
                app._event(event(2))
                self.assertEqual(app._same_observation_count, 2)

    def test_replan_observation_counts_even_when_program_changes(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root, "workspace")
            workspace.mkdir()
            with GeneralAgent(
                session_dir=Path(root, "session"),
                workspace=workspace,
                provider=WorkspaceProvider(),
            ) as app:
                def event(program, value="child_running"):
                    return {
                        "kind": "consumer_check",
                        "candidate": "main",
                        "result": {
                            "verdict": "PASS",
                            "relation": "DEFINED_PREFIX",
                            "witness": {
                                "program_digest": program,
                                "candidate": {
                                    "kind": "replan",
                                    "value": {"observed": {"status": value}},
                                },
                            },
                        },
                    }
                app._event(event("program-1"))
                app._event(event("program-2"))
                with self.assertRaises(PauseRequested):
                    app._event(event("program-3"))
                self.assertIn("same program", app._pause_reason)

    def test_repeated_wait_requests_safe_child_pause(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root, "workspace")
            workspace.mkdir()
            with GeneralAgent(
                session_dir=Path(root, "session"),
                workspace=workspace,
                provider=WorkspaceProvider(),
            ) as app:
                delegation = Mock()
                delegation.is_busy.return_value = True
                app.delegation = delegation
                event = {
                    "kind": "consumer_check",
                    "candidate": "main",
                    "result": {
                        "verdict": "PASS",
                        "relation": "DEFINED_PREFIX",
                        "witness": {
                            "program_digest": "wait-program",
                            "candidate": {
                                "kind": "effect",
                                "request": {"tool": "wait_agents", "args": {"timeout": 30}},
                            },
                        },
                    },
                }
                app._event(event)
                app._event(event)
                with self.assertRaises(PauseRequested):
                    app._event(event)
                delegation.request_pause.assert_called_once()
                app.delegation = None

    def test_new_workspace_task_uses_one_compile_one_observation(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root, "workspace")
            workspace.mkdir()
            provider = WorkspaceProvider()
            with GeneralAgent(
                session_dir=Path(root, "session"), workspace=workspace, provider=provider
            ) as app:
                result = app.run("你当前位于什么路径")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["value"]["workspace_root"], str(workspace.resolve()))
                self.assertEqual(result["value"]["relative_path_base"], str(workspace.resolve()))
                self.assertEqual(result["value"]["process_cwd"], os.getcwd())
                self.assertEqual(provider.calls, 1)
                self.assertEqual(result["budget"]["tool_calls"], 1)
                self.assertFalse(app.agent_capabilities()["shell_commands"])
                self.assertEqual(app.agent.compiler.syntax, "block-list-v2")
                self.assertEqual(app.agent.compiler.prompt_style, "compact-v2")
                self.assertEqual(app.profile["general"]["protocol"], "general-v4")
                self.assertEqual(app.agent.compiler.compilation_timeout, 180)
                self.assertTrue(all(v is None for v in app.profile["budget"].values()))
                saved_profile = copy.deepcopy(app.profile)
                fingerprint = app.agent._fingerprint
            with GeneralAgent(session_dir=Path(root, "session"), provider=provider) as reopened:
                self.assertEqual(reopened.profile, saved_profile)
                self.assertEqual(reopened.agent._fingerprint, fingerprint)
                self.assertEqual(reopened.agent.status()["budget"]["model_calls"], 1)

    def test_legacy_profile_and_capability_identity_not_silently_changed(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("flora.general.agent._new_session_defaults"):
                with GeneralAgent(
                    session_dir=Path(root, "session"), workspace=root, provider=WorkspaceProvider()
                ) as old:
                    self.assertNotIn("protocol", old.profile["general"])
                    profile = copy.deepcopy(old.profile)
                    fingerprint = old.agent._fingerprint
                    tools = old.agent.tools.descriptions()
            with GeneralAgent(
                session_dir=Path(root, "session"), provider=WorkspaceProvider()
            ) as old:
                self.assertEqual(old.profile, profile)
                self.assertEqual(old.agent._fingerprint, fingerprint)
                self.assertEqual(old.agent.tools.descriptions(), tools)
                self.assertNotIn("workspace_context", {t["name"] for t in tools})
                self.assertEqual(old.agent.compiler.syntax, "ir-v1")
                self.assertIsNone(old.agent.compiler.compilation_timeout)

    def test_v2_identity_and_descriptions_survive_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            with GeneralAgent(
                session_dir=Path(root, "v2"),
                workspace=root,
                profile={"general": {"protocol": "general-v2"}},
                provider=WorkspaceProvider(),
            ) as app:
                profile = copy.deepcopy(app.profile)
                fingerprint = app.agent._fingerprint
                tools = app.agent.tools.descriptions()
                self.assertEqual(app.agent.compiler.syntax, "observe-v1")
                self.assertEqual(app.agent.compiler.prompt_style, "full-v1")
                self.assertNotIn("provider", app.profile)
            with GeneralAgent(session_dir=Path(root, "v2"), provider=WorkspaceProvider()) as app:
                self.assertEqual(app.profile, profile)
                self.assertEqual(app.agent._fingerprint, fingerprint)
                self.assertEqual(app.agent.tools.descriptions(), tools)
            with GeneralAgent(
                session_dir=Path(root, "v3"), workspace=root, provider=WorkspaceProvider()
            ) as app:
                new_tools = app.agent.tools.descriptions()
                old_read = next(t for t in tools if t["name"] == "read_file")
                new_read = next(t for t in new_tools if t["name"] == "read_file")
                self.assertTrue(new_read["description"].startswith(old_read["description"]))
                self.assertIn("content:string", new_read["description"])
                self.assertNotIn("content:string", old_read["description"])
                self.assertNotIn("provider", app.profile)

    def test_explicit_source_versions_and_saved_v3_sessions_do_not_upgrade(self):
        for syntax in ("ir-v1", "observe-v1", "block-list-v1", "block-list-v2"):
            with self.subTest(syntax=syntax), tempfile.TemporaryDirectory() as root:
                profile = {"general": {"protocol": "general-v3"}, "compiler": {"syntax": syntax}}
                with GeneralAgent(
                    session_dir=Path(root, "session"),
                    workspace=root,
                    profile=profile,
                    provider=WorkspaceProvider(),
                ) as app:
                    resolved = copy.deepcopy(app.profile)
                    fingerprint = app.agent._fingerprint
                    self.assertEqual(app.agent.compiler.syntax, syntax)
                with GeneralAgent(
                    session_dir=Path(root, "session"),
                    provider=WorkspaceProvider(),
                ) as reopened:
                    self.assertEqual(reopened.profile, resolved)
                    self.assertEqual(reopened.agent.compiler.syntax, syntax)
                    self.assertEqual(reopened.agent._fingerprint, fingerprint)

    def test_builtin_defaults_persist_and_explicit_limits_win(self):
        with tempfile.TemporaryDirectory() as root:
            profile = {
                "provider": {
                    "model": "fixture",
                    "base_url": "http://localhost/v1",
                    "api_key_env": None,
                    "max_json_whitespace": 80,
                },
                "compiler": {"compilation_timeout": 30},
                "budget": {"max_tool_calls": 4},
            }
            with GeneralAgent(
                session_dir=Path(root, "session"), workspace=root, profile=profile
            ) as app:
                self.assertEqual(app.agent.provider.first_program_timeout, 120)
                self.assertEqual(app.agent.provider.max_json_whitespace, 80)
                self.assertEqual(app.agent.compiler.compilation_timeout, 30)
                self.assertEqual(app.profile["budget"]["max_tool_calls"], 4)
                saved_profile = copy.deepcopy(app.profile)
                fingerprint = app.agent._fingerprint
            with GeneralAgent(session_dir=Path(root, "session")) as app:
                self.assertEqual(app.profile, saved_profile)
                self.assertEqual(app.agent._fingerprint, fingerprint)

    def test_opt_out_and_custom_provider_options_not_polluted(self):
        profile = _normalize({"general": {"protocol": "general-v1"}})
        _new_session_defaults(profile, builtin_provider=True)
        self.assertNotIn("compiler", profile)
        self.assertNotIn("provider", profile)
        profile = _normalize({"compiler": {"syntax": "ir-v1", "compilation_timeout": None}})
        _new_session_defaults(profile, builtin_provider=False)
        self.assertNotIn("provider", profile)
        self.assertEqual(profile["compiler"], {"syntax": "ir-v1", "compilation_timeout": None})
        for protocol in ("unknown", [], None):
            with self.subTest(protocol=protocol), self.assertRaises(ValidationError):
                _normalize({"general": {"protocol": protocol}})
