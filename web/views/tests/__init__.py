"""Tests for the view layer's own surfaces.

NO RUNNER DISPATCHES THIS DIRECTORY YET, and that is a fact rather than an intention.
`web/tests/run-all.sh` enumerates `web/tests/*` and nothing else; `tools/check-at-head.sh` knows
the dispatched set across the three test directories and this is not in it. Packet ATT-1d was
scoped away from `web/tests/**`, so its tests landed here, and a suite no runner runs is a suite
nobody will notice going red. Registering it is somebody's next packet, not a thing this one did
quietly.

Run it by name, from the repo root, with a scratch database:

    BRAIN_PG_DB=<scratch> ENGINE_SCRATCH_DB=<scratch> QUEUE_SCRATCH_DB=<scratch> \
        python3 -m web.views.tests.test_act_door
"""
