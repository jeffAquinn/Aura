"""Pillar 2, Mechanics (docs/mechanics.md, approved by Aura 2026-11-26): a Technical Specification
declares what a skill relies on, Aura refuses one that's missing or claims what the Security
Governor blocks, and the four places that list the sections never drift apart."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_developer import SPEC
from test_governor import env, offline_provenance  # noqa: F401 (fixtures)
from test_live import _reachable

from aura.developer import workspace as ws

ROOT = Path(__file__).resolve().parent.parent


def with_mechanics(block: str) -> str:
    return re.sub(r"## Mechanics\n.*?(?=## Acceptance tests)", "## Mechanics\n" + block + "\n", SPEC, flags=re.DOTALL)


def test_a_complete_mechanics_section_passes_and_is_read():
    assert ws.check_specification(SPEC) == []
    assert ws.mechanics(SPEC)["Files"].startswith("reads the PDF")


def test_a_specification_without_mechanics_is_refused():
    spec = re.sub(r"## Mechanics\n.*?(?=## Acceptance tests)", "", SPEC, flags=re.DOTALL)
    assert ws.check_specification(spec) == ["missing sections: Mechanics"]


def test_a_missing_line_is_named():
    spec = with_mechanics("- Network: none\n- Processes: none\n- Files: none, it only computes")
    assert ws.check_specification(spec) == ["Mechanics is missing: Native code, Dynamic code"]


def test_claiming_what_the_governor_blocks_is_refused_with_the_reason():
    spec = with_mechanics("- Network: fetches the PDF from a URL\n- Processes: none\n- Native code: none\n"
                          "- Dynamic code: eval of a formula\n- Files: /tmp")  # fmt: skip
    problems = ws.check_specification(spec)
    assert len(problems) == 2
    assert problems[0].startswith("Mechanics: Network must be 'none': skills never use the network")
    assert problems[1].startswith("Mechanics: Dynamic code must be 'none'")


def test_files_needs_an_answer_and_answers_can_be_worded_freely():
    assert ws.check_specification(with_mechanics(
        "- Network: none\n- Processes: none\n- Native code: none\n- Dynamic code: none\n- Files:")) == [
        "Mechanics: Files needs an answer: the vault, /tmp, or none, and why"]  # fmt: skip
    free = with_mechanics("Network: None.\n* processes: none (pure Python)\n- Native Code: none\n"
                          "- Dynamic code: none\n- Files: reads the CSV from the vault and\n  writes a PNG to /tmp")  # fmt: skip
    assert ws.check_specification(free) == []
    assert ws.declared_places(ws.mechanics(free)["Files"]) == {"vault", "tmp"}
    assert ws.declared_places("none: it only computes") == set()


def test_the_four_places_that_list_the_sections_agree():
    """Aura's rule: the server's check, Aura's prompt, the develop_skill tool and the app's template
    change together, or Aura could accept a specification the build then refuses."""
    swift = (ROOT / "app/Sources/AuraKit/Developer.swift").read_text()
    listed = lambda name: re.findall(r'"([^"]+)"', re.search(rf"static let {name} = \[(.*?)\]", swift).group(1))
    assert tuple(listed("sections")) == ws.SECTIONS
    assert tuple(listed("mechanics")) == ws.MECHANICS
    flat = lambda text: " ".join(text.replace("\\\n", " ").split())
    prompt = flat((ROOT / "src/aura/persona.py").read_text())
    tool = flat((ROOT / "src/aura/tools/workshop.py").read_text())
    for where, text in (("Aura's prompt", prompt), ("the develop_skill tool", tool)):
        positions = [text.find(s) for s in ws.SECTIONS]
        assert -1 not in positions and positions == sorted(positions), f"{where} doesn't list {ws.SECTIONS} in order"
        assert all(m in text for m in ws.MECHANICS), f"{where} doesn't name Mechanics' lines"


def test_the_fixed_none_lines_dont_count_towards_the_minimum_length():
    """Otherwise a blank template with its Mechanics lines would be long enough to send."""
    template = "\n".join(f"## {s}" if s != "Mechanics" else
                         "## Mechanics\n- Network: none\n- Processes: none\n- Native code: none\n- Dynamic code: none\n- Files:"
                         for s in ws.SECTIONS)  # fmt: skip
    assert len(template) > 150 and "characters" in ws.check_specification(template)[0]


# ── T2: the audit compares the Files line with what it sees ─────────────────────────────────


def skill_folder(tmp_path, code: str) -> Path:
    folder = tmp_path / "skills" / "csv_chart"
    folder.mkdir(parents=True)
    (folder / "skill.py").write_text(code)
    return folder


def files(answer: str) -> str:
    return with_mechanics(f"- Network: none\n- Processes: none\n- Native code: none\n- Dynamic code: none\n- Files: {answer}")


READ_VAULT = {"permissions": {"vault": "read", "network": False}}
NO_VAULT = {"permissions": {"vault": "none", "network": False}}


def test_declared_file_use_passes(tmp_path):
    from aura.governor.mechanics import compare
    from aura.governor.scan import scan_skill

    uses = []
    folder = skill_folder(tmp_path, 'import tempfile\nOUT = "/tmp/chart.png"\n')
    scanned, found = compare(files("reads the CSV from the vault; writes the chart to /tmp"), READ_VAULT, scan_skill(folder, uses), uses)  # fmt: skip
    assert [(f.severity, f.message) for f in found] == [("pass", "the static scan found no file use beyond Mechanics' Files line")]
    assert scanned == scan_skill(folder)


def test_undeclared_vault_and_tmp_use_are_yellow_and_plain(tmp_path):
    from aura.governor.mechanics import compare
    from aura.governor.scan import scan_skill

    uses = []
    folder = skill_folder(tmp_path, 'import tempfile\n')
    _, found = compare(files("none, it only computes"), READ_VAULT, scan_skill(folder, uses), uses)
    assert [(f.check, f.severity, f.message) for f in found] == [
        ("mechanics", "yellow", "skill.toml asks to read the vault; Mechanics' Files line doesn't declare it"),
        ("mechanics", "yellow", "the code uses /tmp; Mechanics' Files line doesn't declare it")]
    assert found[1].detail.startswith("csv_chart/skill.py:1")  # located like scan.py's findings


def test_an_outside_path_is_marked_unless_the_files_line_names_it(tmp_path):
    from aura.governor.mechanics import compare
    from aura.governor.scan import scan_skill

    uses = []
    folder = skill_folder(tmp_path, 'FONTS = "/usr/share/fonts"\n')
    scanned, _ = compare(files("none"), NO_VAULT, scan_skill(folder, uses), uses)
    outside = [f for f in scanned if f.severity == "yellow"]
    assert outside[0].message.endswith("; Mechanics' Files line doesn't declare it")
    scanned, found = compare(files("reads fonts from /usr/share/fonts"), NO_VAULT, scan_skill(folder, uses), uses)
    assert not [f for f in scanned if f.message.endswith("doesn't declare it")]
    assert [f.severity for f in found] == ["pass"]  # declared; the scan's own yellow still stands


def test_nothing_here_can_block_a_skill(tmp_path):
    from aura.governor import AuditReport
    from aura.governor.mechanics import compare
    from aura.governor.scan import scan_skill

    uses = []
    folder = skill_folder(tmp_path, 'import tempfile\nX = "/etc/hosts"\n')
    scanned, found = compare("## Goal\nno mechanics at all", READ_VAULT, scan_skill(folder, uses), uses)
    assert {f.severity for f in [*scanned, *found]} <= {"pass", "yellow"}
    assert AuditReport("csv_chart", findings=[*scanned, *found]).risk == "yellow"
    assert found[0].message == "the specification declares no Files line in Mechanics"


def test_the_skills_own_tests_using_tmp_dont_count(tmp_path):
    """Mechanics describes the skill at work; its tests may write fixtures to /tmp."""
    from aura.governor.mechanics import compare
    from aura.governor.scan import scan_skill

    uses = []
    folder = skill_folder(tmp_path, "def run(args):\n    return 'ok'\n")
    (folder / "test_skill.py").write_text('PDF = "/tmp/hello.pdf"\n')
    _, found = compare(files("none"), NO_VAULT, scan_skill(folder, uses), uses)
    assert [f.severity for f in found] == ["pass"]


# ── T4: live, the real local model drafts a specification ───────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _reachable(), reason="no local model")
def test_live_the_model_drafts_a_specification_that_passes_the_mechanics_check():
    """gemma4 writes a Technical Specification from a real request. The card is never approved, so
    nothing goes to Claude Code or Codex: only the drafted text is checked."""
    from aura.brain import ApprovalNeeded, Aura
    from aura.config import Settings

    aura = Aura(Settings.from_env())  # conftest points the vault and data at temp folders
    request = ("Build me a new skill with Claude Code called word_count. Input: path, a UTF-8 text file in the vault. "
               "Output: one line, '<lines> lines, <words> words, <characters> characters'. Standard library only. "
               "You have everything you need: write the Technical Specification and call develop_skill now.")  # fmt: skip
    cards = [e for e in aura.stream(request) if isinstance(e, ApprovalNeeded)]
    if not cards:  # it asked a question first
        cards = [e for e in aura.stream("Yes, that's all. Write the specification and call develop_skill now.")
                 if isinstance(e, ApprovalNeeded)]  # fmt: skip
    builds = [a for c in cards for a in c.actions if a.tool == "develop_skill"]
    assert builds, "the model didn't call develop_skill"
    spec = builds[0].args["specification"]
    print(spec)
    assert ws.check_specification(spec) == [], ws.check_specification(spec)
    assert "vault" in ws.declared_places(ws.mechanics(spec)["Files"])  # it reads a vault file, so it says so


def test_none_means_none_and_odd_labels_dont_crash():
    """From the security review: "none, except…" isn't none, and an unusual label is skipped."""
    sneaky = with_mechanics("- Network: none, except it fetches https://example.com\n- Processes: none\n"
                            "- Native code: none\n- Dynamic code: none\n- Files: none")  # fmt: skip
    assert ws.check_specification(sneaky)[0].startswith("Mechanics: Network must be 'none'")
    odd = with_mechanics("- Network: none\n- Proce\u017f\u017fes: none\n- Native code: none\n- Dynamic code: none\n"
                         "- Files: none")  # fmt: skip
    assert ws.check_specification(odd) == []  # read as Processes (Unicode case folding), not a crash


