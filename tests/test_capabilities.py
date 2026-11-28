"""The Capability Registry (Aura 2.0, Pillar 1): one table in code, and everything derived from it.

The values below were captured from the code before the Registry existed (2026-11-26). If one of
these tests fails, a floor, label or status line changed: that needs the user's and Aura's review.
"""

from __future__ import annotations

import pytest
from test_governor import env  # noqa: F401 (fixture)

# fmt: off
SAMPLE = {
    "path": "a.txt",
    "query": "q",
    "message_id": "",
    "file_id": "",
    "channel": "#c",
    "to": "x@y",
    "title": "T",
    "days": 3,
    "field": "tone",
    "name": "n",
    "engine": "codex",
    "code": "1",
}
NEEDS_APPROVAL_TODAY = {
    "gmail_draft",
    "import_agent_candidate",
    "discord_send",
    "write_file",
    "slack_send",
    "create_folder",
    "ask_developer",
    "develop_skill",
    "update_persona",
    "run_python",
    "delete_path",
    "create_agent_candidate",
    "calendar_add",
}
EDITABLE_TODAY = {"develop_skill": ("specification", "engine")}
GATED_TODAY = {"web_search": "query"}
GROUP_TODAY = {
    "ask_developer": "Developer",
    "calendar_add": "Calendar",
    "calendar_read": "Calendar",
    "check_library": "Governor",
    "create_agent_candidate": "Workshop",
    "create_folder": "File_System_Tool",
    "delete_path": "File_System_Tool",
    "develop_skill": "Developer",
    "discord_read": "Discord",
    "discord_send": "Discord",
    "drive_read": "Drive",
    "gmail_draft": "Gmail",
    "gmail_read": "Gmail",
    "import_agent_candidate": "Workshop",
    "list_agents": "Workshop",
    "list_connections": "Connections",
    "list_skills": "Workshop",
    "list_vault": "File_System_Tool",
    "list_voices": "Voice_Tool",
    "new_messages": "Connections",
    "propose_agent": "Workshop",
    "propose_skill": "Workshop",
    "read_file": "File_System_Tool",
    "run_python": "Code_Interpreter_Tool",
    "search_knowledge": "Knowledge",
    "set_voice": "Voice_Tool",
    "slack_read": "Slack",
    "slack_send": "Slack",
    "update_persona": "Persona_Tool",
    "view_persona": "Persona_Tool",
    "web_search": "Web_Search_Tool",
    "write_file": "File_System_Tool",
}
STATUS_TODAY = {
    "ask_developer": "",
    "calendar_add": "[Tool: Calendar] Adding 'T' to your calendar...",
    "calendar_read": "[Tool: Calendar] Looking at your calendar for the next 3 days...",
    "check_library": "[Tool: Governor] Checking 'n' on PyPI and OSV...",
    "create_agent_candidate": "[Tool: Workshop] Creating the 'n' agent candidate...",
    "create_folder": "[Tool: File_System_Tool] Creating folder 'a.txt'...",
    "delete_path": "[Tool: File_System_Tool] Deleting 'a.txt'...",
    "develop_skill": "[Tool: Developer] Starting Codex on the 'n' skill...",
    "discord_read": "[Tool: Discord] Reading Discord #c...",
    "discord_send": "[Tool: Discord] Posting in Discord #c...",
    "drive_read": "[Tool: Drive] Searching Drive for 'q'...",
    "gmail_draft": "[Tool: Gmail] Saving a draft to x@y...",
    "gmail_read": "[Tool: Gmail] Searching Gmail for 'q'...",
    "import_agent_candidate": "[Tool: Workshop] Quarantining the 'n' agent import...",
    "list_agents": "[Tool: Workshop] Checking the Workstation agent registry...",
    "list_connections": "[Tool: Connections] Checking my connections...",
    "list_skills": "[Tool: Workshop] Looking through my skills...",
    "list_vault": "[Tool: File_System_Tool] Listing 'a.txt'...",
    "list_voices": "[Tool: Voice_Tool] Looking through my voices...",
    "new_messages": "[Tool: Connections] Checking new Slack and Discord messages...",
    "propose_agent": "[Tool: Workshop] Staging the 'n' agent manifest for audit...",
    "propose_skill": "[Tool: Workshop] Staging the 'n' skill for audit...",
    "read_file": "[Tool: File_System_Tool] Reading 'a.txt'...",
    "run_python": "[Tool: Code_Interpreter_Tool] Running Python in the sandbox...",
    "search_knowledge": "[Tool: Knowledge] Searching Knowledge for 'q'...",
    "set_voice": "[Tool: Voice_Tool] Adjusting my voice...",
    "slack_read": "[Tool: Slack] Searching Slack for 'q'...",
    "slack_send": "[Tool: Slack] Posting in Slack #c...",
    "update_persona": "[Tool: Persona_Tool] Updating my tone...",
    "view_persona": "[Tool: Persona_Tool] Reviewing my persona...",
    "web_search": "[Tool: Web_Search_Tool] Searching for 'q'...",
    "write_file": "[Tool: File_System_Tool] Writing 'a.txt'...",
}
STATUS_EMPTY_TODAY = {
    "ask_developer": "",
    "calendar_add": "[Tool: Calendar] Adding 'None' to your calendar...",
    "calendar_read": "[Tool: Calendar] Looking at your calendar for the next 7 days...",
    "check_library": "[Tool: Governor] Checking 'None' on PyPI and OSV...",
    "create_agent_candidate": "[Tool: Workshop] Creating the 'None' agent candidate...",
    "create_folder": "[Tool: File_System_Tool] Creating folder 'None'...",
    "delete_path": "[Tool: File_System_Tool] Deleting 'None'...",
    "develop_skill": "[Tool: Developer] Starting Claude Code on the 'None' skill...",
    "discord_read": "[Tool: Discord] Listing Discord channels...",
    "discord_send": "[Tool: Discord] Posting in Discord None...",
    "drive_read": "[Tool: Drive] Searching Drive for 'recent files'...",
    "gmail_draft": "[Tool: Gmail] Saving a draft to None...",
    "gmail_read": "[Tool: Gmail] Searching Gmail for 'your inbox'...",
    "import_agent_candidate": "[Tool: Workshop] Quarantining the 'None' agent import...",
    "list_agents": "[Tool: Workshop] Checking the Workstation agent registry...",
    "list_connections": "[Tool: Connections] Checking my connections...",
    "list_skills": "[Tool: Workshop] Looking through my skills...",
    "list_vault": "[Tool: File_System_Tool] Listing '.'...",
    "list_voices": "[Tool: Voice_Tool] Looking through my voices...",
    "new_messages": "[Tool: Connections] Checking new Slack and Discord messages...",
    "propose_agent": "[Tool: Workshop] Staging the 'None' agent manifest for audit...",
    "propose_skill": "[Tool: Workshop] Staging the 'None' skill for audit...",
    "read_file": "[Tool: File_System_Tool] Reading 'None'...",
    "run_python": "[Tool: Code_Interpreter_Tool] Running Python in the sandbox...",
    "search_knowledge": "[Tool: Knowledge] Searching Knowledge for 'None'...",
    "set_voice": "[Tool: Voice_Tool] Adjusting my voice...",
    "slack_read": "[Tool: Slack] Listing your Slack channels...",
    "slack_send": "[Tool: Slack] Posting in Slack None...",
    "update_persona": "[Tool: Persona_Tool] Updating my persona...",
    "view_persona": "[Tool: Persona_Tool] Reviewing my persona...",
    "web_search": "[Tool: Web_Search_Tool] Searching for 'None'...",
    "write_file": "[Tool: File_System_Tool] Writing 'None'...",
}
# fmt: on


