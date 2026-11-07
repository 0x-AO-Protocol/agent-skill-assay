# SPDX-License-Identifier: MIT
"""Socket creation is disabled in the tests of bundles outside the legacy set.

The seven bundles shipped by v0.1.3 are unaffected. This is a pre-merge check,
not isolation.
"""

import pytest

from scripts.determinism_guard import is_standard_bundle_test, sockets_disabled


@pytest.fixture(autouse=True)
def _sockets_disabled_for_standard_bundles(request):
    if not is_standard_bundle_test(request.node.path):
        yield
        return
    with sockets_disabled():
        yield
