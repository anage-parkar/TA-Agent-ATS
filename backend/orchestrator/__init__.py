"""Phase 2 — LangGraph orchestrator.

PR 1 (storage) ships only the tenant-scoped checkpointers and the
reference-only state schema; no graph exists yet and nothing in a request
path calls into this package except the lifecycle hooks (export / erasure).
See docs/design/phase2-langgraph-orchestrator.md.
"""