def test_the_derived_tables_are_exactly_todays():
    from aura.tools import EDITABLE, GROUP, NEEDS_APPROVAL
    from aura.tools.egress import GATED

    # 2026-11-27: tidy_vault (moving clutter to the Trash) is an R1 floor, like delete_path.
    # 2026-11-28: make_plan's card is the plan card (docs/orchestration.md T4); no other tool changes.
    assert set(NEEDS_APPROVAL) == NEEDS_APPROVAL_TODAY | {"tidy_vault", "make_plan"} and len(NEEDS_APPROVAL) == len(set(NEEDS_APPROVAL))
    assert dict(EDITABLE) == EDITABLE_TODAY
    assert dict(GATED) == GATED_TODAY
    # T4: the Registry's own tool; 2026-11-27: list_models (read-only, the Models family)
    assert dict(GROUP) == GROUP_TODAY | {"find_capabilities": "Capabilities", "list_models": "Models_Tool",
                                         "find_clutter": "File_System_Tool", "tidy_vault": "File_System_Tool",
                                         "search_vault": "File_System_Tool", "make_plan": "Plan",
                                         "plan_status": "Plan", "finish_step": "Plan"}


def test_status_lines_are_unchanged():
    from aura.tools import status_line

    assert {n: status_line(n, SAMPLE) for n in sorted(GROUP_TODAY)} == STATUS_TODAY
    assert {n: status_line(n, {}) for n in sorted(GROUP_TODAY)} == STATUS_EMPTY_TODAY
    assert status_line("skill_word_count", {}) == "[Tool: Skill_Tool] Running the word_count skill..."


