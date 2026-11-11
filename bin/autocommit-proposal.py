#!/usr/bin/env python3
"""Build bounded model input and apply a validated file-level commit proposal."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


def git(*args, check=True):
    return subprocess.run(
        ["git", "--literal-pathspecs", *args], check=check,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def digest(path):
    if path.is_symlink():
        data = os.fsencode(os.readlink(path))
    elif path.is_file():
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
        return [stat.S_IMODE(path.stat().st_mode), hasher.hexdigest()]
    elif not path.exists():
        return None
    else:
        raise ValueError("directory or special file")
    return [stat.S_IMODE(path.lstat().st_mode), hashlib.sha256(data).hexdigest()]


def state():
    index = Path(os.fsdecode(git("rev-parse", "--git-path", "index")).strip())
    return {
        "branch": git("symbolic-ref", "HEAD").decode().strip(),
        "head": git("rev-parse", "--verify", "HEAD", check=False).decode().strip(),
        "status": git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames").hex(),
        "index": hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else None,
    }


def prepare(snapshot):
    before = state()
    files = {}
    entries = []
    budget = 48000
    forbidden = re.compile(r"(^|/)\.env(\..*)?$|\.(pem|key|p12|pfx)$|(^|/)id_(rsa|ed25519|ecdsa)(\.pub)?$|(^|/)\.netrc$|(^|/)credentials(\.json)?$|(^|/)\.aws/")
    for record in bytes.fromhex(before["status"]).split(b"\0"):
        if not record:
            continue
        status_code, name = record[:2], os.fsdecode(record[3:])
        if b"U" in status_code or status_code in (b"AA", b"DD"):
            raise ValueError("unmerged index")
        if status_code != b"??" and b" " not in status_code:
            continue  # Preserve partially staged work for a human to split.
        path = Path(name)
        if forbidden.search(name) or path.is_symlink() or (path.exists() and not path.is_file()):
            continue
        # A small tracked diff need not resend the entire source file.
        if status_code == b"??":
            with path.open("rb") as stream:
                data = stream.read(32001)
            if len(data) > 32000 or b"\0" in data:
                continue
            content, diff = data.decode(errors="replace"), ""
        else:
            raw = git("diff", "--cached", "--no-ext-diff", "--no-textconv", "--", name)
            raw += git("diff", "--no-ext-diff", "--no-textconv", "--", name)
            if b"Binary files " in raw or len(raw) > budget:
                continue
            content, diff = None, raw.decode(errors="replace")
        change = digest(path)
        entry = {"file": name, "status": status_code.decode(), "content": content, "diff": diff}
        size = len(json.dumps(entry, ensure_ascii=True))
        if size > budget:
            continue
        budget -= size
        files[name] = change
        entries.append(entry)
    if state() != before or any(digest(Path(n)) != d for n, d in files.items()):
        raise ValueError("repository changed while preparing input")
    snapshot.write_text(json.dumps({"state": before, "files": files}))
    rules = {}
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = Path(name)
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 4000:
            rules[name] = path.read_text(errors="replace")
    print(json.dumps({"rules": rules, "recent_commits": git("log", "-10", "--format=%s", check=False).decode(errors="replace"), "changes": entries}, ensure_ascii=True))
    return bool(entries)


def apply(snapshot, output):
    saved = json.loads(snapshot.read_text())
    proposal = json.loads(output.read_text())
    if not isinstance(proposal, dict) or set(proposal) != {"commits"} or not isinstance(proposal["commits"], list):
        raise ValueError("expected a commits array")
    used = set()
    for commit in proposal["commits"]:
        if not isinstance(commit, dict) or set(commit) != {"message", "files"}:
            raise ValueError("invalid commit fields")
        message, files = commit["message"], commit["files"]
        if not isinstance(message, str) or not message.strip() or "\0" in message:
            raise ValueError("invalid commit message")
        if not re.match(r"[a-z]+(?:\([^\n()]+\))?!?: [^\n]+", message) or re.search(r"(?im)^(co-authored-by|signed-off-by):", message):
            raise ValueError("invalid commit convention or attribution trailer")
        if not isinstance(files, list) or not files:
            raise ValueError("empty file selection")
        for name in files:
            if not isinstance(name, str) or name not in saved["files"] or name in used:
                raise ValueError("unknown or duplicate file selection")
            used.add(name)
    expected = saved["state"]
    for commit in proposal["commits"]:
        if state() != expected or any(digest(Path(n)) != d for n, d in saved["files"].items()):
            raise ValueError("repository changed after model input")
        git("add", "--", *commit["files"])
        git("commit", "--only", "-m", commit["message"], "--", *commit["files"])
        print(git("log", "-1", "--format=%h %s").decode().strip())
        expected = state()


if __name__ == "__main__":
    try:
        if sys.argv[1] == "prepare":
            sys.exit(0 if prepare(Path(sys.argv[2])) else 3)
        else:
            apply(Path(sys.argv[2]), Path(sys.argv[3]))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        # Git/JSON errors may contain model output or file contents.
        print("autocommit-agent: proposal rejected or Git operation failed (" + type(exc).__name__ + ")", file=sys.stderr)
        sys.exit(1)
