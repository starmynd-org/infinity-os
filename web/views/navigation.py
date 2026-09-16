"""Allowlisted Attention navigation context; never a caller-supplied return URL."""
from urllib.parse import quote, urlencode

FILTERS = ('queue', 'review', 'restricted', 'stale', 'unmeasured', 'awaiting')
SORT_KEYS = ('title', 'kind', 'freshness', 'tier')

#: The tab reads "Awaiting acceptance" and its key used to be `done`, which is the opposite of
#: what the rows are (finished work NOT yet accepted). Links written before the rename keep
#: working through this alias (A4 INFINITY-STREAMLINE, STREAMLINE item 9, 2026-09-14).
FILTER_ALIASES = {'done': 'awaiting'}


def canonical_filter(key):
    """The one place an old filter key becomes the current one. Unknown keys pass through."""
    return FILTER_ALIASES.get(key, key)


def context(args):
    key = canonical_filter(args.get('filter'))
    result = {'filter': key if key in FILTERS else 'queue'}
    if args.get('sort') in SORT_KEYS:
        result.update(sort=args['sort'], dir='desc' if args.get('dir') == 'desc' else 'asc')
    for key in ('offset', 'limit'):
        value = args.get(key, '')
        if str(value).isascii() and str(value).isdigit() and len(str(value)) <= 9:
            result[key] = int(value)
    return result


def report_url(item_id, args):
    source, separator, sid = item_id.partition(':')
    if source != 'work_item' or not separator or not sid:
        return None
    return '/task/' + quote(sid, safe='') + '?' + urlencode(dict(context(args), from_attention='1')) + '#completion-report'


def task_url(item_id, args):
    """P3-02: the task's own page (brief, report, thread, trail) for any work item, carrying the
    same allowlisted queue context as the report link so its Return link comes back to this
    inspector at the same page. No fragment: the person is reading the whole record, not one
    section. None for anything that is not a work item."""
    source, separator, sid = item_id.partition(':')
    if source != 'work_item' or not separator or not sid:
        return None
    return '/task/' + quote(sid, safe='') + '?' + urlencode(dict(context(args), from_attention='1'))


def inspector_url(item_id, args):
    return '/attention/' + quote(item_id, safe=':') + '?' + urlencode(context(args))
