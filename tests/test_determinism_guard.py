# SPDX-License-Identifier: MIT
"""Tests of scripts/determinism_guard.py on synthetic bundles.

Every bundle here is a copy of tests/fixtures/standard_profile with one module
replaced, so each test changes exactly one thing. Case tables hold one case per
line as "id :: value [:: value]", with \\n standing for a line break.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "standard_profile"
BUNDLE_ID = "sample/word_tally"


def _table(text: str) -> list:
    params = []
    for line in textwrap.dedent(text).strip().splitlines():
        case_id, *values = [part.replace("\\n", "\n") for part in line.split(" :: ")]
        params.append(pytest.param(*values, id=case_id))
    return params


@pytest.fixture(scope="module")
def guard():
    from scripts import determinism_guard

    return determinism_guard


def _bundle(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo, ignore=shutil.ignore_patterns("__pycache__"))
    return repo / "skills" / BUNDLE_ID


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")


def _findings(guard, bundle: Path) -> list[tuple[str, str]]:
    return [(item.path, item.rule) for item in guard.static_findings(bundle)]


def _runtime(guard, tmp_path: Path, source: str, name="skill.py") -> list:
    """Static findings of a bundle whose ``name`` module is replaced by ``source``."""
    bundle = _bundle(tmp_path)
    _write(bundle / name, source)
    return _findings(guard, bundle)


def _test_module(guard, tmp_path: Path, source: str) -> list:
    return _runtime(guard, tmp_path, source, "test_skill.py")


# The fixture bundle is clean ----------------------------------------------


def test_fixture_bundle_has_no_static_finding(guard):
    assert guard.static_findings(FIXTURE_REPO / "skills" / BUNDLE_ID) == []


def test_findings_name_the_file_line_and_rule(guard, tmp_path):
    bundle = _bundle(tmp_path)
    _write(bundle / "skill.py", "VALUE = 1\nimport os\n")
    [finding] = guard.static_findings(bundle)
    assert (finding.path, finding.line, finding.rule) == ("skill.py", 2, "import")
    assert str(finding) == f"skill.py:2: import: {finding.detail}"


# Runtime modules: the closed import allow-list ------------------------------

FORBIDDEN_IMPORTS = _table("""
    model-openai :: import openai
    model-anthropic :: import anthropic
    model-google-genai :: from google import genai
    model-transformers :: import transformers
    network-socket :: import socket
    network-ssl :: import ssl
    network-http :: import http.client
    network-urllib :: import urllib
    network-urllib-parse :: from urllib.parse import quote
    network-urllib-request :: import urllib.request
    network-requests :: import requests
    network-httpx :: import httpx
    process-subprocess :: import subprocess
    process-multiprocessing :: import multiprocessing
    process-os :: import os
    process-threading :: import threading
    process-asyncio :: import asyncio
    process-concurrent :: from concurrent import futures
    random-random :: import random
    random-secrets :: import secrets
    random-uuid :: from uuid import uuid4
    clock-time :: import time
    file-io :: import io
    file-pathlib :: from pathlib import Path
    file-shutil :: import shutil
    file-tempfile :: import tempfile
    file-codecs :: import codecs
    file-shelve :: import shelve
    environment-sys :: import sys
    environment-platform :: import platform
    environment-locale :: import locale
    dynamic-importlib :: import importlib
    dynamic-inspect :: import inspect
    native-ctypes :: import ctypes
    serial-pickle :: import pickle
    serial-marshal :: import marshal
    statistics :: import statistics
    third-party-yaml :: import yaml
    third-party-numpy :: import numpy
    framework-loader :: from skill_assay.core.loader import SkillLoader
    framework-package :: import skill_assay
    other-bundle :: from skills.finance.period_close import skill
    alias-import :: import os as o
    alias-from :: from os import system as s
    """)


@pytest.mark.parametrize("source", FORBIDDEN_IMPORTS)
def test_runtime_import_outside_the_allow_list_is_a_finding(guard, tmp_path, source):
    assert ("skill.py", "import") in _runtime(guard, tmp_path, source)


def test_import_inside_a_function_or_try_block_is_found(guard, tmp_path):
    source = """
    def load():
        try:
            import socket
        except ImportError:
            socket = None
        return socket
    """
    assert ("skill.py", "import") in _runtime(guard, tmp_path, source)


def test_every_runtime_module_is_checked_not_only_skill_py(guard, tmp_path):
    bundle = _bundle(tmp_path)
    _write(bundle / "helpers.py", "import os\n")
    _write(bundle / "sub" / "__init__.py", "")
    _write(bundle / "sub" / "test_skill.py", "import subprocess\n")
    found = _findings(guard, bundle)
    assert ("helpers.py", "import") in found
    assert ("sub/test_skill.py", "import") in found


def test_allow_list_is_the_published_set(guard):
    assert guard.RUNTIME_STDLIB_ALLOWLIST == frozenset(
        "__future__ abc bisect collections collections.abc copy dataclasses decimal "
        "enum fractions functools hashlib heapq html itertools json math numbers "
        "operator re string textwrap typing unicodedata".split()
    )
    assert guard.FRAMEWORK_ALLOWLIST == frozenset({"skill_assay.core.base_skill"})
    assert guard.DATETIME_NAMES == frozenset({"date", "timedelta"})


STDLIB_SOURCE = """
from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