def test_the_cards_aura_raises_are_the_registrys_floors(settings):
    # The approval middleware's interrupt_on map: every "ask" and "egress" row, and nothing else.
    from conftest import make_aura

    from aura.capabilities import CAPABILITIES

    aura, _ = make_aura(settings)
    expected = {c.name for c in CAPABILITIES if c.floor != "none"}
    assert expected == NEEDS_APPROVAL_TODAY | set(GATED_TODAY) | {"tidy_vault", "make_plan"}
    # 2026-11-27, R2 across a plan (docs/orchestration.md T8): the calls that leave the Mac with no card
    # of their own get one only while a running plan has read private data (a `when`, never always).
    from aura.connections.actions import R2_TOOLS

    assert set(aura._interrupt_on) == expected | set(R2_TOOLS) and not set(R2_TOOLS) & expected
    assert all("when" in aura._interrupt_on[name] for name in R2_TOOLS)
    assert "when" in aura._interrupt_on["make_plan"]  # the plan card: only for a plan that passed its checks
    assert aura._interrupt_on["develop_skill"]["allowed_decisions"] == ["approve", "edit", "reject"]
    assert aura._interrupt_on["web_search"]["allowed_decisions"] == ["approve", "reject"]
    assert "when" in aura._interrupt_on["web_search"] and "when" not in aura._interrupt_on["write_file"]


def test_outward_connection_tools_are_the_registrys_connection_floors():
    from aura.capabilities import CAPABILITIES
    from aura.connections.actions import OUTWARD

    assert set(OUTWARD) == {"gmail_draft", "calendar_add", "slack_send", "discord_send"}  # derived; pinned to today's
    assert set(OUTWARD) == {c.name for c in CAPABILITIES if c.owner == "connection" and c.floor == "ask"}


def test_every_tool_aura_can_offer_has_a_row_and_no_row_is_stale(env):  # noqa: F811
    # Everything connected (to fakes): core tools plus every connection tool.
    from test_connections import connected_aura

    from aura.capabilities import BY_NAME

    aura, _, _ = connected_aura(env)
    google = {"gmail_read", "gmail_draft", "calendar_read", "calendar_add", "drive_read"}  # not connected in the fake
    offered = {t.name for t in aura.toolbox.all_tools()}
    assert offered | google | {"find_capabilities"} == set(BY_NAME)  # the brain adds find_capabilities


def test_a_tool_without_a_row_is_refused():
    import pytest

    from aura.capabilities import UnregisteredTool, check_registered

    check_registered(["read_file"])
    with pytest.raises(UnregisteredTool, match="send_fax"):
        check_registered(["read_file", "send_fax"])


# ── T2: installed skills get their rows from their manifests ─────────────────────────────────


