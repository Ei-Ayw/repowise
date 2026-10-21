"""TypeScript plan types after a guard: ``if (!x) return;`` narrows ``x`` for the
lifted lines, so the helper parameter takes the narrowed type, or a note when
the code does not write it (openclaw ``pdf-tool.model-config.ts``: a typed
parameter whose narrowing came from a guard did not compile)."""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.dataflow import Extraction, analyze_file
from repowise.core.analysis.health.dataflow.dialects.ts_js import union_arms
from repowise.core.analysis.health.refactoring import render
from repowise.core.analysis.health.refactoring.extract_method import _narrow
from repowise.core.analysis.health.refactoring.narrowing import narrowed

_SRC = """
function run(reply?: Reply, opts: Opts | null, kind: unknown, n: number, late?: string) {
  if (!reply) {
    return;
  }
  if (opts === null) throw new Error("no opts");
  if (typeof kind !== "string") return;
  const model = pick(n);
  if (!model || n < 0) return;
  if (!late) return;
  late = other(n);
  send(reply, opts, kind, model, late);
  log(reply);
}
"""


def _analysis():
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(_SRC)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")
    lines = src.splitlines()
    span = next(i for i, ln in enumerate(lines, 1) if ln.strip().startswith("send("))
    return res.functions[0], span, lines


def test_a_guard_narrows_the_declared_type_or_says_the_type_is_narrower():
    fn, span, lines = _analysis()
    params = ("reply", "opts", "kind", "model", "late")
    types = {"reply": "Reply | undefined", "opts": "Opts | null", "kind": "unknown", "late": "string | undefined"}
    notes = _narrow(fn, Extraction(span, span + 1, params, (), 0, 0), "typescript", types)
    assert types["reply"] == "Reply" and types["opts"] == "Opts"
    # ``typeof`` narrows to a type the code does not write; ``model`` has none.
    guard = next(i for i, ln in enumerate(lines, 1) if "typeof kind" in ln)
    assert "kind" not in types and notes["kind"] == guard
    assert notes["model"] == next(i for i, ln in enumerate(lines, 1) if "!model" in ln)
    # ``late`` is written after its guard: the declared type stands.
    assert types["late"] == "string | undefined" and "late" not in notes


def test_javascript_and_python_spans_are_left_alone():
    fn, span, _ = _analysis()
    types = {"reply": "Reply | undefined"}
    assert _narrow(fn, Extraction(span, span, ("reply",), (), 0, 0), "javascript", types) == {}
    assert types == {"reply": "Reply | undefined"}


def test_union_arms_split_outside_brackets():
    assert union_arms("Map<string, A | B> | undefined") == ["Map<string, A | B>", "undefined"]
    assert union_arms("(a: A | B) => void") == ["(a: A | B) => void"]
    assert narrowed("A | null | undefined", frozenset({"undefined", "null", "false"})) == "A"
    assert narrowed("A", frozenset({"undefined"})) == "A"
    assert narrowed("undefined", frozenset({"undefined"})) is None


def test_a_narrowed_parameter_without_a_type_gets_its_own_note():
    texts = render.render(
        render.HelperShape(
            language="typescript",
            name="send",
            kind="function",
            is_async=False,
            params=(render.Slot("reply"), render.Slot("model", narrowed_at=7)),
        )
    )
    assert texts.notes == (
        "Write the types of reply: the code declares none for them.",
        "Type model as the guard on line 7 narrows it: a wider type undoes that narrowing.",
    )


def test_a_name_tested_twice_in_one_guard_loses_both_arms():
    from repowise.core.analysis.health.refactoring.narrowing import _merged

    assert _merged({"a": frozenset({"null"})}, {"a": frozenset({"undefined"})}) == {
        "a": frozenset({"null", "undefined"})
    }
    assert _merged({"a": frozenset({"null"})}, {"a": None, "b": None}) == {"a": None, "b": None}



_LOOP_SRC = """
function collect(calls: Call[], seen: Set<string>, name: string) {
  for (const tc of calls) {
    if (tc.status !== "done" || !tc.result) continue;
    if (!seen.has(name)) continue;
    const result = tc.result;
    seen.add(result.id);
    seen.add(tc.name);
  }
}
"""


