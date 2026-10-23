"""The recommendation reads are asked for every plan's files at once.

Finalizing the queues ranks every live plan in the repository, so the target
file set of these reads is the size of the repository. SQLite caps the bound
parameters of one statement (32766 in current builds, 999 before 3.32), so an
``IN`` list built from that set has to go out in pieces: one statement over
every path raises ``sqlite3.OperationalError: too many SQL variables`` and the
finalize, which is shared by the health persist, the queue rebuild after prune
and the idle-file re-score, fails with it.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud.analysis.coverage_map import (
    tests_covering_files as covering_files,
)
from repowise.core.persistence.crud.analysis.function_facts import (
    tests_in_files as load_tests_in_files,
)
from repowise.core.persistence.crud.analysis.refactoring_recommendations import _symbol_spans

# Above the largest per-statement parameter cap SQLite can be built with.
PATHS = [f"src/module_{i}/file_{i}.py" for i in range(33_000)]


@pytest.mark.parametrize(
    "read",
    [
        lambda session: _symbol_spans(session, "repo-1", PATHS),
        lambda session: load_tests_in_files(session, "repo-1", PATHS),
        lambda session: covering_files(session, "repo-1", set(PATHS)),
    ],
    ids=["symbol_spans", "tests_in_files", "tests_covering_files"],
)
async def test_a_target_set_larger_than_the_parameter_cap_is_read(async_session, read):
    assert await read(async_session) == {}
