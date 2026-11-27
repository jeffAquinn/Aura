"""Check that a change to a vault file did exactly what was intended, and nothing else.

Read-only (docs/specs/vault-integrity.md in the vault). Aura runs it twice around a write the
user approved: first with no baseline, to fingerprint the file and the other files in its folder;
then with that baseline, to compare. Nothing is written, deleted, moved or kept between runs:
the fingerprint goes back to Aura in the output and comes back in as `baseline`.
"""

import hashlib
import json
from pathlib import Path

VAULT = Path("/vault")
MAX_FILES = 500
MAX_CONTENT = 200_000  # characters of expected_content
CHUNK = 1 << 20


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve(vault: Path, rel: str) -> tuple[Path | None, str]:
    """The target under the vault, or None and why not. No escaping it, no symbolic links."""
    rel = rel.strip().lstrip("/")
    if not rel:
        return None, "Give the vault-relative path of a file."
    root = vault.resolve()
    target = root / rel
    for part in [target, *target.parents]:
        if part == root:
            break
        if part.is_symlink():
            return None, "That path goes through a symbolic link, which this check doesn't follow."
    real = target.resolve()
    if real != root and root not in real.parents:
        return None, "That path is outside the vault."
    if real.exists() and not real.is_file():
        return None, "That path is a folder; give a file."
    return real, ""


def fingerprint(folder: Path) -> tuple[dict[str, dict], bool]:
    """SHA-256 and size of each regular, non-hidden file directly in `folder` (at most MAX_FILES)."""
    files: dict[str, dict] = {}
    names = sorted(p for p in folder.iterdir() if not p.name.startswith("."))
    for p in names:
        if len(files) >= MAX_FILES:
            return files, True
        if p.is_file() and not p.is_symlink():
            files[p.name] = {"sha256": sha256(p), "size": p.stat().st_size}
    return files, False


def run(args: dict, vault: Path = VAULT) -> str:
    target, problem = resolve(vault, str(args.get("path", "")))
    if target is None:
        return json.dumps({"status": "unsafe", "message": problem})
    root = vault.resolve()
    folder = target.parent
    folder_rel = "." if folder == root else str(folder.relative_to(root))
    target_rel = str(target.relative_to(root))
    if not folder.is_dir():
        return json.dumps({"status": "unsafe", "message": f"There's no folder '{folder_rel}' in the vault."})
    files, cut = fingerprint(folder)
    present = files.get(target.name)
    target_info = {"path": target_rel, **present} if present else None
    note = f" Only the first {MAX_FILES} files in the folder were checked." if cut else ""

    raw_baseline = str(args.get("baseline", "") or "").strip()
    if not raw_baseline:
        return json.dumps({
            "mode": "fingerprint", "status": "fingerprinted", "target": target_info,
            "baseline": {"folder": folder_rel, "files": files},
            "message": f"Fingerprinted {len(files)} file(s) in '{folder_rel}'"
                       f"{'' if present else f', where {target_rel} does not exist yet'}.{note}",
        })

    try:
        baseline = json.loads(raw_baseline)
        before = baseline["files"]
        assert isinstance(before, dict) and isinstance(baseline["folder"], str)
    except (ValueError, KeyError, TypeError, AssertionError):
        return json.dumps({"mode": "verify", "status": "unsafe",
                           "message": "The baseline isn't a fingerprint from this skill; take a new one."})
    if baseline["folder"] != folder_rel:
        return json.dumps({"mode": "verify", "status": "unsafe",
                           "message": f"The baseline is for '{baseline['folder']}', not '{folder_rel}'; take a new one."})

    side_effects = []
    for name in sorted(set(before) | set(files)):
        if name == target.name:
            continue
        if name not in before:
            side_effects.append({"file": name, "change": "added"})
        elif name not in files:
            side_effects.append({"file": name, "change": "removed"})
        elif (files[name].get("sha256"), files[name].get("size")) != (before[name].get("sha256"), before[name].get("size")):
            side_effects.append({"file": name, "change": "changed"})

    expected = str(args.get("expected_sha256", "") or "").strip().lower()
    content = str(args.get("expected_content", "") or "")
    if not expected and content:
        if len(content) > MAX_CONTENT:
            return json.dumps({"mode": "verify", "status": "unsafe",
                               "message": f"expected_content is over {MAX_CONTENT} characters; give expected_sha256 instead."})
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    integrity = None if not expected else bool(present) and present["sha256"] == expected

    safe = not side_effects and integrity is not False
    parts = []
    if integrity is True:
        parts.append(f"{target_rel} holds exactly the expected content.")
    elif integrity is False:
        parts.append(f"{target_rel} does not hold the expected content." if present else f"{target_rel} doesn't exist.")
    else:
        parts.append(f"{target_rel} was compared with the baseline only (no expected content given).")
    if side_effects:
        listed = ", ".join(f"{s['file']} ({s['change']})" for s in side_effects[:10])
        more = f" and {len(side_effects) - 10} more" if len(side_effects) > 10 else ""
        parts.append(f"Other files in '{folder_rel}' changed: {listed}{more}.")
    else:
        parts.append(f"No other file in '{folder_rel}' changed.")
    return json.dumps({
        "mode": "verify", "status": "safe" if safe else "unsafe", "target": target_info,
        "integrity_check": integrity, "side_effects": side_effects, "message": " ".join(parts) + note,
    })
