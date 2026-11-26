"""Tool selection from the Capability Registry's cues (Aura 2.0, Pillar 1, T3).

OLD_FAMILIES is the regex table brain.py used before the Registry (copied from 4412a6c). Every
recorded prompt must select at least what it selected then; prompts without inflected words must
select exactly that. What's new is word endings: "voices", "files", "searching".
"""

from __future__ import annotations

import re

import pytest
from conftest import call, make_aura, tool_turn
from langchain_core.messages import AIMessage, ToolMessage

from aura.capabilities import BY_NAME, select

# fmt: off
OLD_FAMILIES = (
    ({"list_vault", "read_file", "search_knowledge", "write_file", "create_folder", "delete_path"},
     re.compile(r"\b(vault|knowledge|wisdom|source|reference|document|docs?|file|folder|directory|path|read|save|delete|rename|notes?\.txt|\.csv|\.pdf)\b", re.I)),
    ({"web_search"}, re.compile(r"\b(search|web|internet|online|news|weather|latest|current|price|today|forecast)\b", re.I)),
    ({"run_python"}, re.compile(r"\b(python|calculate|compute|dataframe|chart|plot|dataset|spreadsheet)\b", re.I)),
    ({"list_voices", "set_voice"}, re.compile(r"\b(voice|speak|speech|audio|read aloud)\b", re.I)),
    ({"view_persona", "update_persona"}, re.compile(r"\b(persona|tone|brevity|formality|backstory|call yourself)\b", re.I)),
    ({"check_library", "list_skills", "propose_skill", "list_agents", "create_agent_candidate", "import_agent_candidate",
      "propose_agent", "develop_skill"}, re.compile(r"\b(skill|workshop|agent candidate|package|library|dependency|provenance)\b", re.I)),
    ({"ask_developer", "develop_skill"}, re.compile(r"\b(claude code|codex|codebase|repository|repo|git|implement|bug|test suite)\b", re.I)),
    ({"list_connections", "gmail_read", "gmail_draft", "calendar_read", "calendar_add", "drive_read", "slack_read",
      "slack_send", "discord_read", "discord_send", "new_messages"},
     re.compile(r"\b(gmail|email|mailbox|calendar|event|drive|slack|discord|channel|messages?)\b", re.I)),
)
# fmt: on
FILES, WEB, CODE, VOICE, PERSONA, WORKSHOP, DEVELOPER, CONNECTIONS = (f for f, _ in OLD_FAMILIES)


def old_select(text: str) -> set[str]:
    return {name for family, pattern in OLD_FAMILIES if pattern.search(text) for name in family}


# Matched before the Registry, with no inflected words: the selection must be identical.
UNCHANGED = [
    "Read notes.txt from my vault.",
    "What's in my vault?",
    "Use the Knowledge source document.",
    "Save this to budget.csv",
    "Delete old.txt",
    "Rename the folder Projects",
    "Search the latest weather online.",
    "What's the news today?",
    "What's the current price of gold?",
    "Use Python to compute the 30th Fibonacci number.",
    "Plot a chart of this dataset.",
    "Change your voice to something warmer.",
    "Speak more slowly.",
    "Make your tone warmer.",
    "What should I call yourself? Update your persona.",
    "Check the library requests on PyPI.",
    "Open the workshop.",
    "Use Claude Code to fix the repository test suite.",
    "Ask Codex to implement this.",
    "There's a bug in the codebase.",
    "Check my email.",
    "What's on my calendar this week?",
    "Post in the Slack channel.",
    "Read my Discord messages.",
    "Find the PDF in my Drive.",
    "Search the web, calculate the totals, and save them in report.csv.",
    "Read the pdf_text skill's provenance.",
    "What's my mailbox like?",
    "Any new event tomorrow?",
    "Check the dependency tree of the agent candidate.",
]

