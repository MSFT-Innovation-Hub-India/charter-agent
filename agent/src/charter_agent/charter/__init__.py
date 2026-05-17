"""Charter lifecycle: validate → ratify → (amend → re-ratify).

Per invariant 7: only this module may stamp `ratified_at`/`ratified_by` and bump
`version`. Anywhere else that needs a Charter must treat the file as read-only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..state import home_dir
from .schemas import AmendmentSpec, Charter

CHARTER_FILENAME = "charter.json"


def charter_path(home: Path | None = None) -> Path:
    return (home or home_dir()) / CHARTER_FILENAME


def read_charter(home: Path | None = None) -> Charter | None:
    p = charter_path(home)
    if not p.exists():
        return None
    return Charter.model_validate_json(p.read_text(encoding="utf-8"))


def ratify(charter: Charter, *, by_upn: str, home: Path | None = None) -> Charter:
    """Ratify a proposed Charter on behalf of the coordinator.

    Validates structural invariants, stamps ratification fields, atomically
    writes `$HOME/charter.json`. Returns the persisted Charter.
    """
    if charter.is_ratified():
        raise ValueError("charter: already ratified — use amend() for changes.")
    _validate_invariants(charter)

    ratified = charter.model_copy(
        update={
            "ratified_at": datetime.now(timezone.utc),
            "ratified_by": by_upn.strip().lower(),
        }
    )
    _atomic_write(ratified, home)
    return ratified


def amend(
    current: Charter,
    amendment: AmendmentSpec,
    new_charter: Charter,
    *,
    by_upn: str,
    home: Path | None = None,
) -> Charter:
    """Replace a ratified Charter with `new_charter` (version bumped), keeping the
    audit trail of which amendment drove it.

    The amend-charter skill is responsible for producing `new_charter` from
    `current` + `amendment.changes`. This function only enforces invariants.
    """
    if not current.is_ratified():
        raise ValueError("charter: cannot amend an unratified Charter.")
    if new_charter.project_id != current.project_id:
        raise ValueError("charter: project_id is immutable across amendments.")
    if new_charter.version != current.version + 1:
        raise ValueError(
            f"charter: amendment must bump version to {current.version + 1}, "
            f"got {new_charter.version}."
        )
    _validate_invariants(new_charter)

    next_charter = new_charter.model_copy(
        update={
            "ratified_at": datetime.now(timezone.utc),
            "ratified_by": by_upn.strip().lower(),
        }
    )
    _atomic_write(next_charter, home)
    return next_charter


def _validate_invariants(c: Charter) -> None:
    task_ids = [t.task_id for t in c.tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("charter: duplicate task_id.")
    known = set(task_ids)
    for t in c.tasks:
        for dep in t.depends_on:
            if dep not in known:
                raise ValueError(f"charter: task {t.task_id!r} depends on unknown {dep!r}.")

    upns = {c.stakeholders.coordinator, c.stakeholders.deputy, *c.stakeholders.owners}
    for t in c.tasks:
        if t.owner_upn not in upns:
            raise ValueError(
                f"charter: task {t.task_id!r} owner {t.owner_upn!r} not in stakeholders."
            )


def _atomic_write(charter: Charter, home: Path | None) -> None:
    p = charter_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(charter.model_dump(mode="json"), indent=2, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(p)


__all__ = ["amend", "charter_path", "ratify", "read_charter"]
