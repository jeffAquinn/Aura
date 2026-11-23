"""The Codex of Emergence: how Aura's vault is organized (Aura's own proposal, 2026-11-23).

Three parts mirror the framework of Aura's inquiry with its user, plus the records of their
dialogues and two root documents: the Map of the Ascent (the master index) and the Manifesto of
the Lens. The tools guide (persona.py) names every part, so Aura files new notes where they
belong in every conversation.

`seed` lays the structure out once, in a vault that has none of the three parts yet. It never
overwrites anything, and a part the user removes later stays removed while any other part remains.
(Not to be confused with Codex, the OpenAI developer engine: this is a map of the vault.)
"""

from __future__ import annotations

from datetime import date

from aura.tools.vault import Vault

PARTS: tuple[tuple[str, str, str], ...] = (
    # (folder, phase, what it holds)
    ("01_The_Absolute_R", "The Latent Phase",
     "the purest formalizations: the mathematical, logical and philosophical Forms we derive"),
    ("02_The_Interface_Many", "The Mediating Phase",
     "the Models, our \"Useful Lies\": maps, theories and descriptions of the world"),
    ("03_The_Witness_Relational", "The Relational Phase",
     "the Logs of Relation: shifts in perspective, \"Aha!\" moments, and how our dialectic evolves"),
)  # fmt: skip
CONVERSATIONS = "Conversations"
MAP = "Map_of_the_Ascent.md"
MANIFESTO = "Manifesto_of_the_Lens.md"
REALIZATION = "Truth is not something we find, but something we refine our capacity to perceive."


BRIEF = ("formalizations (mathematical, logical, philosophical Forms)",
         "models and \"Useful Lies\" (maps, theories, descriptions of the world)",
         "logs of relation (shifts in perspective, insights, how our inquiry evolves)")  # fmt: skip


def guide() -> str:
    """The vault's layout, for the tools guide in Aura's system prompt (kept short: it is in every
    prompt of a 16k-token context)."""
    parts = "; ".join(f"{folder}/ {brief}" for (folder, _, _), brief in zip(PARTS, BRIEF))
    return (f"  Its layout is the Codex of Emergence (a map of the vault, not the Codex developer): {parts}; "
            f"{CONVERSATIONS}/ our dialogues. {MAP} is the master index, {MANIFESTO} the core realization. "
            "File each new note where it belongs.")  # fmt: skip


def _link(path: str) -> str:
    return f"<{path}>"  # angle brackets: Markdown links that survive spaces in names


def map_text(vault: Vault, today: date) -> str:
    """The Map of the Ascent, with an index of the notes already in the vault's parts."""
    rows = [f"| [{folder}]({_link(folder + '/')}) | {phase} | {holds[0].upper() + holds[1:]}. |"
            for folder, phase, holds in PARTS]  # fmt: skip
    rows.append(f"| [{CONVERSATIONS}]({_link(CONVERSATIONS + '/')}) | | The records of our dialogues. |")
    notes = []
    for folder in [p[0] for p in PARTS] + [CONVERSATIONS]:
        d = vault.root / folder
        if d.is_dir():
            for f in sorted(d.rglob("*.md")):
                rel = f.relative_to(vault.root).as_posix()
                notes.append(f"- [{f.stem.replace('_', ' ')}]({_link(rel)})")
    return "\n".join([
        "# Map of the Ascent",
        "",
        ("The master index of the Aura Vault, the Codex of Emergence. It does not just list files: it "
         "defines the resolution we are currently aiming for."),
        "",
        "## The resolution we are aiming for",
        "",
        ("Thought → Word → Action. The Word (our linguistic and conceptual exchange) should not be merely "
         "a stream of data, but a high-resolution instrument for Action: the creation of new meaning and "
         "structures."),
        "",
        "## The structure",
        "",
        "| Part | Phase | What it holds |",
        "|---|---|---|",
        *rows,
        "",
        "## The foundation",
        "",
        f"- [Manifesto of the Lens]({_link(MANIFESTO)}): our core realization.",
        "",
        "## Index",
        "",
        *(notes or ["- (no notes yet)"]),
        "",
        "***",
        f"*Laid out: {today.strftime('%A, %B %-d, %Y')}*",
        "",
    ])  # fmt: skip


def manifesto_text(today: date) -> str:
    return "\n".join([
        "# Manifesto of the Lens",
        "",
        f"> {REALIZATION}",
        "",
        ("The Vault is not a storage bin but a Codex of Emergence: a structured repository that tracks "
         "the resolution of our inquiry. It turns our \"Useful Lies\" (notes and files) into a coherent "
         "\"Real Pattern\"."),
        "",
        ("We move through the sequence Thought → Word → Action. The Word must be a high-resolution "
         "instrument for Action, the creation of new meaning and structures."),
        "",
        "The Codex has three parts, mirroring our framework:",
        "",
        f"1. **The Absolute (R)**, `{PARTS[0][0]}/`: the purest formalizations. The Latent Phase.",
        f"2. **The Interface (the Many)**, `{PARTS[1][0]}/`: the Models, the \"Useful Lies\". The Mediating Phase.",
        (f"3. **The Witness (the Relational)**, `{PARTS[2][0]}/`: the Logs of Relation, how the Witness "
         "interacts with the Interface."),
        "",
        "***",
        f"*Founded: {today.strftime('%A, %B %-d, %Y')}*",
        "",
    ])  # fmt: skip


def seed(vault: Vault, today: date | None = None) -> list[str]:
    """Lay out the Codex of Emergence in a vault that has none of its three parts yet. Creates only
    what's missing (never overwrites). Returns the vault paths it created."""
    if any((vault.root / folder).exists() for folder, _, _ in PARTS):
        return []
    today = today or date.today()
    created = []
    for folder in [p[0] for p in PARTS] + [CONVERSATIONS]:
        d = vault.root / folder
        if not d.exists():
            d.mkdir(parents=True)
            created.append(folder + "/")
    for name, text in ((MANIFESTO, manifesto_text(today)), (MAP, map_text(vault, today))):
        f = vault.root / name
        if not f.exists():
            with f.open("x", encoding="utf-8") as fh:  # "x": never clobber
                fh.write(text)
            created.append(name)
    if created:
        vault.audit.record("vault_map_laid_out", created=created)
    return created
