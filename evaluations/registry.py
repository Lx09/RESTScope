"""Explicit registry for the three approved Operation Smoke evaluations."""

from evaluations.agents.patch import SUITE as PATCH_SUITE
from evaluations.agents.plan import SUITE as PLAN_SUITE
from evaluations.agents.solve import SUITE as SOLVE_SUITE


SUITES = {
    "plan": PLAN_SUITE,
    "solve": SOLVE_SUITE,
    "patch": PATCH_SUITE,
}
