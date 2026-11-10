#!/usr/bin/env python3
"""Log every subagent run, all projects (hivemind, 2026-11-10; Yorgos: "a global ... hook to track every subagent
that spawns, its name, project, model, effort, tokens, actions").

Events:
  PreToolUse (Agent|Task) -> a pending spawn: type, name, description, requested model + effort, project.
  SubagentStart           -> agent_id bound to the oldest pending spawn of that type in the session.
  SubagentStop            -> one line in runs.jsonl: the spawn facts + what the transcript shows
                             (models used, tokens, turns, tool calls per tool, duration).
runs.jsonl never holds prompts. Prompts + final reports (masked) go ONLY to runs.db (SQLite, FTS5, chmod 600;
never to git, the notepad or any server) - Yorgos 2026-11-10: "for prompts, i wanna save those too" ... "yes".
Always exits 0: logging must never block or slow a session.
Log: ~/dev_dump/hivemind/agent-runs/runs.jsonl + runs.db  (pending state: .../pending/)
"""
import glob
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, "dev_dump", "hivemind", "agent-runs")
PEND = os.path.join(DIR, "pending")
LOG = os.path.join(DIR, "runs.jsonl")
DB = os.path.join(DIR, "runs.db")
PROMPT_MAX, REPORT_MAX = 50000, 20000
SECRET_RX = [
    re.compile(r"\b(ATATT[\w=-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[\w-]{10,}"
               r"|AKIA[0-9A-Z]{16}|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,})"),
    # generic: 32+ chars of base64/url-safe text mixing upper, lower and digits (not paths, not hex SHAs)
    re.compile(r"(?<![\w/.-])(?=[A-Za-z0-9+=_-]*[A-Z])(?=[A-Za-z0-9+=_-]*[a-z])(?=[A-Za-z0-9+=_-]*\d)[A-Za-z0-9+=_-]{32,}"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|bw_session)\b(\s*[:=]\s*)\S+"),
]


def mask(text, n):
    t = str(text or "")[:n]
    t = SECRET_RX[0].sub("<MASKED>", t)
    t = SECRET_RX[1].sub("<MASKED>", t)
    return SECRET_RX[2].sub(lambda m: m.group(1) + m.group(2) + "<MASKED>", t)


def private_open(path, mode="w"):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | (os.O_APPEND if "a" in mode else os.O_TRUNC), 0o600)
    return os.fdopen(fd, mode)


