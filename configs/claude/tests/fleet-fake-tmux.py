#!/usr/bin/env python3
"""Fake tmux for fleet.test.sh: a JSON state file, every call logged. Not a tmux: only what fleet uses.
State: $FAKE_DIR/tmux.json = {"sessions": {name: {"opts": {}, "windows": [{"id","name","opts","panes":[{"id","cmd","pid"}]}]}},
"clients": [{"name","session","activity"}], "next": 100}. Pane text for capture-pane: $FAKE_DIR/capture.<number>.
A file $FAKE_DIR/exit_on_enter.<number> holding a pid: that process is killed when Enter is sent to the pane."""
import json
import os
import re
import signal
import sys

D = os.environ["FAKE_DIR"]
P = os.path.join(D, "tmux.json")
args = sys.argv[1:]
with open(os.path.join(D, "tmux.log"), "a") as f:
    f.write("\t".join(args) + "\n")
try:
    st = json.load(open(P))
except OSError:
    st = {"sessions": {}, "clients": [], "next": 100}


def save():
    json.dump(st, open(P, "w"))


def nid(prefix):
    st["next"] += 1
    return "%s%d" % (prefix, st["next"])


def opt(rest, flag):
    """value of a flag like -t, or None"""
    return rest[rest.index(flag) + 1] if flag in rest and rest.index(flag) + 1 < len(rest) else None


def sess_name(t):
    t = t or ""
    t = t.lstrip("=")
    return t.split(":")[0]


def panes(session=None):
    for sn, s in st["sessions"].items():
        if session and sn != session:
            continue
        for w in s["windows"]:
            for p in w["panes"]:
                yield sn, s, w, p


def expand(fmt, ctx):
    return re.sub(r"#\{([^}]*)\}", lambda m: str(ctx.get(m.group(1), "")), fmt)


def pane_ctx(sn, s, w, p):
    c = {"pane_id": p["id"], "pane_pid": p.get("shellpid", 0), "pane_current_command": p["cmd"], "window_id": w["id"],
         "window_name": w["name"], "session_name": sn, "pane_current_path": p.get("cwd") or "",
         "window_index": str(s["windows"].index(w) + 1), "pane_index": str(w["panes"].index(p) + 1)}
    for k, v in s["opts"].items():
        c[k] = v
    for k, v in w["opts"].items():
        c[k] = v
    for k, v in p.get("opts", {}).items():
        c[k] = v
    return c


def find_pane(t):
    for sn, s, w, p in panes():
        if p["id"] == t:
            return sn, s, w, p
    return None


cmd = args[0] if args else ""
rest = args[1:]
rc = 0
if cmd == "list-panes":
    fmt = opt(rest, "-F") or "#{pane_id}"
    sess = sess_name(opt(rest, "-t")) if "-t" in rest else None
    if "-t" in rest and sess not in st["sessions"]:
        sys.exit(1)
    for sn, s, w, p in panes(sess):
        print(expand(fmt, pane_ctx(sn, s, w, p)))
elif cmd == "capture-pane":
    pane = opt(rest, "-t") or ""
    fp = os.path.join(D, "capture.%s" % pane.lstrip("%"))
    if os.path.exists(fp):
        sys.stdout.write(open(fp).read())
    else:
        rc = 1
elif cmd == "send-keys":
    pane = opt(rest, "-t") or ""
    if "Enter" in rest:
        fp = os.path.join(D, "exit_on_enter.%s" % pane.lstrip("%"))
        if os.path.exists(fp):
            try:
                os.kill(int(open(fp).read().strip()), signal.SIGKILL)
            except OSError:
                pass
            os.unlink(fp)
elif cmd == "has-session":
    rc = 0 if sess_name(opt(rest, "-t")) in st["sessions"] else 1
