"""Everything under tests/fixtures/ is data for the framework tests.

The synthetic bundles here carry their own test_skill.py modules, which the
determinism guard reads as source; pytest does not collect them.
"""

collect_ignore_glob = ["*"]
