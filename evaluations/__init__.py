"""Developer evaluations for RESTScope's LLM Agents.

The package is intentionally separate from :mod:`restscope`: production code
does not import it.  Its command-line runner turns version-controlled scenarios
into Phoenix Datasets and Experiments while each Agent-specific suite owns the
domain details needed to execute and score one isolated Agent.
"""
