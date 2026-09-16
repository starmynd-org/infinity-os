"""Resolve a native option by typed identity before ordinary guarded dispatch."""
from . import actions


def perform(action, item_id, text, *, source_type, port, operator):
    expected = 'work_item' if action == 'mark_done' else 'recommendation'
    if action not in ('mark_done', 'approve', 'reject') or source_type != expected:
        raise actions.ActionRefused('This action requires its original typed source.')
    row = port.item(expected + ':' + str(item_id))
    if row is None or not row.readable:
        raise actions.ActionRefused('This item is no longer available for this action in Attention.')
    option = next((o for o in row.native_options if o.action == action
                   and o.source_type == expected and o.source_id == str(item_id)), None)
    if option is None:
        raise actions.ActionRefused('This item no longer offers that action. Reload its current state.')
    item = {'id': str(item_id), 'kind': row.kind, 'source_type': expected}
    if action == 'mark_done':
        return actions.mark_my_task_done('attention', item=item, summary=text, operator=operator)
    return actions.recommend('attention', item=item, accept=action == 'approve',
                             reason=text, operator=operator)