def db_write(rec, prompt, report):
    import sqlite3
    new = not os.path.exists(DB)
    con = sqlite3.connect(DB, timeout=5)
    if new:
        os.chmod(DB, 0o600)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS runs (agent_id TEXT, session TEXT, project TEXT, agent_type TEXT, name TEXT,
        description TEXT, model TEXT, effort TEXT, spawned TEXT, stopped TEXT, secs INTEGER, turns INTEGER, actions INTEGER,
        tok_in INTEGER, tok_out INTEGER, tok_cache_read INTEGER, tok_cache_write INTEGER, tools TEXT, prompt TEXT, report TEXT)""")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS runs_fts USING fts5(agent_id UNINDEXED, description, prompt, report)")
    t = rec.get("tokens") or {}
    model = ",".join(sorted((rec.get("models") or {}).keys())) or rec.get("model_req") or rec.get("model_pinned") or ""
    row = (rec.get("agent_id"), rec.get("session"), rec.get("project"), rec.get("agent_type"), rec.get("name"),
           rec.get("description"), model, rec.get("effort"), rec.get("spawned"), rec.get("stopped"), rec.get("secs"),
           rec.get("turns"), rec.get("actions"), t.get("in"), t.get("out"), t.get("cache_read"), t.get("cache_write"),
           json.dumps(rec.get("tools") or {}), prompt, report)
    with con:  # one row per agent: a resumed agent stops again with cumulative totals, so replace
        old = con.execute("SELECT prompt FROM runs WHERE agent_id=?", (rec.get("agent_id"),)).fetchone()
        if old:
            prompt = prompt or old[0]
            con.execute("DELETE FROM runs WHERE agent_id=?", (rec.get("agent_id"),))
            con.execute("DELETE FROM runs_fts WHERE agent_id=?", (rec.get("agent_id"),))
            row = row[:-2] + (prompt, report)
        con.execute("INSERT INTO runs VALUES (" + ",".join("?" * len(row)) + ")", row)
        con.execute("INSERT INTO runs_fts VALUES (?,?,?,?)", (rec.get("agent_id"), rec.get("description"), prompt, report))
    con.close()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def short(s, n):
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(s or ""))
    return s[:n]


def pinned(agent_type, cwd):
    """(model, effort) from the agent file: project .claude/agents, else ~/.claude/agents."""
    for d in (os.path.join(cwd or "", ".claude", "agents"), os.path.join(HOME, ".claude", "agents")):
        f = os.path.join(d, f"{agent_type}.md")
        try:
            fm = open(f).read().split("---")[1]
        except (OSError, IndexError):
            continue
        m = re.search(r"(?m)^model:\s*(\S+)", fm)
        e = re.search(r"(?m)^effort:\s*(\S+)", fm)
        return (m.group(1) if m else "inherit"), (e.group(1) if e else "")
    return "", ""


def transcript_stats(path):
    st = {"models": {}, "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0},
          "turns": 0, "tools": {}, "first": None, "last": None, "report": "", "last_text": ""}
    seen = set()
    try:
        fh = open(path)
    except (OSError, TypeError):
        return None
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ts = r.get("timestamp")
            if ts:
                st["first"] = st["first"] or ts
                st["last"] = ts
            msg = r.get("message") if isinstance(r.get("message"), dict) else None
            if r.get("type") != "assistant" or not msg:
                continue
            mid = msg.get("id") or id(r)
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                    st["last_text"] = c["text"]
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "SubagentHandback":
                    inp = c.get("input") or {}
                    st["report"] = next((v for v in inp.values() if isinstance(v, str) and len(v) > 20), "") or json.dumps(inp)
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    key = c.get("id") or (mid, c.get("name"))
                    if key not in seen:
                        seen.add(key)
                        st["tools"][c.get("name", "?")] = st["tools"].get(c.get("name", "?"), 0) + 1
            if ("u", mid) in seen:  # streamed message repeated over several lines: count usage once
                continue
            seen.add(("u", mid))
            st["turns"] += 1
            model = msg.get("model") or "?"
            st["models"][model] = st["models"].get(model, 0) + 1
            u = msg.get("usage") or {}
            st["tokens"]["in"] += u.get("input_tokens") or 0
            st["tokens"]["out"] += u.get("output_tokens") or 0
            st["tokens"]["cache_read"] += u.get("cache_read_input_tokens") or 0
            st["tokens"]["cache_write"] += u.get("cache_creation_input_tokens") or 0
    return st


def secs(a, b):
    try:
        f = lambda s: time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
        return int(f(b) - f(a))
    except Exception:
        return None


def pending_for(session, agent_type, agent_id=None):
    files = sorted(glob.glob(os.path.join(PEND, f"{session}--*.json")))
    best = None
    for f in files:
        try:
            p = json.load(open(f))
        except (OSError, ValueError):
            continue
        if agent_id and p.get("agent_id") == agent_id:
            return f, p
        if best is None and p.get("agent_type") == agent_type and not p.get("agent_id"):
            best = (f, p)
    return best or (None, None)


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    ev = d.get("hook_event_name", "")
    session = short(d.get("session_id"), 64) or "nosession"
    cwd = d.get("cwd") or ""
    os.makedirs(PEND, mode=0o700, exist_ok=True)

    if ev == "PreToolUse" and d.get("tool_name") in ("Agent", "Task"):
        i = d.get("tool_input") or {}
        t = short(i.get("subagent_type") or "general-purpose", 60)
        pm, pe = pinned(t, cwd)
        rec = {"spawned": now(), "session": session, "project": cwd.replace(HOME, "~"),
               "parent_agent": short(d.get("agent_type"), 60) or None,
               "agent_type": t, "name": short(i.get("name"), 60) or None,
               "description": short(i.get("description"), 100),
               "model_req": short(i.get("model"), 30) or None, "model_pinned": pm or None,
               "effort": short(i.get("effort"), 10) or pe or None,
               "effort_src": "param" if i.get("effort") else ("pinned" if pe else "inherit"),
               "background": bool(i.get("run_in_background")), "isolation": short(i.get("isolation"), 20) or None,
               "_prompt": mask(i.get("prompt"), PROMPT_MAX)}
        f = os.path.join(PEND, f"{session}--{time.time():.6f}.json")
        with private_open(f) as out:
            json.dump(rec, out)
        return

    if ev == "SubagentStart":
        f, p = pending_for(session, d.get("agent_type"))
        if f:
            p["agent_id"] = d.get("agent_id")
            p["started"] = now()
            with private_open(f) as out:
                json.dump(p, out)
        return

    if ev == "SubagentStop":
        aid, atype = d.get("agent_id"), d.get("agent_type") or "?"
        f, p = pending_for(session, atype, aid)
        rec = dict(p or {"session": session, "project": cwd.replace(HOME, "~"), "agent_type": atype,
                         "note": "no spawn record (spawned before the hook, or by a tool other than Agent)"})
        rec["agent_id"] = aid
        rec["stopped"] = now()
        if not rec.get("effort"):
            pm, pe = pinned(atype, cwd)
            rec["model_pinned"] = rec.get("model_pinned") or pm or None
            rec["effort"], rec["effort_src"] = (pe, "pinned") if pe else (None, "inherit")
        st = transcript_stats(d.get("agent_transcript_path"))
        if st:
            rec.update({"models": st["models"], "tokens": st["tokens"], "turns": st["turns"], "tools": st["tools"],
                        "actions": sum(st["tools"].values()), "secs": secs(st["first"], st["last"])})
        else:
            rec["note"] = (rec.get("note", "") + " no transcript").strip()
        prompt = rec.pop("_prompt", "")
        report = mask((st or {}).get("report") or (st or {}).get("last_text"), REPORT_MAX)
        with open(LOG, "a") as out:
            out.write(json.dumps(rec, sort_keys=True) + "\n")
        try:
            db_write(rec, prompt, report)
        except Exception as e:
            open(os.path.join(DIR, "errors.log"), "a").write(f"{now()} db {type(e).__name__}: {e}\n")
        if f:
            try:
                os.remove(f)
            except OSError:
                pass
        # Drop pending spawns older than 2 days (agents that never stopped).
        cutoff = time.time() - 2 * 86400
        for g in glob.glob(os.path.join(PEND, "*.json")):
            try:
                if os.path.getmtime(g) < cutoff:
                    os.remove(g)
            except OSError:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break a session; leave a trace instead
        try:
            os.makedirs(DIR, exist_ok=True)
            open(os.path.join(DIR, "errors.log"), "a").write(f"{now()} {type(e).__name__}: {e}\n")
        except Exception:
            pass
    sys.exit(0)