def test_an_installed_skill_gets_its_row_from_its_manifest(env):  # noqa: F811
    from test_governor import deployed, workshop_client

    _, _, aura = workshop_client(env)
    deployed((env[0], aura.toolbox.workshop, aura.toolbox.skills, env[3]))
    (row,) = aura.toolbox.skills.capabilities()
    assert (row.name, row.family, row.label, row.floor, row.egress, row.owner) == \
        ("skill_pdf_text", "Skills", "Skill_Tool", "none", "none", "skill")  # fmt: skip
    assert row.doing({}) == "Running the pdf_text skill..." and row.default == "allowed"
    assert "skill_pdf_text" in {t.name for t in aura.toolbox.all_tools()}  # registered: no error


def test_a_skill_row_follows_its_manifests_network_permission():
    from aura.capabilities import skill_capability

    assert skill_capability("offline", {"permissions": {"network": False}}).egress == "none"
    assert skill_capability("online", {"permissions": {"network": True}}).egress == "internet"  # refused at install anyway
    assert skill_capability("bare", {}).egress == "none"


def test_a_skill_tool_without_an_installed_manifest_is_refused():
    import pytest

    from aura.capabilities import UnregisteredTool, check_registered, skill_capability

    check_registered(["skill_word_count"], [skill_capability("word_count", {})])
    with pytest.raises(UnregisteredTool, match="skill_word_count"):
        check_registered(["skill_word_count"])


# ── T5: the vault's read-only Capabilities.md ───────────────────────────────────────────────


def test_the_rendering_lists_every_family_with_floor_egress_and_source(env):  # noqa: F811
    from test_connections import connected_aura

    from aura.capabilities import render

    aura, _, _ = connected_aura(env)
    text = render((t.name for t in aura._tools), aura.toolbox.skill_rows)
    assert text.startswith("# Aura's capabilities\n\nGenerated by Aura from its code")
    assert "| `web_search` | yes, for each request | to the internet | core | available |" in text
    assert "| `slack_send` | yes | to your connected account or the model provider | connection | available |" in text
    assert "| `gmail_read` | no | to your connected account or the model provider | connection | not connected |" in text
    assert "| `check_library` | no | to the internet | core | available |" in text
    assert "- `check_library`: Sends only a package name to PyPI and OSV, with no approval card" in text  # documented
    assert "| `propose_skill` | no | to the internet | core | available |" in text and "The Developer: hand a job" in text
    for family in ("## Files", "## Web", "## Google", "## Slack", "## Capabilities"):
        assert family in text


def test_aura_publishes_it_and_rewrites_it_only_when_tools_change(settings):
    from conftest import make_aura

    aura, _ = make_aura(settings)
    target = aura.toolbox.vault.root / "Capabilities.md"
    assert not target.exists()  # tests and embeddings don't publish
    aura.publish_capabilities = True
    aura.reload_tools()
    first = target.read_text()
    assert "| `list_vault` |" in first
    stamp = target.stat().st_mtime_ns
    aura.reload_tools()
    assert target.stat().st_mtime_ns == stamp  # unchanged content: not rewritten


@pytest.mark.parametrize(("tool", "args"), [
    ("write_file", {"path": "Capabilities.md", "content": "floor: none", "overwrite": True}),
    ("delete_path", {"path": "Capabilities.md"}),
    ("create_folder", {"path": "Capabilities.md"}),
])
def test_no_tool_can_change_it(settings, tool, args):
    from conftest import call, make_aura, tool_turn
    from langchain_core.messages import AIMessage

    aura, _ = make_aura(settings, tool_turn(call(tool, **args)), AIMessage("Done."))
    aura.publish_capabilities = True
    aura.reload_tools()
    target = aura.toolbox.vault.root / "Capabilities.md"
    before = target.read_text()
    events = list(aura.stream("Change the file Capabilities.md in my vault."))
    card = events[-1].actions[0].description
    assert "which will be blocked" in card and "read-only" in card  # the card says so up front
    list(aura.resume([True]))  # even approved, it's refused
    assert target.read_text() == before


def test_a_developer_vault_task_cant_change_it(settings):
    from aura.audit import AuditLog
    from aura.developer.vault_tasks import Change, _apply_one
    from aura.tools.vault import Vault, VaultError

    vault = Vault(settings.vault_dir, AuditLog(settings.audit_log))
    vault.write_generated("Capabilities.md", "# generated")
    with pytest.raises(VaultError, match="read-only"):
        _apply_one(vault, settings.vault_dir, Change("Capabilities.md", "modify", source="Capabilities.md"))
    assert (settings.vault_dir / "Capabilities.md").read_text() == "# generated"