elif cmd in ("new-session", "new-window"):
    name = opt(rest, "-n")
    cwd = opt(rest, "-c")
    fmt = opt(rest, "-F")
    command = rest[-1] if rest and not rest[-1].startswith("-") and rest[-1] != name and rest[-1] != cwd and rest[-1] != fmt else ""
    win = {"id": nid("@"), "name": name or "w", "opts": {}, "panes": [{"id": nid("%"), "cmd": "python3" if command else "zsh", "start": command, "cwd": cwd}]}
    if cmd == "new-session":
        sn = opt(rest, "-s")
        if sn in st["sessions"]:
            sys.exit(1)
        st["sessions"][sn] = {"opts": {}, "windows": [win]}
    else:
        sn = sess_name(opt(rest, "-t"))
        if sn not in st["sessions"]:
            sys.exit(1)
        st["sessions"][sn]["windows"].append(win)
    if "-P" in rest:
        print(expand(fmt or "#{window_id}", {"window_id": win["id"], "pane_id": win["panes"][0]["id"]}))
    save()
elif cmd == "set-option":
    t = opt(rest, "-t") or ""
    key = rest[-1] if "-u" in rest else rest[-2]
    val = None if "-u" in rest else rest[-1]
    if "-p" in rest and t.startswith("%"):
        for sn, s, w, p in panes():
            if p["id"] == t:
                p.setdefault("opts", {})
                if val is None:
                    p["opts"].pop(key, None)
                else:
                    p["opts"][key] = val
    elif t.startswith("@") or t.startswith("%"):
        for sn, s, w, p in panes():
            if w["id"] == t or p["id"] == t:
                if val is None:
                    w["opts"].pop(key, None)
                else:
                    w["opts"][key] = val
    else:
        sess = st["sessions"].get(sess_name(t))
        if sess is not None:
            if val is None:
                sess["opts"].pop(key, None)
            else:
                sess["opts"][key] = val
    save()
elif cmd == "show-options":
    sn = sess_name(opt(rest, "-t"))
    print(st["sessions"].get(sn, {"opts": {}})["opts"].get(rest[-1], ""))
elif cmd == "respawn-pane":
    r = find_pane(opt(rest, "-t") or "")
    if r:
        r[3]["cmd"] = "python3"
        r[3]["start"] = rest[-1]
        save()
    else:
        rc = 1
elif cmd == "kill-window":
    t = opt(rest, "-t")
    for sn, s in st["sessions"].items():
        s["windows"] = [w for w in s["windows"] if w["id"] != t]
    st["sessions"] = {k: v for k, v in st["sessions"].items() if v["windows"]}
    save()
elif cmd == "kill-session":
    st["sessions"].pop(sess_name(opt(rest, "-t")), None)
    save()
elif cmd == "switch-client":
    c, t = opt(rest, "-c"), sess_name(opt(rest, "-t"))
    for cl in st["clients"]:
        if c is None or cl["name"] == c:
            cl["session"] = t
    save()
elif cmd == "list-clients":
    sess = sess_name(opt(rest, "-t")) if "-t" in rest else None
    fmt = opt(rest, "-F") or "#{client_name}"
    for cl in st["clients"]:
        if sess is None or cl["session"] == sess:
            print(expand(fmt, {"client_name": cl["name"], "session_name": cl["session"], "client_activity": cl.get("activity", 0)}))
elif cmd == "split-window":
    t = opt(rest, "-t")
    fmt = opt(rest, "-F")
    command = rest[-1] if rest and not rest[-1].startswith("-") and rest[-1] not in (t, opt(rest, "-c"), fmt) else ""
    for sn, s, w, p in panes():
        if w["id"] == t:
            np = {"id": nid("%"), "cmd": "python3" if command else "zsh", "start": command, "cwd": opt(rest, "-c"), "shellpid": os.getpid()}
            w["panes"].append(np)
            if "-P" in rest:
                print(expand(fmt or "#{pane_id}", {"window_id": w["id"], "pane_id": np["id"]}))
            break
    else:
        rc = 1
    save()
elif cmd == "select-layout":
    pass
elif cmd == "display-message":
    c = opt(rest, "-c")
    tp = opt(rest, "-t")
    if c is None and tp and tp.startswith("%"):
        r = find_pane(tp)
        print(expand(rest[-1], {"session_name": r[0] if r else ""}))
    elif c is None:
        print(expand(rest[-1], {"start_time": st.get("start", 0)}))
    for cl in st["clients"]:
        if cl["name"] == c:
            print(expand(rest[-1], {"session_name": cl["session"]}))
sys.exit(rc)
