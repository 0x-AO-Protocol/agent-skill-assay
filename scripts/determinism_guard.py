#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Determinism guards of the standard profile. They are pre-merge checks, not isolation.

static_findings() parses every module of a bundle (its AST; text is never searched)
and applies two rule sets. Runtime modules import only from a closed allow-list and
use no forbidden built-in or attribute. The test module test_skill.py may run a
hash-seed child, but imports no model SDK or network module and writes no file.

dynamic_check() runs every fixtures/*_params.json of a bundle in two child processes
with different PYTHONHASHSEED values, under an audit hook, and requires
byte-identical canonical JSON output.

Usage: python scripts/determinism_guard.py [--static | --dynamic] skills/<category>/<name>
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import importlib.util
import inspect
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The bundles shipped by v0.1.3. Frozen: the set never grows, and a member leaves
# it only through a recorded upgrade that brings the bundle under the profile.
LEGACY_BUNDLES = frozenset(
    "security/prompt_injection_firewall monitoring/kpi_gate wellness/mental_coach "
    "monitoring/business_diagnostic office/outreach_manager finance/period_close "
    "dev_tools/upgrade_gate".split()
)

# The closed import allow-list of runtime modules (published in CONTRIBUTING.md).
RUNTIME_STDLIB_ALLOWLIST = frozenset(
    "__future__ abc bisect collections collections.abc copy dataclasses decimal enum "
    "fractions functools hashlib heapq html itertools json math numbers operator re "
    "string textwrap typing unicodedata".split()
)
# datetime is not on the list: only "from datetime import date" and
# "from datetime import timedelta" are admitted, without an alias.
DATETIME_NAMES = frozenset({"date", "timedelta"})
FRAMEWORK_ALLOWLIST = frozenset({"skill_assay.core.base_skill"})
ALLOWED_MODULES = RUNTIME_STDLIB_ALLOWLIST | FRAMEWORK_ALLOWLIST

FORBIDDEN_BUILTINS = frozenset(
    "eval exec compile __import__ open input breakpoint vars globals locals "
    "help copyright credits license exit quit".split()
)
ATTRIBUTE_BUILTINS = frozenset({"getattr", "setattr", "delattr"})
# Dunder names and attributes are closed lists: anything not named here is a finding.
DUNDER_NAMES_READ = frozenset({"__name__", "__doc__"})
DUNDER_NAMES_ASSIGNED = frozenset({"__all__", "__slots__"})
DUNDER_ATTRIBUTES_READ = frozenset(
    "__class__ __doc__ __init__ __members__ __name__ __new__ __qualname__ "
    "__setattr__".split()
)
FRAME_ATTRIBUTES = frozenset(
    "f_back f_builtins f_code f_globals f_locals f_trace gi_code gi_frame cr_code "
    "cr_frame ag_code ag_frame tb_frame tb_next".split()
)
CLOCK_ATTRIBUTES = frozenset(
    "now today utcnow fromtimestamp utcfromtimestamp timestamp astimezone "
    "strptime strftime".split()
)
ENVIRONMENT_ATTRIBUTES = frozenset({"environ", "getenv", "credential"})
FORBIDDEN_ATTRIBUTES = {
    **dict.fromkeys(CLOCK_ATTRIBUTES, "clock-attribute"),
    **dict.fromkeys(ENVIRONMENT_ATTRIBUTES, "environment-attribute"),
    **dict.fromkeys(FRAME_ATTRIBUTES, "frame-attribute"),
}
# Members of allow-listed modules that turn a string into an attribute access or
# into evaluated code.
REFLECTION_MEMBERS = {
    "operator": frozenset({"attrgetter", "methodcaller"}),
    "string": frozenset({"Formatter"}),
    "typing": frozenset({"get_type_hints", "ForwardRef", "get_args"}),
    "functools": frozenset(
        {"singledispatch", "singledispatchmethod", "update_wrapper", "wraps"}
    ),
}
REFLECTION_NAMES = frozenset().union(*REFLECTION_MEMBERS.values())
FORMAT_METHODS = frozenset({"format", "format_map"})

# The test module: no model SDK or runtime, no network module, no file writes.
TEST_FORBIDDEN_IMPORTS = frozenset(
    "socket ssl http urllib urllib3 requests httpx aiohttp ftplib smtplib poplib "
    "imaplib telnetlib xmlrpc websocket websockets shutil tempfile openai anthropic "
    "google.genai google.generativeai vertexai ollama transformers llama_cpp mlx "
    "mlx_lm litellm cohere mistralai groq huggingface_hub vllm torch boto3 "
    "botocore langchain llama_index".split()
)
TEST_WRITE_ATTRIBUTES = frozenset(
    "write_text write_bytes mkdir makedirs remove unlink rename rmdir touch".split()
)
# The test module also uses no dynamic import or code, and opens files only for reading.
TEST_DYNAMIC_NAMES = frozenset(
    {"__import__", "eval", "exec", "compile", "import_module"}
)
TEST_FILE_CONSTRUCTORS = frozenset({"FileIO", "fdopen"})
# Importing one of these names from one of these modules counts as writing a file.
TEST_FILE_MODULES = frozenset({"io", "codecs", "os"})
TEST_FILE_NAMES = frozenset({"open", "FileIO"})
COMPILED_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll"})

SEEDS = ("0", "4242")
CHILD_FLAGS = ("-B", "-s")  # never -I or -E: both make Python ignore PYTHONHASHSEED
CHILD_TIMEOUT = 120
AUDIT_EXIT = 97
AUDIT_MARKER = "determinism-guard-audit: "


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}"


# Static guard ----------------------------------------------------------------


def _transit(name: str) -> bool:
    """True for a package on the way to an allowed module, such as skill_assay.core."""
    return any(allowed.startswith(name + ".") for allowed in ALLOWED_MODULES)


def _allowed_prefix(name: str) -> bool:
    parts = name.split(".")
    return any(".".join(parts[:i]) in ALLOWED_MODULES for i in range(1, len(parts)))


def _module_object(name: str):
    if name in ALLOWED_MODULES or _transit(name):
        return importlib.import_module(name)
    return None


def _submodule(module, name: str, attr: str) -> str | None:
    """The module name reached by ``module.attr``, or None when it is not a module."""
    value = getattr(module, attr, None)
    if inspect.ismodule(value):
        return value.__name__
    if value is None and hasattr(module, "__path__"):
        with contextlib.suppress(ImportError, ValueError):
            if importlib.util.find_spec(f"{name}.{attr}") is not None:
                return f"{name}.{attr}"
    return None


class _Bundle:
    def __init__(self, bundle_dir: Path):
        self.dir = bundle_dir
        self.package = f"skills.{bundle_dir.parent.name}.{bundle_dir.name}"
        self.trees: dict[str, ast.Module] = {}
        self.bindings: dict[str, dict] = {}

    def dotted(self, relative: str) -> str:
        parts = relative[: -len(".py")].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join([self.package, *parts])

    def own_file(self, dotted: str) -> str | None:
        if dotted != self.package and not dotted.startswith(self.package + "."):
            return None
        rel = dotted[len(self.package) + 1 :].replace(".", "/")
        for candidate in (f"{rel}.py", f"{rel}/__init__.py" if rel else "__init__.py"):
            if candidate in self.trees:
                return candidate
        return None


def _imports(node, dotted: str, is_package: bool, bundle: _Bundle):
    """Yield (bound name, target, problem) for one import node of a runtime module.

    A target is ("module", name), ("own", name) or None for a plain value.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.name
            if bundle.own_file(name) and alias.asname:
                yield alias.asname, ("own", name), None
            elif name not in ALLOWED_MODULES:
                yield None, None, (
                    "submodule" if _allowed_prefix(name) else "import",
                    name,
                )
            elif alias.asname:
                yield alias.asname, ("module", name), None
            else:
                yield name.split(".")[0], ("module", name.split(".")[0]), None
        return
    if node.level:
        base = dotted.split(".") if is_package else dotted.split(".")[:-1]
        base = base[: len(base) - node.level + 1]
        source = ".".join(base + ([node.module] if node.module else []))
        joined = ".".join(base)
        if joined != bundle.package and not joined.startswith(bundle.package + "."):
            yield None, None, (
                "relative-import",
                "." * node.level + (node.module or ""),
            )
            return
    else:
        source = node.module or ""
    for alias in node.names:
        bound = alias.asname or alias.name
        if alias.name == "*":
            yield None, None, ("star-import", source)
        elif bundle.own_file(source):
            target = f"{source}.{alias.name}"
            if bundle.own_file(target):
                yield bound, ("own", target), None
            else:
                yield bound, bundle.bindings.get(bundle.own_file(source), {}).get(
                    alias.name
                ), None
        elif source == "datetime":
            if alias.name in DATETIME_NAMES and not alias.asname:
                yield bound, None, None
            else:
                as_name = f" as {alias.asname}" if alias.asname else ""
                yield None, None, ("import", f"datetime.{alias.name}{as_name}")
        elif alias.name in REFLECTION_MEMBERS.get(source, ()):
            yield None, None, ("reflection-member", f"{source}.{alias.name}")
        elif source in ALLOWED_MODULES:
            reached = _submodule(_module_object(source), source, alias.name)
            if reached is None:
                yield bound, None, None
            elif reached in ALLOWED_MODULES:
                yield bound, ("module", reached), None
            else:
                yield None, None, ("submodule", f"{source}.{alias.name}")
        else:
            yield None, None, (
                "submodule" if _allowed_prefix(source) else "import",
                source,
            )


def _parents(tree: ast.AST) -> dict:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _walk_chain(node: ast.Name, target, parents, bundle: _Bundle):
    """Follow an attribute chain from an imported module; return (rule, detail) or None."""
    current = node
    while (
        isinstance(parents.get(current), ast.Attribute)
        and parents[current].value is current
    ):
        attribute = parents[current]
        attr = attribute.attr
        kind, name = target
        if kind == "own":
            bound = bundle.bindings.get(bundle.own_file(name), {}).get(attr)
            target = (
                ("own", f"{name}.{attr}")
                if bundle.own_file(f"{name}.{attr}")
                else bound
            )
        elif attr.startswith("_"):
            return "private-attribute", f"{name}.{attr}"
        elif attr in REFLECTION_MEMBERS.get(name, ()):
            return "reflection-member", f"{name}.{attr}"
        else:
            reached = _submodule(_module_object(name), name, attr)
            if reached is None:
                if name not in ALLOWED_MODULES:
                    return "submodule", f"{name}.{attr}"
                target = None
            elif reached in ALLOWED_MODULES or _transit(reached):
                target = ("module", reached)
            else:
                return "submodule", f"{name}.{attr}"
        if target is None:
            return None
        current = attribute
    if target[0] == "module":
        # The chain's value is a module object that is used as a value.
        if target[1] not in ALLOWED_MODULES:
            return "submodule", target[1]
        return "module-reference", node.id if current is node else target[1]
    if current is node:
        return "module-reference", node.id
    return None


def _dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def attribute_rule(name: str, read: bool = True) -> str | None:
    """The rule that reading (or, with ``read=False``, assigning) ``name`` breaks."""
    if _dunder(name):
        allowed = read and name in DUNDER_ATTRIBUTES_READ
        return None if allowed else "dunder-attribute"
    return FORBIDDEN_ATTRIBUTES.get(name)


def _spec_is_allowed(spec) -> bool:
    """A literal format spec whose only "%" is its last character (the percent type)."""
    return isinstance(spec, str) and "%" not in spec[:-1]


def _literal_spec(spec: ast.JoinedStr) -> str | None:
    """The text of an f-string format spec made of literal parts only, else None."""
    if all(isinstance(part, ast.Constant) for part in spec.values):
        return "".join(part.value for part in spec.values)
    return None


def _literal_attribute_call(node: ast.Name, parents, bindings) -> bool:
    """True for getattr/setattr/delattr called with an allowed literal name on a value."""
    call = parents.get(node)
    if not (isinstance(call, ast.Call) and call.func is node and len(call.args) >= 2):
        return False
    target, name = call.args[0], call.args[1]
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return False
    if name.value.startswith("_") or name.value in FORMAT_METHODS:
        return False
    if name.value in FORBIDDEN_BUILTINS or name.value in REFLECTION_NAMES:
        return False
    if attribute_rule(name.value) is not None:
        return False
    return not (isinstance(target, ast.Name) and bindings.get(target.id))


def _type_problem(node: ast.Name, parents):
    """``type`` is allowed only as a call with exactly one positional argument."""
    parent = parents.get(node)
    if isinstance(parent, ast.ClassDef) and any(base is node for base in parent.bases):
        return "class-creation", "type base"
    if (
        isinstance(parent, ast.Call)
        and parent.func is node
        and len(parent.args) == 1
        and not isinstance(parent.args[0], ast.Starred)
        and not parent.keywords
    ):
        return None
    return "class-creation", "type"


def _format_builtin_problem(node: ast.Name, parents):
    """``format`` is allowed only as a call whose spec is absent or an allowed literal."""
    call = parents.get(node)
    if not (isinstance(call, ast.Call) and call.func is node):
        return "format-spec", "format used as a value"
    if call.keywords or any(isinstance(arg, ast.Starred) for arg in call.args):
        return "format-spec", "format()"
    if len(call.args) < 2:
        return None
    spec = call.args[1]
    if isinstance(spec, ast.Constant) and _spec_is_allowed(spec.value):
        return None
    return "format-spec", "format()"


def _name_problem(node: ast.Name, parents, bindings, bundle: _Bundle):
    name, load = node.id, isinstance(node.ctx, ast.Load)
    if name in FORBIDDEN_BUILTINS:
        return "builtin", name
    if name in ATTRIBUTE_BUILTINS:
        if _literal_attribute_call(node, parents, bindings):
            return None
        return "dynamic-attribute", name
    if _dunder(name):
        store = isinstance(node.ctx, ast.Store)
        allowed = DUNDER_NAMES_ASSIGNED if store else DUNDER_NAMES_READ
        return None if name in allowed else ("dunder-name", name)
    if name == "type" and load:
        return _type_problem(node, parents)
    if name == "format" and load:
        return _format_builtin_problem(node, parents)
    if load and bindings.get(name):
        return _walk_chain(node, bindings[name], parents, bundle)
    return None


def _class_pattern_problems(node: ast.MatchClass):
    """A positional sub-pattern reads the attribute ``__match_args__`` names."""
    if node.patterns:
        yield "class-pattern", "positional sub-pattern"
    for attr in node.kwd_attrs:
        rule = attribute_rule(attr)
        if rule:
            yield rule, attr
        elif attr in FORMAT_METHODS:
            yield "format-call", attr


def _runtime_findings(
    relative: str, tree, bundle: _Bundle, dotted: str, is_package: bool, bindings
) -> list[Finding]:
    found: list[Finding] = []
    parents = _parents(tree)

    def add(node, rule, detail):
        found.append(Finding(relative, node.lineno, rule, detail))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for _, _, problem in _imports(node, dotted, is_package, bundle):
                if problem:
                    add(node, *problem)
        elif isinstance(node, ast.Attribute):
            read = isinstance(node.ctx, ast.Load)
            rule = attribute_rule(node.attr, read)
            if rule:
                add(node, rule, node.attr)
            elif read and node.attr in FORMAT_METHODS:
                add(node, "format-call", node.attr)
        elif isinstance(node, ast.FormattedValue) and node.format_spec is not None:
            spec = _literal_spec(node.format_spec)
            if not _spec_is_allowed(spec):
                add(node, "format-spec", spec if spec is not None else "nested field")
        elif isinstance(node, ast.MatchClass):
            for problem in _class_pattern_problems(node):
                add(node, *problem)
        elif isinstance(node, ast.ClassDef):
            if any(keyword.arg in ("metaclass", None) for keyword in node.keywords):
                add(node, "class-creation", "metaclass")
        elif isinstance(node, ast.Name):
            problem = _name_problem(node, parents, bindings, bundle)
            if problem:
                add(node, *problem)
    return found


def _module_name(node) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        return []
    return [node.module or ""] + [f"{node.module}.{alias.name}" for alias in node.names]


def _write_mode(call: ast.Call, position: int) -> bool:
    """True unless the call's mode argument is absent or a literal read mode."""
    mode = next((kw.value for kw in call.keywords if kw.arg == "mode"), None)
    if mode is None and len(call.args) > position:
        mode = call.args[position]
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(char in mode.value for char in "wax+")
    return True


def _test_findings(relative: str, tree) -> list[Finding]:
    found: list[Finding] = []
    modules = {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    # A name bound by a from-import is checked as the name it imports, under any alias.
    imported = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in _module_name(node):
                parts = name.split(".")
                prefixes = {".".join(parts[:i]) for i in range(1, len(parts) + 1)}
                if prefixes & TEST_FORBIDDEN_IMPORTS:
                    found.append(Finding(relative, node.lineno, "test-import", name))
                    break
            if isinstance(node, ast.ImportFrom) and node.module in TEST_FILE_MODULES:
                for alias in node.names:
                    if alias.name in TEST_FILE_NAMES:
                        what = f"{node.module}.{alias.name}"
                        found.append(Finding(relative, node.lineno, "test-write", what))
        elif (
            isinstance(node, ast.Name)
            and imported.get(node.id, node.id) in TEST_DYNAMIC_NAMES
        ):
            name = imported.get(node.id, node.id)
            found.append(Finding(relative, node.lineno, "test-import", name))
        elif isinstance(node, ast.Attribute) and node.attr == "import_module":
            found.append(Finding(relative, node.lineno, "test-import", node.attr))
        elif isinstance(node, ast.Attribute) and node.attr in TEST_WRITE_ATTRIBUTES:
            found.append(Finding(relative, node.lineno, "test-write", node.attr))
        elif isinstance(node, ast.Call):
            func, writes = node.func, False
            if isinstance(func, ast.Name):
                what = imported.get(func.id, func.id)
            else:
                what = getattr(func, "attr", None)
            if isinstance(func, ast.Name) and what == "open":
                writes = _write_mode(node, 1)
            elif isinstance(func, ast.Attribute) and what == "open":
                receiver = func.value.id if isinstance(func.value, ast.Name) else None
                writes = receiver == "os" or _write_mode(node, int(receiver in modules))
            elif what in TEST_FILE_CONSTRUCTORS:
                writes = _write_mode(node, 1)
            if writes:
                found.append(Finding(relative, node.lineno, "test-write", what))
    return found


def _manifest_findings(bundle_dir: Path) -> list[Finding]:
    import yaml

    try:
        manifest = yaml.safe_load((bundle_dir / "manifest.yaml").read_text("utf-8"))
    except Exception as exc:  # missing, unreadable or invalid: fail closed
        return [Finding("manifest.yaml", 0, "manifest", type(exc).__name__)]
    if not isinstance(manifest, dict):
        return [Finding("manifest.yaml", 0, "manifest", "not a mapping")]
    rules = {"env_vars": "manifest-env-vars", "requirements": "manifest-requirements"}
    return [Finding("manifest.yaml", 0, rules[f], f) for f in rules if manifest.get(f)]


def _package_modules(bundle_dir: Path) -> list[tuple[str, Path, str]]:
    """The package modules that importing the bundle runs first.

    They are skills/__init__.py and skills/<category>/__init__.py, each given as
    (path relative to the repository, path, dotted module name).
    """
    category, skills = bundle_dir.parent, bundle_dir.parent.parent
    if skills.name != "skills":
        return []
    return [
        (path.relative_to(skills.parent).as_posix(), path, dotted)
        for path, dotted in (
            (skills / "__init__.py", "skills"),
            (category / "__init__.py", f"skills.{category.name}"),
        )
        if path.is_file()
    ]


def _package_findings(bundle: _Bundle) -> list[Finding]:
    """Runtime findings of the package modules, reported by repository path."""
    found: list[Finding] = []
    for relative, path, dotted in _package_modules(bundle.dir):
        try:
            tree = ast.parse(path.read_text("utf-8"), relative)
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            line = getattr(exc, "lineno", 0) or 0
            found.append(Finding(relative, line, "syntax", type(exc).__name__))
            continue
        bindings = {
            bound: target
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for bound, target, _ in _imports(node, dotted, True, bundle)
            if bound and target
        }
        found.extend(_runtime_findings(relative, tree, bundle, dotted, True, bindings))
    return found


def static_findings(bundle_dir) -> list[Finding]:
    """Return every static finding of one bundle, sorted by file, line and rule.

    The package modules that importing the bundle runs first (skills/__init__.py
    and the category's __init__.py) are checked with the runtime rule set too.
    """
    bundle = _Bundle(Path(bundle_dir).resolve())
    found = _manifest_findings(bundle.dir)
    for path in sorted(bundle.dir.rglob("*")):
        relative = path.relative_to(bundle.dir).as_posix()
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if path.suffix in COMPILED_SUFFIXES:
            found.append(Finding(relative, 0, "compiled-module", path.suffix))
        elif path.suffix == ".py":
            try:
                bundle.trees[relative] = ast.parse(path.read_text("utf-8"), relative)
            except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
                line = getattr(exc, "lineno", 0) or 0
                found.append(Finding(relative, line, "syntax", type(exc).__name__))
    runtime = [relative for relative in bundle.trees if relative != "test_skill.py"]
    for _ in range(2):  # twice, so that re-exports between own modules resolve
        for relative in runtime:
            dotted, is_package = bundle.dotted(relative), relative.endswith(
                "__init__.py"
            )
            bundle.bindings[relative] = {
                bound: target
                for node in ast.walk(bundle.trees[relative])
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for bound, target, _ in _imports(node, dotted, is_package, bundle)
                if bound and target
            }
    for relative in runtime:
        found.extend(
            _runtime_findings(
                relative,
                bundle.trees[relative],
                bundle,
                bundle.dotted(relative),
                relative.endswith("__init__.py"),
                bundle.bindings[relative],
            )
        )
    found.extend(_package_findings(bundle))
    if "test_skill.py" in bundle.trees:
        found.extend(_test_findings("test_skill.py", bundle.trees["test_skill.py"]))
    return sorted(set(found))


# Dynamic guard ---------------------------------------------------------------

CHILD_SOURCE = r"""
import importlib, json, os, sys

config = json.loads(sys.argv[1])
sys.path[:] = [config["package_root"]] + [entry for entry in sys.path if entry]
for name in config["preload"]:
    importlib.import_module(name)
package, allowed = config["package"], set(config["allowed"])
category = package.rsplit(".", 1)[0]
packages = {"skills", category}
fixtures = []
for path in config["fixtures"]:
    with open(path, "r", encoding="utf-8") as handle:
        fixtures.append((os.path.basename(path), json.load(handle)))
roots = [os.path.realpath(entry) for entry in sys.path if os.path.isdir(entry)]
WRITE = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
EVENTS = ("socket.", "ctypes.", "os.exec", "os.spawn", "os.posix_spawn", "os.fork")

def stop(detail):
    sys.stderr.write("determinism-guard-audit: " + detail + "\n")
    sys.stderr.flush()
    os._exit(97)

def inside(path):
    real = os.path.realpath(path)
    return any(real == root or real.startswith(root + os.sep) for root in roots)

def hook(event, args):
    if event in ("subprocess.Popen", "os.system") or event.startswith(EVENTS):
        stop(event)
    elif event == "open":
        path, mode, flags = args
        if isinstance(mode, str):
            writing = any(char in mode for char in "wax+")
        else:
            writing = bool((flags or 0) & WRITE)
        if writing:
            stop("open for writing")
        try:
            text = os.fsdecode(path)
        except TypeError:
            stop("open of a file descriptor")
        if not inside(text):
            stop("open outside the import paths")
    elif event == "import":
        name = args[0]
        own = name == package or name.startswith(package + ".")
        if name not in allowed and name not in packages and not own:
            stop("import " + name)

sys.addaudithook(hook)  # before any skills package runs
importlib.import_module(category)
module = importlib.import_module(package + ".skill")
from skill_assay.core.base_skill import BaseSkill
classes = [value for value in vars(module).values() if isinstance(value, type)
           and issubclass(value, BaseSkill) and value is not BaseSkill
           and value.__module__ == module.__name__]
if len(classes) != 1:
    sys.stderr.write("expected exactly one BaseSkill subclass in skill.py\n")
    sys.exit(3)
skill = classes[0]()
results = []
for name, params in fixtures:
    try:
        output = skill.execute(params)
    except Exception as exc:
        results.append({"fixture": name, "raised": type(exc).__name__})
        continue
    try:
        text = json.dumps(output, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        results.append({"fixture": name, "not_json": type(exc).__name__})
        continue
    results.append({"fixture": name, "output": text})
sys.stdout.buffer.write(json.dumps({"results": results}, ensure_ascii=False).encode("utf-8"))
"""


def child_environment(seed: str) -> dict[str, str]:
    """The child's whole environment: no PYTHON* variable beyond these three."""
    return {
        "PYTHONHASHSEED": seed,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT),
        "PATH": os.environ.get("PATH", os.defpath),
    }


def _params_fixtures(bundle: Path) -> list[Path]:
    return sorted((bundle / "fixtures").glob("*_params.json"))


def run_child(bundle_dir, seed: str, *, preload=(), timeout: int = CHILD_TIMEOUT):
    """Run every params fixture of a bundle once, in one child, under ``seed``."""
    bundle = Path(bundle_dir).resolve()
    config = {
        "package_root": str(bundle.parents[2]),
        "package": f"skills.{bundle.parent.name}.{bundle.name}",
        "fixtures": [str(path) for path in _params_fixtures(bundle)],
        "preload": sorted(ALLOWED_MODULES) + ["datetime", *preload],
        "allowed": sorted(ALLOWED_MODULES),
    }
    return subprocess.run(
        [sys.executable, *CHILD_FLAGS, "-c", CHILD_SOURCE, json.dumps(config)],
        cwd=config["package_root"],
        env=child_environment(seed),
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def dynamic_check(bundle_dir, *, preload=()) -> list[Finding]:
    """Run the fixtures under both seeds; return the findings (empty when clean)."""
    bundle = Path(bundle_dir).resolve()
    if bundle.parent.parent.name != "skills":
        return [Finding(str(bundle), 0, "layout", "not at skills/<category>/<name>")]
    if not _params_fixtures(bundle):
        return [Finding("fixtures", 0, "no-fixtures", "no fixtures/*_params.json")]
    outputs, results = [], []
    for seed in SEEDS:
        try:
            proc = run_child(bundle, seed, preload=preload)
        except subprocess.TimeoutExpired:
            return [Finding("fixtures", 0, "timeout", f"seed {seed}")]
        error = proc.stderr.decode("utf-8", "replace")
        if proc.returncode == AUDIT_EXIT and AUDIT_MARKER in error:
            detail = error.split(AUDIT_MARKER, 1)[1].splitlines()[0]
            return [Finding("fixtures", 0, "audit", detail)]
        if proc.returncode != 0:
            last = (error.strip().splitlines() or ["no output"])[-1]
            return [Finding("fixtures", 0, "child-failed", f"seed {seed}: {last}")]
        try:
            results.append(list(json.loads(proc.stdout)["results"]))
        except (ValueError, KeyError, TypeError):
            detail = f"seed {seed}: the output is not one JSON object"
            return [Finding("fixtures", 0, "child-failed", detail)]
        outputs.append(proc.stdout)
    found = []
    for item in results[0]:
        for key, rule in (("raised", "fixture-raised"), ("not_json", "not-json")):
            if key in item:
                found.append(Finding(f"fixtures/{item['fixture']}", 0, rule, item[key]))
    if outputs[0] != outputs[1]:
        names = [a["fixture"] for a, b in zip(*results) if a != b] or ["output"]
        found.append(Finding("fixtures", 0, "seed-divergence", ", ".join(names)))
    return sorted(found)


# Sockets disabled for the tests of standard bundles (used by conftest.py) -------

_BLOCKED = "socket use is disabled in the tests of standard-profile bundles"


class _BlockedSocket(socket.socket):
    def __new__(cls, *args, **kwargs):
        raise OSError(_BLOCKED)


def _blocked_connection(*args, **kwargs):
    raise OSError(_BLOCKED)


@contextlib.contextmanager
def sockets_disabled():
    saved = socket.socket, socket.create_connection
    socket.socket, socket.create_connection = _BlockedSocket, _blocked_connection
    try:
        yield
    finally:
        socket.socket, socket.create_connection = saved


def is_standard_bundle_test(path, repo_root=ROOT) -> bool:
    """True for a test file of a bundle under skills/ that is outside the legacy set."""
    skills = Path(repo_root).resolve() / "skills"
    try:
        parts = Path(path).resolve().relative_to(skills).parts
    except ValueError:
        return False
    return len(parts) >= 3 and "/".join(parts[:2]) not in LEGACY_BUNDLES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundles", nargs="+", metavar="BUNDLE_DIR")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--static", action="store_true", help="static guard only")
    mode.add_argument("--dynamic", action="store_true", help="dynamic guard only")
    args = parser.parse_args(argv)
    failed = False
    for bundle in args.bundles:
        if not Path(bundle).is_dir():
            parser.error(f"{bundle} is not a directory")
        found = [] if args.dynamic else static_findings(bundle)
        found += [] if args.static else dynamic_check(bundle)
        for finding in found:
            print(finding if len(args.bundles) == 1 else f"{bundle}: {finding}")
        failed = failed or bool(found)
    if not failed:
        print("determinism-guard: no findings")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
