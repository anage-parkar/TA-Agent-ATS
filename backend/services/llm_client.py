"""Single source of truth for all LLM calls.

Every agent in this codebase MUST import `call_claude` / `call_claude_json`
from here. Never invoke the model any other way.

**Max-subscription approach (no API key, no proxy).** Calls are made by
shelling out to the authenticated Claude Code CLI in headless mode
(`claude -p`), exactly like the Contiloe sales agents. The CLI uses the local
Max-plan OAuth session, so there is no ANTHROPIC_API_KEY and no localhost proxy
to keep alive. We pass `--system-prompt` to replace the default agentic system
prompt with our own (pure-transform completion) and `--output-format json` to
get a parseable envelope.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

from services.config import settings

logger = logging.getLogger("ta_agent.llm")

# Resolve the CLI once. shutil.which honours PATHEXT on Windows, so this finds
# claude.cmd / claude.exe / the bash shim as appropriate. An explicit CLAUDE_BIN
# in .env.local takes precedence.
CLAUDE_BIN = settings.claude_bin or shutil.which("claude")

# CLI model alias: 'sonnet' | 'opus' | 'haiku', or a full model id.
DEFAULT_MODEL = settings.llm_model or "sonnet"

# Headless step timeout (seconds). A cold CLI start + a transform fits well under this.
STEP_TIMEOUT = settings.llm_step_timeout


class LLMError(RuntimeError):
    """Raised when the CLI call fails or returns unusable output."""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=STEP_TIMEOUT,
    )


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


def call_claude(system: str, user: str, max_tokens: int = 1000, model: str | None = None) -> str:
    """Single helper used by all agents. Routes through the Claude Code CLI.

    `max_tokens` is accepted for signature compatibility; the CLI does not take
    a token cap, so it is advisory only. `model` overrides the default (e.g.
    "haiku" for fast, simple transforms like email drafting).

    Raises:
        LLMError: if the CLI is missing, times out, or returns an error.
    """
    if not CLAUDE_BIN:
        raise LLMError(
            "Claude Code CLI not found on PATH. Install it "
            "(`npm i -g @anthropic-ai/claude-code`) and authenticate (`claude`), "
            "or set CLAUDE_BIN to its full path."
        )

    cmd = [
        CLAUDE_BIN,
        "-p",
        user,
        "--system-prompt",
        system,
        "--model",
        model or DEFAULT_MODEL,
        "--output-format",
        "json",
        # These are pure text transforms — load NO MCP servers. Without this,
        # `claude -p` discovers the project .mcp.json and launches the LinkedIn
        # MCP server on every call, adding huge latency / timeouts.
        "--strict-mcp-config",
        "--dangerously-skip-permissions",
    ]

    try:
        result = _run(cmd)
    except FileNotFoundError as exc:
        raise LLMError(f"Could not execute Claude CLI at {CLAUDE_BIN}: {exc}") from exc
    except subprocess.TimeoutExpired:
        # A timeout is usually transient (the call queued behind the Max-plan
        # concurrency limit). Retry once before giving up.
        logger.warning("Claude CLI timed out after %ss; retrying once.", STEP_TIMEOUT)
        try:
            result = _run(cmd)
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"Claude CLI timed out after {STEP_TIMEOUT}s (twice)") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:500]
        logger.error("Claude CLI exited %s: %s", result.returncode, stderr)
        raise LLMError(f"Claude CLI failed (exit {result.returncode}): {stderr}")

    return _extract_result_text(result.stdout)


def _extract_result_text(stdout: str) -> str:
    """Pull the assistant text out of the `--output-format json` envelope.

    The envelope looks like: {"type":"result","subtype":"success",
    "is_error":false,"result":"<assistant text>", ...}.
    """
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


# Appended to the user prompt for JSON calls. The CLI's default chat behaviour
# tends to fence the JSON and add a prose/table summary after it; this curbs that.
_JSON_DIRECTIVE = (
    "\n\nIMPORTANT: Respond with ONLY the raw JSON object and nothing else — "
    "no markdown code fences, no commentary, no summary tables, no trailing commas."
)


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced top-level {...} object found in `text`.

    Survives surrounding markdown fences and any prose/tables the model appends
    after the JSON. String contents (incl. escaped quotes) are respected so
    braces inside strings don't break the match.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def call_claude_json(
    system: str, user: str, max_tokens: int = 1000, model: str | None = None
) -> dict:
    """Same as call_claude but parses and validates JSON output.

    Robust to the CLI fencing the JSON, appending prose/tables after it, or
    emitting a trailing comma. Raises LLMError only when no JSON object can be
    recovered, so callers can log + degrade gracefully.
    """
    raw = call_claude(system, f"{user}{_JSON_DIRECTIVE}", max_tokens, model=model)

    extracted = _extract_json_object(raw)
    for candidate in (raw, extracted):
        if not candidate:
            continue
        for attempt in (candidate, _remove_trailing_commas(candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue

    logger.error("LLM returned non-JSON output: %s", raw[:500])
    raise LLMError("Expected JSON from LLM but could not recover a JSON object.")