# Now matched because of word endings (before: nothing, or less).
INFLECTED = [
    ("What British voices do you have?", VOICE),
    ("List my files.", FILES),
    ("Show me the folders.", FILES),
    ("I was searching for flights.", WEB),
    ("Any events this week?", CONNECTIONS),
    ("Check my emails.", CONNECTIONS),
    ("Which skills do you have?", WORKSHOP),
    ("Plots of this, please.", CODE),
    ("What libraries does it use?", WORKSHOP),
    ("Look at those repos.", DEVELOPER),
    ("Fix these bugs.", DEVELOPER),
    ("Read the documents.", FILES),
    ("Saved prices from today's news.", FILES | WEB),
    ("Change the tones you use.", PERSONA),
    ("Show the calendars.", CONNECTIONS),
]

# No cue at all: no schemas (T4 adds find_capabilities for these).
UNMATCHED = [
    "Explain recursion in two sentences.",
    "Hi! How are you?",
    "What's 12 times 12?",
    "Tell me a joke.",
    "Summarise our conversation.",
    "Translate 'good morning' into French.",
    "I'm currently tired.",  # "current" is a cue; "currently" isn't one of its forms
    "Write me a haiku about rain.",
    "Who are you?",
    "Give me a recipe for pancakes.",
    "How do I tie a tie?",
    "Thank you!",
    "Report back when done.",  # "repo" is a cue; "report" isn't one of its forms
    "What's the meaning of life?",
    "Remind me what we discussed.",
]


@pytest.mark.parametrize("text", UNCHANGED)
def test_what_matched_before_matches_the_same(text):
    assert old_select(text), f"not a 'before' match: {text}"
    assert select(BY_NAME, text) == old_select(text)


@pytest.mark.parametrize(("text", "family"), INFLECTED)
def test_word_endings_now_match(text, family):
    chosen = select(BY_NAME, text)
    assert family <= chosen
    assert old_select(text) <= chosen  # never less than before


@pytest.mark.parametrize("text", UNMATCHED)
def test_requests_with_no_cue_select_nothing(text):
    assert select(BY_NAME, text) == set() == old_select(text)


def test_the_recorded_set_is_big_enough():
    assert len(UNCHANGED) + len(INFLECTED) + len(UNMATCHED) >= 60


# ── The middleware: what the model sees, and what it may call ──────────────────────────────


def test_the_model_sees_only_the_selected_schemas(settings):
    aura, _ = make_aura(settings, AIMessage("Hello!"))
    list(aura.stream("What British voices do you have?"))
    assert aura._visible_tool_names == {"list_voices", "set_voice", "find_capabilities"}
    assert {t.name for t in aura._tools} > aura._visible_tool_names  # everything stays registered


def test_a_tool_not_shown_this_turn_is_refused(settings):
    aura, _ = make_aura(settings, tool_turn(call("list_vault")), AIMessage("OK."))
    list(aura.stream("Tell me a joke."))
    msgs = aura._agent.get_state(aura._config).values["messages"]
    (refused,) = [m for m in msgs if isinstance(m, ToolMessage)]
    assert refused.status == "error" and refused.text.startswith("Error: list_vault isn't available in this turn")


def test_an_approval_continuation_and_a_developer_handoff_keep_their_menus(settings):
    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Receipts")), AIMessage("Created it."))
    list(aura.stream("Create a folder named Receipts."))
    assert "create_folder" in aura._visible_tool_names and aura._selection.visible is not None
    list(aura.resume([True]))
    assert aura._selection.visible is None  # after an approval: the full menu, as before
    assert (aura.toolbox.vault.root / "Receipts").is_dir()



# ── T4: find_capabilities, for requests no cue matched ──────────────────────────────────────


def test_an_unmatched_request_sees_only_find_capabilities(settings):
    aura, _ = make_aura(settings, AIMessage("Hello!"))
    list(aura.stream("Tell me a joke."))
    assert aura._visible_tool_names == {"find_capabilities"}


