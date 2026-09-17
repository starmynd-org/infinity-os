"""Typed options for existing acts, without inventing proposals to create more work.

These are runtime representations, not producer ITEM payloads. Reads never write an
option row or bypass a transition's authority checks.
"""
from dataclasses import dataclass
from . import rooms

# P3-01, 2026-09-13. The engine's `done` writes the note into `work_item.result` (`_finish`) and
# `reopen` clears state, claim and attempts but never touches `result`, so a done-and-undo leaves
# the note where the prior result text stood (Screening measured a 55-character note replacing a
# 147-character text on 0410). Restoring the prior bytes is a transition change and is Platform's;
# what this surface owes the person is the truth BEFORE the press, in the one sentence every Mark
# done control and every inspector's "Available actions" row already renders. Named constants so a
# test reads exactly the words a person sees.
DONE_DISCLOSURE = ('Your note becomes this task\'s stored result text and replaces whatever '
                   'result it held before.')
DONE_INVERSE_NOTE = ('Undo reopens this task as unfinished work and keeps your note as its '
                     'stored result; the earlier result text is not restored.')


@dataclass(frozen=True)
class NativeOption:
    option_id: str
    action: str
    label: str
    does: str
    source_type: str
    source_id: str
    verb: str
    text_label: str = ""
    min_text: int = 0
    inverse_verb: str = ""
    inverse_note: str = ""


def for_row(row, facts, *, operator):
    """Read actual source facts and use the existing guard; recheck at POST."""
    source = row['source_type']
    sid = str(row['source_id'])
    if (source == 'work_item' and row.get('primary_verb') == 'Mark my task done'
            and facts.get('work_state') in ('inbox', 'active')):
        try:
            rooms.assert_allowed_on('attention', 'done', {'id': sid},
                                    acting_on=sid, actor=operator)
        except rooms.ItemRefusal:
            return ()
        return (NativeOption('mark-done', 'mark_done', 'Mark done',
                             'Record what you did and mark this task complete. ' + DONE_DISCLOSURE,
                             source, sid, 'done', 'What you did, in one line', 12,
                             'reopen', DONE_INVERSE_NOTE),)
    if (source == 'recommendation' and facts.get('recommendation_state') == 'open'
            and (facts.get('recommendation_text') or '').strip()):
        return (
            NativeOption('approve-proposal', 'approve', 'Approve',
                         'Approve this proposal and create the work it describes.',
                         source, sid, 'recommend accept',
                         inverse_note='A decision on a proposal cannot be undone.'),
            NativeOption('reject-proposal', 'reject', 'Reject',
                         'Reject this proposal with your reason; no work is created.',
                         source, sid, 'recommend reject', 'Why you are rejecting it', 12,
                         inverse_note='A decision on a proposal cannot be undone.'),
        )
    return ()
