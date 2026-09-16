"""The human queue: recommendations, tiers, ranking, typed defers, and the defaults gate.

    human_queue.transitions   the verbs, registered once each on D1's narrow waist
    human_queue.rank          U(i) over the depends_on DAG, and the decomposition
    human_queue.tiers         Decide / Judge / Shape, by cognitive mode
    human_queue.defaults      the hard-flag null-branch rule, classified by the database
    human_queue.checkpoints   07:00 and 19:00, and the grace rule
    human_queue.reads         what D7 renders. Reads only.

WHY THE PACKAGE IS `human_queue` AND NOT `queue`. `queue` is a Python standard library module,
and a top-level package by that name on `sys.path` shadows it for every import in the process,
including any dependency importing `queue` for a thread-safe FIFO. Measured, not feared: with
the repo root on the path, `from queue.human_queue import transitions` raises
`ModuleNotFoundError: 'queue' is not a package` -- a directory with no `__init__.py` is only a
namespace portion, so the stdlib's regular module wins the scan. The lane owns `queue/**` as its
directory; the importable package inside it is named for what it is, which is the same shape
`engine/swarm_engine/` and `adapter/brain_adapter/` already use.

The one property this package exists to hold:

    **There is no code path by which a recommendation becomes executed work without a human
    decider.**

Proven by `queue/tests/test_no_self_execution.py`, which is named in the report because a
criterion verified by review rather than by test is a criterion nobody can re-check.
"""

from . import transitions as _transitions  # noqa: F401  (import registers the verbs)
# The stopwatch's three verbs. Imported HERE and not only by the CLI, for the reason
# `store.transitions._HOOK_MODULES` gives about surfaces forgetting: a verb that registers only
# when one surface happens to import its module is a verb the console, the MCP server and
# `store.registered()` each discover differently.
from . import time_ledger as _time_ledger  # noqa: F401  (import registers the verbs)
# `queue draft options` (task 0165, V3). Here for the same reason as the two above: a verb that
# registers only when one surface happens to import its module is a verb the console, the MCP
# server and `store.registered()` each discover differently. It is on NO room's allowlist --
# drafting is a producer act and the console's verb on an option is `recommend accept`.
from . import options as _options  # noqa: F401  (import registers the verb)

__all__ = ["transitions", "time_ledger", "options", "rank", "tiers", "defaults",
           "checkpoints", "reads", "cli"]
