"""The Capability Registry (Aura 2.0, Pillar 1): one row per tool, the single source of truth.

Each row says what the tool is (family, status line), how far it must be gated (floor), and what
it sends off this Mac (egress). The approval wiring (tools/__init__.py NEEDS_APPROVAL, EDITABLE),
the egress gate (tools/egress.py GATED), status lines and labels are all derived from this table,
so a tool's floor is written once. The table lives in code, not in the vault (Aura's ruling D6):
an approved vault write must never be able to change Aura's security posture.

Terms (docs/aura-2-plan.md): a capability is one tool; a family is a user-facing group of them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool

# none: runs; ask: an approval card; egress: a card for each call plus a single-use grant for the
# exact words that leave the Mac (tools/egress.py).
Floor = Literal["none", "ask", "egress"]
# What leaves this Mac: nothing; the user's connected accounts or a model provider; the open internet.
Egress = Literal["none", "account", "internet"]
Owner = Literal["core", "connection", "skill"]

FAMILIES = ("Files", "Knowledge", "Web", "Code", "Voice", "Persona", "Models", "Workshop", "Developer",
            "Google", "Slack", "Discord", "Connections", "Skills", "Plans", "Capabilities")  # fmt: skip
# What find_capabilities says about each family (one line each, for a small local model).
FAMILY_SUMMARY = {
    "Files": "list, read, write, create and delete files and folders in the vault",
    "Knowledge": "search the Knowledge notes, with citations",
    "Web": "search the internet (each query needs the user's approval)",
    "Code": "run Python in an offline sandbox, e.g. to calculate or chart",
    "Voice": "how Aura sounds: its voices and accents, or a new voice",
    "Persona": "view and change Aura's persona (tone, brevity, name)",
    "Models": "AI models: what is loaded, what each role uses",
    "Workshop": "installed skills (add-ons) and agents; propose, audit, check a library",
    "Developer": "the Developer: hand a job on files or scripts to Claude Code or Codex",
    "Google": "Gmail, Google Calendar and Google Drive",
    "Slack": "read and send Slack messages",
    "Discord": "read and send Discord messages",
    "Connections": "list connected accounts and check new messages",
    "Skills": "run an installed skill (Workshop lists them)",
    "Plans": "a plan for a large job: steps run in order, each still asking you as usual",
}


@dataclass(frozen=True)
class Capability:
    name: str
    family: str
    label: str  # the status line's group, e.g. "[Tool: Web_Search_Tool]"
    doing: Callable[[dict], str]  # what the status line says it's doing ("" announces itself)
    floor: Floor = "none"
    egress: Egress = "none"
    owner: Owner = "core"
    editable: tuple[str, ...] = ()  # fields the user may edit on its approval card
    gated_arg: str | None = None  # floor "egress": the argument that leaves the Mac
    cues: tuple[str, ...] = ()  # words that show this tool's schema to the model for a turn
    note: str = ""  # a deliberate exception the user should know about (shown in Capabilities.md)

    def matches(self, text: str) -> bool:
        return bool(self.cues) and _cue_pattern(self.cues).search(text) is not None

    @property
    def default(self) -> str:
        """The Permissions screen's starting setting (Sprint 7 §2): today's behaviour."""
        return "allowed" if self.floor == "none" else "ask"


# A cue matches its word and the word's common endings ("voice" matches "voices"), not any word
# that merely starts with it ("repo" doesn't match "report"; "current" doesn't match "currently").
_FORMS = r"(?:s|es|d|ed|ing|'s)?"


@lru_cache(maxsize=256)
def _cue_pattern(cues: tuple[str, ...]) -> re.Pattern[str]:
    words = "|".join(re.escape(cue) for cue in sorted(cues, key=len, reverse=True))
    return re.compile(rf"\b(?:{words}){_FORMS}\b", re.I)


# Cue sets: a local model is shown only the schemas a request plainly needs (89c6752: the prompt
# budget). These are the families Aura matched before the Registry, word for word, so a request
# selects what it always did; the word endings are what's new.
FILES_CUES = ("vault", "knowledge", "wisdom", "source", "reference", "document", "doc", "file", "folder",
              "directory", "path", "read", "save", "delete", "rename", "note.txt", "notes.txt", ".csv", ".pdf")
WEB_CUES = ("search", "web", "internet", "online", "news", "weather", "latest", "current", "price", "today",
            "forecast")
CODE_CUES = ("python", "calculate", "compute", "dataframe", "chart", "plot", "dataset", "spreadsheet")
VOICE_CUES = ("voice", "speak", "speech", "audio", "read aloud")
PERSONA_CUES = ("persona", "tone", "brevity", "formality", "backstory", "call yourself")
WORKSHOP_CUES = ("skill", "workshop", "agent candidate", "package", "library", "libraries", "dependency",
                 "dependencies", "provenance")
# "developer": a request that names the Developer shows its tools at once (2026-11-27: "Have the Developer
# write a script…" was answered without them in 3 of 4 runs).
DEVELOPER_CUES = ("claude code", "codex", "codebase", "repository", "repositories", "repo", "git", "implement",
                  "bug", "test suite", "developer")
# Plans (docs/orchestration.md): only explicit requests for a plan; a running plan's tools are
# shown every turn anyway (brain._select_turn_tools).
PLAN_CUES = ("make a plan", "plan out", "multi-step", "multistep")  # not "step by step": that's how people ask for explanations
CONNECTION_CUES = ("gmail", "email", "mailbox", "calendar", "event", "drive", "slack", "discord", "channel",
                   "message")  # fmt: skip
_CUES_BY_FAMILY = {"Files": FILES_CUES, "Knowledge": FILES_CUES, "Web": WEB_CUES, "Code": CODE_CUES,
                   "Voice": VOICE_CUES, "Persona": PERSONA_CUES, "Workshop": WORKSHOP_CUES,
                   "Developer": DEVELOPER_CUES, "Google": CONNECTION_CUES, "Slack": CONNECTION_CUES,
                   "Discord": CONNECTION_CUES, "Connections": CONNECTION_CUES, "Plans": PLAN_CUES}  # fmt: skip


def _c(name: str, family: str, label: str, doing: Callable[[dict], str], extra_cues: tuple[str, ...] = (),
       **more) -> Capability:  # fmt: skip
    return Capability(name, family, label, doing, cues=_CUES_BY_FAMILY.get(family, ()) + extra_cues, **more)


def _slack_doing(a: dict) -> str:
    if a.get("query"):
        return f"Searching Slack for '{a['query']}'..."
    return f"Reading Slack {a['channel']}..." if a.get("channel") else "Listing your Slack channels..."


CAPABILITIES: tuple[Capability, ...] = (
    # Files: the vault (writes are R1 floors)
    _c("list_vault", "Files", "File_System_Tool", lambda a: f"Listing '{a.get('path') or '.'}'..."),
    _c("read_file", "Files", "File_System_Tool", lambda a: f"Reading '{a.get('path')}'..."),
    _c("write_file", "Files", "File_System_Tool", lambda a: f"Writing '{a.get('path')}'...", floor="ask"),
    _c("create_folder", "Files", "File_System_Tool", lambda a: f"Creating folder '{a.get('path')}'...", floor="ask"),
    _c("delete_path", "Files", "File_System_Tool", lambda a: f"Deleting '{a.get('path')}'...", floor="ask"),
    _c("search_vault", "Files", "File_System_Tool", lambda a: f"Searching the vault for '{a.get('query')}'..."),
    # Tidying: listing clutter is read-only; moving it to the Trash is an R1 floor, one card for the list
    _c("find_clutter", "Files", "File_System_Tool", lambda a: "Looking for clutter in the vault..."),
    _c("tidy_vault", "Files", "File_System_Tool", lambda a: f"Moving {len(a.get('paths') or [])} item(s) to the Trash...",
       floor="ask"),
    _c("search_knowledge", "Knowledge", "Knowledge", lambda a: f"Searching Knowledge for '{a.get('query')}'..."),
    # Web: the Egress Gatekeeper
    _c("web_search", "Web", "Web_Search_Tool", lambda a: f"Searching for '{a.get('query')}'...",
       floor="egress", egress="internet", gated_arg="query"),
    # Code: an offline container (Gemma's Sprint 4 floor)
    _c("run_python", "Code", "Code_Interpreter_Tool", lambda a: "Running Python in the sandbox...", floor="ask"),
    _c("list_voices", "Voice", "Voice_Tool", lambda a: "Looking through my voices..."),
    _c("set_voice", "Voice", "Voice_Tool", lambda a: "Adjusting my voice..."),
    _c("view_persona", "Persona", "Persona_Tool", lambda a: "Reviewing my persona..."),
    # Models: read-only (what Aura can run and what is loaded; switching is in Settings)
    _c("list_models", "Models", "Models_Tool", lambda a: "Checking my models...",
       extra_cues=("model", "llm", "gemma", "qwen", "bonsai", "ollama")),
    _c("update_persona", "Persona", "Persona_Tool", lambda a: f"Updating my {a.get('field', 'persona')}...", floor="ask"),
    # Workshop: skills and agents (the Governor's checks query PyPI and OSV by package name)
    _c("check_library", "Workshop", "Governor", lambda a: f"Checking '{a.get('name')}' on PyPI and OSV...",
       egress="internet", note="Sends only a package name to PyPI and OSV, with no approval card: "
                               "the Governor's provenance and vulnerability check (Aura's review, 2026-11-26)."),
    _c("list_skills", "Workshop", "Workshop", lambda a: "Looking through my skills..."),
    _c("propose_skill", "Workshop", "Workshop", lambda a: f"Staging the '{a.get('name')}' skill for audit...",
       egress="internet", note="Its audit looks up the skill's dependencies on PyPI and OSV and builds its image "
                               "from PyPI, with no approval card: only package names leave this Mac."),
    _c("list_agents", "Workshop", "Workshop", lambda a: "Checking the Workstation agent registry..."),
    _c("create_agent_candidate", "Workshop", "Workshop",
       lambda a: f"Creating the '{a.get('name')}' agent candidate...", floor="ask"),
    _c("import_agent_candidate", "Workshop", "Workshop",
       lambda a: f"Quarantining the '{a.get('name')}' agent import...", floor="ask"),
    _c("propose_agent", "Workshop", "Workshop", lambda a: f"Staging the '{a.get('name')}' agent manifest for audit..."),
    # Developer: Claude Code or Codex in the Developer container (it reaches the model provider)
    _c("develop_skill", "Developer", "Developer",
       lambda a: f"Starting {'Codex' if a.get('engine') == 'codex' else 'Claude Code'} on the '{a.get('name')}' skill...",
       floor="ask", egress="account", editable=("specification", "engine"),
       extra_cues=WORKSHOP_CUES),  # in both the Workshop and Developer cue sets, as before
    _c("ask_developer", "Developer", "Developer", lambda a: "", floor="ask", egress="account"),  # announces itself
    # Connections (Sprint 7): present only when the service is connected; sends are R1 floors
    _c("list_connections", "Connections", "Connections", lambda a: "Checking my connections...", owner="connection"),
    _c("new_messages", "Connections", "Connections", lambda a: "Checking new Slack and Discord messages...",
       owner="connection"),
    _c("gmail_read", "Google", "Gmail",
       lambda a: "Opening an email..." if a.get("message_id") else f"Searching Gmail for '{a.get('query') or 'your inbox'}'...",
       egress="account", owner="connection"),
    _c("gmail_draft", "Google", "Gmail", lambda a: f"Saving a draft to {a.get('to')}...",
       floor="ask", egress="account", owner="connection"),
    _c("calendar_read", "Google", "Calendar",
       lambda a: f"Looking at your calendar for the next {a.get('days', 7)} days...", egress="account", owner="connection"),
    _c("calendar_add", "Google", "Calendar", lambda a: f"Adding '{a.get('title')}' to your calendar...",
       floor="ask", egress="account", owner="connection"),
    _c("drive_read", "Google", "Drive",
       lambda a: "Opening a Drive file..." if a.get("file_id") else f"Searching Drive for '{a.get('query') or 'recent files'}'...",
       egress="account", owner="connection"),
    _c("slack_read", "Slack", "Slack", _slack_doing, egress="account", owner="connection"),
    _c("slack_send", "Slack", "Slack", lambda a: f"Posting in Slack {a.get('channel')}...",
       floor="ask", egress="account", owner="connection"),
    _c("discord_read", "Discord", "Discord",
       lambda a: f"Reading Discord {a['channel']}..." if a.get("channel") else "Listing Discord channels...",
       egress="account", owner="connection"),
    _c("discord_send", "Discord", "Discord", lambda a: f"Posting in Discord {a.get('channel')}...",
       floor="ask", egress="account", owner="connection"),
    # Plans (docs/orchestration.md T4). make_plan's card is the plan card: approving it approves the
    # plan's direction only; every step's own tool still asks as usual.
    _c("make_plan", "Plans", "Plan", lambda a: "Checking the plan...", floor="ask"),
    _c("plan_status", "Plans", "Plan", lambda a: "Reading the plan..."),
    _c("finish_step", "Plans", "Plan", lambda a: f"Finishing step {a.get('step', '')}..."),
    # The Registry itself: always shown, so a request no cue matched can still reach its tools
    _c("find_capabilities", "Capabilities", "Capabilities",
       lambda a: f"Loading my {a['family']} tools..." if a.get("family") else "Looking through my capabilities..."),
)  # fmt: skip

BY_NAME: dict[str, Capability] = {c.name: c for c in CAPABILITIES}


def skill_capability(name: str, manifest: dict) -> Capability:
    """An installed skill's row, from its approved skill.toml: skills run in the sandbox without a
    card (their approval was the Workshop audit), and the manifest says whether they reach the
    network (the Sprint 5 policy refuses any skill that asks)."""
    network = bool((manifest.get("permissions") or {}).get("network", False))
    return Capability(f"skill_{name}", "Skills", "Skill_Tool", lambda a: f"Running the {name} skill...",
                      egress="internet" if network else "none", owner="skill",
                      cues=("skill", name.replace("_", " ")))  # fmt: skip


def rows(skills: Iterable[Capability] = ()) -> dict[str, Capability]:
    """The table's rows plus the installed skills' (a skill row never replaces a table row)."""
    return {**{c.name: c for c in skills}, **BY_NAME}


def select(names: Iterable[str], text: str, skills: Iterable[Capability] = ()) -> set[str]:
    """The tools (of `names`) whose cues the request matches: the schemas a turn shows."""
    known, matched = rows(skills), {}
    chosen = set()
    for n in names:
        if (row := known.get(n)) is not None and row.cues:
            if row.cues not in matched:  # many rows share one cue set: match it once
                matched[row.cues] = row.matches(text)
            if matched[row.cues]:
                chosen.add(n)
    return chosen


class UnregisteredTool(RuntimeError):
    """A tool was built with no row in the Registry: its floor would be unknown."""


def check_registered(names: Iterable[str], skills: Iterable[Capability] = ()) -> None:
    """Every tool Aura offers needs a row: the table's, or an installed skill's from its manifest."""
    known = rows(skills)
    missing = sorted(n for n in names if n not in known)
    if missing:
        raise UnregisteredTool(f"No Capability Registry row for: {', '.join(missing)} (src/aura/capabilities.py).")


def _check_table() -> None:
    names = [c.name for c in CAPABILITIES]
    problems = ["duplicate rows"] if len(names) != len(set(names)) else []
    for c in CAPABILITIES:
        if c.family not in FAMILIES:
            problems.append(f"{c.name}: unknown family {c.family!r}")
        if (c.floor == "egress") != (c.gated_arg is not None):
            problems.append(f"{c.name}: an egress floor needs gated_arg, and only it")
        if c.editable and c.floor != "ask":
            problems.append(f"{c.name}: editable fields need an approval card")
    problems += [f"alias {k!r}: unknown family {v!r}" for k, v in FAMILY_ALIASES.items() if v not in FAMILIES]
    if problems:
        raise ValueError("Capability Registry: " + "; ".join(problems))



FINDER = "find_capabilities"
# Names a model reaches for instead of the family's own (seen in the live eval, 2026-11-26).
FAMILY_ALIASES = {
    "vault": "Files", "file": "Files", "folder": "Files", "notes": "Knowledge", "web search": "Web",
    "search": "Web", "internet": "Web", "python": "Code", "sandbox": "Code", "code interpreter": "Code",
    "voices": "Voice", "speech": "Voice", "personality": "Persona", "settings": "Persona", "skill": "Skills",
    "llm": "Models", "llms": "Models", "ai models": "Models",
    "agents": "Workshop", "add-ons": "Workshop", "addons": "Workshop", "accent": "Voice", "accents": "Voice",
    "developers": "Developer", "claude code": "Developer", "codex": "Developer",
    "gmail": "Google", "email": "Google", "calendar": "Google", "drive": "Google", "accounts": "Connections",
}  # fmt: skip


def family_named(text: str) -> str | None:
    """The family a model meant: its own name in any case, an alias, or a family named in the text."""
    wanted = re.sub(r"^the\s+", "", " ".join(text.lower().split()))
    exact = {f.lower(): f for f in FAMILIES} | FAMILY_ALIASES
    if wanted in exact:
        return exact[wanted]
    return next((f for f in FAMILIES if re.search(rf"\b{re.escape(f.lower())}\b", wanted)), None)


def finder_tool(families: Callable[[], dict[str, list[str]]], load: Callable[[list[str]], None]) -> BaseTool:
    """find_capabilities: list the families Aura has now, or load one family's tools for this turn.
    `families()` maps each family to its registered tool names (connected services only); `load`
    shows those names to the model for the rest of the turn. It grants nothing: every floor, card
    and egress grant still applies inside each tool."""

    def find_capabilities(family: str = "") -> str:
        """Load a family of tools for this turn when the request needs a tool you don't have, then
        call that tool (e.g. family="Voice"). Approval cards still apply. No family: list them."""
        available = {name: tools for name, tools in families().items() if tools and name != "Capabilities"}
        if not family.strip():
            lines = [f"- {name}: {FAMILY_SUMMARY.get(name, '')} ({len(tools)} tools)" for name, tools in available.items()]
            return "Families you can load with find_capabilities(family):\n" + "\n".join(lines)
        meant = family_named(family)
        match = meant if meant in available else None
        if match is None:
            why = "isn't available right now (not connected, or nothing installed)" if meant else "isn't a family"
            return f"'{family}' {why}. Families: {', '.join(available) or 'none'}."
        load(available[match])
        return f"Loaded {match}: {', '.join(available[match])}. Call the one you need now."

    families_line = "\n".join(f"- {name}: {FAMILY_SUMMARY[name]}" for name in FAMILIES if name in FAMILY_SUMMARY)
    return StructuredTool.from_function(find_capabilities,
                                        description=f"{find_capabilities.__doc__}\n\nFamilies:\n{families_line}")  # fmt: skip


RENDERED = "Capabilities.md"  # at the vault root; read-only to every tool (tools/vault.py GENERATED)
# Marks a file as Aura's rendering: Aura replaces only a file that carries it, never a user's note.
GENERATED_MARK = "Generated by Aura from its code"
_FLOOR_TEXT = {"none": "no", "ask": "yes", "egress": "yes, for each request"}
_EGRESS_TEXT = {"none": "nothing", "account": "to your connected account or the model provider",
                "internet": "to the internet"}  # fmt: skip


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:].rstrip(".") + "."


def render(available: Iterable[str], skills: Iterable[Capability] = ()) -> str:
    """The Registry as a vault note, grouped by family: for reading only. It is regenerated from
    code whenever Aura's tools change, and no tool may edit it (Aura's ruling D6)."""
    shown = set(available)
    rows = [*CAPABILITIES, *skills]
    lines = ["# Aura's capabilities", "",
             (f"{GENERATED_MARK} (`src/aura/capabilities.py`). It is read-only: "
              "edits are refused, and the file is rewritten whenever Aura's tools change."), ""]  # fmt: skip
    for family in FAMILIES:
        members = [r for r in rows if r.family == family]
        if not members:
            continue
        lines += [f"## {family}", "", _sentence(FAMILY_SUMMARY.get(family, "Aura's own tools")), "",
                  "| Tool | Approval card | What leaves this Mac | From | Now |", "|---|---|---|---|---|"]  # fmt: skip
        for r in members:
            now = "available" if r.name in shown else ("not connected" if r.owner == "connection" else "not loaded")
            lines.append(f"| `{r.name}` | {_FLOOR_TEXT[r.floor]} | {_EGRESS_TEXT[r.egress]} | {r.owner} | {now} |")
        lines += ["", *(f"- `{r.name}`: {r.note}" for r in members if r.note)]
        if lines[-1] != "":
            lines.append("")
    return "\n".join(lines)


_check_table()
