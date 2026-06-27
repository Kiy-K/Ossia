"""Programmatic subagent orchestrator pipelines.

Each module defines a deterministic pipeline that chains subagent calls
via the interpreter's ``task()`` global, passing structured data between
stages. Pipelines are designed to be called from interpreter code
(``eval`` tool) so the coordinator can run a complete multi-step
workflow in a single turn.
"""