# ── after review ────────────────────────────────────────────────────────────────────────────


def test_a_build_interrupted_before_mechanics_can_still_be_retried(env, tmp_path):  # noqa: F811
    """Its specification was approved before Mechanics existed: retried as it was; the audit says so."""
    from test_developer import ENGINES, FakeContainer
    from test_reliability import build_record

    from aura.developer.workflow import Developer, DeveloperError
    from aura.secrets import Secrets
    from aura.state import StateStore

    settings, workshop, _, _ = env
    old_spec = re.sub(r"## Mechanics\n.*?(?=## Acceptance tests)", "", SPEC, flags=re.DOTALL)
    store = StateStore(tmp_path / "state.sqlite")
    store.put_record("build", "b-develo", "developing", build_record("developing") | {"specification": old_spec})
    d = Developer(settings, workshop, FakeContainer(error="Docker isn't running"), Secrets(), workshop.audit,
                  engines=ENGINES, state=store)  # fmt: skip
    assert d.retry("b-develo").specification == old_spec.strip()
    with pytest.raises(DeveloperError, match="missing sections: Mechanics"):  # a new build still needs it
        d.start("pdf_text", old_spec, "claude")


def test_a_wrapped_files_answer_isnt_cut_by_a_section_word():
    spec = files("reads the CSV from the vault and\n  Output files go to /tmp")
    assert ws.declared_places(ws.mechanics(spec)["Files"]) == {"vault", "tmp"}


def test_outside_paths_in_the_skills_tests_and_partial_paths(tmp_path):
    from aura.governor.mechanics import compare
    from aura.governor.scan import scan_skill

    uses = []
    folder = skill_folder(tmp_path, 'X = "/etc"\n')
    (folder / "test_skill.py").write_text('WORDS = "/usr/share/dict/words"\n')
    scanned, _ = compare(files("reads /etc/hosts"), NO_VAULT, scan_skill(folder, uses), uses)
    marked = [f.detail for f in scanned if f.message.endswith("doesn't declare it")]
    assert marked == ["csv_chart/skill.py:1"]  # "/etc" isn't named by "/etc/hosts"; the test's path doesn't count
