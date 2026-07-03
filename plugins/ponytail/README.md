# Ponytail plugin

First shipped plugin. Adds a `ponytail_review` tool the agent (or any
reviewer subagent) can invoke to check a diff, code snippet, or design
proposal against the Ponytail ladder.

See [`__init__.py`](./__init__.py) for the heuristic list and the
verdict rubric. The tool is deterministic — same input, same output —
which makes it testable and free of LLM cost.