def test_a_guard_on_a_property_is_named_for_the_object_holding_it():
    # ``chat/source-citations.tsx``: ``!tc.result`` narrows the path, not ``tc``,
    # so a helper typed with any type a caller's ``tc`` has stops compiling.
    from repowise.core.analysis.health.complexity.languages import get_language_map
    from repowise.core.analysis.health.refactoring.extract_method import _def_lines
    from repowise.core.analysis.health.refactoring.narrowing import property_narrowings
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(_LOOP_SRC)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")
    fn = res.functions[0]
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines, 1) if "const result" in ln)
    guard = next(i for i, ln in enumerate(lines, 1) if "!tc.result" in ln)
    found = property_narrowings(
        fn.fn_node, get_language_map("typescript"), start, start + 2, {"seen", "tc"}, _def_lines(fn)
    )
    # ``seen.has`` is a callee, not a property the guard tests.
    assert found == {"tc": guard}


_PLANNED_SRC = """
function scan(calls: Call[], out: string[], strict: boolean) {
  for (const tc of calls) {
    if (!GUARD) continue;
    const result = tc.result;
    if (result.a) {
      out.push(result.a);
    } else if (result.b) {
      out.push(result.b);
    }
    if (result.c && strict) {
      out.push(result.c);
    }
    out.push(String(out.length));
    out.push(String(out.length + 1));
  }
  if (strict) {
    out.push("strict");
  }
  return out;
}
"""


@pytest.mark.parametrize(("guard", "planned"), [("tc.result", False), ("strict", True)])
def test_a_span_reading_a_property_a_guard_narrowed_is_refused(guard, planned):
    from dataclasses import dataclass

    from repowise.core.analysis.health.refactoring.extract_method import (
        PROPERTY_NARROWED,
        STAGE_REFUSED_DETAIL,
        ExtractMethodDetector,
    )
    from repowise.core.analysis.health.refactoring.models import RefactoringContext
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(_PLANNED_SRC).replace("GUARD", guard)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")

    @dataclass
    class _Finding:
        biomarker_type: str
        function_name: str
        line_start: int
        health_impact: float
        details: dict

    finding = _Finding("complex_method", "scan", res.functions[0].start_line, 2.0, {})
    ctx = RefactoringContext(
        file_path="m.ts", language="typescript", nloc=40, findings=[finding],
        function_analyses=res.functions,
    )
    plans = ExtractMethodDetector().detect(ctx)
    assert any("tc" in p.plan["params"] for p in plans) is planned
    assert (PROPERTY_NARROWED in finding.details.get(STAGE_REFUSED_DETAIL, ())) is not planned


_GUARDED_SRC = """
function f(calls: Call[], xs: number[], seen: Set<string>) {
  for (let tc of calls) {
    GUARD
    REBIND
    seen.add(BODY);
  }
}
"""


@pytest.mark.parametrize(
    ("guard", "body", "rebind", "refused"),
    [
        ("if (!tc.result) continue;", "tc.result.id", "", True),
        ("if (tc.result == null) continue;", "tc?.result.id", "", True),
        # The span never reads the narrowed path: nothing to lose.
        ("if (!tc.result) continue;", "tc.name", "", False),
        # A literal comparison may discriminate the whole union.
        ("if (tc.kind !== 'done') continue;", "tc.name", "", True),
        # An ordering test narrows nothing.
        ("if (xs.length > 0) continue;", "String(xs[0])", "", False),
        # A rebinding after the guard ends its narrowing.
        ("if (!tc.result) continue;", "tc.result.id", "tc = calls[0];", False),
    ],
)
def test_which_property_tests_narrow_a_span_input(guard, body, rebind, refused):
    from repowise.core.analysis.health.complexity.languages import get_language_map
    from repowise.core.analysis.health.refactoring.extract_method import _def_lines
    from repowise.core.analysis.health.refactoring.narrowing import property_narrowings
    from repowise.core.ingestion.parser import _get_language

    if _get_language("typescript") is None:
        pytest.skip("tree-sitter language pack missing for typescript")
    src = textwrap.dedent(_GUARDED_SRC).replace("GUARD", guard).replace("BODY", body)
    src = src.replace("REBIND", rebind)
    res = analyze_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if not res.functions:
        pytest.skip("tree-sitter language pack missing for typescript")
    fn = res.functions[0]
    line = next(i for i, ln in enumerate(src.splitlines(), 1) if "seen.add" in ln)
    found = property_narrowings(
        fn.fn_node, get_language_map("typescript"), line, line, {"tc", "xs", "seen"}, _def_lines(fn)
    )
    assert bool(found) is refused
