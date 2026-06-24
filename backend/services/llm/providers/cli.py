"""Claude Code CLI provider — LOCAL DEVELOPMENT ONLY.

Shells out to the authenticated Claude Code CLI (`claude -p`) using the local
Max-plan OAuth session. This is retained only for `LLM_PROVIDER=cli` during
local development. It MUST NOT be used in production: Anthropic's Consumer Terms
restrict OAuth (Free/Pro/Max) credentials to Claude Code and claude.ai, so any
product calling Claude on behalf of users must use API-key auth (see
AnthropicAPIProvider). The default provider is `api` everywhere.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from services.config import settings
from services.llm.base import LLMError, Provider, ProviderResponse, TokenUsage

logger = logging.getLogger("ta_agent.llm.cli")

# Resolve the CLI once. shutil.which honours PATHEXT on Windows. An explicit
# CLAUDE_BIN in the environment takes precedence.
CLAUDE_BIN = settings.claude_bin or shutil.which("claude")


def cli_available() -> bool:
    """True if the Claude Code CLI is on PATH and responds to --version."""
    if not CLAUDE_BIN:
        return False
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _extract_result_text(stdout: str) -> str:
    """Pull the assistant text out of the `--output-format json` envelope."""
    raw = (stdout or "").strip()
    if not raw:
        raise LLMError("Claude CLI returned no output.")
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Unexpected non-JSON CLI output: %s", raw[:500])
        raise LLMError(f"Could not parse Claude CLI output: {exc}") from exc

    if envelope.get("is_error"):
        raise LLMError(f"Claude CLI reported an error: {envelope.get('result')!r}")

    text = envelope.get("result")
    if not isinstance(text, str) or not text.strip():
        raise LLMError("Claude CLI envelope had no 'result' text.")
    return text


class CLIProvider(Provider):
    name = "cli"

    def available(self) -> bool:
        return cli_available()

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.llm_step_timeout,
        )

    def complete(
        self, system: str, user: str, *, max_tokens: int, model: str
    ) -> ProviderResponse:
        if not CLAUDE_BIN:
            raise LLMError(
                "Claude Code CLI not found on PATH. Install it "
                "(`npm i -g @anthropic-ai/claude-code`) and authenticate (`claude`), "
                "or set CLAUDE_BIN — or use LLM_PROVIDER=api (recommended)."
            )

        # max_tokens is advisory only for the CLI (it takes no token cap).
        cmd = [
            CLAUDE_BIN,
            "-p",
            user,
            "--system-prompt",
            system,
            "--model",
            model,
            "--output-format",
            "json",
            # Pure text transforms — load NO MCP servers (avoids launching the
            # project's LinkedIn MCP server on every call).
            "--strict-mcp-config",
            "--dangerously-skip-permissions",
        ]

        try:
            result = self._run(cmd)
        except FileNotFoundError as exc:
            raise LLMError(f"Could not execute Claude CLI at {CLAUDE_BIN}: {exc}") from exc
        except subprocess.TimeoutExpired:
            logger.warning(
                "Claude CLI timed out after %ss; retrying once.", settings.llm_step_timeout
            )
            try:
                result = self._run(cmd)
            except subprocess.TimeoutExpired as exc:
                raise LLMError(
                    f"Claude CLI timed out after {settings.llm_step_timeout}s (twice)"
                ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:500]
            logger.error("Claude CLI exited %s: %s", result.returncode, stderr)
            raise LLMError(f"Claude CLI failed (exit {result.returncode}): {stderr}")

        text = _extract_result_text(result.stdout)
        # The CLI envelope does not reliably expose token usage; record the
        # requested alias as the served model.
        return ProviderResponse(text=text, model_version=model, usage=TokenUsage())
