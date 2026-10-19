"""Run a small lookup QueryPlan through the same safety path as answers.

Compiler -> validator -> guardrails -> read-only executor. Used by preflight
guards (entity identity, value domains); never bypasses validation.
"""
from planning.models import QueryPlan


class ProbeRunner:
    def __init__(self, compiler, validator, guardrails, executor):
        self._compiler = compiler
        self._validator = validator
        self._guardrails = guardrails
        self._executor = executor

    def run(self, probe: QueryPlan):
        compiled = self._compiler.compile(probe)
        validation = self._validator.validate(compiled=compiled, plan=probe)
        if not validation.valid:
            raise ValueError("Lookup query failed SQL validation.")
        approval = self._guardrails.evaluate(compiled=compiled, plan=probe, validation=validation)
        if not approval.allowed:
            raise PermissionError("Lookup query was blocked by guardrails.")
        return self._executor.execute(compiled=compiled, validation=validation, guardrails=approval)
