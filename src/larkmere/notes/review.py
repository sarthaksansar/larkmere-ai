"""The approval gate. Code can only get an ApprovedNote from a named person's decision here.

The robot's note writer accepts nothing else, and re-checks the hash just before typing, so a
note changed after approval is refused. Decisions go to an append-only audit log.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from larkmere.notes import grounding
from larkmere.notes.transcripts import SOURCES, TRANSCRIPTS, parse


class NotApproved(Exception):
    pass


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovedNote:
    draft_id: str
    account_id: str
    text: str
    reviewer: str
    text_sha256: str

    def verify(self):
        if not self.reviewer.strip():
            raise NotApproved("no reviewer named")
        if _sha(self.text) != self.text_sha256:
            raise NotApproved("the note changed after it was approved")


class ReviewQueue:
    def __init__(self, drafts, audit_path):
        self.drafts = {d.draft_id: d for d in drafts}
        self.audit_path = Path(audit_path)
        self.decisions = {}
        self.approved = []

    def _utterances(self, draft_id):
        return parse(TRANSCRIPTS[draft_id][2])

    def flags(self, draft_id):
        return grounding.check_draft(self.drafts[draft_id], self._utterances(draft_id))

    def view(self):
        rows = []
        for draft_id, draft in self.drafts.items():
            utterances = self._utterances(draft_id)
            rows.append({
                **draft.to_json(),
                "text": draft.text(),
                "transcript_source": SOURCES.get(draft_id, "transcript"),
                "flags": self.flags(draft_id),
                "injection_lines": grounding.injection_lines(utterances),
                "transcript": [{"n": u.number, "time": u.time, "speaker": u.speaker, "text": u.text} for u in utterances],
                "decision": self.decisions.get(draft_id),
            })
        return rows

    def approve(self, draft_id, reviewer, text):
        """Approve final text, possibly edited. Raises NotApproved if it's blocked."""
        draft = self.drafts[draft_id]
        if draft_id in self.decisions:
            raise NotApproved("already decided")
        if not reviewer.strip():
            raise NotApproved("a reviewer's name is required")
        edited = text != draft.text()
        # An untouched draft answers for its citations too. Edited text has no citations left to check.
        flags = (self.flags(draft_id) if not edited
                 else grounding.check_text(text, self._utterances(draft_id), draft.meeting))
        if flags:
            raise NotApproved("; ".join(flags))
        note = ApprovedNote(draft_id, draft.account_id, text, reviewer.strip(), _sha(text))
        self._record(draft, "approved", reviewer, text, edited)
        self.approved.append(note)
        return note

    def reject(self, draft_id, reviewer, reason=""):
        draft = self.drafts[draft_id]
        if draft_id in self.decisions:
            raise NotApproved("already decided")
        if not reviewer.strip():
            raise NotApproved("a reviewer's name is required")
        self._record(draft, "rejected", reviewer, draft.text(), False, reason)

    def finished(self):
        return len(self.decisions) == len(self.drafts)

    def _record(self, draft, decision, reviewer, text, edited, reason=""):
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "draft_id": draft.draft_id,
            "account_id": draft.account_id,
            "decision": decision,
            "reviewer": reviewer.strip(),
            "edited": edited,
            "source": draft.source,
            "draft_sha256": _sha(draft.text()),
            "final_sha256": _sha(text),
            "reason": reason,
        }
        self.decisions[draft.draft_id] = {"decision": decision, "reviewer": reviewer.strip(), "edited": edited,
                                          "reason": reason, "final_text": text}
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