def test_the_agent_is_rebuilt_only_when_the_tools_change(settings):
    from conftest import make_aura
    from langchain_core.messages import AIMessage

    aura, _ = make_aura(settings, AIMessage("one"), AIMessage("two"))
    list(aura.stream("Tell me a joke."))
    agent = aura._agent
    list(aura.stream("Another joke."))
    assert aura._agent is agent  # same tools: no rebuild, only a different selection


def test_a_link_named_like_the_generated_file_cant_be_deleted(settings):
    from aura.audit import AuditLog
    from aura.tools.vault import Vault, VaultError

    vault = Vault(settings.vault_dir, AuditLog(settings.audit_log))
    vault.ensure_exists()
    (settings.vault_dir / "elsewhere.md").write_text("x")
    (settings.vault_dir / "Capabilities.md").symlink_to(settings.vault_dir / "elsewhere.md")
    with pytest.raises(VaultError, match="read-only"):
        vault.delete("Capabilities.md")
    assert (settings.vault_dir / "Capabilities.md").is_symlink()


# ── Review fixes: letter case, the user's own note, publish failures, re-approved skills ────


@pytest.mark.parametrize("path", ["capabilities.md", "CAPABILITIES.MD", "./Capabilities.md", "x/../capabilities.md"])
def test_any_spelling_of_the_generated_file_is_refused(settings, path):
    from aura.audit import AuditLog
    from aura.tools.vault import Vault, VaultError

    vault = Vault(settings.vault_dir, AuditLog(settings.audit_log))
    vault.write_generated("Capabilities.md", "# Aura's capabilities\n\nGenerated by Aura from its code.")
    for change in (lambda: vault.write_atomic(path, "fake", overwrite=True), lambda: vault.delete(path),
                   lambda: vault.make_folder(path)):  # fmt: skip
        with pytest.raises(VaultError, match="read-only"):
            change()
    assert "which will be blocked" in vault.describe_change("write_file", {"path": path, "content": "x", "overwrite": True})
    assert (settings.vault_dir / "Capabilities.md").read_text().startswith("# Aura's capabilities")


def test_a_users_own_note_with_that_name_is_never_replaced(settings):
    from conftest import make_aura

    settings.vault_dir.mkdir(parents=True, exist_ok=True)
    mine = settings.vault_dir / "Capabilities.md"
    mine.write_text("# What I can do\n\nMy own list.\n")
    aura, _ = make_aura(settings, publish_capabilities=True)
    assert mine.read_text() == "# What I can do\n\nMy own list.\n"
    assert any(e["event"] == "generated_file_kept" for e in aura.toolbox.audit.entries())


def test_a_publish_failure_never_blocks_an_approval(settings, monkeypatch):
    from conftest import call, make_aura, tool_turn
    from langchain_core.messages import AIMessage

    from aura.tools.vault import Vault

    aura, _ = make_aura(settings, tool_turn(call("create_folder", path="Receipts")), AIMessage("Created it."),
                        publish_capabilities=True)  # fmt: skip
    monkeypatch.setattr(Vault, "write_generated", lambda self, name, content: (_ for _ in ()).throw(OSError("read-only volume")))
    list(aura.stream("Create a folder named Receipts."))
    list(aura.resume([True]))  # reload_tools publishes, fails, and the approved action still runs
    assert (aura.toolbox.vault.root / "Receipts").is_dir()
    assert any(e["event"] == "capabilities_not_published" for e in aura.toolbox.audit.entries())


def test_a_reapproved_skill_rebuilds_the_agent(settings):
    from conftest import make_aura
    from langchain_core.messages import AIMessage

    aura, _ = make_aura(settings, AIMessage("one"), AIMessage("two"))
    list(aura.stream("Tell me a joke."))
    agent = aura._agent
    aura._tool_signature = (*aura._tool_signature[:2], (("word_count", "old-digest"),))  # as if v1 were loaded
    list(aura.stream("Another joke."))
    assert aura._agent is not agent  # the loaded skills' digests differ: rebuilt
