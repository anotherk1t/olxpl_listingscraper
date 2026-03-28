"""Tests for llm.py — Copilot CLI integration layer."""

import asyncio
import os
import subprocess
import unittest
from unittest.mock import call, patch

import llm

# ---------------------------------------------------------------------------
# _strip_copilot_noise
# ---------------------------------------------------------------------------


class TestStripCopilotNoise(unittest.TestCase):
    def test_removes_total_usage_footer(self):
        text = "Some useful answer\n\nTotal usage est: $0.003 (input: 42, output: 10)"
        assert llm._strip_copilot_noise(text) == "Some useful answer"

    def test_removes_total_usage_footer_with_trailing(self):
        text = "Answer\n  Total usage est: $0.01\nExtra trailing stuff"
        assert llm._strip_copilot_noise(text) == "Answer"

    def test_removes_tool_use_indicators(self):
        text = "● Running tool: search\n  └ query: test\nHere is the result."
        assert llm._strip_copilot_noise(text) == "Here is the result."

    def test_removes_multiple_tool_indicators(self):
        text = "● Tool A\n  └ param1\n● Tool B\n  └ param2\n  └ param3\nFinal answer."
        assert llm._strip_copilot_noise(text) == "Final answer."

    def test_removes_both_noise_types(self):
        text = "● Searching DB\n  └ query: foo\nThe answer is 42.\n\nTotal usage est: $0.001 (input: 5, output: 3)"
        assert llm._strip_copilot_noise(text) == "The answer is 42."

    def test_clean_text_unchanged(self):
        text = "Just a normal answer with no noise."
        assert llm._strip_copilot_noise(text) == text

    def test_empty_string(self):
        assert llm._strip_copilot_noise("") == ""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_completed_process(stdout="response text", stderr="", returncode=0):
    cp = subprocess.CompletedProcess(
        args=["copilot"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    return cp


# ---------------------------------------------------------------------------
# _ask_llm_sync — success
# ---------------------------------------------------------------------------


class TestAskLlmSync(unittest.TestCase):
    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_success_returns_cleaned_text(self, mock_run, _mock_sleep):
        mock_run.return_value = _make_completed_process(stdout="Good answer\n\nTotal usage est: $0.01")
        result = llm._ask_llm_sync("hello")
        assert result == "Good answer"
        mock_run.assert_called_once()

    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_timeout_returns_empty_after_retries(self, mock_run, _mock_sleep):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="copilot", timeout=120)
        result = llm._ask_llm_sync("hello", retries=1)
        assert result == ""
        assert mock_run.call_count == 2  # 1 initial + 1 retry

    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_nonzero_exit_returns_empty_after_retries(self, mock_run, _mock_sleep):
        mock_run.return_value = _make_completed_process(returncode=1, stderr="error msg")
        result = llm._ask_llm_sync("hello", retries=0)
        assert result == ""
        assert mock_run.call_count == 1

    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_file_not_found_returns_empty(self, mock_run, _mock_sleep):
        mock_run.side_effect = FileNotFoundError("copilot not found")
        result = llm._ask_llm_sync("hello", retries=0)
        assert result == ""

    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_command_contains_expected_flags(self, mock_run, _mock_sleep):
        mock_run.return_value = _make_completed_process()
        llm._ask_llm_sync("test prompt", model="gpt-4")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "copilot"
        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "test prompt"
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-4"
        assert "-s" in cmd


# ---------------------------------------------------------------------------
# ask_llm — async wrapper
# ---------------------------------------------------------------------------


class TestAskLlmAsync(unittest.TestCase):
    @patch("llm._ask_llm_sync", return_value="async result")
    def test_delegates_to_sync(self, mock_sync):
        result = asyncio.run(llm.ask_llm("prompt", model="m", mcp=False, timeout=60))
        assert result == "async result"
        mock_sync.assert_called_once_with(
            "prompt",
            model="m",
            mcp=False,
            timeout=60,
        )


# ---------------------------------------------------------------------------
# MCP config path appended when mcp=True
# ---------------------------------------------------------------------------


class TestMcpConfigPath(unittest.TestCase):
    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_mcp_flag_appends_config(self, mock_run, _mock_sleep):
        mock_run.return_value = _make_completed_process()
        # Point SCRIPT_DIR to a temp location with the config file
        fake_dir = os.path.join(os.path.dirname(__file__), "_mcp_test_dir")
        os.makedirs(fake_dir, exist_ok=True)
        config_path = os.path.join(fake_dir, "copilot-mcp-config.json")
        try:
            with open(config_path, "w") as f:
                f.write("{}")
            with patch.object(llm, "SCRIPT_DIR", fake_dir):
                llm._ask_llm_sync("hi", mcp=True, retries=0)
            cmd = mock_run.call_args[0][0]
            assert "--additional-mcp-config" in cmd
            idx = cmd.index("--additional-mcp-config")
            assert cmd[idx + 1] == f"@{config_path}"
        finally:
            os.remove(config_path)
            os.rmdir(fake_dir)

    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_mcp_false_does_not_append_config(self, mock_run, _mock_sleep):
        mock_run.return_value = _make_completed_process()
        llm._ask_llm_sync("hi", mcp=False, retries=0)
        cmd = mock_run.call_args[0][0]
        assert "--additional-mcp-config" not in cmd


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class TestRetryBehaviour(unittest.TestCase):
    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_retries_then_succeeds(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="copilot", timeout=120),
            _make_completed_process(returncode=1, stderr="err"),
            _make_completed_process(stdout="finally works"),
        ]
        result = llm._ask_llm_sync("hi", retries=2)
        assert result == "finally works"
        assert mock_run.call_count == 3

    @patch("llm.time.sleep", return_value=None)
    @patch("llm.subprocess.run")
    def test_sleep_called_between_retries(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="copilot", timeout=120),
            subprocess.TimeoutExpired(cmd="copilot", timeout=120),
            _make_completed_process(stdout="ok"),
        ]
        llm._ask_llm_sync("hi", retries=2)
        # sleep(10) after attempt 1, sleep(20) after attempt 2
        assert mock_sleep.call_args_list == [call(10), call(20)]


if __name__ == "__main__":
    unittest.main()