VALUE = json.dumps({"a": str(Decimal("1.5"))}) + re.escape("x")
ORDER, WRAP = OrderedDict(), dataclass
"""
ALLOWED_SOURCES = [pytest.param(STDLIB_SOURCE, id="stdlib")] + _table(r"""
    collections-abc :: import collections.abc\n\nKIND = collections.abc.Mapping
    from-collections-abc :: from collections import abc\n\nKIND = abc.Mapping
    base-skill :: from skill_assay.core.base_skill import BaseSkill
    base-skill-module :: import skill_assay.core.base_skill\nB = skill_assay.core.base_skill.BaseSkill
    re-compile :: import re\n\nPATTERN = re.compile(r'[a-z]+')
    string-only-mention :: NOTE = "import os; os.environ['K']; open('x', 'w'); socket.socket(); eval"
    comment-only-mention :: # import socket\n# os.system("ls"); datetime.now(); open("x", "w")\nVALUE = 1
    local-variable-named-requests :: def count(items):\n    requests = list(items)\n    return len(requests)
    getattr-literal-on-a-value :: def real_part(value):\n    return getattr(value, "real", 0)
    """)


@pytest.mark.parametrize("source", ALLOWED_SOURCES)
def test_allowed_runtime_source_has_no_finding(guard, tmp_path, source):
    assert _runtime(guard, tmp_path, source) == []


def test_relative_and_absolute_imports_of_the_bundle_itself_are_allowed(
    guard, tmp_path
):
    bundle = _bundle(tmp_path)
    _write(bundle / "helpers.py", "def double(value):\n    return value * 2\n")
    _write(bundle / "sub" / "__init__.py", "")
    _write(
        bundle / "sub" / "inner.py", "from .. import helpers\n\nX = helpers.double(1)"
    )
    _write(
        bundle / "skill.py",
        """
        from . import helpers
        from .helpers import double
        from skills.sample.word_tally.helpers import double as twice

        VALUE = helpers.double(1) + double(2) + twice(3)
        """,
    )
    assert _findings(guard, bundle) == []


def test_relative_import_that_leaves_the_bundle_is_a_finding(guard, tmp_path):
    found = _runtime(guard, tmp_path, "from .. import other\n")
    assert ("skill.py", "relative-import") in found


def test_star_import_is_a_finding(guard, tmp_path):
    assert ("skill.py", "star-import") in _runtime(
        guard, tmp_path, "from json import *"
    )


# Runtime modules: submodule chains and module references --------------------

SUBMODULE_CASES = _table("""
    import-submodule :: import json.decoder
    from-import-submodule :: from json import decoder
    attribute-chain :: import json\\n\\nDECODER = json.decoder.JSONDecoder
    attribute-to-forbidden-module :: import json\\n\\nCODECS = json.codecs
    chain-through-own-module :: from . import helpers\\n\\nCODECS = helpers.json.codecs
    """)


@pytest.mark.parametrize("source", SUBMODULE_CASES)
def test_submodule_outside_the_allow_list_is_a_finding(guard, tmp_path, source):
    bundle = _bundle(tmp_path)
    _write(bundle / "helpers.py", "import json\n\nVALUE = json.dumps(1)\n")
    _write(bundle / "skill.py", source)
    assert ("skill.py", "submodule") in _findings(guard, bundle)


def test_imported_module_used_as_a_value_is_a_finding(guard, tmp_path):
    found = _runtime(guard, tmp_path, "import json\n\nALIAS = json\n")
    assert ("skill.py", "module-reference") in found


MODULE_VALUE_CASES = _table(r"""
    chain-to-a-module-as-a-value :: import dataclasses\n\nMOD = dataclasses.re
    chain-to-a-module-in-a-local :: import dataclasses\n\n\ndef run():\n    found = dataclasses.re\n    return found.sys
    chain-to-a-module-as-getattr-receiver :: import dataclasses\n\nMOD = getattr(dataclasses.re, "sys")
    own-module-chain-to-a-module :: from . import helpers\n\nMOD = helpers.json
    """)


@pytest.mark.parametrize("source", MODULE_VALUE_CASES)
def test_attribute_chain_that_ends_on_a_module_is_a_finding(guard, tmp_path, source):
    bundle = _bundle(tmp_path)
    _write(bundle / "helpers.py", "import json\n\nVALUE = json.dumps(1)\n")
    _write(bundle / "skill.py", source)
    assert ("skill.py", "module-reference") in _findings(guard, bundle)


# Package modules that importing the bundle runs ------------------------------

PACKAGE_MODULES = _table("""
    category-package :: skills/sample/__init__.py
    skills-package :: skills/__init__.py
    """)


@pytest.mark.parametrize("relative", PACKAGE_MODULES)
def test_package_module_that_runs_before_the_bundle_is_checked(
    guard, tmp_path, relative
):
    bundle = _bundle(tmp_path)
    _write(bundle.parents[2] / relative, "import os\n")
    assert (relative, "import") in _findings(guard, bundle)


def test_real_category_packages_have_no_finding(guard):
    for package in sorted((ROOT / "skills").glob("*/__init__.py")):
        bundles = sorted(path.parent for path in package.parent.glob("*/manifest.yaml"))
        if bundles:
            found = guard.static_findings(bundles[0])
            assert [item for item in found if item.path.startswith("skills/")] == []


# Runtime modules: forbidden built-ins and dynamic name resolution -----------

FORBIDDEN_BUILTINS = _table("""
    eval :: VALUE = eval('1 + 1')
    exec :: exec('VALUE = 1')
    compile :: CODE = compile('1', 'x', 'eval')
    __import__ :: MODULE = __import__('json')
    open :: HANDLE = open('data.txt')
    open-alias :: READ = open
    input :: ANSWER = input()
    breakpoint :: breakpoint()
    vars :: NAMES = vars()
    globals :: NAMES = globals()
    locals :: NAMES = locals()
    """)


@pytest.mark.parametrize("source", FORBIDDEN_BUILTINS)
def test_forbidden_builtin_is_a_finding(guard, tmp_path, source):
    assert ("skill.py", "builtin") in _runtime(guard, tmp_path, source)


DYNAMIC_ATTRIBUTE_CASES = _table("""
    getattr-non-literal :: def get(value, name):\\n    return getattr(value, name)
    getattr-on-a-module :: import json\\n\\nDUMPS = getattr(json, 'dumps')
    setattr-non-literal :: def put(value, name):\\n    setattr(value, name, 1)
    delattr-non-literal :: def drop(value, name):\\n    delattr(value, name)
    getattr-as-a-value :: GET = getattr
    getattr-literal-forbidden-name :: def env(value):\\n    return getattr(value, 'environ')
    """)


@pytest.mark.parametrize("source", DYNAMIC_ATTRIBUTE_CASES)
def test_dynamic_attribute_access_is_a_finding(guard, tmp_path, source):
    assert ("skill.py", "dynamic-attribute") in _runtime(guard, tmp_path, source)


LITERAL_NAME_CASES = _table("""
    getattr-literal-builtin-name :: def get(value):\\n    return getattr(value, 'eval')
    getattr-literal-reflection-name :: def get(value):\\n    return getattr(value, 'attrgetter')
    setattr-literal-builtin-name :: def put(value):\\n    setattr(value, 'open', 1)
    """)


@pytest.mark.parametrize("source", LITERAL_NAME_CASES)
def test_attribute_builtin_with_a_forbidden_literal_name_is_a_finding(
    guard, tmp_path, source
):
    assert ("skill.py", "dynamic-attribute") in _runtime(guard, tmp_path, source)


# Runtime modules: forbidden attributes ---------------------------------------


def test_underscore_attribute_of_an_imported_module_is_a_finding(guard, tmp_path):
    source = "import json\n\nENCODER = json._default_encoder\n"
    assert ("skill.py", "private-attribute") in _runtime(guard, tmp_path, source)


DUNDERS = (
    "__dict__ __builtins__ __globals__ __subclasses__ __bases__ __mro__ __code__ "
    "__closure__ __getattribute__ __loader__ __spec__ __self__"
).split()


@pytest.mark.parametrize("name", DUNDERS)
def test_forbidden_dunder_attribute_is_a_finding(guard, tmp_path, name):
    source = f"def peek(value):\n    return value.{name}\n"
    assert ("skill.py", "dunder-attribute") in _runtime(guard, tmp_path, source)


CLOCK_ATTRIBUTES = _table("""
    now :: datetime.now()
    today :: date.today()
    utcnow :: datetime.utcnow()
    fromtimestamp :: datetime.fromtimestamp(0)
    utcfromtimestamp :: datetime.utcfromtimestamp(0)
    timestamp :: STAMP.timestamp()
    astimezone :: STAMP.astimezone()
    strptime :: datetime.strptime('2026', '%Y')
    """)


@pytest.mark.parametrize("expression", CLOCK_ATTRIBUTES)
def test_clock_and_local_time_attribute_is_a_finding(guard, tmp_path, expression):
    source = f"from datetime import date, datetime\n\nVALUE = {expression}\n"
    assert ("skill.py", "clock-attribute") in _runtime(guard, tmp_path, source)


DATES_ALLOWED = _table("""
    from-datetime-import-date :: from datetime import date\\n\\nDAY = date(2026, 1, 1)
    from-datetime-import-timedelta :: from datetime import timedelta\\n\\nSTEP = timedelta(1)
    from-datetime-import-both :: from datetime import date, timedelta
    two-statements :: from datetime import date\\nfrom datetime import timedelta
    isoformat :: def render(value):\\n    return value.isoformat()
    """)


@pytest.mark.parametrize("source", DATES_ALLOWED)
def test_dates_through_date_timedelta_and_isoformat_have_no_finding(
    guard, tmp_path, source
):
    assert _runtime(guard, tmp_path, source) == []


DATES_FORBIDDEN = _table("""
    import-datetime :: import datetime :: import
    import-datetime-as :: import datetime as dt :: import
    from-datetime-import-datetime :: from datetime import datetime :: import
    from-datetime-import-timezone :: from datetime import timezone :: import
    from-datetime-import-date-as :: from datetime import date as day :: import
    strftime :: def render(value):\\n    return value.strftime("%Y") :: clock-attribute
    import-time :: import time :: import
    import-calendar :: import calendar :: import
    """)


@pytest.mark.parametrize(("source", "rule"), DATES_FORBIDDEN)
def test_datetime_beyond_date_and_timedelta_is_a_finding(guard, tmp_path, source, rule):
    assert ("skill.py", rule) in _runtime(guard, tmp_path, source)


ENVIRONMENT_ATTRIBUTES = _table("""
    environ :: def read(value):\\n    return value.environ
    getenv :: def read(value):\\n    return value.getenv('KEY')
    credential :: class Probe:\\n    def key(self):\\n        return self.credential('API_KEY')
    """)


@pytest.mark.parametrize("source", ENVIRONMENT_ATTRIBUTES)
def test_environment_and_credential_attribute_is_a_finding(guard, tmp_path, source):
    assert ("skill.py", "environment-attribute") in _runtime(guard, tmp_path, source)


# Runtime modules: reflection routes, formatting and class creation -----------
#
# Each case is a source string that the static guard parses; none is imported or run.

REFLECTION_ROUTES = _table(r"""
    builtins-name :: x = __builtins__ :: dunder-name
    loader-name :: x = __loader__ :: dunder-name
    spec-name :: x = __spec__ :: dunder-name
    file-name :: x = __file__ :: dunder-name
    match-args-assignment :: class K:\n    __match_args__ = ("a",) :: dunder-name
    traceback-attribute :: x = obj.__traceback__ :: dunder-attribute
    base-attribute :: x = obj.__base__ :: dunder-attribute
    func-attribute :: x = obj.__func__ :: dunder-attribute
    wrapped-attribute :: x = obj.__wrapped__ :: dunder-attribute
    class-attribute-assignment :: obj.__class__ = C :: dunder-attribute
    doc-attribute-deletion :: del obj.__doc__ :: dunder-attribute
    generator-frame-attribute :: x = gen.gi_frame :: frame-attribute
    frame-globals-attribute :: x = frame.f_globals :: frame-attribute
    frame-back-attribute :: x = frame.f_back :: frame-attribute
    traceback-frame-attribute :: x = tb.tb_frame :: frame-attribute
    getattr-frame-literal :: x = getattr(gen, "gi_frame") :: dynamic-attribute|frame-attribute
    operator-attrgetter :: import operator\n\nF = operator.attrgetter("a") :: reflection-member
    from-operator-methodcaller :: from operator import methodcaller :: reflection-member
    from-string-formatter :: from string import Formatter :: reflection-member
    typing-alias-get-type-hints :: import typing as t\n\nF = t.get_type_hints :: reflection-member
    from-typing-forward-ref :: from typing import ForwardRef :: reflection-member
    typing-get-args :: import typing\n\nF = typing.get_args :: reflection-member
    functools-singledispatch :: import functools\n\nF = functools.singledispatch :: reflection-member
    functools-singledispatchmethod :: import functools\n\nF = functools.singledispatchmethod :: reflection-member
    functools-update-wrapper :: import functools\n\nF = functools.update_wrapper :: reflection-member
    from-functools-wraps :: from functools import wraps :: reflection-member
    class-pattern-positional :: match p:\n    case C(x):\n        pass :: class-pattern
    class-pattern-dunder-keyword :: match p:\n    case C(__globals__=x):\n        pass :: dunder-attribute
    class-pattern-frame-keyword :: match p:\n    case C(gi_frame=x):\n        pass :: frame-attribute
    f-string-date-directive :: X = f"{d:%B}" :: format-spec
    f-string-nested-spec :: X = f"{x:{spec}}" :: format-spec
    format-date-directive :: X = format(d, "%A") :: format-spec
    format-variable-spec :: X = format(x, spec) :: format-spec
    str-format-call :: X = "{}".format(x) :: format-call
    format-map-call :: X = t.format_map(m) :: format-call
    help-builtin :: x = help :: builtin
    copyright-builtin :: x = copyright :: builtin
    credits-builtin :: x = credits :: builtin
    license-builtin :: x = license :: builtin
    exit-builtin :: x = exit :: builtin
    quit-builtin :: x = quit :: builtin
    metaclass-keyword :: class A(metaclass=M):\n    pass :: class-creation
    type-base :: class M(type):\n    pass :: class-creation
    type-three-arguments :: X = type("X", (), {}) :: class-creation
    type-as-a-value :: t = type :: class-creation
    """)


@pytest.mark.parametrize(("source", "rules"), REFLECTION_ROUTES)
def test_reflection_route_is_a_finding(guard, tmp_path, source, rules):
    found = _runtime(guard, tmp_path, source)
    assert any(("skill.py", rule) in found for rule in rules.split("|"))


REFLECTION_ALLOWED = _table(r"""
    name-read :: x = __name__
    doc-read :: x = __doc__
    all-assignment :: __all__ = ["run"]
    slots-assignment :: class K:\n    __slots__ = ("a",)
    class-name :: x = self.__class__.__name__
    type-qualname :: x = type(x).__qualname__
    super-init :: super().__init__()
    object-setattr :: object.__setattr__(self, "a", 1)
    enum-members :: x = list(C.__members__)
    getattr-plain-literal :: x = getattr(obj, "name")
    operator-itemgetter :: import operator\n\nF = operator.itemgetter(0)
    functools-lru-cache :: import functools\n\nF = functools.lru_cache
    string-ascii-letters :: import string\n\nX = string.ascii_letters
    typing-optional :: import typing\n\nX = typing.Optional[int]
    from-typing-any :: from typing import Any
    class-pattern-keyword :: match p:\n    case C(a=0):\n        pass
    class-pattern-empty :: match p:\n    case C():\n        pass
    f-string-fixed-point :: X = f"{x:.2f}"
    f-string-percent :: X = f"{x:.1%}"
    f-string-grouping :: X = f"{x:,}"
    format-fixed-point :: X = format(x, ".1f")
    percent-formatting :: X = "%s" % x
    string-template :: import string\n\nX = string.Template("$a").substitute(a=1)
    len-builtin :: x = len
    type-one-argument :: x = type(x)
    enum-class :: from enum import Enum\n\n\nclass E(Enum):\n    A = 1
    abc-class :: import abc\n\n\nclass B(abc.ABC):\n    pass
    """)


@pytest.mark.parametrize("source", REFLECTION_ALLOWED)
def test_ordinary_use_beside_a_reflection_route_has_no_finding(guard, tmp_path, source):
    assert _runtime(guard, tmp_path, source) == []


ORDINARY_SKILL_SOURCE = """
import functools
import operator
import string
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Optional