def test_find_capabilities_lists_then_loads_a_family_and_its_tool_runs(settings):
    aura, _ = make_aura(settings,
                            tool_turn(call("find_capabilities")),
                            tool_turn(call("find_capabilities", family="voice")),
                            tool_turn(call("list_voices")),
                            AIMessage("Here are my voices."))  # fmt: skip
    list(aura.stream("Which accents can you do?"))  # no cue matches
    msgs = aura._agent.get_state(aura._config).values["messages"]
    listing, loaded, voices = [m for m in msgs if isinstance(m, ToolMessage)]
    assert listing.text.startswith("Families you can load") and "- Voice: list and change" in listing.text
    assert "- Slack:" not in listing.text  # not connected: not offered
    assert loaded.text == "Loaded Voice: list_voices, set_voice. Call the one you need now."
    assert voices.status != "error" and "isn't available" not in voices.text
    assert {"list_voices", "set_voice"} <= aura._visible_tool_names
    # The next turn starts narrow again.
    list(aura.stream("Tell me a joke."))
    assert aura._visible_tool_names == {"find_capabilities"}


def test_find_capabilities_cant_load_what_isnt_connected_or_doesnt_exist(settings):
    aura, _ = make_aura(settings,
                        tool_turn(call("find_capabilities", family="Slack")),
                        tool_turn(call("find_capabilities", family="Teleport")),
                        AIMessage("Sorry."))  # fmt: skip
    list(aura.stream("Tell my team I'm late."))
    msgs = [m for m in aura._agent.get_state(aura._config).values["messages"] if isinstance(m, ToolMessage)]
    assert msgs[0].text.startswith("'Slack' isn't available right now (not connected")
    assert msgs[1].text.startswith("'Teleport' isn't a family")
    assert aura._visible_tool_names == {"find_capabilities"}  # nothing was loaded


def test_loading_a_family_keeps_its_approval_card(settings):
    aura, _ = make_aura(settings,
                        tool_turn(call("find_capabilities", family="Files")),
                        tool_turn(call("delete_path", path="old.txt")),
                        AIMessage("Deleted."))  # fmt: skip
    (aura.toolbox.vault.root / "old.txt").write_text("keep me")
    events = list(aura.stream("Get rid of old.txt"))  # "old.txt" alone isn't a cue
    from aura.brain import ApprovalNeeded

    assert isinstance(events[-1], ApprovalNeeded) and events[-1].actions[0].tool == "delete_path"
    list(aura.resume([False]))
    assert (aura.toolbox.vault.root / "old.txt").read_text() == "keep me"


def test_an_unmatched_turn_sends_a_fraction_of_the_full_menu(settings):
    # The prompt budget (89c6752): the schemas for an unmatched turn stay small.
    import json

    from langchain_core.utils.function_calling import convert_to_openai_tool

    aura, _ = make_aura(settings, AIMessage("Hello!"))
    list(aura.stream("Tell me a joke."))
    size = lambda tools: len(json.dumps([convert_to_openai_tool(t) for t in tools]))  # noqa: E731
    shown = size([t for t in aura._tools if t.name in aura._visible_tool_names])
    full = size(aura._tools)
    assert shown <= 0.1 * full, (shown, full)



@pytest.mark.parametrize(("said", "family"), [
    ("Voice", "Voice"), ("voice", "Voice"), ("  VOICE ", "Voice"), ("Vault", "Files"), ("The Vault", "Files"),
    ("Web search", "Web"), ("python", "Code"), ("the Persona family", "Persona"), ("Teleport", None), ("", None),
])
def test_close_family_names_are_understood(said, family):
    from aura.capabilities import family_named

    assert family_named(said) == family


def test_the_tool_description_lists_the_families():
    from aura.capabilities import FAMILIES, finder_tool

    description = finder_tool(dict, lambda names: None).description
    assert "Families:\n- Files: list, read, write" in description
    assert all(f"- {f}:" in description for f in FAMILIES if f != "Capabilities")
