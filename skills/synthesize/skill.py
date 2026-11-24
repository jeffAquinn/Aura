"""Synthesize a scope of vault notes into a structured Markdown report.

Steps: (1) Read and Ingest, (2) Cross-reference, (3) Synthesize.
File contents are untrusted text: they are only tokenized and quoted, never
executed or interpreted as instructions. The skill is read-only and returns
the Markdown string to the host.
"""

import re
from collections import defaultdict
from pathlib import Path

VAULT = Path("/vault")
EXTENSIONS = {".md", ".txt"}
MAX_FILES = 50
MAX_FILE_CHARS = 50_000
MAX_QUOTE = 200
MIN_THEME_NOTES = 3
MAX_SKIP_LISTED = 20

STOPWORDS = frozenset(
    """a an and are as at be but by for from has have he her his i in is it its of on or
    our she so than that the their them then there these they this to was we were what
    when which who will with you your not no never cannot can do does did all any more most
    some only also into about over under been being would should could may might must
    always often just very each other such those how why""".split()
)
NEGATIONS = frozenset(
    "not no never cannot cant dont doesnt isnt arent wont without neither nor false".split()
)
OPPOSITES = [("increase", "decrease"), ("true", "false"), ("always", "never"),
             ("possible", "impossible"), ("real", "illusion"), ("fixed", "fluid"),
             ("one", "many"), ("static", "dynamic")]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower().replace("’", "'"))


def _content(text: str) -> set[str]:
    return {w for w in _words(text) if w not in STOPWORDS and w not in NEGATIONS and len(w) > 3}