__all__ = ["run"]


class Color(Enum):
    RED = 1


@dataclass(frozen=True)
class Point:
    x: int
    y: int

    def __post_init__(self):
        object.__setattr__(self, "x", self.x)


class Base:
    def __init__(self):
        self.kind = self.__class__.__name__


class Child(Base):
    def __init__(self):
        super().__init__()


@functools.lru_cache(maxsize=None)
def run(params: Optional[Any] = None):
    d = date(2026, 11, 1)
    share, count, value = 0.25, 1234567, 3.14159
    p = Point(0, 1)
    match p:
        case Point(x=0):
            hit = True
        case _:
            hit = False
    return [
        f"{value:.2f}", f"{share:.1%}", f"{count:,}", f"{d}", d.isoformat(),
        "%s-%s" % (d, count), string.Template("$x").substitute(x=1),
        operator.itemgetter(0)([1]), list(Color.__members__), type(p).__qualname__,
        Child().kind, hit, string.ascii_letters[:3], __name__, format(value, ".1f"),
    ]
"""


def test_ordinary_skill_source_has_no_finding(guard, tmp_path):
    assert _runtime(guard, tmp_path, ORDINARY_SKILL_SOURCE) == []


# Manifest declarations, unparseable modules and compiled modules -------------


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        pytest.param("env_vars", {"KEY": {}}, "manifest-env-vars", id="env-vars"),
        pytest.param("requirements", ["requests"], "manifest-requirements", id="req"),
    ],
)
def test_manifest_declaring_env_vars_or_requirements_is_a_finding(
    guard, tmp_path, field, value, rule
):
    import yaml

    bundle = _bundle(tmp_path)
    path = bundle / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    assert ("manifest.yaml", rule) in _findings(guard, bundle)
    manifest[field] = type(value)()
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    assert _findings(guard, bundle) == []


def test_unparseable_module_is_a_finding(guard, tmp_path):
    assert ("skill.py", "syntax") in _runtime(guard, tmp_path, "def broken(:\n")


def test_compiled_module_in_the_bundle_is_a_finding(guard, tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "native.so").write_bytes(b"\x7fELF")
    assert ("native.so", "compiled-module") in _findings(guard, bundle)


# The test module rule set ----------------------------------------------------


def test_test_module_may_run_the_hash_seed_child(guard, tmp_path):
    source = """
    import json
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    import pytest

    BUNDLE = Path(__file__).resolve().parent


    def test_child():
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = "0"
        start = time.perf_counter()
        proc = subprocess.run([sys.executable, "-c", "print(1)"], env=env, check=True)
        assert proc.returncode == 0 and time.perf_counter() - start < 60
        with open(BUNDLE / "fixtures" / "example_params.json", "r") as handle:
            assert json.load(handle)
        assert (BUNDLE / "manifest.yaml").read_text()
        with pytest.raises(ZeroDivisionError):
            1 / 0
    """
    assert _test_module(guard, tmp_path, source) == []


TEST_MODULE_FORBIDDEN = _table("""
    socket :: import socket :: test-import
    ssl :: import ssl :: test-import
    http :: from http import client :: test-import
    urllib :: from urllib import request :: test-import
    requests :: import requests :: test-import
    httpx :: import httpx :: test-import
    model-sdk :: import openai :: test-import
    model-runtime :: import transformers :: test-import
    open-write :: def t():\\n    open('out.txt', 'w') :: test-write
    open-append :: def t():\\n    open('out.txt', mode='a') :: test-write
    open-plus :: def t():\\n    open('out.txt', 'r+') :: test-write
    open-variable-mode :: def t(mode):\\n    open('out.txt', mode) :: test-write
    write-text :: def t(path):\\n    path.write_text('x') :: test-write
    write-bytes :: def t(path):\\n    path.write_bytes(b'x') :: test-write
    mkdir :: def t(path):\\n    path.mkdir() :: test-write
    remove :: import os\\n\\n\\ndef t():\\n    os.remove('x') :: test-write
    unlink :: def t(path):\\n    path.unlink() :: test-write
    rename :: import os\\n\\n\\ndef t():\\n    os.rename('a', 'b') :: test-write
    """)


@pytest.mark.parametrize(("source", "rule"), TEST_MODULE_FORBIDDEN)
def test_test_module_forbidden_use_is_a_finding(guard, tmp_path, source, rule):
    assert ("test_skill.py", rule) in _test_module(guard, tmp_path, source)


def test_test_module_may_read_files(guard, tmp_path):
    source = """
    def t(path):
        with open(path) as a, open(path, "r") as b, open(path, "rb") as c:
            return a.read(), b.read(), c.read(), path.read_text(), path.read_bytes()
    """
    assert _test_module(guard, tmp_path, source) == []


TEST_MODULE_DYNAMIC_AND_FILES = _table(r"""
    dunder-import :: def t():\n    return __import__('socket') :: test-import
    importlib-import-module :: import importlib\n\n\ndef t():\n    importlib.import_module('socket') :: test-import
    eval :: def t():\n    return eval('1') :: test-import
    exec :: def t():\n    exec('x = 1') :: test-import
    compile :: def t():\n    return compile('1', 'x', 'eval') :: test-import
    io-fileio-write :: import io\n\n\ndef t():\n    io.FileIO('out.txt', 'w') :: test-write
    fileio-write :: from io import FileIO\n\n\ndef t():\n    FileIO('out.txt', mode='w') :: test-write
    os-fdopen-append :: import os\n\n\ndef t(fd):\n    os.fdopen(fd, 'a') :: test-write
    fdopen-variable-mode :: import os\n\n\ndef t(fd, mode):\n    os.fdopen(fd, mode) :: test-write
    """)


@pytest.mark.parametrize(("source", "rule"), TEST_MODULE_DYNAMIC_AND_FILES)
def test_test_module_dynamic_import_or_file_constructor_is_a_finding(
    guard, tmp_path, source, rule
):
    assert ("test_skill.py", rule) in _test_module(guard, tmp_path, source)


TEST_MODULE_OPEN_WRITES = _table(r"""
    io-open-write :: import io\n\n\ndef t():\n    io.open('out.txt', 'w')
    codecs-open-append :: import codecs\n\n\ndef t():\n    codecs.open('out.txt', 'a')
    """)


@pytest.mark.parametrize("source", TEST_MODULE_OPEN_WRITES)
def test_test_module_write_through_io_or_codecs_open_is_a_finding(
    guard, tmp_path, source
):
    assert ("test_skill.py", "test-write") in _test_module(guard, tmp_path, source)


def test_test_module_reading_through_fileio_has_no_finding(guard, tmp_path):
    source = "import io\n\n\ndef t(path):\n    return io.FileIO(path).read()\n"
    assert _test_module(guard, tmp_path, source) == []


TEST_MODULE_FROM_IMPORTS = _table(r"""
    import-module-by-name :: from importlib import import_module\n\n\ndef t():\n    import_module('json') :: test-import
    import-module-by-alias :: from importlib import import_module as load\n\n\ndef t():\n    load('json') :: test-import
    eval-by-alias :: from builtins import eval as run\n\n\ndef t():\n    return run('1') :: test-import
    io-open-by-name :: from io import open :: test-write
    codecs-open-by-alias :: from codecs import open as reader :: test-write
    os-open-by-alias :: from os import open as raw_open :: test-write
    io-fileio-by-alias :: from io import FileIO as raw :: test-write
    fdopen-by-alias :: from os import fdopen as wrap\n\n\ndef t(fd):\n    wrap(fd, 'w') :: test-write
    """)


@pytest.mark.parametrize(("source", "rule"), TEST_MODULE_FROM_IMPORTS)
def test_test_module_name_imported_from_a_module_is_checked_under_any_alias(
    guard, tmp_path, source, rule
):
    assert ("test_skill.py", rule) in _test_module(guard, tmp_path, source)


def test_test_module_other_names_imported_from_io_or_os_have_no_finding(
    guard, tmp_path
):
    source = "from io import StringIO\nfrom os import path\n\n\ndef t():\n    return StringIO(path.sep).read()\n"
    assert _test_module(guard, tmp_path, source) == []


# The command line ------------------------------------------------------------


def test_cli_exits_zero_on_the_fixture_bundle_and_one_on_a_finding(tmp_path):
    script = str(ROOT / "scripts" / "determinism_guard.py")
    fixture = str(FIXTURE_REPO / "skills" / BUNDLE_ID)
    clean = subprocess.run([sys.executable, script, fixture], capture_output=True)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    bundle = _bundle(tmp_path)
    _write(bundle / "skill.py", "import os\n")
    dirty = subprocess.run(
        [sys.executable, script, "--static", str(bundle)],
        capture_output=True,
        text=True,
    )
    assert dirty.returncode == 1
    assert "skill.py:1: import: os" in dirty.stdout


# The dynamic guard -----------------------------------------------------------

PROBE = """
from typing import Any, Dict

