"""Current scene dependency checks for the project router, without generation.

The caller retains its project lock and transaction.  Pass validate_current_plan
as generation_tasks.begin's current_validator; the ledger resumes existing
receipts before invoking it.  There is no new provider, approval, or task here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import generation_tasks


def validate_current_plan(root: str | Path, plan: dict[str, Any],
                          project: dict[str, Any], *,
                          current_compiled: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compare only the planned slot's freshly compiled, semantic dependencies.

    Normal scene plans are compiled through project.compile_scene.  Adapters
    with candidate-specific prompts can supply a current_compiled descriptor
    rebuilt for that same candidate, not an unvalidated old round file.  The
    pure ledger validator remains available for other generation workflows.

    Returns valid/task_id/slot_id/expected_compile_fingerprint/
    current_compile_fingerprint/prepared_identity_fingerprint/relevant_change/
    change_scope/changed_paths.  A mismatch raises generation_tasks.TaskError
    with the same fields on error.details.  No outer revision is compared.
    """
    directory = Path(root).expanduser().resolve()
    if not isinstance(project, dict) or not isinstance(plan, dict):
        raise generation_tasks.TaskError("invalid_current_project: expected a project and generation plan")
    identity = plan.get("identity")
    if not isinstance(identity, dict) or identity.get("project_id") != project.get("project_id"):
        raise generation_tasks.TaskError("project_mismatch: plan must belong to the current project")
    if current_compiled is None:
        try:
            import project as project_module
            current_compiled = project_module.compile_scene(directory, project, identity["slot_id"])
        except generation_tasks.TaskError:
            raise
        except Exception as error:
            failure = generation_tasks.TaskError("stale_generation_plan: current scene contract cannot be compiled")
            failure.details = {
                "valid": False, "task_id": plan.get("task_id"), "slot_id": identity.get("slot_id"),
                "expected_compile_fingerprint": generation_tasks.current_compiled_fingerprint(directory, plan["compiled"]),
                "current_compile_fingerprint": None,
                "prepared_identity_fingerprint": identity.get("compile_fingerprint"),
                "relevant_change": True, "change_scope": "scene_contract", "changed_paths": [], "reason": str(error),
            }
            raise failure from error
    return generation_tasks.validate_current_compiled(directory, plan, current_compiled)