def _sentences(text: str) -> list[str]:
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)  # frontmatter
    text = re.sub(r"[#*>`|_]+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if len(p.split()) >= 4]


def _label(s: str) -> str:
    """Single-line, backtick-free rendering of an untrusted path for the report."""
    return re.sub(r"[\r\n`]+", " ", s)


def resolve_scope(scope: str, vault: Path) -> tuple[list[Path], str | None, list[tuple[str, str]]]:
    """Return (files, error, skipped). Scope is one vault-relative directory/file,
    or a comma-separated list of files/directories. Refuses anything outside the vault.
    `skipped` is a list of (path, reason) for items deliberately not read."""
    root = vault.resolve()
    raw = str(scope or "").strip()
    whole = (root / raw.lstrip("/")).resolve()
    parts = [raw] if ("," not in raw or whole.exists()) else [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        parts = [""]
    multi = len(parts) > 1
    files, skipped, seen = [], [], set()
    for part in parts:
        rel = part.lstrip("/")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            return [], "That scope is outside the vault.", []
        if not target.exists():
            if multi:
                skipped.append((rel, "not found"))
                continue
            return [], f"There's nothing at '{rel}'.", []
        candidates = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file())
        for c in candidates:
            r = c.resolve()
            shown = str(c.relative_to(root)) if root in c.parents else str(c)
            if root not in r.parents:
                skipped.append(("(link withheld)", "symlink resolving outside the vault"))
            elif r.suffix.lower() not in EXTENSIONS:
                skipped.append((shown, "unsupported file type"))
            elif r not in seen:
                seen.add(r)
                files.append(r)
    if len(files) > MAX_FILES:
        skipped += [(str(p.relative_to(root)), f"over the {MAX_FILES}-file limit") for p in files[MAX_FILES:]]
        files = files[:MAX_FILES]
    return files, None, skipped


def ingest(files: list[Path], root: Path):
    """Step 1: read each note into a list of sentences, keyed by relative path.
    Returns (notes, skipped, truncated)."""
    notes, skipped, truncated = {}, [], []
    for p in files:
        name = str(p.relative_to(root))
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped.append((name, "unreadable"))
            continue
        if "\x00" in text:
            skipped.append((name, "binary content"))
            continue
        if len(text) > MAX_FILE_CHARS:
            truncated.append((name, len(text)))
            text = text[:MAX_FILE_CHARS]
        notes[name] = _sentences(text)
    return notes, skipped, truncated


def find_themes(notes: dict[str, list[str]], focus: str = "") -> list[tuple[str, list[str]]]:
    """Step 2a: terms that recur across several notes (or, for 1-2 notes, are frequent)."""
    where = defaultdict(set)
    count = defaultdict(int)
    for name, sentences in notes.items():
        for s in sentences:
            for w in _content(s):
                where[w].add(name)
                count[w] += 1
    threshold = min(MIN_THEME_NOTES, max(len(notes), 1))
    themes = [(w, sorted(n)) for w, n in where.items() if len(n) >= threshold and (len(n) > 1 or count[w] >= 3)]
    if focus:
        f = focus.lower()
        themes = [t for t in themes if f in t[0] or t[0] in f] or themes
    themes.sort(key=lambda t: (-len(t[1]), -count[t[0]], t[0]))
    return themes[:10]


def _polarity(s: str) -> bool:
    return any(w in NEGATIONS for w in _words(s))


def find_tensions(notes: dict[str, list[str]], focus: str = "") -> list[tuple[str, str, str, str]]:
    """Step 2b: pairs of sentences from different notes that overlap but disagree."""
    items = [(n, s, _content(s), set(_words(s))) for n, ss in notes.items() for s in ss]
    out, seen = [], set()
    for i, (na, sa, ca, wa) in enumerate(items):
        for nb, sb, cb, wb in items[i + 1:]:
            if na == nb:
                continue
            if focus and focus.lower() not in (sa + sb).lower():
                continue
            shared = ca & cb
            if len(shared) < 2 or len(shared) / max(min(len(ca), len(cb)), 1) < 0.4:
                continue
            opposite = any((x in wa and y in wb) or (y in wa and x in wb) for x, y in OPPOSITES)
            if _polarity(sa) != _polarity(sb) or opposite:
                key = (na, nb, sa, sb)
                if key not in seen:
                    seen.add(key)
                    out.append((na, sa, nb, sb))
    return out[:10]


def _q(s: str) -> str:
    return s if len(s) <= MAX_QUOTE else s[:MAX_QUOTE] + "…"


def _tension_insight(i: int, na: str, sa: str, nb: str, sb: str) -> list[str]:
    """Tie an insight to one specific tension: shared ground, where it splits, how to reconcile."""
    shared = sorted(_content(sa) & _content(sb))[:4]
    wa, wb = set(_words(sa)), set(_words(sb))
    pair = next(((x, y) if x in wa else (y, x) for x, y in OPPOSITES
                 if (x in wa and y in wb) or (y in wa and x in wb)), None)
    topic = ", ".join(f"**{w}**" for w in shared) or "the same subject"
    if pair:
        split = f"they diverge on '{pair[0]}' vs '{pair[1]}'"
        move = (f"test whether '{pair[0]}' and '{pair[1]}' each hold under different conditions "
                f"(scope, scale or time) rather than competing as universal claims")
    else:
        neg_in_a = _polarity(sa)
        split = f"`{na if neg_in_a else nb}` negates what `{nb if neg_in_a else na}` affirms"
        move = "ask what the negation is scoped to; the claim and its denial may apply to different senses of the term"
    return [f"{i}. Both `{na}` and `{nb}` speak about {topic}, but {split}. "
            f"Possible reconciliation: {move}."]


def synthesize(notes, themes, tensions, title: str, focus: str, scope: str,
               skipped=(), truncated=()) -> str:
    """Step 3: assemble the structured Markdown report."""
    title = re.sub(r"[\r\n#\s]+", " ", title).strip() or "Synthesis"
    scope_s = _label(scope or "/")
    lines = [f"# {title}", "",
             f"_Scope: `{scope_s}` · Notes read: {len(notes)}" + (f" · Focus: {_label(focus)}" if focus else "") + "_",
             "", "## Observations", ""]
    if themes:
        for w, names in themes:
            lines.append(f"- **{w}** recurs across {len(names)} note(s): " + ", ".join(f"`{_label(n)}`" for n in names))
    else:
        lines.append("- No repeated themes found.")
    lines += ["", "## Dialectic Tension", ""]
    if tensions:
        for na, sa, nb, sb in tensions:
            lines += [f"- `{_label(na)}`: \"{_q(sa)}\"", f"  vs. `{_label(nb)}`: \"{_q(sb)}\""]
    else:
        lines.append("- No contradictions found.")
    lines += ["", "## Emergent Insight", ""]
    if tensions:
        if themes:
            lines.append(f"The theme **{themes[0][0]}** runs through the notes, yet they pull against each other "
                         f"in {len(tensions)} place(s). Each tension, with a way to reconcile it:")
        else:
            lines.append("The notes disagree without a shared recurring theme. Each tension, with a way to reconcile it:")
        lines.append("")
        for i, (na, sa, nb, sb) in enumerate(tensions[:3], 1):
            lines += _tension_insight(i, _label(na), sa, _label(nb), sb)
        if len(tensions) > 3:
            lines.append(f"({len(tensions) - 3} further tension(s) listed above are not elaborated.)")
    elif themes:
        lines.append(f"The notes consistently return to **{themes[0][0]}** without internal conflict.")
    else:
        lines.append("Not enough material to draw a synthesis.")
    lines += ["", "### Confidence and Limits", "",
              "- Interpretive assumptions: themes are words recurring across notes; tensions are sentence pairs "
              "with overlapping vocabulary but differing negation or opposing word pairs. This is keyword "
              "heuristics, not semantic understanding, so irony, context and paraphrase can be misread.",
              f"- Remaining uncertainty: only the first {MAX_FILE_CHARS} characters of each note and up to "
              f"{MAX_FILES} files are read; at most 10 themes and 10 tensions are shown; "
              "real contradictions phrased without negation or listed opposites may be missed.",
              "", "## Data Integrity", "",
              f"- Files successfully ingested: {len(notes)}"]
    if truncated:
        lines.append(f"- Truncated to {MAX_FILE_CHARS} characters ({len(truncated)}): "
                     + ", ".join(f"`{_label(n)}` ({c} chars)" for n, c in truncated))
    else:
        lines.append("- Truncated files: none")
    if skipped:
        lines.append(f"- Skipped files ({len(skipped)}):")
        lines += [f"  - `{_label(n)}`: {why}" for n, why in list(skipped)[:MAX_SKIP_LISTED]]
        if len(skipped) > MAX_SKIP_LISTED:
            lines.append(f"  - …and {len(skipped) - MAX_SKIP_LISTED} more")
    else:
        lines.append("- Skipped files: none")
    return "\n".join(lines) + "\n"


def run(args: dict, vault: Path = VAULT) -> str:
    scope = str(args.get("scope", "")).strip()
    focus = str(args.get("focus", "") or "").strip()
    title = str(args.get("report_title", "") or "")
    files, error, skipped = resolve_scope(scope, vault)
    if error:
        return error
    notes, skipped2, truncated = ingest(files, vault.resolve())
    skipped += skipped2
    if not notes:
        msg = f"No readable notes found in '{scope}'."
        if skipped:
            msg += f" Skipped {len(skipped)} file(s): " + "; ".join(f"{_label(n)} ({w})" for n, w in skipped[:MAX_SKIP_LISTED])
        return msg
    return synthesize(notes, find_themes(notes, focus), find_tensions(notes, focus), title, focus, scope,
                      skipped, truncated)