from skill_assay.core.base_skill import BaseSkill


class ProbeSkill(BaseSkill):
    @property
    def manifest(self) -> Dict[str, Any]:
        return {{"name": "sample/word_tally"}}

    def execute(self, params: Dict[str, Any]) -> Any:
{body}
"""


def _probe_bundle(tmp_path: Path, body: str, params: list[dict]) -> Path:
    """A bundle whose execute() runs ``body`` and whose fixtures are ``params``."""
    bundle = _bundle(tmp_path)
    body = textwrap.indent(textwrap.dedent(body).strip(), " " * 8)
    (bundle / "skill.py").write_text(PROBE.format(body=body), encoding="utf-8")
    for path in (bundle / "fixtures").glob("*_params.json"):
        path.unlink()
    for index, value in enumerate(params):
        target = bundle / "fixtures" / f"case{index}_params.json"
        target.write_text(json.dumps(value), encoding="utf-8")
    return bundle


def _rules(findings) -> list[str]:
    return [item.rule for item in findings]


def test_fixture_bundle_passes_the_dynamic_guard(guard):
    assert guard.dynamic_check(FIXTURE_REPO / "skills" / BUNDLE_ID) == []


def test_child_runs_under_the_given_hash_seed(guard, tmp_path):
    bundle = _probe_bundle(tmp_path, 'return {"hash": hash("a")}', [{}])

    def child_hash(seed):
        proc = guard.run_child(bundle, seed)
        assert proc.returncode == 0, proc.stderr
        [result] = json.loads(proc.stdout)["results"]
        return json.loads(result["output"])["hash"]

    reference = subprocess.run(
        [sys.executable, "-c", "print(hash('a'))"],
        env={"PYTHONHASHSEED": "0", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=True,
    )
    first = child_hash("0")
    assert first == child_hash("0") == int(reference.stdout)
    assert first != child_hash("4242")


def test_child_environment_keeps_the_hash_seed_and_nothing_else(guard):
    env = guard.child_environment("4242")
    assert set(env) == {
        "PYTHONHASHSEED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPATH",
        "PATH",
    }
    assert env["PYTHONHASHSEED"] == "4242" and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONPATH"] == str(ROOT)
    assert guard.CHILD_FLAGS == ("-B", "-s")


def test_set_iteration_is_caught_across_hash_seeds(guard, tmp_path):
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi"
    body = f"return {{'order': list(set('{words} omicron pi'.split()))}}"
    found = guard.dynamic_check(_probe_bundle(tmp_path, body, [{}]))
    assert _rules(found) == ["seed-divergence"]


def _audit(guard, tmp_path, body, preload):
    bundle = _probe_bundle(tmp_path, body + "\nreturn {}", [{}])
    found = guard.dynamic_check(bundle, preload=(preload,))
    assert _rules(found) == ["audit"]
    return found[0].detail


def test_socket_attempt_is_caught_by_the_audit_hook(guard, tmp_path):
    body = "import socket\n\ntry:\n    socket.socket()\nexcept Exception:\n    pass"
    assert _audit(guard, tmp_path, body, "socket").startswith("socket.")


def test_process_start_is_caught_by_the_audit_hook(guard, tmp_path):
    body = "import subprocess\n\nsubprocess.run(['true'], check=False)"
    assert _audit(guard, tmp_path, body, "subprocess").startswith("subprocess.Popen")


def test_file_write_is_caught_by_the_audit_hook(guard, tmp_path):
    target = tmp_path / "written.txt"
    body = "with open(params['path'], 'w') as handle:\n    handle.write('x')\nreturn {}"
    found = guard.dynamic_check(_probe_bundle(tmp_path, body, [{"path": str(target)}]))
    assert _rules(found) == ["audit"] and found[0].detail.startswith("open")
    assert not target.exists()


def test_read_outside_the_import_paths_is_caught_by_the_audit_hook(guard, tmp_path):
    # A path just under the file-system root is outside every import path of the
    # child, wherever pytest puts tmp_path (a temporary folder inside the
    # repository is itself on the child's import path). The hook stops the open
    # before the file is looked up, so the path need not exist.
    outside = Path(ROOT.anchor) / "determinism-guard-outside" / "outside.txt"
    body = (
        "with open(params['path'], 'r') as handle:\n    return {'text': handle.read()}"
    )
    found = guard.dynamic_check(_probe_bundle(tmp_path, body, [{"path": str(outside)}]))
    assert _rules(found) == ["audit"]
    assert found[0].detail == "open outside the import paths"


def test_import_outside_the_allow_list_is_caught_by_the_audit_hook(guard, tmp_path):
    body = "import colorsys\n\nreturn {'value': colorsys.rgb_to_hsv(0, 0, 0)}"
    found = guard.dynamic_check(_probe_bundle(tmp_path, body, [{}]))
    assert _rules(found) == ["audit"] and found[0].detail == "import colorsys"


def test_import_in_the_category_package_is_caught_by_the_audit_hook(guard, tmp_path):
    bundle = _probe_bundle(tmp_path, "return {}", [{}])
    (bundle.parent / "__init__.py").write_text("import colorsys\n", encoding="utf-8")
    found = guard.dynamic_check(bundle)
    assert _rules(found) == ["audit"] and found[0].detail == "import colorsys"


def _problem(guard, tmp_path, body, params):
    return _rules(guard.dynamic_check(_probe_bundle(tmp_path, body, params)))


def test_bundle_without_params_fixture_is_a_finding(guard, tmp_path):
    assert _problem(guard, tmp_path, "return {}", []) == ["no-fixtures"]


def test_fixture_that_raises_is_a_finding(guard, tmp_path):
    assert _problem(guard, tmp_path, "raise ValueError('x')", [{}]) == [
        "fixture-raised"
    ]


def test_output_that_is_not_json_is_a_finding(guard, tmp_path):
    assert _problem(guard, tmp_path, "return {'value': object()}", [{}]) == ["not-json"]


def test_child_output_that_is_not_one_json_object_is_a_finding(guard, tmp_path):
    bundle = _probe_bundle(tmp_path, "print('trace')\nreturn {}", [{}])
    found = guard.dynamic_check(bundle)
    assert _rules(found) == ["child-failed"] and found[0].detail.startswith("seed 0")


def test_all_fixtures_of_a_bundle_run_in_one_pair_of_children(
    guard, tmp_path, monkeypatch
):
    bundle = _probe_bundle(tmp_path, "return dict(params)", [{"a": 1}, {"b": 2}, {}])
    calls, original = [], guard.run_child
    monkeypatch.setattr(
        guard, "run_child", lambda b, seed, **k: calls.append(seed) or original(b, seed)
    )
    assert guard.dynamic_check(bundle) == []
    assert sorted(calls) == ["0", "4242"]


# Sockets disabled for the tests of standard bundles ------------------------


def test_sockets_disabled_blocks_socket_creation_and_connection(guard):
    import socket

    original = socket.socket, socket.create_connection
    with guard.sockets_disabled():
        with pytest.raises(OSError):
            socket.socket()
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", 9))
    assert (socket.socket, socket.create_connection) == original


def test_socket_stub_applies_only_to_bundles_outside_the_legacy_set(guard):
    skills = ROOT / "skills"
    assert not guard.is_standard_bundle_test(
        skills / "finance" / "period_close" / "test_skill.py", ROOT
    )
    assert guard.is_standard_bundle_test(
        skills / "sample" / "x" / "test_skill.py", ROOT
    )
    assert not guard.is_standard_bundle_test(ROOT / "tests" / "test_x.py", ROOT)
