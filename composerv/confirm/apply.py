"""Apply a parsed confirm submission to the store: person names/notes + the per-scope brief."""

from __future__ import annotations

from composerv.confirm.form import BriefInput, PersonUpdate


def apply_submission(store, scope: str, updates: list[PersonUpdate], brief: BriefInput,
                     *, updated_at: str = "") -> None:
    for u in updates:
        store.upsert_person(u.person_id)
        store.set_person_name(u.person_id, u.name, sensitive=u.sensitive)
        store.set_person_note(u.person_id, u.note)
    store.set_brief(scope, brief.context, brief.style, updated_at=updated_at)
