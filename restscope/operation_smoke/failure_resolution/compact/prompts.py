"""Define the temporary instruction C used for Resolution compaction.

The instruction is appended only to a deep copy of the current Resolution
conversation. It asks the FAST model to create a Markdown handoff summary and
never becomes part of the saved conversation history.
"""


COMPACT_INSTRUCTION = """You are performing a Failure Resolution context checkpoint.

Create a concise Markdown handoff summary for the next Resolution model.

Include:
- The operation currently being processed.
- Failures resolved in this session.
- The root cause and Patch or no-Patch result for each resolved Failure.
- The active Failure and its current investigation progress.
- Important tool calls and results needed to continue the active investigation.
- Failures that still require investigation or a decision.
- Relevant worklist item IDs, revisions, E*, TC*, P*, and parameter handles.

Do not make new resolution decisions.
Do not reproduce precise Patch, Test Case, HTTP, Schema, Memory, or database objects.
Return Markdown only."""
