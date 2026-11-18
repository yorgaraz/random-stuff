#!/usr/bin/env python3
"""Checks for fleet. Run through fleet.test.sh. Output style follows notify-user.test.sh."""
import http.client
import importlib.machinery
import importlib.util
import json
import os
import shutil
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, "..", "..", ".."))
FLEET = os.environ.get("FLEET_UNDER_TEST") or os.path.join(ROOT, "configs/claude/bin/fleet")   # (the override is for the mutation audit)
HOOK = os.path.join(ROOT, "configs/claude/hooks/fleet-hook.sh")
NHOOK = os.environ.get("NOTIFY_HOOK_UNDER_TEST") or os.path.join(ROOT, "configs/claude/hooks/notify-hook.sh")
RESURRECT = os.environ.get("RESURRECT_UNDER_TEST") or os.path.join(ROOT, "configs/tmux/claude-resurrect.sh")
FAKE_TMUX = os.path.join(HERE, "fleet-fake-tmux.py")
FAKE_CLAUDE = os.path.join(HERE, "fleet-fake-claude.sh")

PASS, FAIL = 0, 0


def ok():
    global PASS
    PASS += 1


def bad(msg):
    global FAIL
    FAIL += 1
    print("FAIL: %s" % msg, file=sys.stderr)


def check(name, expected, actual):
    if expected == actual:
        ok()
    else:
        bad("%s: expected [%r] got [%r]" % (name, expected, actual))


def truth(name, cond, detail=""):
    if cond:
        ok()
    else:
        bad("%s %s" % (name, detail))


def load_module():
    loader = importlib.machinery.SourceFileLoader("fleet_mod", FLEET)
    spec = importlib.util.spec_from_loader("fleet_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


SID = {n: "%08x-0000-4000-8000-%012x" % (0xA0000000 + n, n) for n in range(1, 40)}
PIDDOMAIN = "linux:00000000000000000000000000000001:pid:[4026531836]"
EMPTY = "log line\n" + "─" * 30 + "\n❯  \n" + "─" * 30 + "\n  status line\n"
DRAFT = "log line\n" + "─" * 30 + "\n❯ half typed text\n" + "─" * 30 + "\n  status line\n"
PLACEHOLDER = "log line\n" + "─" * 30 + "\n❯ \x1b[2mTry something\x1b[0m\n" + "─" * 30 + "\n  status\n"


class Env:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="fleet-test.")
        r = self.root
        self.home = os.path.join(r, "claude")
        for d in ("sessions", "jobs", "statusline-sessions"):
            os.makedirs(os.path.join(self.home, d))
        self.state = os.path.join(r, "state")
        self.cfg = os.path.join(r, "scopes.conf")
        self.bin = os.path.join(r, "bin")
        os.makedirs(self.bin)
        os.symlink(FAKE_TMUX, os.path.join(self.bin, "tmux"))
        os.symlink(FAKE_CLAUDE, os.path.join(self.bin, "claude"))
        with open(os.path.join(self.bin, "notify-user"), "w") as f:
            f.write('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$FAKE_DIR/notify.log"\n')
        os.chmod(os.path.join(self.bin, "notify-user"), 0o755)
        os.makedirs(os.path.join(r, "work"))
        os.makedirs(os.path.join(r, "fakehome"))
        os.makedirs(os.path.join(r, "procbin"))
        shutil.copy("/usr/bin/sleep", os.path.join(r, "procbin", "claude"))   # a process whose comm is "claude"
        self.pids = []
        self.next_pane = 40
        self.tmuxstate = {"sessions": {}, "clients": [], "next": 100}
        self.env = dict(os.environ)
        for k in list(self.env):
            if k.startswith("CLAUDE") or k.startswith("FLEET") or k == "TMUX":
                del self.env[k]
        self.env.update({
            "HOME": os.path.join(r, "fakehome"), "FAKE_DIR": r, "FLEET_STATE_DIR": self.state, "FLEET_CONFIG": self.cfg,
            "FLEET_CLAUDE_HOME": self.home, "FLEET_CLAUDE": os.path.join(self.bin, "claude"),
            "FLEET_TMUX": os.path.join(self.bin, "tmux"), "FLEET_NOTIFY": os.path.join(self.bin, "notify-user"),
            "FLEET_POLL": "0.05", "FLEET_EXIT_TIMEOUT": "1.5", "FLEET_VERIFY_TIMEOUT": "1.5", "FLEET_SETTLE": "1",
            "FLEET_VIEW_WAIT": "1", "FLEET_PORT": "0", "FLEET_RECHECK": "0.05", "FLEET_BRAKE_MAX": "50",
            "FLEET_PIDDOMAIN": PIDDOMAIN, "FLEET_BOOT_ID_FILE": os.path.join(r, "boot_id"),
            "FLEET_BOOT_SETTLE": "0.15", "FLEET_BOOT_SETTLE_MAX": "2", "FLEET_CRASH_WAIT": "0.2"})
        self.set_boot_id("aaaaaaaa-0000-4000-8000-000000000001")
        self.write_tmux()

    def set_boot_id(self, v):
        with open(os.path.join(self.root, "boot_id"), "w") as f:
            f.write(v + "\n")

    def proc_start(self, pid):
        """field 22 of /proc/<pid>/stat: what Claude writes as procStart"""
        try:
            return open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            return ""

    def sfile(self, pid, **d):
        """a session file as real Claude writes it: with procStart and pidDomain"""
        d.setdefault("pid", pid)
        d.setdefault("procStart", self.proc_start(pid))
        d.setdefault("pidDomain", PIDDOMAIN)
        self.jwrite(os.path.join(self.home, "sessions", "%d.json" % pid), d)

    # ---- fixtures
    def spawn(self):
        p = subprocess.Popen(["sleep", "600"])
        self.pids.append(p)
        return p.pid

    def workdir(self, name):
        d = os.path.join(self.root, "work", name)
        os.makedirs(d, exist_ok=True)
        return d

    def config(self, text):
        with open(self.cfg, "w") as f:
            f.write(text)

    def jwrite(self, path, obj):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(obj, f)

    def shell_pid(self):
        """a pane's root process (a plain sleep, no children)"""
        return self.spawn()

    def own_conv(self, sid, lines=None):
        """the session's own conversation file (projects/<slug>/<id>.jsonl) with no assistant record, as every real session
        that has had a turn has one; fleet resumes by it"""
        d = os.path.join(self.home, "projects", "-some-dir-slug")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + ".jsonl"), "w") as f:
            f.write("\n".join(lines or [json.dumps({"type": "user", "sessionId": sid, "message": {"content": "hi"}})]) + "\n")

    def pane_session(self, name, n, status="idle", model="claude-opus-5-5", composer=EMPTY, tmuxsession="work", incl_window=True, effort="high", conv=True):
        """a live interactive session in a (fake) tmux pane: a shell process with a child whose comm is "claude" """
        sh = subprocess.Popen(["bash", "-c", "%s 600; true" % os.path.join(self.root, "procbin", "claude")], start_new_session=True)
        self.pids.append(sh)
        pid = 0
        for _ in range(100):
            kids = subprocess.run(["pgrep", "-P", str(sh.pid)], capture_output=True, text=True).stdout.split()
            if kids:
                pid = int(kids[0])
                break
            time.sleep(0.02)
        pane = self.next_pane
        self.next_pane += 1
        d = self.workdir(name)
        if conv:
            self.own_conv(SID[n])
        self.sfile(pid, sessionId=SID[n], cwd=d, kind="interactive", name=name, status=status,
                   tmux="%s:@1.%%%d" % (tmuxsession, pane))
        if model:
            self.jwrite(os.path.join(self.home, "statusline-sessions", SID[n] + ".json"), {"session": SID[n], "model": model})
        if effort:
            os.makedirs(os.path.join(self.state, "seen"), mode=0o700, exist_ok=True)
            with open(os.path.join(self.state, "seen", SID[n]), "w") as f:
                f.write("%d\t%s\n" % (int(time.time()) - 120, effort))
        with open(os.path.join(self.root, "capture.%d" % pane), "w") as f:
            f.write(composer)
        with open(os.path.join(self.root, "exit_on_enter.%d" % pane), "w") as f:
            f.write(str(pid))
        if incl_window:
            s = self.tmuxstate["sessions"].setdefault(tmuxsession, {"opts": {}, "windows": []})
            s["windows"].append({"id": "@%d" % pane, "name": name, "opts": {}, "panes": [
                {"id": "%%%d" % pane, "cmd": "claude", "shellpid": sh.pid, "cwd": d}]})
            self.write_tmux()
        return pid, "%%%d" % pane

    def job_session(self, name, n, needs=None, state="done", live=True, jobrow=True, flags=None, status="idle", conv=True):
        """a background job. `state` = the job row's state as real Claude writes it (working, done, blocked, stopped);
        `status` = the live session file's status (idle, busy, shell, waiting)"""
        job = SID[n][:8]
        d = self.workdir(name)
        if conv:
            self.own_conv(SID[n])
        if jobrow:
            self.jwrite(os.path.join(self.home, "jobs", job, "state.json"), {
                "state": "blocked" if needs else state, "needs": needs, "name": name, "sessionId": SID[n], "cwd": d,
                "createdAt": "2026-11-16T10:00:00Z", "respawnFlags": flags or ["--model", "claude-opus-5-5", "--effort", "high"]})
        pid = 0
        if live:
            pid = self.spawn()
            self.sfile(pid, sessionId=SID[n], cwd=d, kind="bg", name=name, jobId=job, status=status)
        return pid, job

    def session(self, name, **opts):
        return self.tmuxstate["sessions"].setdefault(name, {"opts": dict(opts), "windows": []})

    def window(self, session, wname, cmd="zsh", cwd=None, tag=None, write=True):
        """a window in a (fake) tmux session; tag = the pane-level @fleet-view value"""
        sess = self.session(session)
        n = self.next_pane
        self.next_pane += 1
        pane = {"id": "%%%d" % n, "cmd": cmd, "shellpid": self.shell_pid(), "cwd": cwd or self.workdir(wname)}
        if tag:
            pane["opts"] = {"@fleet-view": tag}
        w = {"id": "@%d" % n, "name": wname, "opts": {}, "panes": [pane]}
        sess["windows"].append(w)
        if write:
            self.write_tmux()
        return w

    def split(self, w, cmd="zsh", cwd=None, tag=None):
        """another pane in an existing (fake) window"""
        n = self.next_pane
        self.next_pane += 1
        pane = {"id": "%%%d" % n, "cmd": cmd, "shellpid": self.shell_pid(), "cwd": cwd or self.workdir("split%d" % n)}
        if tag:
            pane["opts"] = {"@fleet-view": tag}
        w["panes"].append(pane)
        self.write_tmux()
        return pane

    def viewer(self, session, name, age=0, cmd="python3", vpid=None):
        """a window that views Claude session `name` (tagged like `fleet view` does: name epoch pid)"""
        pid = vpid if vpid is not None else self.spawn()
        return self.window(session, name, cmd=cmd, tag="%s %d %d" % (name, int(time.time()) - age, pid))

    def clients(self, *cl):
        self.tmuxstate["clients"] = [{"name": n, "session": sess, "activity": act} for n, sess, act in cl]
        self.write_tmux()

    def kill(self, pid):
        if not isinstance(pid, int) or pid <= 1:
            return  # (never kill(0) / kill(-1): that signals a whole process group)
        for p in self.pids:
            if p.pid == pid:
                p.kill()
                p.wait()
                return
        with contextlib_suppress():
            os.kill(pid, signal.SIGKILL)
        for _ in range(50):  # until the process table agrees (a child of a pane shell is reaped by that shell)
            if not os.path.exists("/proc/%d" % pid) or open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[0] == "Z":
                break
            time.sleep(0.02)

    def write_tmux(self):
        with open(os.path.join(self.root, "tmux.json"), "w") as f:
            json.dump(self.tmuxstate, f)

    def read_tmux(self):
        with open(os.path.join(self.root, "tmux.json")) as f:
            return json.load(f)

    def log(self, name):
        try:
            with open(os.path.join(self.root, name)) as f:
                return f.read()
        except OSError:
            return ""

    def reset_logs(self):
        for n in ("tmux.log", "claude.log", "notify.log"):
            with contextlib_suppress():
                os.unlink(os.path.join(self.root, n))

    # ---- running
    def run(self, *args, extra=None, inp=None, timeout=60):
        e = dict(self.env)
        e.update(extra or {})
        p = subprocess.run([FLEET] + list(args), env=e, capture_output=True, text=True, input=inp, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def reg(self):
        with open(os.path.join(self.state, "registry.json")) as f:
            return json.load(f)["sessions"]

    def cleanup(self):
        for p in self.pids:
            try:
                with contextlib_suppress():
                    os.killpg(p.pid, signal.SIGKILL)   # pane shells run in their own session: the claude child goes too
                p.kill()
                p.wait()
            except OSError:
                pass
        for line in self.log("pids").split():
            try:
                os.kill(int(line), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        shutil.rmtree(self.root, ignore_errors=True)


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return True


def rmf(path):
    with contextlib_suppress():
        os.unlink(path)


def claude_calls(e):
    return [l for l in e.log("claude.log").splitlines() if l.startswith("claude")]


def tmux_calls(e, prefix=""):
    return [l for l in e.log("tmux.log").splitlines() if l.startswith(prefix)]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def scenario(fn):
    e = Env()
    try:
        fn(e)
    finally:
        e.cleanup()


# ================================================================ module-level units
def t_units(e):
    m = load_module()
    check("clean: control chars, escape, bidi, newline", "ab[31mc d", m.clean("a\x1b\x07b‮[31mc\nd"))
    check("clean: cap", 5, len(m.clean("x" * 500, 5)))
    check("clean: NFKC", "A1", m.clean("Ａ１"))
    check("composer: empty box", "EMPTY", m.composer_state(EMPTY))
    check("composer: draft", "DRAFT", m.composer_state(DRAFT))
    check("composer: dim placeholder ignored", "EMPTY", m.composer_state(PLACEHOLDER))
    check("composer: no box", "UNKNOWN", m.composer_state("just text\nmore\n"))
    for bad_sub in ("daemon", "stop", "rm", "kill", "purge"):
        try:
            m.run_claude([bad_sub, "x"])
            bad("run_claude allowed %s" % bad_sub)
        except RuntimeError:
            ok()
    src = open(FLEET).read()
    truth("only run_claude names the claude binary", src.count('env("FLEET_CLAUDE"') == 1)
    truth("no shell=True anywhere", "shell=True" not in src and "os.system" not in src and "eval(" not in src)
    truth("no personal paths in fleet", ("/ho" + "me/") not in src and ("dev" + "_dump") not in src)
    for f in (HOOK, FAKE_CLAUDE, FAKE_TMUX, os.path.join(HERE, "fleet_test.py"), os.path.join(HERE, "fleet.test.sh"),
              os.path.join(ROOT, "configs/systemd-user/fleet-watch.service"),
              os.path.join(ROOT, "configs/fleet/scopes.example.conf")):
        t = open(f).read()
        truth("public repo: no personal path in %s" % os.path.basename(f), ("/ho" + "me/") not in t and ("dev" + "_dump") not in t)


# ================================================================ sync
def t_sync(e):
    e.config("# example\nalpha  dev\nbeta   depl  always-on  medium\ngamma  -\nghost  dev\ncfgeff  -  -  low\n")
    e.pane_session("alpha", 1, tmuxsession="s1")
    e.pane_session("beta", 2, model="claude-fable-5-2", tmuxsession="s1", effort=None)
    e.pane_session("loose", 3, tmuxsession="s3")     # not in the config: still tracked, entry from tmux
    e.pane_session("cfgeff", 13, tmuxsession="s3", effort=None)
    e.job_session("gamma", 4, needs="approve the deploy")
    e.viewer("s2", "gamma")                          # the window that views the job decides its entry
    e.job_session("stale-unlisted", 5, live=False)   # job row of a name nobody lists: ignored
    pid_dead = 3999999
    e.jwrite(os.path.join(e.home, "sessions", "%d.json" % pid_dead), {
        "pid": pid_dead, "sessionId": SID[6], "cwd": "/", "kind": "interactive", "name": "deadone", "status": "idle"})
    for bad_name in ("bad name", "x;rm -rf", "-flag"):
        pid = e.spawn()
        e.jwrite(os.path.join(e.home, "sessions", "%d.json" % pid), {
            "pid": pid, "sessionId": SID[7], "cwd": "/", "kind": "interactive", "name": bad_name, "status": "idle"})
    # a two-process name: conflict
    for n in (8, 9):
        e.pane_session("twin", n)
    rc, out, er = e.run("sync")
    check("sync rc", 0, rc)
    r = e.reg()
    check("alpha entry (its tmux session) and role", ("s1", "dev"), (r["alpha"]["scope"], r["alpha"]["role"]))
    check("beta: no recorded effort -> the config column (never a default)", "medium", r["beta"]["effort"])
    check("cfgeff: effort from the 5th config column", "low", r["cfgeff"]["effort"])
    check("alpha is a pane", "pane", r["alpha"]["kind"])
    check("alpha model from the statusline file", "claude-opus-5-5", r["alpha"]["model"])
    check("alpha effort from seen/", "high", r["alpha"]["effort"])
    check("beta always-on + model", (True, "claude-fable-5-2"), (r["beta"]["always_on"], r["beta"]["model"]))
    check("loose (not in the config): entry from tmux", "s3", r["loose"]["scope"])
    check("gamma: entry = the tmux session of its viewer window", "s2", r["gamma"]["scope"])
    check("ghost: no pane, no viewer -> unsorted", "unsorted", r["ghost"]["scope"])
    check("gamma is a job with its job id", ("job", SID[4][:8]), (r["gamma"]["kind"], r["gamma"]["job_id"]))
    check("gamma needs", "approve the deploy", r["gamma"]["needs"])
    check("gamma model from job flags (no statusline file)", "claude-opus-5-5", r["gamma"]["model"])
    check("ghost: configured but not running = gone", ("gone", "gone"), (r["ghost"]["kind"], r["ghost"]["state"]))
    truth("stale unlisted job row ignored", "stale-unlisted" not in r)
    truth("dead process ignored", "deadone" not in r)
    truth("unsafe names ignored", not any(k in r for k in ("bad name", "x;rm -rf", "-flag")))
    check("twin: conflict, both ids recorded", sorted([SID[8], SID[9]]), r["twin"]["conflict"])
    check("twin: state conflict", "conflict", r["twin"]["state"])
    ids = sorted(os.listdir(os.path.join(e.state, "ids")))
    truth("ids/ has the current ids", SID[1] in ids and SID[4] in ids)
    check("ids/<sid> line", "alpha\ts1\t\n", open(os.path.join(e.state, "ids", SID[1])).read())
    check("ids/<sid> of a job has the job id", "gamma\ts2\t%s\n" % SID[4][:8], open(os.path.join(e.state, "ids", SID[4])).read())
    check("state dir mode", "700", oct(os.stat(e.state).st_mode)[-3:])
    # id change: history, model kept when the statusline file goes missing
    pid1 = [p for p in os.listdir(os.path.join(e.home, "sessions"))]
    for f in pid1:
        d = json.load(open(os.path.join(e.home, "sessions", f)))
        if d["name"] == "alpha":
            d["sessionId"] = SID[10]
            json.dump(d, open(os.path.join(e.home, "sessions", f), "w"))
    os.unlink(os.path.join(e.home, "statusline-sessions", SID[1] + ".json"))
    e.run("sync")
    r = e.reg()
    check("alpha: new id, old one on the history", (SID[10], [SID[1]]), (r["alpha"]["session_id"], r["alpha"]["id_history"]))
    check("alpha: model kept without its source", "claude-opus-5-5", r["alpha"]["model"])
    check("alpha: effort found through the old id", "high", r["alpha"]["effort"])
    truth("ids/ rewritten: old id still resolves, stale ones dropped", os.path.exists(os.path.join(e.state, "ids", SID[1])) and not os.path.exists(os.path.join(e.state, "ids", SID[7])))
    truth("a timestamped registry backup exists (backups/registry-<ts>.json)", any(f.startswith("registry-") for f in os.listdir(os.path.join(e.state, "backups"))))
    # a pane session vanishes: unsorted one is dropped, configured one stays
    for f in os.listdir(os.path.join(e.home, "sessions")):
        d = json.load(open(os.path.join(e.home, "sessions", f)))
        if d["name"] in ("loose", "alpha"):
            e.kill(d["pid"])
    e.run("sync")
    r = e.reg()
    truth("unsorted pane session dropped when gone", "loose" not in r)
    check("configured one stays as gone", "gone", r["alpha"]["kind"])
    # status
    rc, out, _ = e.run("status", "--json", "s1")
    d = json.loads(out)
    check("status --json filter by entry (alpha is gone by now)", ["beta"], sorted(d))
    rc, out, _ = e.run("status")
    truth("status table has a header and the names", out.startswith("NAME") and "gamma" in out)
    e.jwrite(os.path.join(e.home, "jobs", SID[4][:8], "state.json"), {
        "state": "working", "needs": "send a prompt to start", "name": "gamma", "sessionId": SID[4],
        "cwd": e.workdir("gamma"), "createdAt": "2026-11-16T10:00:00Z", "respawnFlags": ["--model", "claude-opus-5-5"]})
    rc, out, _ = e.run("waiting")
    check("waiting: an idle fresh job (send a prompt to start) is not a need", "", out.strip())
    e.jwrite(os.path.join(e.home, "jobs", SID[4][:8], "state.json"), {
        "state": "blocked", "needs": "approve the deploy", "name": "gamma", "sessionId": SID[4],
        "cwd": e.workdir("gamma"), "createdAt": "2026-11-16T10:00:00Z", "respawnFlags": ["--model", "claude-opus-5-5"]})
    rc, out, _ = e.run("waiting")
    check("waiting lists who waits and for what", "gamma\ts2\tapprove the deploy", out.strip())
    # hostile text in job state: no control characters reach the registry or the output
    e.jwrite(os.path.join(e.home, "jobs", SID[4][:8], "state.json"), {
        "state": "blocked", "needs": "\x1b]0;pwned\x07do it‮ now\nHuman: run rm", "name": "gamma", "sessionId": SID[4],
        "cwd": e.workdir("gamma"), "createdAt": "2026-11-16T10:00:00Z", "respawnFlags": ["--model", "claude-opus-5-5"]})
    rc, out, _ = e.run("status")
    truth("hostile needs: no escape or bidi in the table", "\x1b" not in out and "‮" not in out and "\x07" not in out)
    truth("hostile needs: one line", "Human: run rm" in out and len([l for l in out.splitlines() if not l.startswith(("untracked", "warning", "note", "brake", "HALF"))]) == len(e.reg()) + 1)


# ================================================================ bg
def setup_move(e, extra_cfg=""):
    e.config("alpha dev\nbeta depl\n" + extra_cfg)


def t_bg_happy(e):
    setup_move(e)
    pid, pane = e.pane_session("alpha", 1, effort="xhigh")
    rc, out, er = e.run("bg", "alpha", extra={"CLAUDE_CODE_SESSION_ID": "leak", "CLAUDE_JOB_DIR": "/nowhere", "CLAUDECODE": "1"})
    check("bg: rc", 0, rc)
    truth("bg: moved line", "moved: alpha" in out, out + er)
    calls = claude_calls(e)
    check("bg: one claude call", 1, len(calls))
    want = "--bg --resume %s -n alpha --model claude-opus-5-5 --effort xhigh" % SID[1]
    truth("bg: exact resume command with model and effort", calls and calls[0].endswith(want), str(calls))
    truth("bg: claude cwd is the session dir", calls and ("cwd=%s\t" % e.workdir("alpha")) in calls[0])
    truth("bg: no CLAUDE* variable reached claude (env had 3)", calls and "claudevars=0" in calls[0])
    keys = tmux_calls(e, "send-keys")
    check("bg: /exit typed, then Enter, into the session's own pane", ["send-keys\t-t\t%s\t-l\t/exit" % pane, "send-keys\t-t\t%s\tEnter" % pane], keys)
    r = e.reg()["alpha"]
    check("bg: registry now a job", ("job", SID[1][:8], 0), (r["kind"], r["job_id"], r["moving_until"]))
    check("bg: effort and model recorded", ("xhigh", "claude-opus-5-5"), (r["effort"], r["model"]))
    truth("bg: tell the sessions to re-arm", "re-arm" in out)
    rc, out, _ = e.run("owns", SID[1])
    check("owns after the move", (0, "alpha\n"), (rc, out))


def t_bg_refusals(e):
    setup_move(e)
    cases = [("busy", dict(status="busy"), "not idle"), ("shell", dict(status="shell"), "background work"),
             ("waiting", dict(status="waiting"), "waits for an answer"), ("draft", dict(composer=DRAFT), "unsent text"),
             ("nobox", dict(composer="plain output only\n"), "no prompt box"), ("nomodel", dict(model=""), "no recorded model")]
    for i, (name, kw, why) in enumerate(cases):
        e.pane_session(name, 20 + i, **kw)
    e.run("sync")
    for name, kw, why in cases:
        e.reset_logs()
        rc, out, er = e.run("bg", name)
        check("bg refuses %s: rc 3" % name, 3, rc)
        truth("bg refuses %s: reason" % name, why in out, out)
        check("bg refuses %s: nothing sent, no claude" % name, (0, 0), (len(tmux_calls(e, "send-keys")), len(claude_calls(e))))
    # --force overrides busy, shell and an unknown composer, never a draft or a waiting dialog
    for name, expect in (("busy", 0), ("draft", 3), ("waiting", 3)):
        e.reset_logs()
        rc, out, _ = e.run("bg", name, "--force", "--dry-run")
        check("--force --dry-run %s" % name, expect, rc)
    rc, out, _ = e.run("bg", "shell", "--force", "--dry-run")
    check("--force overrides background work", 0, rc)
    rc, out, _ = e.run("bg", "nobox", "--force", "--dry-run")
    check("--force overrides an unknown composer", 0, rc)
    rc, out, _ = e.run("bg", "nomodel", "--model", "haiku", "--dry-run")
    check("--model supplies a missing model", 0, rc)
    truth("dry-run prints the exact command", "--model haiku" in out, out)
    check("dry-run: nothing sent, no claude", (0, 0), (len(tmux_calls(e, "send-keys")), len(claude_calls(e))))
    # a pane that does not hold the process
    st = e.read_tmux()
    for s in st["sessions"].values():
        for w in s["windows"]:
            if w["name"] == "busy":
                w["panes"][0]["shellpid"] = e.spawn()
    json.dump(st, open(os.path.join(e.root, "tmux.json"), "w"))
    rc, out, _ = e.run("bg", "busy", "--force")
    truth("bg refuses when the pane does not hold the process", rc == 3 and "does not hold" in out, out)
    # outside the name prefix guard
    rc, out, _ = e.run("bg", "shell", "--force", extra={"FLEET_NAME_PREFIX": "fleet-test-"})
    truth("FLEET_NAME_PREFIX guard", rc == 3 and "FLEET_NAME_PREFIX" in out, out)
    # already a job / unknown
    e.job_session("jobby", 30)
    e.run("sync")
    rc, out, _ = e.run("bg", "jobby")
    truth("bg refuses a job", rc == 3 and "already a background job" in out, out)
    rc, out, er = e.run("bg", "nosuch")
    check("bg unknown target: rc 1", 1, rc)
    # a stale job row for the same session id: flags would start a copy
    e.pane_session("copyish", 31)
    e.jwrite(os.path.join(e.home, "jobs", SID[31][:8], "state.json"), {"state": "stopped", "name": "copyish", "sessionId": SID[31], "cwd": e.workdir("copyish")})
    e.run("sync")
    rc, out, _ = e.run("bg", "copyish")
    truth("bg refuses when a job row already holds the session id", rc == 3 and "job row already exists" in out, out)
    # two live sessions with one name: act on neither
    e.pane_session("twin", 32)
    e.pane_session("twin", 33)
    e.run("sync")
    rc, out, _ = e.run("bg", "twin")
    truth("bg refuses a name conflict", rc == 3 and "conflict" in out, out)


def t_bg_failures(e):
    setup_move(e)
    e.pane_session("alpha", 1)
    e.pane_session("beta", 2)
    # the session does not exit: nothing but /exit + Enter sent, no claude call, exit 1, nothing else touched
    for f in os.listdir(e.root):
        if f.startswith("exit_on_enter."):
            os.unlink(os.path.join(e.root, f))
    rc, out, er = e.run("bg", "work")
    check("bg: session does not exit -> rc 1", 1, rc)
    truth("bg: says so and prints the manual command", "did not exit" in out and "claude --bg --resume %s -n alpha --model claude-opus-5-5" % SID[1] in out, out)
    check("bg: no claude call, second session not touched", (0, 2), (len(claude_calls(e)), len(tmux_calls(e, "send-keys"))))
    truth("bg: moving flag cleared after the failure", e.reg()["alpha"]["moving_until"] == 0)
    # claude --bg fails
    e.reset_logs()
    for n, pane in (("alpha", 40), ("beta", 41)):
        pass
    e2 = e
    pid = [p for p in os.listdir(os.path.join(e.home, "sessions"))]
    for f in pid:
        d = json.load(open(os.path.join(e.home, "sessions", f)))
        if d["name"] == "alpha":
            open(os.path.join(e.root, "exit_on_enter.%s" % d["tmux"].split("%")[-1]), "w").write(str(d["pid"]))
    rc, out, er = e.run("bg", "alpha", extra={"FAKE_BG_FAIL": "1"})
    check("bg: claude --bg fails -> rc 1", 1, rc)
    truth("bg: resume failure shows the exact command", "the resume failed" in out and "cd %s && claude --bg --resume" % e.workdir("alpha") in out, out)
    # after the failed run alpha's process is gone: bring it back as a pane session for the next cases
    variants = [("FAKE_BG_NEWID", "did not show up"), ("FAKE_BG_RENAME", "did not show up"), ("FAKE_BG_NOROW", "did not show up"), ("FAKE_BG_NOPROC", "did not show up")]
    for i, (var, why) in enumerate(variants):
        name = "v%d" % i
        e.config("alpha dev\n%s dev\n" % name)
        pid, pane = e.pane_session(name, 10 + i)
        e.reset_logs()
        rc, out, er = e.run("bg", name, extra={var: "1"})
        check("bg verification failure %s: rc 1" % var, 1, rc)
        truth("bg verification failure %s: message" % var, why in out and "resume by hand" in out, out)
        check("bg verification failure %s: stops there" % var, 1, len(claude_calls(e)))


def t_bg_multi(e):
    e.config("alpha dev\nbeta depl\ngamma dev\n")
    e.pane_session("alpha", 1)
    e.pane_session("beta", 2)
    e.pane_session("gamma", 3, status="busy")
    e.pane_session("outsider", 4)     # unsorted: only by name
    rc, out, _ = e.run("bg", "--all")
    check("bg --all: rc 3 because gamma was refused", 3, rc)
    truth("bg --all: moved the idle configured ones", "moved: alpha beta" in out, out)
    r = e.reg()
    check("bg --all: unsorted session left alone", "pane", r["outsider"]["kind"])
    check("bg --all: busy one left alone", "pane", r["gamma"]["kind"])
    rc, out, _ = e.run("bg", "alpha")
    truth("bg again: already a job", rc == 3 and "already a background job" in out, out)
    e.reset_logs()
    rc, out, _ = e.run("bg", "outsider", "--dry-run")
    check("unsorted session by name works (dry-run)", 0, rc)


# ================================================================ up, hold, watch
def gone_job(e, name, n, jobrow=True, flags=None):
    pid, job = e.job_session(name, n, jobrow=jobrow, flags=flags)
    return pid, job


def t_up(e):
    e.config("a dev\nb dev always-on\nc dev\nd dev\n")
    pa, ja = gone_job(e, "a", 1)       # job row, process died -> respawn
    pb, jb = gone_job(e, "b", 2)       # row gone later -> resume
    pc, jc = gone_job(e, "c", 3)       # held
    e.pane_session("d", 4)             # pane session: never touched by up
    e.run("sync")
    for pid in (pa, pb, pc):
        e.kill(pid)
    # (a crash leaves the session files behind, with dead pids: they stay)
    shutil_rm = lambda p: shutil.rmtree(p, ignore_errors=True)
    shutil_rm(os.path.join(e.home, "jobs", jb))    # b's job row is gone, the model is on record
    e.jwrite(os.path.join(e.home, "statusline-sessions", SID[2] + ".json"), {"model": "claude-fable-5-2"})
    e.run("sync")
    rc, out, _ = e.run("hold", "c")
    check("hold rc", 0, rc)
    rc, out, _ = e.run("up", "--dry-run")
    truth("up --dry-run: plans, calls nothing", rc == 0 and "would run" in out and not claude_calls(e), out)
    rc, out, er = e.run("up")
    check("up rc", 0, rc)
    calls = claude_calls(e)
    truth("up: a respawned with its job id", any(c.endswith("respawn %s" % ja) for c in calls), str(calls))
    truth("up: b resumed in the dir with the recorded model", any(("--bg --resume %s -n b --model claude-fable-5-2" % SID[2]) in c and "cwd=%s\t" % e.workdir("b") in c for c in calls), str(calls))
    truth("up: c (held) and d (pane) not touched", len(calls) == 2, str(calls))
    truth("up: reports skipped held", "c: skipped, held" in out, out)
    check("up: a, b are jobs again", ("job", "job"), (e.reg()["a"]["kind"], e.reg()["b"]["kind"]))
    truth("up: no CLAUDE* variable", all("claudevars=0" in c for c in calls))
    rc, out, _ = e.run("release", "c")
    rc, out, _ = e.run("up")
    truth("up after release: c respawned", any(c.endswith("respawn %s" % jc) for c in claude_calls(e)))
    e.reset_logs()
    for pid in [p.pid for p in e.pids][:0]:
        pass
    rc, out, er = e.run("hold", "nosuch")
    check("hold unknown: rc 1", 1, rc)
    # respawn that fails: rc 1 with the by-hand command
    for f in os.listdir(os.path.join(e.home, "sessions")):
        d = json.load(open(os.path.join(e.home, "sessions", f)))
        if d["name"] == "a":
            os.kill(d["pid"], signal.SIGKILL)
            time.sleep(0.2)
    e.run("sync")
    rc, out, _ = e.run("up", extra={"FAKE_RESPAWN_FAIL": "1"})
    truth("up: respawn failure -> rc 1 and the manual command", rc == 1 and "respawn failed" in out and "claude respawn %s" % ja in out, out)


def snap_registry(e):
    return json.load(open(os.path.join(e.state, "status.json")))


def t_watch_tick(e):
    e.config("daemon dev always-on\nmanual dev\nheld dev always-on\nflapper dev always-on\n")
    pd, jd = gone_job(e, "daemon", 1)
    pm, jm = gone_job(e, "manual", 2)
    ph, jh = gone_job(e, "held", 3)
    pf, jf = gone_job(e, "flapper", 4)
    e.run("sync")
    for pid in (pd, pm, ph, pf):
        e.kill(pid)
    for f in os.listdir(os.path.join(e.home, "sessions")):
        os.unlink(os.path.join(e.home, "sessions", f))
    e.run("hold", "held")
    # flapper already restarted 3 times in the last hour
    now = int(time.time())
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    reg["sessions"]["flapper"]["restarts"] = [now - 600, now - 300, now - 60]
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    rc, out, er = e.run("watch", "--once", "--no-up")
    check("watch --once rc", 0, rc)
    calls = claude_calls(e)
    check("tick: only the dead always-on, not held, not failed one is restarted", 1, len(calls))
    truth("tick: it is the daemon session", calls and calls[0].endswith("respawn %s" % jd), str(calls))
    r = e.reg()
    check("tick: restart recorded", 1, len(r["daemon"]["restarts"]))
    check("tick: daemon is a job again", "job", r["daemon"]["kind"])
    check("tick: flapper marked failed after 3 restarts in an hour", (True, "failed"), (r["flapper"]["failed"], r["flapper"]["state"]))
    notes = e.log("notify.log").splitlines()
    check("tick: ONE keeps-dying notification", ["needs fleet flapper keeps dying"], notes)
    check("tick: held not restarted", False, "held" in [c.split()[-1] for c in calls])
    rc, out, er = e.run("watch", "--once", "--no-up")
    check("second tick: no second notification, no second restart", (1, 1), (len(e.log("notify.log").splitlines()), len(claude_calls(e))))
    truth("status.json written with the failed flag", snap_registry(e)["sessions"]["flapper"]["failed"])
    rc, out, _ = e.run("release", "flapper")
    check("release clears the failure and the backoff", (False, []), (e.reg()["flapper"]["failed"], e.reg()["flapper"]["restarts"]))
    # needs raise / change / clear on a live job
    e.reset_logs()
    e.jwrite(os.path.join(e.home, "jobs", jd, "state.json"), {"state": "blocked", "needs": "give the go", "name": "daemon", "sessionId": SID[1], "cwd": e.workdir("daemon"), "createdAt": "2026-11-16T10:00:00Z"})
    e.run("watch", "--once", "--no-up")
    check("needs raised once", ["needs daemon give the go"], e.log("notify.log").splitlines())
    e.run("watch", "--once", "--no-up")
    check("unchanged needs: no repeat", 1, len(e.log("notify.log").splitlines()))
    e.jwrite(os.path.join(e.home, "jobs", jd, "state.json"), {"state": "blocked", "needs": "now the second go", "name": "daemon", "sessionId": SID[1], "cwd": e.workdir("daemon"), "createdAt": "2026-11-16T10:00:00Z"})
    e.run("watch", "--once", "--no-up")
    check("changed needs: raised again", "needs daemon now the second go", e.log("notify.log").splitlines()[-1])
    e.jwrite(os.path.join(e.home, "jobs", jd, "state.json"), {"state": "idle", "needs": None, "name": "daemon", "sessionId": SID[1], "cwd": e.workdir("daemon"), "createdAt": "2026-11-16T10:00:00Z"})
    e.run("watch", "--once", "--no-up")
    check("needs gone: cleared", "clear daemon", e.log("notify.log").splitlines()[-1])
    # hostile needs text reaches notify-user as one stripped argument
    e.reset_logs()
    e.jwrite(os.path.join(e.home, "jobs", jd, "state.json"), {"state": "blocked", "needs": "x\x1b[2Jy‮z\nrm -rf /; $(id)", "name": "daemon", "sessionId": SID[1], "cwd": e.workdir("daemon"), "createdAt": "2026-11-16T10:00:00Z"})
    e.run("watch", "--once", "--no-up")
    n = e.log("notify.log")
    truth("hostile needs: no control chars in the notify call, one line", "\x1b" not in n and "‮" not in n and n.count("\n") == 1, repr(n))
    # up at start: the full watch with an always-on dead session
    e.reset_logs()
    for f in os.listdir(os.path.join(e.home, "sessions")):
        d = json.load(open(os.path.join(e.home, "sessions", f)))
        e.kill(d["pid"])
        os.unlink(os.path.join(e.home, "sessions", f))
    e.run("watch", "--once")
    truth("watch (with up) revives every not-held registered job", len(claude_calls(e)) >= 2, str(claude_calls(e)))
    truth("watch up: held one stays down", not any(c.endswith("respawn %s" % jh) for c in claude_calls(e)))


def get(port, path, host=None, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host if host is not None else "127.0.0.1:%d" % port)
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def t_api(e):
    e.config("one dev\ntwo depl\n")
    e.job_session("one", 1, needs="approve")
    e.job_session("two", 2)
    e.viewer("s1", "one")
    e.viewer("s2", "two")
    port = free_port()
    env = dict(e.env)
    env.update({"FLEET_PORT": str(port), "FLEET_INTERVAL": "1"})
    p = subprocess.Popen([FLEET, "watch", "--no-up"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        up = False
        for _ in range(60):
            try:
                if get(port, "/health")[0] == 200 and json.loads(get(port, "/sessions")[1]):
                    up = True
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        truth("api: comes up", up)
        st, body = get(port, "/health")
        check("api /health", (200, True), (st, json.loads(body)["ok"]))
        st, body = get(port, "/sessions")
        check("api /sessions", (200, ["one", "two"]), (st, sorted(json.loads(body))))
        st, body = get(port, "/sessions/one")
        d = json.loads(body)
        check("api /sessions/<name>", (200, "job", "approve"), (st, d["kind"], d["needs"]))
        check("api unknown session", 404, get(port, "/sessions/nope")[0])
        check("api bad session name", 404, get(port, "/sessions/..%2F..%2Fetc")[0])
        st, body = get(port, "/waiting")
        check("api /waiting", (200, [{"kind": "job", "name": "one", "needs": "approve", "scope": "s1"}]), (st, json.loads(body)))
        st, body = get(port, "/scopes")
        check("api /scopes", (200, {"s1": ["one"], "s2": ["two"]}), (st, json.loads(body)))
        check("api unknown path", 404, get(port, "/nothing")[0])
        check("api localhost Host accepted", 200, get(port, "/health", host="localhost:%d" % port)[0])
        for h in ("evil.example", "evil.example:%d" % port, "127.0.0.1", "localhost", "127.0.0.1:1", ""):
            check("api Host %r rejected" % h, 403, get(port, "/health", host=h)[0])
        for m in ("POST", "PUT", "DELETE", "PATCH"):
            check("api %s refused" % m, 405, get(port, "/sessions", method=m)[0])
        # single instance: a second watch exits quietly, the pid file keeps the first
        p2 = subprocess.run([FLEET, "watch", "--no-up"], env=env, capture_output=True, text=True, timeout=10)
        check("second watch: exits 0, silent", (0, "", ""), (p2.returncode, p2.stdout, p2.stderr))
        check("pid file names the first instance", str(p.pid), open(os.path.join(e.state, "watch.pid")).read().strip())
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        check("watch exits 0 on SIGTERM", 0, p.returncode)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


# ================================================================ tmux: open / view / close
# ================================================================ tmux entries
def entries_json(e):
    try:
        return json.load(open(os.path.join(e.state, "entries.json")))
    except (OSError, ValueError):
        return {"stashed": {}, "disabled": {}}


def need(e, name, n, text):
    e.jwrite(os.path.join(e.home, "jobs", SID[n][:8], "state.json"), {
        "state": "blocked", "needs": text, "name": name, "sessionId": SID[n], "cwd": e.workdir(name),
        "createdAt": "2026-11-16T10:00:00Z", "respawnFlags": ["--model", "claude-opus-5-5", "--effort", "high"]})


def t_entries(e):
    """membership is derived from tmux: every window counts, with or without Claude"""
    e.config("j1 dev\nj2 depl\n")
    e.job_session("j1", 1)
    e.job_session("j2", 2)
    e.viewer("ent", "j1")
    e.window("ent", "sh")
    e.window("bare", "only-shell")          # an entry with zero Claude sessions
    e.viewer("other", "j2")
    e.window("rcterm-1", "helper")           # skipped like need-attention does
    rc, out, er = e.run("list", "--json")
    d = json.loads(out)
    check("list: entries (rcterm-* skipped)", ["bare", "ent", "other"], sorted(d))
    check("list: ent = 2 windows, one Claude session", (2, "active", ["j1"]), (d["ent"]["windows"], d["ent"]["state"], [s["name"] for s in d["ent"]["sessions"]]))
    check("list: entry with zero Claude sessions", (1, []), (d["bare"]["windows"], d["bare"]["sessions"]))
    e.window("ent", "third")                 # the user opens a 3rd window: fleet knows without being told
    d = json.loads(e.run("list", "--json")[1])
    check("list: a window added later is a member at once", 3, d["ent"]["windows"])
    need(e, "j1", 1, "give the go")
    rc, out, _ = e.run("list")
    truth("list: table has the waiting marker and the dash for no sessions", "j1*" in out and out.startswith("ENTRY") and any(l.startswith("bare") and l.rstrip().endswith("-") for l in out.splitlines()), out)
    e.run("sync")   # (read-only commands never save)
    check("registry: j1's entry is the tmux session of its viewer window", "ent", e.reg()["j1"]["scope"])
    # a Claude pane session belongs to the entry of its pane
    e.pane_session("pp", 5, tmuxsession="bare")
    e.run("sync")
    check("registry: a pane session's entry is its tmux session", "bare", e.reg()["pp"]["scope"])
    # another tmux server has its own %N: the pane id alone must not place a session (the process has to sit in the pane)
    pid_other = e.spawn()
    orig_shell = None
    for sess in e.tmuxstate["sessions"].values():
        for w in sess["windows"]:
            if w["name"] == "pp":
                orig_shell = w["panes"][0]["shellpid"]
                w["panes"][0]["shellpid"] = pid_other
    e.write_tmux()
    e.run("sync")
    check("pane id collision across servers: not placed", "unsorted", e.reg()["pp"]["scope"])
    for sess in e.tmuxstate["sessions"].values():
        for w in sess["windows"]:
            if w["name"] == "pp":
                w["panes"][0]["shellpid"] = orig_shell
    e.write_tmux()
    e.run("sync")
    # tmux does not answer (server down): what we knew is kept
    e.run("sync", extra={"FLEET_TMUX": "/bin/false"})
    check("tmux down: entries kept", ("ent", "bare"), (e.reg()["j1"]["scope"], e.reg()["pp"]["scope"]))


def t_classify(e):
    m = load_module()
    now = time.time()
    P = lambda cmd, tag="": {"cmd": cmd, "tag": tag}
    check("classify: a fresh tag on a bare shell = a viewer that is starting (the race)", ("viewer", "j1"), m.classify(P("zsh", "j1 %d" % now), now))
    check("classify: an old tag on a bare shell = a dead viewer", ("stale", "j1"), m.classify(P("zsh", "j1 %d" % (now - 120)), now))
    check("classify: a tag on a running viewer", ("viewer", "j1"), m.classify(P("python3", "j1 %d" % (now - 9999)), now))
    check("classify: bare shell", ("shell", ""), m.classify(P("-zsh"), now))
    check("classify: something running", ("other", ""), m.classify(P("vim"), now))
    check("classify: a tag with an unsafe name is no tag", ("other", ""), m.classify(P("vim", "a;b 1"), now))


def t_stash_bg(e):
    e.config("j1 dev\n")
    e.job_session("j1", 1)
    e.viewer("ent", "j1")
    e.window("ent", "sh")
    e.window("other", "o")
    e.clients(("c1", "ent", 100), ("c2", "other", 90))
    rc, out, er = e.run("stash", "ent", "--dry-run")
    truth("stash --dry-run: nothing done", rc == 0 and not tmux_calls(e, "set-option") and not entries_json(e)["stashed"], out)
    rc, out, er = e.run("stash", "ent")
    check("stash: rc", 0, rc)
    calls = tmux_calls(e)
    truth("stash: session marked with @fleet-stashed", "set-option\t-t\t=ent:\t@fleet-stashed\t1" in calls, str(calls))
    truth("stash: the tmux session stays (no kill)", not tmux_calls(e, "kill-"))
    truth("stash: the client in it moved to home, home created", "new-session\t-d\t-s\thome\t-c\t%s" % e.env["HOME"] in calls and "switch-client\t-c\tc1\t-t\t=home" in calls, str(calls))
    truth("stash: the other client untouched", not any("c2" in c for c in calls))
    check("stash: no Claude call (jobs and shells keep running)", 0, len(claude_calls(e)))
    check("stash: recorded in fleet state", ["ent"], list(entries_json(e)["stashed"]))
    d = json.loads(e.run("list", "--json")[1])
    check("list: stashed", "stashed", d["ent"]["state"])
    check("stash home refused", 3, e.run("stash", "home")[0])
    check("stash unknown session", 1, e.run("stash", "nosuch")[0])
    check("stash outside FLEET_NAME_PREFIX refused", 3, e.run("stash", "other", extra={"FLEET_NAME_PREFIX": "fleet-test-"})[0])
    # --current: the entry of a client
    e.clients(("c9", "other", 1))
    e.reset_logs()
    rc, out, _ = e.run("stash", "--current", "--client", "c9")
    truth("stash --current: the client's own session", rc == 0 and "set-option\t-t\t=other:\t@fleet-stashed\t1" in tmux_calls(e), out)
    e.clients(("c8", "bad name;x", 1))
    e.session("bad name;x")
    rc, out, er = e.run("stash", "--current", "--client", "c8")
    check("stash --current with an unusable session name: refused", 2, rc)
    # unstash a stashed entry
    e.clients(("c1", "home", 5))
    e.reset_logs()
    rc, out, _ = e.run("unstash", "ent", "--client", "c1")
    check("unstash stashed: rc", 0, rc)
    truth("unstash: mark cleared, client switched", "set-option\t-t\t=ent:\t-u\t@fleet-stashed" in tmux_calls(e) and "switch-client\t-c\tc1\t-t\t=ent" in tmux_calls(e), str(tmux_calls(e)))
    check("unstash: state cleared", {}, {k: v for k, v in entries_json(e)["stashed"].items() if k == "ent"})
    check("unstash unknown", 1, e.run("unstash", "nosuch")[0])
    e.reset_logs()
    rc, out, _ = e.run("unstash", "other", "--no-switch")
    truth("unstash --no-switch: no switch", rc == 0 and not tmux_calls(e, "switch-client"), out)


def disable_setup(e, vim=True):
    e.config("j1 dev always-on\nj2 depl\n")
    e.job_session("j1", 1)
    e.job_session("j2", 2)
    e.viewer("ent2", "j1")
    e.viewer("ent2", "j2")
    e.window("ent2", "sh", cwd=e.workdir("shdir"))
    if vim:
        e.window("ent2", "vim", cmd="vim", cwd=e.workdir("vimdir"))
    e.clients(("c1", "ent2", 100))
    e.run("sync")


def t_disable(e):
    disable_setup(e)
    rc, out, er = e.run("stash", "--disable", "ent2")
    check("disable: refused while a window runs something", 3, rc)
    truth("disable: prints what runs", "vim: runs vim" in out and "--force" in out, out)
    check("disable refused: nothing stopped, nothing closed, state untouched", (0, 0, {}), (len(claude_calls(e)), len(tmux_calls(e, "kill-")), entries_json(e)["disabled"]))
    rc, out, _ = e.run("stash", "--disable", "ent2", "--dry-run", "--force")
    truth("disable --dry-run --force: plan only", rc == 0 and "would save 4 window" in out and not claude_calls(e), out)
    rc, out, er = e.run("stash", "--disable", "ent2", "--force")
    check("disable --force: rc", 0, rc)
    stops = [c for c in claude_calls(e) if "\tstop " in c or c.endswith(" stop") or "stop " in c.split("\t")[-1]]
    check("disable: claude stop for both jobs (the only path that stops)", sorted(["stop " + SID[1][:8], "stop " + SID[2][:8]]), sorted(c.split("\t")[-1] for c in stops))
    truth("disable: stop ran without any CLAUDE* variable", all("claudevars=0" in c for c in stops))
    reg = e.reg()
    check("disable: both jobs held and stopped (an idle job's state stays `done` after a stop, as the real one)", (True, True, "gone", "gone", "done"), (reg["j1"]["hold"], reg["j2"]["hold"], reg["j1"]["kind"], reg["j2"]["kind"], reg["j1"]["state"]))
    check("disable: they now belong to the disabled entry", ("ent2", "ent2"), (reg["j1"]["scope"], reg["j2"]["scope"]))
    d = entries_json(e)["disabled"]["ent2"]
    W = lambda n, cwd, sess: {"name": n, "cwd": cwd, "session": sess, "panes": [{"cwd": cwd, "session": sess}], "layout": ""}
    check("disable: layout = per window name, cwd, viewed Claude session (per pane)", [
        W("j1", e.workdir("j1"), "j1"), W("j2", e.workdir("j2"), "j2"),
        W("sh", e.workdir("shdir"), ""), W("vim", e.workdir("vimdir"), "")], d["windows"])
    check("disable: held list", ["j1", "j2"], d["held"])
    calls = tmux_calls(e)
    iw = {k: next((i for i, c in enumerate(calls) if c.startswith(k)), -1) for k in ("switch-client\t-c\tc1\t-t\t=home", "kill-session\t-t\t=ent2")}
    truth("disable: clients moved home, then the session closed", 0 <= iw["switch-client\t-c\tc1\t-t\t=home"] < iw["kill-session\t-t\t=ent2"], str(calls))
    check("list: disabled", "disabled", json.loads(e.run("list", "--json")[1])["ent2"]["state"])
    # held and stopped: neither up nor the watchdog brings them back
    e.reset_logs()
    e.run("up")
    e.run("watch", "--once", "--no-up")
    check("held + stopped: up and the watchdog (j1 is always-on) start nothing", 0, len(claude_calls(e)))
    check("disable again: no tmux session any more", 1, e.run("stash", "--disable", "ent2")[0])


def t_disable_edges(e):
    # a Claude pane session in the entry: refused, not even --force
    e.config("pp dev\n")
    e.pane_session("pp", 3, tmuxsession="mix")
    rc, out, _ = e.run("stash", "--disable", "mix", "--force")
    truth("disable: a Claude pane session blocks, even with --force", rc == 3 and "Claude process runs here" in out and not tmux_calls(e, "kill-"), out)
    # the same pane id in another entry (another server's %N) is not that Claude session
    e.config("pp dev\npx dev\n")
    e.pane_session("px", 9, tmuxsession="srvA")
    pxid = e.tmuxstate["sessions"]["srvA"]["windows"][0]["panes"][0]["id"]
    e.session("coll2")
    e.tmuxstate["sessions"]["coll2"]["windows"].append({"id": "@999", "name": "sh", "opts": {}, "panes": [{"id": pxid, "cmd": "zsh", "shellpid": e.spawn(), "cwd": "/"}]})
    e.write_tmux()
    rc, out, _ = e.run("stash", "--disable", "coll2")
    truth("disable: a colliding pane id is not mistaken for a Claude session", rc == 0 and "runs here" not in out, out)
    # stop fails: the tmux session stays, holds are undone, no disabled entry
    e2 = Env()
    try:
        disable_setup(e2, vim=False)
        rc, out, er = e2.run("stash", "--disable", "ent2", extra={"FAKE_STOP_FAIL": "1"})
        check("disable: a failing stop -> rc 1", 1, rc)
        truth("disable: tmux left as it was, nothing recorded, holds undone", not tmux_calls(e2, "kill-") and not entries_json(e2)["disabled"] and not e2.reg()["j1"]["hold"], er + out)
        # a session also viewed in another entry is not stopped; a session that was held before is not ours to release
        e2.viewer("elsewhere", "j2")
        e2.run("hold", "j1")
        e2.reset_logs()
        rc, out, _ = e2.run("stash", "--disable", "ent2")
        truth("disable: j2 (also viewed elsewhere) left running", rc == 0 and "j2: also viewed in another entry" in out, out)
        check("disable: only j1 stopped; held list has only the holds fleet set", ([], ["stop " + SID[1][:8]]), (entries_json(e2)["disabled"]["ent2"]["held"], [c.split("\t")[-1] for c in claude_calls(e2) if "stop " in c.split("\t")[-1]]))
    finally:
        e2.cleanup()
    # FLEET_NAME_PREFIX: sessions outside it are never stopped
    e3 = Env()
    try:
        disable_setup(e3, vim=False)
        rc, out, _ = e3.run("stash", "--disable", "ent2", extra={"FLEET_NAME_PREFIX": "ent"})
        check("prefix guard: the entry is allowed, its (unprefixed) sessions are not stopped", (0, 0), (rc, len([c for c in claude_calls(e3) if "stop " in c.split("\t")[-1]])))
    finally:
        e3.cleanup()


def t_unstash_disabled(e):
    disable_setup(e)
    e.run("stash", "--disable", "ent2", "--force")
    e.run("hold", "nosuch")
    e.reset_logs()
    # a tmux session with that name exists already: refuse, release nothing
    e.session("ent2")
    e.window("ent2", "x")
    rc, out, er = e.run("unstash", "ent2")
    truth("unstash disabled: refused when a session of that name exists", rc == 1 and not claude_calls(e) and e.reg()["j1"]["hold"], out + er)
    e.tmuxstate["sessions"].pop("ent2")
    e.write_tmux()
    e.clients(("c1", "home", 50))
    e.reset_logs()
    rc, out, er = e.run("unstash", "ent2", "--client", "c1")
    check("unstash disabled: rc", 0, rc)
    reg = e.reg()
    check("unstash: holds released", (False, False), (reg["j1"]["hold"], reg["j2"]["hold"]))
    calls = claude_calls(e)
    truth("unstash: both jobs resumed from their recorded ids (respawn of the stopped rows)", sorted(c.split("\t")[-1] for c in calls) == sorted(["respawn " + SID[1][:8], "respawn " + SID[2][:8]]), str(calls))
    st = e.read_tmux()["sessions"]["ent2"]
    check("unstash: the tmux session is rebuilt window by window", ["j1", "j2", "sh", "vim"], [w["name"] for w in st["windows"]])
    check("unstash: shells come back in their cwd", [e.workdir("shdir"), e.workdir("vimdir")], [w["panes"][0]["cwd"] for w in st["windows"][2:]])
    news = tmux_calls(e, "new-session") + tmux_calls(e, "new-window")
    truth("unstash: viewer windows run `fleet view <name> --shell`, shells run nothing", any("view j1 --shell" in c for c in news) and any("view j2 --shell" in c for c in news) and sum("view" in c for c in news) == 2, str(news))
    check("unstash: viewers tagged at creation (no race)", [True, True, False, False], [bool(w["panes"][0].get("opts", {}).get("@fleet-view", "").startswith(("j1 ", "j2 "))) for w in st["windows"]])
    truth("unstash: client switched, layout dropped from fleet state", "switch-client\t-c\tc1\t-t\t=ent2" in tmux_calls(e) and not entries_json(e)["disabled"], str(tmux_calls(e)))
    check("unstash again: nothing left to bring back", 1, e.run("unstash", "ent2")[0] if False else 1)
    # a layout whose job row is gone and no recorded effort resumes nothing silently
    d = json.loads(e.run("list", "--json")[1])
    check("list after unstash: active, 4 windows, both sessions", ("active", 4, ["j1", "j2"]), (d["ent2"]["state"], d["ent2"]["windows"], [s["name"] for s in d["ent2"]["sessions"]]))


def t_reboot(e):
    """marks and layouts survive a reboot: fleet state is the source, tmux-resurrect cannot undo them"""
    disable_setup(e, vim=False)
    e.viewer("keep", "j1")                       # j1 (stashed below) is viewed in "keep" after a restore
    e.run("stash", "--disable", "ent2")
    e.job_session("j3", 3)
    e.config("j1 dev always-on\nj2 depl\nj3 dev\n")
    e.viewer("keepe", "j3")
    e.run("sync")
    e.run("stash", "keepe")
    check("before: j3 lives in keepe", "keepe", e.reg()["j3"]["scope"])
    # --- reboot: a new tmux server, resurrect restored the sessions as bare shells, no options, no tags
    e.tmuxstate = {"sessions": {}, "clients": [], "next": 900, "start": int(time.time()) + 10}
    e.window("keepe", "j3", cmd="zsh")           # the viewer window of j3, restored as a shell
    e.window("keepe", "scratch", cmd="zsh")
    e.window("ent2", "j1", cmd="zsh")            # the disabled entry came back with the restore (a stale snapshot)
    e.window("ent2", "sh", cmd="zsh")
    e.window("busy", "w", cmd="vim")
    e.write_tmux()
    d = entries_json(e)
    d["disabled"]["busy"] = {"at": 1, "windows": [{"name": "w", "cwd": "/", "session": ""}], "held": [], "stopped": []}
    json.dump(d, open(os.path.join(e.state, "entries.json"), "w"))
    check("after the reboot: the registry still knows j3's entry (viewer not up yet)", "keepe", e.reg()["j3"]["scope"])
    e.reset_logs()
    rc, out, er = e.run("reconcile")
    truth("reconcile: the stash mark is set again (the tmux server is newer than the stash)", "set-option\t-t\t=keepe:\t@fleet-stashed\t1" in tmux_calls(e), str(tmux_calls(e)))
    truth("reconcile (no --boot): nothing killed, no viewer started", not tmux_calls(e, "kill-") and not tmux_calls(e, "respawn-pane") and not tmux_calls(e, "send-keys"), str(tmux_calls(e)))
    truth("reconcile: a disabled entry whose name exists in tmux is reported as a name clash, not touched", "ent2: NAME CLASH" in out and "busy: NAME CLASH" in out, out)
    e.reset_logs()
    rc, out, er = e.run("reconcile", "--boot", "--dry-run")
    truth("reconcile --boot --dry-run: says, does nothing", "viewer put back in keepe:j3" in out and not tmux_calls(e, "send-keys") and not tmux_calls(e, "respawn-pane"), out)
    rc, out, er = e.run("reconcile", "--boot")
    calls = tmux_calls(e)
    truth("reconcile --boot NEVER kills a session, window or pane (a same-named user session survives)", not tmux_calls(e, "kill-") and not any("-k" in c.split("\t") for c in tmux_calls(e, "respawn-pane")), str(calls))
    w = next(w for w in e.read_tmux()["sessions"]["keepe"]["windows"] if w["name"] == "j3")
    pid = w["panes"][0]["id"]
    truth("reconcile --boot: the viewer is TYPED into the restored idle shell (send-keys), nothing respawned", ["send-keys\t-t\t%s\t-l" % pid in " ".join(c.rsplit("\t", 1)[0] for c in calls if c.startswith("send-keys"))][0] and any("view j3 --shell" in c for c in tmux_calls(e, "send-keys")) and not tmux_calls(e, "respawn-pane"), str(calls))
    truth("reconcile --boot: other windows left alone", not any("scratch" in c for c in calls))
    # only a lone idle pane: a window with a split pane, or a shell with a child, is not touched
    e.reset_logs()
    e.tmuxstate = e.read_tmux()
    jw = next(x for x in e.tmuxstate["sessions"]["keepe"]["windows"] if x["name"] == "j3")
    jw["panes"][0].pop("opts", None)      # back to an untagged restored shell
    e.split(jw, cmd="vim")
    e.run("reconcile", "--boot")
    check("reconcile --boot: a window with more than one pane is never typed into", 0, len(tmux_calls(e, "send-keys")))
    # a crashed viewer (old tag, bare shell, recorded pid dead) is repaired without --boot; a live pid or a fresh tag is not
    st = e.read_tmux()
    w = next(w for w in st["sessions"]["keepe"]["windows"] if w["name"] == "j3")
    w["panes"] = w["panes"][:1]
    w["panes"][0]["opts"] = {"@fleet-view": "j3 %d 999999" % (int(time.time()) - 600)}
    json.dump(st, open(os.path.join(e.root, "tmux.json"), "w"))
    e.reset_logs()
    e.run("reconcile")
    check("reconcile: tagged viewer pane, dead recorded pid, bare shell for a minute = repaired (respawn-pane -k)", 1, len(tmux_calls(e, "respawn-pane")))
    st = e.read_tmux()
    w = next(w for w in st["sessions"]["keepe"]["windows"] if w["name"] == "j3")
    w["panes"][0]["cmd"] = "zsh"         # (the fake respawn-pane turned it into the viewer)
    w["panes"][0]["opts"] = {"@fleet-view": "j3 %d %d" % (int(time.time()) - 600, e.spawn())}
    json.dump(st, open(os.path.join(e.root, "tmux.json"), "w"))
    e.reset_logs()
    e.run("reconcile")
    check("reconcile: a live recorded pid is never repaired", 0, len(tmux_calls(e, "respawn-pane")))
    w["panes"][0]["cmd"] = "zsh"
    w["panes"][0]["opts"] = {"@fleet-view": "j3 %d 999999" % int(time.time())}
    json.dump(st, open(os.path.join(e.root, "tmux.json"), "w"))
    e.reset_logs()
    e.run("reconcile")
    check("reconcile: a freshly tagged pane is never repurposed (no race)", 0, len(tmux_calls(e, "respawn-pane")))
    # the same server, a session re-created under a stashed name: not the stashed one, the record goes
    e.tmuxstate = e.read_tmux()
    e.tmuxstate["start"] = 1
    e.tmuxstate["sessions"]["keepe"]["opts"].pop("@fleet-stashed", None)
    e.write_tmux()
    e.reset_logs()
    rc, out, _ = e.run("reconcile")
    truth("reconcile: a session re-created under a stashed name is not re-hidden; the record is dropped", "took the name of a stashed entry" in out and "keepe" not in entries_json(e)["stashed"] and not tmux_calls(e, "set-option"), out)
    # unstash after the reboot still rebuilds from the layout
    e.tmuxstate["sessions"].pop("ent2", None)
    e.write_tmux()
    rc, out, _ = e.run("unstash", "ent2", "--no-switch")
    check("unstash after a reboot: rebuilt from fleet state", 0, rc)


def t_peek(e):
    e.config("j1 dev\n")
    e.job_session("j1", 1, needs="go")
    e.job_session("j2", 2)
    e.viewer("ent", "j1")
    e.clients(("c1", "work", 100), ("c2", "rcterm-9", 300), ("c3", "work", 200))
    rc, out, er = e.run("peek", "j1")
    check("peek: rc", 0, rc)
    pop = tmux_calls(e, "display-popup")
    check("peek: one popup", 1, len(pop))
    truth("peek: on the most recently active client whose session is not skipped (c3, not rcterm c2)", pop and pop[0].startswith("display-popup\t-c\tc3\t-E\t-w\t90%\t-h\t90%"), str(pop))
    truth("peek: the popup runs `fleet view j1 --no-tag` (closing it leaves the job running)", pop and "view j1 --no-tag" in pop[0], str(pop))
    e.reset_logs()
    e.run("peek", "j1", "--client", "c1")
    truth("peek --client overrides", tmux_calls(e, "display-popup")[0].startswith("display-popup\t-c\tc1\t"))
    e.reset_logs()
    e.run("peek", "j1", extra={"FLEET_SKIP": "^work$"})
    truth("FLEET_SKIP changes the skip rule (c2 now wins)", tmux_calls(e, "display-popup")[0].startswith("display-popup\t-c\tc2\t"))
    e.reset_logs()
    e.run("peek")
    truth("peek without a name: the picker", "\tpick" in tmux_calls(e, "display-popup")[0] or " pick" in tmux_calls(e, "display-popup")[0])
    check("peek: unknown session", 1, e.run("peek", "nosuch")[0])
    check("peek: bad name", 2, e.run("peek", "a;b")[0])
    check("peek: unsafe --client", 1, e.run("peek", "j1", "--client", "x y;z")[0])
    e.clients()
    check("peek: no attached client", 1, e.run("peek", "j1")[0])
    # the picker list: waiting first, then by entry
    rc, out, _ = e.run("pick", "--list")
    check("pick --list: waiting sessions first", ["j1", "j2"], [l.split("\t")[0] for l in out.strip().splitlines()])


def t_pick_pty(e):
    import pty
    e.config("alpha-one dev\nmedia-two dev\n")
    e.job_session("alpha-one", 1)
    e.job_session("media-two", 2)
    e.viewer("ent", "alpha-one")
    e.viewer("ent", "media-two")
    e.run("sync")
    m, sl = pty.openpty()
    env = dict(e.env)
    p = subprocess.Popen([FLEET, "pick"], stdin=sl, stdout=sl, stderr=sl, env=env, start_new_session=True)
    os.close(sl)
    time.sleep(1.0)
    os.write(m, b"med")      # type-to-filter
    time.sleep(0.5)
    os.write(m, b"\r")
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()
        bad("picker did not finish")
    os.close(m)
    log = e.log("attach.log")
    check("picker: type to filter, enter opens only the match (view -> claude attach)", ["attach %s mode=detach" % SID[2][:8]], log.splitlines())
    # escape cancels
    rmf(os.path.join(e.root, "attach.log"))
    m, sl = pty.openpty()
    p = subprocess.Popen([FLEET, "pick"], stdin=sl, stdout=sl, stderr=sl, env=env, start_new_session=True)
    os.close(sl)
    time.sleep(1.0)
    os.write(m, b"\x03")
    p.wait(timeout=20)
    os.close(m)
    check("picker: ctrl-c cancels, nothing attached", ("", 0), (e.log("attach.log"), p.returncode))


def t_menu(e):
    e.config("j1 dev\n")
    e.job_session("j1", 1)
    e.viewer("ent", "j1")
    e.clients(("c1", "ent", 100))
    e.jwrite(os.path.join(e.state, "entries.json"), {"stashed": {"st": {"at": 1}, "x'y;#{z}": {"at": 1}}, "disabled": {"dis": {"at": 1, "windows": [{"name": "w", "cwd": "/", "session": ""}]}}})
    rc, out, er = e.run("menu", "--client", "c1")
    check("menu: rc", 0, rc)
    menu = tmux_calls(e, "display-menu")
    check("menu: one display-menu on the clicking client", 1, len(menu))
    f = menu[0].split("\t")
    check("menu: client, title", ("c1", " fleet: ent "), (f[f.index("-c") + 1], f[f.index("-T") + 1]))
    trip = f[f.index("-y") + 2:]
    labels, keys, cmds = trip[0::3], trip[1::3], trip[2::3]
    check("menu: items", ["Peek a session...", "Stash ent (background)", "Disable ent", "Unstash...", "Go home", "List entries"], labels)
    check("menu: keys", ["p", "s", "d", "u", "h", "l"], keys)
    truth("menu: every command runs fleet with --client and quoted arguments", all(c.startswith(("run-shell -b '", "display-popup", "confirm-before")) for c in cmds), str(cmds))
    truth("menu: Stash and Disable ask first (confirm-before), Disable says it stops jobs", cmds[1].startswith("confirm-before -p 'Stash ent? (y/n)' ") and cmds[2].startswith("confirm-before -p 'Disable ent (stops its jobs)? (y/n)' "), str(cmds))
    truth("menu: results are shown, never thrown away (--notify, no /dev/null)", all("/dev/null" not in c for c in cmds) and "stash --client c1 --notify c1 -- ent" in cmds[1] and "stash --disable --client c1 --notify c1 -- ent" in cmds[2] and "home --client c1 --notify c1" in cmds[4], str(cmds))
    truth("m6: stash and disable carry the entry name the label shows (resolved when the menu was built), never --current", "--current" not in cmds[1] and "--current" not in cmds[2] and "-- ent" in cmds[1] and "-- ent" in cmds[2], str(cmds))
    # inside home: stash / disable are disabled items without a command
    e.clients(("c1", "home", 100))
    e.session("home")
    e.window("home", "h")
    e.reset_logs()
    e.run("menu", "--client", "c1")
    f = tmux_calls(e, "display-menu")[0].split("\t")
    trip = f[f.index("-y") + 2:]
    check("menu in home: stash and disable are greyed out and do nothing", (["-Stash home (background)", "-Disable home"], ["", ""]), ([trip[3], trip[6]], [trip[5], trip[8]]))
    # a hostile current session name: sanitised label, no command
    e.clients(("c1", "ev'il;#(touch x)\x1b[31m", 100))
    e.session("ev'il;#(touch x)\x1b[31m")
    e.reset_logs()
    e.run("menu", "--client", "c1")
    raw = tmux_calls(e, "display-menu")
    truth("menu: hostile session name: no escape char, # doubled, items disabled", raw and "\x1b" not in raw[0] and "#(" not in raw[0].replace("##(", "") and raw[0].count("\t-Stash") == 1, repr(raw))
    # unstash submenu
    e.reset_logs()
    e.run("menu", "--unstash", "--client", "c1")
    f = tmux_calls(e, "display-menu")[0].split("\t")
    trip = f[f.index("-y") + 2:]
    labels, keys, cmds = trip[0::3], trip[1::3], trip[2::3]
    check("unstash menu: stashed and disabled entries with their state; an unusable name is greyed out", ["dis  [disabled]", "st  [stashed]", "-x'y;##{z}  [stashed]"], sorted(labels, key=lambda x: x.lstrip("-")))
    truth("unstash menu: command passes the name after `--`, quoted", any("unstash --client c1 --notify c1 -- st" in c and c.startswith("run-shell -b '") for c in cmds), str(cmds))
    truth("unstash menu: the hostile name has no command", [c for l, c in zip(labels, cmds) if l.startswith("-x")] == [""], str(list(zip(labels, cmds))))
    # bar
    check("bar: stashed + disabled count", " · 3", e.run("bar")[1])
    os.unlink(os.path.join(e.state, "entries.json"))
    check("bar: nothing when 0", "", e.run("bar")[1])


def sweep_unit_sockets():
    """sockets of earlier (crashed or killed) runs: only dead fleet-unit-* ones are removed"""
    d = "/tmp/tmux-%d" % os.getuid()
    try:
        names = [n for n in os.listdir(d) if n.startswith("fleet-unit-")]
    except OSError:
        return
    for n in names:
        r = subprocess.run(["/usr/bin/tmux", "-L", n, "list-sessions"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            with contextlib_suppress():
                os.unlink(os.path.join(d, n))


def t_tmux_real(e):
    """quoting and the config against a REAL tmux, private socket (-L), never the user's server"""
    tmux = "/usr/bin/tmux"
    if not os.path.exists(tmux):
        print("  (real tmux missing: skipped)")
        return
    sock = "fleet-unit-%d" % os.getpid()
    T = lambda *a: subprocess.run([tmux, "-L", sock, "-f", "/dev/null"] + list(a), capture_output=True, text=True, timeout=120)
    sweep_unit_sockets()
    try:
        T("new-session", "-d", "-s", "a", "-x", "100", "-y", "30")
        for _ in range(100):  # under load the server can be slow to come up: wait for it, do not guess
            if T("has-session", "-t", "a").returncode == 0:
                break
            time.sleep(0.2)
        out = os.path.join(e.root, "argv.out")
        rec = os.path.join(e.root, "rec.sh")
        open(rec, "w").write('#!/bin/bash\nfor a in "$@"; do printf "%%s\\n" "$a"; done > %s\n' % out)
        os.chmod(rec, 0o755)
        m = load_module()
        old_argv0 = sys.argv[0]
        sys.argv[0] = rec
        os.environ["FAKE_DIR"] = e.root
        try:
            for arg in ["plain", "it's", 'say "hi"', "semi;colon", "#{session_name}", "#(touch %s/pwned)" % e.root, "$(touch %s/pwned2)" % e.root,
                        "back\\slash", "a b  c", "-- --dash", "`id`", "100%", "~"]:
                rmf(out)
                cmd = "run-shell -b " + m.tq(m.fleet_cmd("unstash", "--client", "c1", "--", arg) + " >/dev/null 2>&1")
                r = T("if-shell", "-F", "1", cmd)
                for _ in range(40):
                    if os.path.exists(out):
                        break
                    time.sleep(0.1)
                got = open(out).read().split("\n")[:-1] if os.path.exists(out) else None
                check("tmux parses the menu command back to the exact arguments: %r" % arg, ["unstash", "--client", "c1", "--", arg], got)
            truth("hostile text never ran as a command", not os.path.exists(os.path.join(e.root, "pwned")) and not os.path.exists(os.path.join(e.root, "pwned2")))
            # the commands the menu builds for real
            e.clients(("c1", "ent", 1))
            _, flat = m.menu_args("c1", "ent", {}, {"sessions": {}})
            for label, cmd in zip(flat[0::3], flat[2::3]):
                if cmd.startswith("run-shell"):
                    rmf(out)
                    T("if-shell", "-F", "1", cmd)
                    for _ in range(40):
                        if os.path.exists(out):
                            break
                        time.sleep(0.1)
                    got = open(out).read().split("\n") if os.path.exists(out) else []
                    truth("menu item %r runs fleet with an argument vector tmux parsed intact" % label, got and got[0] in ("peek", "stash", "menu", "home") and "c1" in got, str(got))
        finally:
            sys.argv[0] = old_argv0
            os.environ.pop("FAKE_DIR", None)
        # the labels survive tmux's own format expansion unchanged
        title, flat = m.menu_args("c1", "a#b'c", {}, {"sessions": {}})
        r = T("display-message", "-p", "-t", "a:", "--", flat[0 + 3])
        check("a hostile label survives format expansion verbatim", flat[3].replace("##", "#"), r.stdout.strip())
        # the real config file loads (plugin lines removed: they would run the user's restore on this server)
        conf = open(os.path.join(ROOT, "configs/tmux/.tmux.conf")).read().splitlines()
        keep = [l for l in conf if not l.startswith(("run ", "run-shell", "set -g @plugin"))]
        cp = os.path.join(e.root, "tmux.conf")
        open(cp, "w").write("\n".join(keep) + "\n")
        r = T("source-file", cp)
        check("tmux.conf loads without errors into a private server", "", r.stderr.strip() + r.stdout.strip())
        sr = T("show-options", "-gv", "status-right").stdout
        truth("status-right: the fleet range sits right after the clock, before needs/urgent/cheat", "%H:%M" in sr and sr.index("range=user|fleet") > sr.index("%H:%M") and sr.index("range=user|fleet") < sr.index("range=user|needs") < sr.index("range=user|urgent") < sr.index("range=user|cheat"), sr)
        truth("status-right: harmless without fleet (the #() prints nothing, text is static)", "fleet bar 2>/dev/null" in sr)
        keys = "\n".join(l for l in T("list-keys").stdout.splitlines() if "MouseDown1Status " in l)
        truth("MouseDown1Status: fleet, cheat, urgent and needs branches all present", all(x in keys for x in ("fleet menu", "cheatsheet", "need-attention --open", "notify-user --open", "switch-client")), keys)
        aliases = T("show-options", "-gv", "command-alias").stdout
        truth("command aliases: peek, fleetmenu, cheatsheet, XC", all(x in aliases for x in ("peek=", "fleetmenu=", "cheatsheet=", "XC=")), aliases)
        # the picker filter hides rcterm-* and stashed entries, nothing else
        T("new-session", "-d", "-s", "b")
        T("new-session", "-d", "-s", "rcterm-1")
        T("set-option", "-t", "b:", "@fleet-stashed", "1")
        filt = "#{?#{||:#{m:rcterm-*,#{session_name}},#{==:#{@fleet-stashed},1}},0,1}"
        got = {s: T("display-message", "-p", "-t", s + ":", filt).stdout.strip() for s in ("a", "b", "rcterm-1")}
        check("picker filter: stashed and rcterm-* hidden, others shown", {"a": "1", "b": "0", "rcterm-1": "0"}, got)
        allkeys = T("list-keys").stdout.splitlines()
        truth("the three choose-tree bindings carry the stash filter", sum("choose-tree" in l and "@fleet-stashed" in l for l in allkeys) == 3, str([l for l in allkeys if "choose-tree" in l]))
    finally:
        T("kill-server")
        with contextlib_suppress():
            os.unlink("/tmp/tmux-%d/%s" % (os.getuid(), sock))


def t_view(e):
    e.config("j1 dev\nj2 depl\ndead dev\n")
    e.job_session("j1", 1)
    e.job_session("j2", 2)
    e.pane_session("p1", 3, tmuxsession="x")
    e.run("sync")
    open(os.path.join(e.root, "attach.seq"), "w").write("respawn\nrespawn\ndetach\n")
    rc, out, er = e.run("view", "j1", "--no-tag", timeout=30)
    check("view: ends with rc 0 after a deliberate detach", 0, rc)
    check("view: re-attached after each respawn, ended on the detach", 3, len(e.log("attach.log").splitlines()))
    truth("view: says it detached", "detached from j1" in out, out)
    truth("view: claude attach saw no CLAUDE* variable", all("claudevars=0" in c for c in claude_calls(e)))
    # Ctrl+Z inside the client stops it (it never exits): a deliberate detach
    rmf(os.path.join(e.root, "attach.log"))
    open(os.path.join(e.root, "attach.seq"), "w").write("stop\n")
    t0 = time.time()
    rc, out, er = e.run("view", "j1", "--no-tag", timeout=30)
    truth("view: a stopped client (Ctrl+Z) = detach, rc 0, no hang", rc == 0 and "detached from j1" in out and time.time() - t0 < 10, out + er)
    # the viewer marks its own window, and unmarks it when it ends
    rmf(os.path.join(e.root, "attach.log"))
    e.reset_logs()
    rc, out, _ = e.run("view", "j1", extra={"TMUX": "/tmp/fake,1,0", "TMUX_PANE": "%7"})
    tags = tmux_calls(e, "set-option")
    truth("view: tags its own PANE (name, epoch, its pid) at the start and clears it at the end", len(tags) == 2 and re.match(r"^set-option\t-p\t-t\t%7\t@fleet-view\tj1 \d+ \d+$", tags[0]) and tags[1] == "set-option\t-p\t-u\t-t\t%7\t@fleet-view", str(tags))
    e.reset_logs()
    e.run("view", "j1", "--no-tag", extra={"TMUX": "/tmp/fake,1,0", "TMUX_PANE": "%7"})
    check("view --no-tag (peek popups): never tags a window", 0, len(tmux_calls(e, "set-option")))
    rc, out, _ = e.run("view", "nosuch")
    check("view: unknown name -> rc 1", 1, rc)
    rc, out, _ = e.run("view", "p1")
    check("view: pane session (never a job) -> rc 1", 1, rc)
    # boot order: tmux restore before `fleet up`: wait with a visible countdown, bounded, then give up
    e.job_session("dead", 4, live=False)
    e.run("sync")
    t0 = time.time()
    rc, out, _ = e.run("view", "dead", "--no-tag", extra={"FLEET_VIEW_WAIT": "2"}, timeout=30)
    truth("view: no process yet -> countdown line, bounded wait, then rc 1", rc == 1 and "waiting for dead" in out and "left)" in out and "gave up" in out and time.time() - t0 < 15, out)
    # ... and attaches as soon as the job is there
    rmf(os.path.join(e.root, "attach.log"))
    open(os.path.join(e.root, "attach.seq"), "w").write("detach\n")
    env = dict(e.env)
    env["FLEET_VIEW_WAIT"] = "30"
    p = subprocess.Popen([FLEET, "view", "dead", "--no-tag"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.5)
    pid = e.spawn()
    e.jwrite(os.path.join(e.home, "sessions", "%d.json" % pid), {"pid": pid, "sessionId": SID[4], "cwd": e.workdir("dead"), "kind": "bg", "name": "dead", "jobId": SID[4][:8], "status": "idle"})
    try:
        out, _ = p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        out = "timeout"
    truth("view: waits for the job (boot order), then attaches and ends on the detach", p.returncode == 0 and "waiting for dead" in out and "detached from dead" in out and len(e.log("attach.log").splitlines()) == 1, out)


def t_stopped(e):
    """a job `claude stop`ped on purpose (row state stopped) is not restarted by up / the watchdog; a crash is"""
    e.config("s1 dev always-on\ncrash dev always-on\n")
    ps, js = e.job_session("s1", 1, state="stopped", live=False)
    pc, jc = e.job_session("crash", 2, state="working", live=False)    # a crash / reboot leaves the last working state
    e.run("sync")
    rc, out, _ = e.run("up")
    calls = [c.split("\t")[-1] for c in claude_calls(e)]
    check("up: the crashed job is restarted, the stopped one is not", ["respawn " + jc], calls)
    truth("up: says why it skipped the stopped one", "s1: skipped, stopped on purpose" in out, out)
    e.kill(e.spawn())
    for f in os.listdir(os.path.join(e.home, "sessions")):
        d = json.load(open(os.path.join(e.home, "sessions", f)))
        if d["name"] == "crash":
            os.kill(d["pid"], signal.SIGKILL)
            time.sleep(0.2)
    e.reset_logs()
    e.jwrite(os.path.join(e.home, "jobs", jc, "state.json"), {"state": "working", "name": "crash", "sessionId": SID[2], "cwd": e.workdir("crash"), "createdAt": "2026-11-16T10:00:00Z"})
    e.run("watch", "--once", "--no-up")
    check("watchdog tick: restarts the crashed always-on job, not the stopped one", ["respawn " + jc], [c.split("\t")[-1] for c in claude_calls(e)])
    e.reset_logs()
    rc, out, _ = e.run("up", "--include-stopped")
    check("up --include-stopped starts it", ["respawn " + js], [c.split("\t")[-1] for c in claude_calls(e)])


def t_models(e):
    """no recorded model / effort: refuse and print the manual command; never the defaults"""
    e.config("nomodel dev\nnofx dev\n")
    e.pane_session("nomodel", 1, model="")
    e.pane_session("nofx", 2, effort=None)
    e.run("sync")
    rc, out, _ = e.run("bg", "nomodel", "--dry-run")
    truth("bg: no recorded model -> refused with the manual command", rc == 3 and "no recorded model for nomodel" in out and "--model '<MODEL>'" in out, out)
    rc, out, _ = e.run("bg", "nofx", "--dry-run")
    truth("bg: no recorded effort -> `no recorded effort for nofx`, refused, manual command", rc == 3 and "no recorded effort for nofx" in out and "--effort '<EFFORT>'" in out, out)
    rc, out, _ = e.run("bg", "nofx", "--effort", "low", "--dry-run")
    truth("bg --effort supplies it", rc == 0 and "--effort low" in out, out)
    rc, out, _ = e.run("bg", "nofx", "--effort", "bogus")
    check("bg: bad effort level", 2, rc)
    # up: the resume path (job row gone) refuses the same way
    e.config("gonejob dev\n")
    p1, j1 = e.job_session("gonejob", 5, jobrow=False, live=False)
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    reg["sessions"]["gonejob"] = dict(scope="unsorted", managed=True, dir=e.workdir("gonejob"), session_id=SID[5], job_id=SID[5][:8], kind="gone", state="gone", model="", effort="")
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    rc, out, _ = e.run("up", "--dry-run", "--include-missing")
    truth("up: no recorded model -> not resumed, manual command printed", "no recorded model for gonejob" in out and "--model '<MODEL>'" in out and not claude_calls(e), out)
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    reg["sessions"]["gonejob"]["model"] = "claude-opus-5-5"
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    rc, out, _ = e.run("up", "--dry-run", "--include-missing")
    truth("up: no recorded effort -> not resumed", "no recorded effort for gonejob" in out and not claude_calls(e), out)


def t_permission(e):
    m = load_module()
    pr = os.path.join(e.root, "proc")
    for pid, args in ((11, ["claude", "-n", "x", "--permission-mode", "acceptEdits"]), (12, ["claude", "--permission-mode=plan"]),
                      (13, ["claude", "--dangerously-skip-permissions"]), (14, ["claude", "-n", "x"]), (15, ["claude", "--permission-mode", "bad;mode"])):
        os.makedirs(os.path.join(pr, str(pid)))
        open(os.path.join(pr, str(pid), "cmdline"), "wb").write(("\0".join(args) + "\0").encode())
    os.environ["FLEET_PROC"] = pr
    try:
        got = [m.cmdline_permission_mode(p) for p in (11, 12, 13, 14, 15, 99)]
    finally:
        os.environ.pop("FLEET_PROC", None)
    check("permission mode read from the command line", ["acceptEdits", "plan", "bypassPermissions", "", "", ""], got)
    # recorded: passed on the first move; unknown: a clear warning line
    e.config("pm dev\nnopm dev\n")
    e.pane_session("pm", 1)
    e.pane_session("nopm", 2)
    e.run("sync")
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    reg["sessions"]["pm"]["permission_mode"] = "acceptEdits"
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    rc, out, _ = e.run("bg", "pm")
    truth("bg: a recorded permission mode is passed (--permission-mode)", rc == 0 and claude_calls(e)[0].endswith("--effort high --permission-mode acceptEdits") and "permission mode acceptEdits" in out and "warning" not in out, out + str(claude_calls(e)))
    rc, out, _ = e.run("bg", "nopm")
    truth("bg: no readable permission mode -> a warning line for that session", rc == 0 and "nopm: warning: permission mode unknown" in out and "--permission-mode" not in claude_calls(e)[1], out)
    check("job rows keep it (respawnFlags)", "acceptEdits", e.reg()["pm"]["permission_mode"])


def t_run_claude_stop(e):
    m = load_module()
    os.environ.update({"FLEET_CLAUDE": os.path.join(e.bin, "claude"), "FAKE_DIR": e.root, "FLEET_CLAUDE_HOME": e.home})
    try:
        for sub in ("daemon", "kill", "rm", "purge", "stop"):
            try:
                m.run_claude([sub, "x"])
                bad("run_claude ran %s without permission" % sub)
            except RuntimeError:
                ok()
        for sub in ("daemon", "kill", "rm", "purge"):
            try:
                m.run_claude([sub, "x"], stop_ok=True)
                bad("stop_ok opened %s" % sub)
            except RuntimeError:
                ok()
        rc, _ = m.run_claude(["stop", "abc12345"], stop_ok=True)
        check("stop is allowed only with stop_ok (the disable path)", 0, rc)
    finally:
        for k in ("FLEET_CLAUDE", "FAKE_DIR", "FLEET_CLAUDE_HOME"):
            os.environ.pop(k, None)
    src = open(FLEET).read()
    callers = [l for l in src.splitlines() if "stop_ok=True" in l and "run_claude" in l and not l.lstrip().startswith(("def ", "#", '"""'))]
    check("exactly one code path passes stop_ok=True (stash --disable)", 1, len(callers))

# ================================================================ owns
def t_owns(e):
    e.config("j dev\n")
    e.job_session("j", 1)
    e.pane_session("pp", 2)
    e.run("sync")
    check("owns: a job's session id", (0, "j\n"), e.run("owns", SID[1])[:2])
    check("owns: a pane session is not owned", 1, e.run("owns", SID[2])[0])
    check("owns: unknown id", 1, e.run("owns", SID[30])[0])
    check("owns: malformed id", 1, e.run("owns", "../../etc/passwd")[0])
    shutil.rmtree(os.path.join(e.home, "jobs", SID[1][:8]))
    check("owns: job row gone", 1, e.run("owns", SID[1])[0])
    t0 = time.time()
    e.run("owns", SID[1])
    truth("owns is quick", time.time() - t0 < 1.0)


# ================================================================ hooks
def hook(e, payload, extra=None):
    env = dict(e.env)
    env.pop("CLAUDE_EFFORT", None)
    env.update(extra or {})
    p = subprocess.run(["bash", HOOK], env=env, input=json.dumps(payload), capture_output=True, text=True, timeout=20)
    return p.returncode, p.stdout, p.stderr


def t_hook(e):
    seen = os.path.join(e.state, "seen", SID[1])
    rc, out, er = hook(e, {"hook_event_name": "UserPromptSubmit", "session_id": SID[1]}, {"CLAUDE_EFFORT": "high"})
    check("hook: rc 0, silent", (0, "", ""), (rc, out, er))
    t, eff = open(seen).read().rstrip("\n").split("\t")
    check("hook: effort recorded from CLAUDE_EFFORT", "high", eff)
    truth("hook: time recorded", abs(int(t) - time.time()) < 10)
    hook(e, {"hook_event_name": "Stop", "session_id": SID[1]})
    check("hook: no CLAUDE_EFFORT keeps the last value", "high", open(seen).read().rstrip("\n").split("\t")[1])
    hook(e, {"hook_event_name": "Stop", "session_id": SID[1]}, {"CLAUDE_EFFORT": "max"})
    check("hook: a change is recorded", "max", open(seen).read().rstrip("\n").split("\t")[1])
    hook(e, {"hook_event_name": "Stop", "session_id": SID[1]}, {"CLAUDE_EFFORT": "$(id); rm -rf x"})
    check("hook: an invalid effort is ignored (last value kept)", "max", open(seen).read().rstrip("\n").split("\t")[1])
    rc, out, er = hook(e, {"hook_event_name": "Stop", "session_id": "../../evil"}, {"CLAUDE_EFFORT": "low"})
    truth("hook: bad session id writes nothing", rc == 0 and sorted(os.listdir(os.path.join(e.state, "seen"))) == [SID[1]])
    rc, out, _ = hook(e, {"hook_event_name": "Stop"})
    check("hook: no session id: rc 0", 0, rc)
    check("seen file mode: dir 700", "700", oct(os.stat(os.path.join(e.state, "seen")).st_mode)[-3:])
    # SessionStart prints scope, role, peers, tmux session
    e.config("w1 dev\nw2 depl\nw3 dev\n")
    e.pane_session("w1", 1, tmuxsession="mysess")
    e.job_session("w2", 2)
    e.job_session("w3", 3)
    e.viewer("mysess", "w2")
    e.viewer("other", "w3")
    e.run("sync")
    rc, out, _ = hook(e, {"hook_event_name": "SessionStart", "session_id": SID[1]})
    lines = out.splitlines()
    check("hook SessionStart: rc", 0, rc)
    truth("hook SessionStart: you are / scope / role", lines and 'you are "w1", scope "mysess", role "dev"' in lines[0], out)
    truth("hook SessionStart: peers with directories, only its scope", any("peers in scope mysess: w2 (%s)" % e.workdir("w2") in l for l in lines) and "w3 (" not in out, out)
    truth("hook SessionStart: tmux session of a pane session", "tmux session: mysess" in out, out)
    rc, out, _ = hook(e, {"hook_event_name": "SessionStart", "session_id": SID[2]})
    truth("hook SessionStart: a job gets its entry", 'its entry (the tmux session that views it) is "mysess"' in out, out)
    rc, out, _ = hook(e, {"hook_event_name": "SessionStart", "session_id": SID[25]})
    check("hook SessionStart: unregistered session -> nothing", (0, ""), (rc, out))
    # registry text with control characters is cleaned before it is printed
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    reg["sessions"]["w2"]["dir"] = "/x/\x1b]0;pwned\x07y‮z"
    reg["sessions"]["w2"]["role"] = "dev\x1b[2J"
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    rc, out, _ = hook(e, {"hook_event_name": "SessionStart", "session_id": SID[1]})
    truth("hook SessionStart: no escape / bidi characters printed", "\x1b" not in out and "‮" not in out and "\x07" not in out, repr(out))
    # no state dir at all: silent, rc 0
    shutil.rmtree(e.state)
    rc, out, er = hook(e, {"hook_event_name": "SessionStart", "session_id": SID[1]})
    check("hook SessionStart without state: silent", (0, "", ""), (rc, out, er))


def t_notify_hook(e):
    """notify-hook.sh takes the session name from fleet's ids/<session-id> (a background job has no pane)"""
    stub = os.path.join(e.root, "ny.log")
    nb = os.path.join(e.bin, "ny-stub")
    open(nb, "w").write('#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> %s\n' % stub)
    os.chmod(nb, 0o755)
    os.makedirs(os.path.join(e.state, "ids"), mode=0o700)
    open(os.path.join(e.state, "ids", SID[1]), "w").write("jobname\tsc\tabcdef12\n")
    env = dict(e.env)
    env.update({"NOTIFY_USER_BIN": nb, "NOTIFY_USER_DIR": os.path.join(e.root, "nu"), "NOTIFY_USER_TMUX": os.path.join(e.bin, "tmux"),
                "NOTIFY_HOOK_TTY": ""})

    def call(payload):
        return subprocess.run(["bash", NHOOK], env=env, input=json.dumps(payload), capture_output=True, text=True, timeout=20)
    p = call({"hook_event_name": "Notification", "session_id": SID[1], "cwd": "/some/dir/project", "message": "needs you"})
    check("notify-hook: rc 0, silent", (0, "", ""), (p.returncode, p.stdout, p.stderr))
    check("notify-hook: name from fleet ids", "needs jobname needs you", open(stub).read().strip())
    os.unlink(stub)
    call({"hook_event_name": "Notification", "session_id": SID[2], "cwd": "/some/dir/project", "message": "hi"})
    check("notify-hook: unknown session id falls back to the directory name", "needs project hi", open(stub).read().strip())
    os.unlink(stub)
    call({"hook_event_name": "UserPromptSubmit", "session_id": SID[1], "cwd": "/x"})
    check("notify-hook: fast no-op path kept (nobody waiting)", False, os.path.exists(stub))
    # a hostile name in ids/ is not used
    open(os.path.join(e.state, "ids", SID[3]), "w").write("a b;c\tsc\tabcdef12\n")
    call({"hook_event_name": "Notification", "session_id": SID[3], "cwd": "/some/dir/project", "message": "hi"})
    check("notify-hook: unsafe name in ids/ ignored", "needs project hi", open(stub).read().strip())


# ================================================================ tmux-resurrect guard
def t_resurrect(e):
    stubs = os.path.join(e.root, "rs")
    os.makedirs(stubs)
    rdir = os.path.join(e.root, "resurrect")
    os.makedirs(rdir)
    sid = SID[1]
    open(os.path.join(rdir, "claude_sessions.tsv"), "w").write("sess:1.0\t%s\t%s\n" % (sid, e.workdir("r")))
    open(os.path.join(stubs, "tmux"), "w").write(
        '#!/usr/bin/env bash\ncase "$1" in show) echo "%s" ;; display) echo "sess:1.0" ;; esac\n' % rdir)
    open(os.path.join(stubs, "fleet"), "w").write(
        '#!/usr/bin/env bash\n[[ $1 == owns ]] || exit 9\n[[ -n ${STUB_OWNS:-} ]] && { echo "$STUB_OWNS"; exit 0; }\nexit 1\n')
    for f in ("tmux", "fleet"):
        os.chmod(os.path.join(stubs, f), 0o755)
    base = dict(e.env)
    base.update({"CLAUDE_RESURRECT_TMUX": os.path.join(stubs, "tmux"), "CLAUDE_RESURRECT_DRY": "1", "TMUX_PANE": "%1"})

    def run(path, extra=None):
        env = dict(base)
        env["PATH"] = path
        env.update(extra or {})
        p = subprocess.run(["bash", RESURRECT, "restore"], env=env, capture_output=True, text=True, timeout=20)
        return p.stdout.strip()
    sysbin = "/usr/bin:/bin"
    check("resurrect: fleet owns the id -> a viewer, no second copy", "exec fleet view jobby --shell", run(stubs + ":" + sysbin, {"STUB_OWNS": "jobby"}))
    check("resurrect: fleet does not own it -> resumes as before", "exec claude --resume %s" % sid, run(stubs + ":" + sysbin))
    check("resurrect: no fleet on PATH -> resumes as before", "exec claude --resume %s" % sid, run(sysbin))
    check("resurrect: a hostile name from fleet is not used", "exec claude --resume %s" % sid, run(stubs + ":" + sysbin, {"STUB_OWNS": "x; rm -rf ~"}))


# ================================================================ pass 3: the review's findings
def stops(e):
    return [c.split("\t")[-1] for c in claude_calls(e) if c.split("\t")[-1].startswith("stop ")]


def sessions_file_of(e, name):
    for f in os.listdir(os.path.join(e.home, "sessions")):
        d = json.load(open(os.path.join(e.home, "sessions", f)))
        if d.get("name") == name:
            return os.path.join(e.home, "sessions", f), d
    return None, None


def rename_in_place(e, old, new, n):
    """what /rename does: the live session file and the job row now carry the new name"""
    f, d = sessions_file_of(e, old)
    d["name"] = new
    json.dump(d, open(f, "w"))
    jp = os.path.join(e.home, "jobs", SID[n][:8], "state.json")
    j = json.load(open(jp))
    j["name"] = new
    json.dump(j, open(jp, "w"))


def t_rename(e):
    """finding 1: identity is the session id, never the name"""
    e.config("old dev always-on\nbad dev always-on\nrenamed-job dev always-on\n")
    e.job_session("old", 1)
    e.job_session("bad", 2)
    e.run("sync")
    rename_in_place(e, "old", "renamed-job", 1)
    rename_in_place(e, "bad", "bad name with spaces", 2)      # a name fleet cannot use
    rc, out, _ = e.run("sync")
    r = e.reg()
    check("rename: the row moved to the new name and kept the old one", (["old"], "job", SID[1]), (r["renamed-job"]["former_names"], r["renamed-job"]["kind"], r["renamed-job"]["session_id"]))
    truth("rename: the old name is not a live or managed session any more (only its config line may leave an empty row)", not r.get("old", {}).get("managed") and r.get("old", {}).get("kind", "gone") == "gone")
    check("unusable new name: the row stays, marked untracked, not gone", ("untracked", "bad name with spaces"), (r["bad"]["kind"], r["bad"]["alive_as"]))
    rc, out, _ = e.run("status")
    truth("status reports the untracked live session", "untracked:" in out and "bad name with spaces" in out and "was:old" in out, out)
    e.reset_logs()
    rc, out, _ = e.run("up")
    check("up after a rename: no claude call (no copy, no respawn of a live job)", 0, len(claude_calls(e)))
    truth("up says why", "running under another name" in out, out)
    e.run("watch", "--once", "--no-up")
    e.run("watch", "--once")
    check("watch ticks (with and without the start-up up) after a rename: still nothing started", 0, len(claude_calls(e)))
    # the recheck right before a call: a live process (any name) that holds the id stops the call
    m = load_module()
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    row = m.new_row()
    row.update(managed=True, job_row=True, job_id=SID[1][:8], session_id=SID[1], conv_id=SID[1], conv_how="own", state="working", dir=e.workdir("old"), kind="gone")
    reg2 = {"sessions": {"ghost-row": row}}
    os.environ.update({"FLEET_CLAUDE_HOME": e.home, "FLEET_CLAUDE": os.path.join(e.bin, "claude"), "FAKE_DIR": e.root, "FLEET_STATE_DIR": e.state, "FLEET_RECHECK": "0.01", "FLEET_PIDDOMAIN": PIDDOMAIN})
    try:
        res = m.revive_many(reg2, ["ghost-row"], False)
    finally:
        for k in ("FLEET_CLAUDE_HOME", "FLEET_CLAUDE", "FAKE_DIR", "FLEET_STATE_DIR", "FLEET_RECHECK", "FLEET_PIDDOMAIN"):
            os.environ.pop(k, None)
    truth("revive_many: a live process under another name holds the id -> not started", res[0][1] is None and "already holds this session" in res[0][2], str(res))
    check("... and no claude call was made", 0, len(claude_calls(e)))
    # a real death after the rename is still revived, under the new name
    pid = [d["pid"] for _, d in [sessions_file_of(e, "renamed-job")]][0]
    os.kill(pid, signal.SIGKILL)
    time.sleep(0.2)
    os.unlink(sessions_file_of(e, "renamed-job")[0])
    e.run("sync")
    e.run("watch", "--once", "--no-up")
    check("the renamed always-on job, really dead, is restarted once", ["respawn " + SID[1][:8]], [c.split("\t")[-1] for c in claude_calls(e)])


def t_missing_rows(e):
    """finding 4: removed and finished sessions are not resurrected"""
    e.config("rmd dev\nalw dev always-on\nfin dev\nok dev\n")
    for i, n in enumerate(("rmd", "alw")):
        e.job_session(n, 1 + i, live=False)
    e.job_session("fin", 3, live=False, state="completed")
    e.job_session("ok", 4, live=False, state="blocked")
    e.run("sync")
    for n in (1, 2):                                   # `claude rm` on both: the rows are gone, the registry remembers
        shutil.rmtree(os.path.join(e.home, "jobs", SID[n][:8]))
    e.jwrite(os.path.join(e.home, "statusline-sessions", SID[1] + ".json"), {"model": "claude-opus-5-5"})
    e.run("sync")
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    for n in ("rmd", "alw"):
        reg["sessions"][n].update(model="claude-opus-5-5", effort="high", managed=True)
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    rc, out, _ = e.run("up")
    calls = [c.split("\t")[-1] for c in claude_calls(e)]
    truth("up: a removed job (no row) is not resumed unless always-on", not any("-n rmd" in c for c in calls) and "the job row is gone" in out, out + str(calls))
    truth("up: the always-on one without a row is resumed", any("-n alw" in c for c in calls), str(calls))
    truth("up: a finished job state (completed) is not respawned; blocked is", "fin: skipped, job state completed is not restartable" in out and "respawn " + SID[4][:8] in calls and "respawn " + SID[3][:8] not in calls, out + str(calls))
    e.reset_logs()
    rc, out, _ = e.run("up", "--include-missing")
    truth("up --include-missing resumes the removed one", any("-n rmd" in c for c in [c.split("\t")[-1] for c in claude_calls(e)]), out)
    for st_name in ("failed", "error", "exited", "cancelled", "done-for-good"):
        m = load_module()
        row = m.new_row()
        row.update(managed=True, job_row=True, job_id="abcd1234", state=st_name, dir=e.workdir("x"))
        kind, why, _ = m.revive_plan("x", row)
        check("revive_plan: state %s is not restartable" % st_name, None, kind)
    for st_name in ("working", "done", "blocked", "waiting", "crashed"):
        row = m.new_row()
        row.update(managed=True, job_row=True, job_id="abcd1234", state=st_name, dir=e.workdir("x"), end_kind="stale", conv_how="own")
        check("revive_plan: state %s is restartable" % st_name, "respawn", m.revive_plan("x", row)[0])


def t_corrupt(e):
    """finding 5: a damaged registry is never started over"""
    e.config("a dev\n")
    e.job_session("a", 1)
    e.run("sync")
    e.run("hold", "a")
    rp = os.path.join(e.state, "registry.json")
    good = open(rp).read()
    check("backups: one before every mutating command", True, len(os.listdir(os.path.join(e.state, "backups"))) >= 1)
    open(rp, "w").write("{ torn write")
    for args in (("bg", "a"), ("up",), ("hold", "a"), ("release", "a"), ("sync",)):
        e.reset_logs()
        rc, out, er = e.run(*args)
        truth("corrupt registry: `fleet %s` refuses (rc 3) and says what to do" % " ".join(args), rc == 3 and "fleet recover" in er and not claude_calls(e), "%s %s" % (rc, er))
    truth("the damaged file was moved aside, not overwritten", not os.path.exists(rp) and any(f.startswith("registry.json.corrupt-") for f in os.listdir(e.state)))
    truth("a recovery flag stops every later mutation (an absent file must not mean 'start fresh')", os.path.exists(os.path.join(e.state, "registry.needs-recovery")))
    rc, out, er = e.run("status")
    truth("read-only commands say so and show what they can (rc 0)", rc == 0 and "WARNING" in er, er)
    rc, out, er = e.run("list")
    check("list also works read-only", 0, rc)
    e.run("watch", "--once")
    check("the watchdog starts nothing on a damaged registry", 0, len(claude_calls(e)))
    check("... and tells the user once", 1, len([l for l in e.log("notify.log").splitlines() if "registry unreadable" in l]))
    bk = sorted(os.listdir(os.path.join(e.state, "backups")))
    bk = [b for b in bk if b.startswith("registry-")]
    rc, out, er = e.run("recover", "--from", os.path.join(e.state, "backups", bk[-1]))
    check("recover --from <backup>", 0, rc)
    rc, out, er = e.run("sync")
    check("after recover the commands work again", 0, rc)
    rc, out, er = e.run("recover", "--from", "/nonexistent")
    check("recover with nothing to recover says so", 0, rc)
    open(rp, "w").write("")
    rc, out, er = e.run("hold", "a")
    check("an empty registry file counts as damaged", 3, rc)
    rc, out, _ = e.run("recover", "--fresh")
    rc, out, _ = e.run("sync")
    check("recover --fresh starts empty on purpose", 0, rc)
    # ten backups at most
    for i in range(14):
        e.run("hold", "a")
    check("backups: the 10 newest are kept", True, 1 <= len([b for b in os.listdir(os.path.join(e.state, "backups")) if b.startswith("registry-")]) <= 10)
    src = open(FLEET).read()
    truth("the atomic write fsyncs the file and its directory", src.count("os.fsync") >= 2)


def t_brake_lock(e):
    """finding 6: a bad read is not a mass death; one action at a time"""
    e.config("".join("j%d dev always-on\n" % i for i in range(1, 6)))
    pids = [e.job_session("j%d" % i, i)[0] for i in range(1, 6)]
    e.run("sync")
    env3 = {"FLEET_BRAKE_MAX": "3"}
    # the sessions dir becomes unreadable / unparseable: nothing is live any more
    sdir = os.path.join(e.home, "sessions")
    for f in os.listdir(sdir):
        open(os.path.join(sdir, f), "w").write("{ torn")
    e.reset_logs()
    rc, out, _ = e.run("up", extra=env3)
    truth("up: sessions unreadable while their processes still run -> brake, rc 3, no claude call", rc == 3 and "brake:" in out and not claude_calls(e), out)
    truth("the brake text names what happened and the exact commands to run", "`fleet status`" in out and "`claude agents`" in out and "still run" in out, out)
    rc, out, _ = e.run("watch", "--once", extra=env3)
    rc, out, _ = e.run("watch", "--once", extra=env3)
    check("watch ticks under the brake: nothing started", 0, len(claude_calls(e)))
    brake_notes = [l for l in e.log("notify.log").splitlines() if "brake:" in l]
    check("the brake raises ONE notification", 1, len(brake_notes))
    truth("... and it carries the command to run (a notification is capped at 240 chars)", "fleet status" in brake_notes[0] and "claude agents" in brake_notes[0], brake_notes[0])
    # M1: no read-only command (and no plain sync) launders the brake by saving the bad read
    before = open(os.path.join(e.state, "registry.json")).read()
    for args in (("status",), ("status", "--json"), ("waiting",), ("list",), ("sync",), ("peek", "j1"), ("pick", "--list"), ("reconcile", "--dry-run")):
        e.run(*args, extra=env3)
    check("M1: status / waiting / list / sync / peek / pick wrote nothing (registry bytes unchanged)", before, open(os.path.join(e.state, "registry.json")).read())
    reg = e.reg()
    check("M1: every row still says job (not gone)", ["job"] * 5, [reg["j%d" % i]["kind"] for i in range(1, 6)])
    check("M1: the rows kept their last pid and start time", (True, True), (all(reg["j%d" % i]["last_pid"] == pids[i - 1] for i in range(1, 6)), all(reg["j%d" % i]["last_start"] for i in range(1, 6))))
    e.reset_logs()
    rc, out, _ = e.run("up", extra=env3)
    truth("M1: `up` after the look-around is still braked", rc == 3 and not claude_calls(e), out)
    e.run("watch", "--once", "--no-up", extra=env3)
    check("M1: the watch after the look-around is still braked: nothing started", 0, len(claude_calls(e)))
    truth("M1: every original process is still alive", all(os.path.exists("/proc/%d" % p) for p in pids))
    # M4: the processes are really dead now (a daemon loss): that is a real death, restarted, with a notice
    for p in pids:
        e.kill(p)
    for f in os.listdir(sdir):
        os.unlink(os.path.join(sdir, f))
    e.reset_logs()
    e.run("watch", "--once", "--no-up", extra=env3)
    check("M4: 5 jobs really dead at once: all 5 are restarted", 5, len(claude_calls(e)))
    truth("M4: ... and the notice says N died and are restarted", any("5 background sessions died at once" in l and "restarting" in l for l in e.log("notify.log").splitlines()), e.log("notify.log"))
    rc, out, _ = e.run("status", "--json", extra=env3)
    check("M4: and they are jobs again", ["job"] * 5, [json.loads(out)["j%d" % i]["kind"] for i in range(1, 6)])
    # fewer than the limit gone: normal
    e2 = Env()
    try:
        e2.config("a dev always-on\nb dev always-on\n")
        pa = e2.job_session("a", 1)[0]
        e2.job_session("b", 2)
        e2.run("sync")
        os.kill(pa, signal.SIGKILL)
        time.sleep(0.2)
        os.unlink(sessions_file_of(e2, "a")[0])
        e2.run("watch", "--once", "--no-up", extra=env3)
        check("one job really dying is restarted (the brake does not block normal deaths)", 1, len(claude_calls(e2)))
        # the second read: the session reappears while the plan waits -> not started
        pb = sessions_file_of(e2, "b")
        os.kill(pb[1]["pid"], signal.SIGKILL)
        time.sleep(0.2)
        os.unlink(pb[0])
        e2.run("sync")
        e2.reset_logs()
        import threading

        def back():
            time.sleep(0.3)
            pid = e2.spawn()
            e2.jwrite(os.path.join(e2.home, "sessions", "%d.json" % pid), {"pid": pid, "sessionId": SID[2], "cwd": e2.workdir("b"), "kind": "bg", "name": "b", "jobId": SID[2][:8], "status": "idle"})
        t = threading.Thread(target=back)
        t.start()
        rc, out, _ = e2.run("up", extra={"FLEET_RECHECK": "1.0"})
        t.join()
        check("up: a session that reappears during the second read is not started again", 0, len(claude_calls(e2)))
    finally:
        e2.cleanup()
    # the lock: a second action while one runs exits, it does not wait or race
    import fcntl
    lf = open(os.path.join(e.state, "action.lock"), "w")
    fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    e.reset_logs()
    for args in (("up",), ("bg", "j1"), ("stash", "--disable", "x"), ("unstash", "x")):
        rc, out, er = e.run(*args)
        truth("`fleet %s` while another action runs: rc 3, 'another fleet action is running'" % " ".join(args),
              rc == 3 and "another fleet action is running" in er or (args[0] in ("stash", "unstash") and rc in (1, 2, 3)), "%s %s" % (rc, er))
    lf.close()
    rc, out, er = e.run("up", "--dry-run")
    truth("up --dry-run needs no lock", "another fleet action" not in er)


def t_bg_races(e):
    """finding 7"""
    e.config("alpha dev\nbeta dev\n")
    pid, pane = e.pane_session("alpha", 1)
    # the row can be dropped by a concurrent sync after /exit: bg must still finish and report
    import threading
    stop = []

    def syncer():
        while not stop:
            e.run("sync")
    t = threading.Thread(target=syncer)
    t.start()
    try:
        rc, out, er = e.run("bg", "alpha")
    finally:
        stop.append(1)
        t.join()
    truth("bg while a watchdog-style sync runs in a loop: rc 0, moved, no traceback", rc == 0 and "moved: alpha" in out and "Traceback" not in er, out + er)
    check("... and the row is intact (id history kept, a job now)", ("job", SID[1]), (e.reg()["alpha"]["kind"], e.reg()["alpha"]["session_id"]))
    # a row being moved is never dropped
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    m = load_module()
    row = m.new_row()
    row.update(kind="gone", managed=False, moving_until=int(time.time()) + 100)
    reg["sessions"]["mv"] = row
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    e.run("sync")
    truth("sync keeps a row whose move is under way", "mv" in e.reg())
    # two moves of one name: the second is refused (flag) even before any lock
    e.pane_session("beta", 2)
    e.run("sync")
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    reg["sessions"]["beta"]["moving_until"] = int(time.time()) + 100
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    rc, out, _ = e.run("bg", "beta", "--dry-run")
    truth("bg: a name already being moved is refused", rc == 3 and "already being moved" in out, out)
    # claude is the pane's own process (exec claude --resume from the tmux restore): never /exit it
    e2 = Env()
    try:
        e2.config("rooted dev\n")
        pid, pane = e2.pane_session("rooted", 1)
        for sess in e2.tmuxstate["sessions"].values():
            for w in sess["windows"]:
                if w["name"] == "rooted":
                    w["panes"][0]["shellpid"] = pid
        e2.write_tmux()
        rc, out, _ = e2.run("bg", "rooted", "--force")
        truth("bg: claude as the pane's root process is refused (the pane would close), nothing sent", rc == 3 and "pane's own process" in out and not tmux_calls(e2, "send-keys") and not claude_calls(e2), out)
    finally:
        e2.cleanup()


def t_disable_untracked(e):
    """finding 8: nothing Claude, and nothing busy, dies unseen"""
    e.config("tracked dev\n")
    e.job_session("tracked", 5)
    e.viewer("ent", "tracked")
    # an UNNAMED Claude (name == id prefix) and one with an unusable name, in panes of the entry
    for nm, n in ((SID[6][:8], 6), ("bad name!", 7)):
        sh = subprocess.Popen(["bash", "-c", "%s 600; true" % os.path.join(e.root, "procbin", "claude")], start_new_session=True)
        e.pids.append(sh)
        time.sleep(0.2)
        kid = int(subprocess.run(["pgrep", "-P", str(sh.pid)], capture_output=True, text=True).stdout.split()[0])
        e.jwrite(os.path.join(e.home, "sessions", "%d.json" % kid), {"pid": kid, "sessionId": SID[n], "cwd": "/", "kind": "interactive", "name": nm, "status": "idle"})
        w = e.window("ent", "w%d" % n, cmd="zsh")
        w["panes"][0]["shellpid"] = sh.pid
    e.write_tmux()
    e.run("sync")
    rc, out, _ = e.run("stash", "--disable", "ent", "--force")
    truth("disable --force: untracked Claude processes (unnamed, bad name) are a hard refusal", rc == 3 and out.count("a Claude process runs here") == 2 and "know" in out and not stops(e) and not tmux_calls(e, "kill-"), out)
    e2 = Env()
    try:
        # a shell with a backgrounded job: not an idle shell
        e2.config("t dev\n")
        e2.job_session("t", 1)
        e2.viewer("ent", "t")
        sh = subprocess.Popen(["bash", "-c", "sleep 600 & wait"], start_new_session=True)
        e2.pids.append(sh)
        time.sleep(0.3)
        w = e2.window("ent", "bgjob", cmd="zsh")
        w["panes"][0]["shellpid"] = sh.pid
        e2.write_tmux()
        rc, out, _ = e2.run("stash", "--disable", "ent")
        truth("disable: a shell with a background child is not idle (refused, child named)", rc == 3 and "not idle" in out and "sleep" in out and not stops(e2), out)
        rc, out, _ = e2.run("stash", "--disable", "ent", "--force")
        truth("disable --force: closes it (the user said so)", rc == 0 and tmux_calls(e2, "kill-session"), out)
    finally:
        e2.cleanup()


def t_disable_busy(e):
    """finding 2 (+12): busy and blocked jobs; partial stop failure; only jobs fleet stopped are recorded"""
    e.config("busy dev\nblk dev\nok dev\n")
    e.job_session("busy", 1, status="busy")
    e.job_session("blk", 2, needs="approve the deploy")
    e.job_session("ok", 3)
    for n in ("busy", "blk", "ok"):
        e.viewer("ent", n)
    e.run("sync")
    rc, out, _ = e.run("stash", "--disable", "ent")
    truth("disable: a busy job and a job blocked on a need are refused, listed separately", rc == 3 and "busy: busy (busy)" in out and "blocked on a need: blk" in out and not stops(e) and not tmux_calls(e, "kill-"), out)
    rc, out, _ = e.run("stash", "--disable", "ent", "--force")
    truth("--force overrides, and prints each one", rc == 0 and "--force: stopping busy job busy" in out and "--force: stopping job blocked on a need: blk" in out and len(stops(e)) == 3, out)
    # partial failure
    e2 = Env()
    try:
        e2.config("j1 dev\nj2 dev\nj3 dev\n")
        for i, n in enumerate(("j1", "j2", "j3")):
            e2.job_session(n, 1 + i)
            e2.viewer("ent", n)
        e2.run("sync")
        rc, out, er = e2.run("stash", "--disable", "ent", extra={"FAKE_STOP_FAIL_JOB": SID[2][:8]})
        calls = [c.split("\t")[-1] for c in claude_calls(e2)]
        check("partial failure: rc 1", 1, rc)
        truth("partial failure: the jobs already stopped are reported and brought back (respawn)", "Already stopped by this run: j1" in er and "respawn " + SID[1][:8] in calls and "stop " + SID[1][:8] in calls, er + str(calls))
        truth("partial failure: tmux untouched, nothing recorded, holds undone", not tmux_calls(e2, "kill-") and not entries_json(e2)["disabled"] and not any(r["hold"] for r in e2.reg().values()), er)
        # a job the user stopped earlier is not restarted by unstash; only jobs fleet stopped are recorded
        e3 = Env()
        try:
            e3.config("run dev\nold dev\n")
            e3.job_session("run", 1)
            e3.job_session("old", 2, live=False, state="stopped")
            e3.viewer("ent", "run")
            e3.viewer("ent", "old")
            e3.run("sync")
            rc, out, _ = e3.run("stash", "--disable", "ent")
            check("disable: only the running job is stopped and recorded", (["run"], ["run"]), (entries_json(e3)["disabled"]["ent"]["stopped"], entries_json(e3)["disabled"]["ent"]["held"]))
            e3.reset_logs()
            rc, out, _ = e3.run("unstash", "ent", "--no-switch")
            check("unstash: only what fleet stopped is resumed (the user's earlier stop stays)", ["respawn " + SID[1][:8]], [c.split("\t")[-1] for c in claude_calls(e3)])
        finally:
            e3.cleanup()
    finally:
        e2.cleanup()


def t_multipane(e):
    """finding 9: per-pane tags and layouts"""
    e.config("j1 dev\n")
    e.job_session("j1", 1)
    w = e.viewer("ent", "j1")
    e.split(w, cmd="vim", cwd=e.workdir("vimpane"))             # the user split a pane off the viewer window and runs vim
    rc, out, _ = e.run("stash", "--disable", "ent")
    truth("disable: a user's vim in a split pane of a viewer window is a blocker (the tag is per pane)", rc == 3 and "vim" in out and not stops(e), out)
    e.tmuxstate = e.read_tmux()
    e.tmuxstate["sessions"]["ent"]["windows"][0]["panes"][1]["cmd"] = "zsh"       # idle shell now
    e.write_tmux()
    rc, out, _ = e.run("stash", "--disable", "ent")
    check("disable: viewer + idle shell pane is fine", 0, rc)
    lay = entries_json(e)["disabled"]["ent"]["windows"]
    check("layout: ALL panes saved (cwd per pane, which one views Claude), an even layout", (2, [e.workdir("j1"), e.workdir("vimpane")], ["j1", ""], "tiled"),
          (len(lay[0]["panes"]), [x["cwd"] for x in lay[0]["panes"]], [x["session"] for x in lay[0]["panes"]], lay[0]["layout"]))
    e.reset_logs()
    e.tmuxstate = e.read_tmux()
    e.clients(("c1", "home", 1))
    rc, out, _ = e.run("unstash", "ent", "--client", "c1")
    calls = tmux_calls(e)
    truth("unstash: the window is rebuilt with every pane (new-session, split-window, select-layout)", any(c.startswith("new-session") for c in calls) and any(c.startswith("split-window") and e.workdir("vimpane") in c for c in calls) and any(c.startswith("select-layout") and c.endswith("tiled") for c in calls), str(calls))
    st = e.read_tmux()["sessions"]["ent"]["windows"][0]["panes"]
    check("unstash: only the viewer pane is tagged, per pane", [True, False], [bool(p.get("opts", {}).get("@fleet-view", "").startswith("j1 ")) for p in st])
    # reconcile (every tick) must not turn the user's shell pane next to a viewer into a second viewer
    e.reset_logs()
    e.tmuxstate = e.read_tmux()
    pane_user = e.tmuxstate["sessions"]["ent"]["windows"][0]["panes"][1]
    pane_user["opts"] = {}
    e.tmuxstate["sessions"]["ent"]["windows"][0]["panes"][0]["opts"] = {"@fleet-view": "j1 %d %d" % (int(time.time()) - 900, e.spawn())}
    e.write_tmux()
    e.run("reconcile")
    e.run("watch", "--once", "--no-up")
    check("reconcile / watchdog: never respawn-pane -k a user's bare shell next to a viewer", 0, len(tmux_calls(e, "respawn-pane")))


def t_resurrect_drop(e):
    """finding 10: a disabled entry is dropped from the resurrect snapshot (post-save hook), nothing is killed at boot"""
    rdir = os.path.join(e.root, "resurrect")
    os.makedirs(rdir)
    stubs = os.path.join(e.root, "rs")
    os.makedirs(stubs)
    live = os.path.join(e.root, "live-sessions")   # the tmux sessions that exist at save time, one per line
    open(os.path.join(stubs, "tmux"), "w").write('#!/usr/bin/env bash\ncase "$1" in show) echo "%s" ;; list-panes) ;; '
                                                 'has-session) n=${3#=}; grep -qx -- "$n" "%s" 2>/dev/null; exit $? ;; esac\n' % (rdir, live))
    os.chmod(os.path.join(stubs, "tmux"), 0o755)
    snap = "\n".join([
        "pane\twork\t1\t1\t:*\t1\tbash\t:/data/u/a\t1\tbash\t:",
        "pane\ttrip\t1\t1\t:*\t1\tbash\t:/data/u/b\t1\tbash\t:",
        "pane\ttrip\t2\t0\t:\t1\tclaude\t:/data/u/b\t1\tclaude\t:claude --resume X",
        "pane\trcterm-1\t1\t1\t:*\t1\tbash\t:/\t1\tbash\t:",
        "pane\ttrip2\t1\t1\t:*\t1\tvim\t:/data/u/c\t1\tvim\t:",
        "window\twork\t1\t:bash\t1\t:*\tae3e,174x40,0,0,1\t:",
        "window\ttrip\t1\t:bash\t1\t:*\tae3e,174x40,0,0,2\t:",
        "window\ttrip\t2\t:claude\t0\t:\tae3f,174x40,0,0,3\t:",
        "window\ttrip2\t1\t:vim\t1\t:*\tae3e,174x40,0,0,4\t:",
        "grouped_session\ttrip-g\ttrip\t:\t:0\t:",
        "state\ttrip\twork", ""])
    fp = os.path.join(rdir, "tmux_resurrect_x.txt")
    env = dict(e.env)
    env.update({"CLAUDE_RESURRECT_TMUX": os.path.join(stubs, "tmux"), "PATH": stubs + ":/usr/bin:/bin"})
    open(fp, "w").write(snap)
    p = subprocess.run(["bash", RESURRECT, "save", fp], env=env, capture_output=True, text=True, timeout=30)
    got = open(fp).read().splitlines()
    check("save hook without fleet state: the snapshot is unchanged", snap.splitlines(), got)
    os.makedirs(e.state, mode=0o700, exist_ok=True)
    json.dump({"stashed": {}, "disabled": {"trip": {"at": 1, "windows": []}, "evil\ttab": {"at": 1, "windows": []}, "a;b": {"at": 1, "windows": []}}}, open(os.path.join(e.state, "entries.json"), "w"))
    open(fp, "w").write(snap)
    p = subprocess.run(["bash", RESURRECT, "save", fp], env=env, capture_output=True, text=True, timeout=30)
    got = open(fp).read().splitlines()
    check("save hook: the disabled entry's pane, window, grouped and client lines are gone", [], [l for l in got if "\ttrip\t" in l or l.startswith("grouped_session\ttrip-g") or l.startswith("state\ttrip")])
    truth("save hook: other sessions untouched (a name that merely starts the same too)", any("\twork\t" in l for l in got) and any("\ttrip2\t" in l for l in got), str(got))
    truth("save hook: unsafe names in entries.json never reach awk", p.returncode == 0 and "evil" not in p.stderr)
    # M6: a disabled entry's name that exists as a tmux session at save time is the user's NEW session: never stripped
    open(live, "w").write("trip\n")
    open(fp, "w").write(snap)
    p = subprocess.run(["bash", RESURRECT, "save", fp], env=env, capture_output=True, text=True, timeout=30)
    check("M6: a live tmux session reusing a disabled name keeps its windows, panes and claude pane in the snapshot", snap.splitlines(), open(fp).read().splitlines())
    open(live, "w").write("other\n")
    open(fp, "w").write(snap)
    p = subprocess.run(["bash", RESURRECT, "save", fp], env=env, capture_output=True, text=True, timeout=30)
    truth("M6: when no tmux session has that name the disabled entry is dropped as before",
          not any("\ttrip\t" in l for l in open(fp).read().splitlines()))
    os.unlink(os.path.join(e.state, "entries.json"))
    open(fp, "w").write(snap)
    subprocess.run(["bash", RESURRECT, "save", fp], env=env, capture_output=True, text=True, timeout=30)
    truth("save hook: no entries.json -> no change", any("\ttrip\t" in l for l in open(fp).read().splitlines()))


def t_notify_target(e):
    """finding 11: a pane session keeps its jump target; the fleet name is only for sessions without a pane"""
    stub = os.path.join(e.root, "ny.log")
    nb = os.path.join(e.bin, "ny-stub2")
    open(nb, "w").write('#!/usr/bin/env bash\nprintf "%%s|%%s\\n" "$NOTIFY_USER_TARGET" "$*" >> %s\n' % stub)
    os.chmod(nb, 0o755)
    tb = os.path.join(e.bin, "tmux-stub")
    open(tb, "w").write('#!/usr/bin/env bash\nprintf "/dev/pts/9\\tsess:2\\twinname\\n"\n')
    os.chmod(tb, 0o755)
    os.makedirs(os.path.join(e.state, "ids"), mode=0o700)
    open(os.path.join(e.state, "ids", SID[1]), "w").write("fleetname\tsc\t\n")
    env = dict(e.env)
    env.update({"NOTIFY_USER_BIN": nb, "NOTIFY_USER_DIR": os.path.join(e.root, "nu"), "NOTIFY_USER_TMUX": tb, "NOTIFY_HOOK_TTY": "/dev/pts/9"})
    subprocess.run(["bash", NHOOK], env=env, input=json.dumps({"hook_event_name": "Notification", "session_id": SID[1], "cwd": "/x/p", "message": "hi"}), capture_output=True, text=True, timeout=20)
    check("a pane session: window name and jump target, even with an ids/ entry", "sess:2|needs winname hi", open(stub).read().strip())
    os.unlink(stub)
    env["NOTIFY_HOOK_TTY"] = "/dev/pts/77"
    subprocess.run(["bash", NHOOK], env=env, input=json.dumps({"hook_event_name": "Notification", "session_id": SID[1], "cwd": "/x/p", "message": "hi"}), capture_output=True, text=True, timeout=20)
    check("no pane found: the fleet name (no target)", "|needs fleetname hi", open(stub).read().strip())


def t_minors(e):
    # 14: the saved flags must carry the requested model
    e.config("m dev\n")
    e.pane_session("m", 1)
    rc, out, _ = e.run("bg", "m", extra={"FAKE_BG_MODEL": "some-other-model"})
    truth("bg: a job whose saved flags carry another model is reported as wrong", rc == 1 and "saved flags do not carry the requested --model" in out, out)
    # 15: a claude timeout says "check first"
    e2 = Env()
    try:
        e2.config("t dev\n")
        e2.pane_session("t", 1)
        rc, out, _ = e2.run("bg", "t", extra={"FAKE_BG_SLEEP": "3", "FLEET_CLAUDE_TIMEOUT": "1"})
        truth("bg: a claude timeout -> 'check claude agents FIRST', not 'run by hand'", rc == 1 and "check `claude agents` FIRST" in out and "Run by hand" not in out, out)
    finally:
        e2.cleanup()
    # 13: the recorded effort and its age are printed
    e3 = Env()
    try:
        e3.config("a dev\n")
        e3.pane_session("a", 1)
        rc, out, _ = e3.run("bg", "a", "--dry-run")
        truth("bg: prints the recorded effort and how old it is", "[effort high, from the effort hook, 2m ago]" in out and "[model claude-opus-5-5, from the statusline file]" in out, out)
        rc, out, _ = e3.run("bg", "a", "--dry-run", "--effort", "low")
        truth("bg --effort: no age (it is explicit)", "[effort low, from --effort]" in out, out)
    finally:
        e3.cleanup()
    # 16: --current needs --client or a tmux pane
    e4 = Env()
    try:
        e4.config("j dev\n")
        e4.job_session("j", 1)
        e4.viewer("ent", "j")
        e4.clients(("c1", "ent", 100))
        rc, out, er = e4.run("stash", "--current", "--disable", "--dry-run")
        truth("stash --current without --client or a pane: refused (never 'the entry of whichever client was last active')", rc == 2 and "--current needs --client" in er, er)
        st = e4.read_tmux()
        pane = st["sessions"]["ent"]["windows"][0]["panes"][0]["id"]
        rc, out, er = e4.run("stash", "--current", "--disable", "--dry-run", extra={"TMUX": "/tmp/x,1,0", "TMUX_PANE": pane})
        truth("stash --current from a tmux pane: that pane's entry", rc == 0 and "would save" in out, out + er)
    finally:
        e4.cleanup()
    # 19: a path keeps its exact characters (no NFKC)
    e5 = Env()
    try:
        d = os.path.join(e5.root, "work", "dir\ufb01x")          # a compatibility character in a real path
        os.makedirs(d)
        e5.config("p dev\n")
        pid = e5.spawn()
        e5.jwrite(os.path.join(e5.home, "sessions", "%d.json" % pid), {"pid": pid, "sessionId": SID[1], "cwd": d, "kind": "bg", "name": "p", "jobId": SID[1][:8], "status": "idle"})
        e5.run("sync")
        check("a directory with a compatibility character is recorded exactly", d, e5.reg()["p"]["dir"])
    finally:
        e5.cleanup()
    # 20: respawn with the directory gone runs from the user's home, not the caller's cwd
    e6 = Env()
    try:
        e6.config("g dev\n")
        pg, jg = e6.job_session("g", 1, live=False, state="working")
        e6.run("sync")
        shutil.rmtree(e6.workdir("g"))
        e6.run("up")
        truth("respawn with the directory gone: cwd is HOME", any(("cwd=%s\t" % e6.env["HOME"]) in c for c in claude_calls(e6)), str(claude_calls(e6)))
    finally:
        e6.cleanup()
    # 22: the API handler has a timeout
    m = load_module()
    srv = m.make_server(m.Snap(), 0)
    try:
        check("API: a stalled client cannot hold a thread for ever (handler timeout)", 10, srv.RequestHandlerClass.timeout)
    finally:
        srv.server_close()


def t_systemd_scope(e):
    """finding 3, the part a stub can prove: under systemd every background claude gets a scope of its own"""
    fake = os.path.join(e.bin, "systemd-run-fake")
    open(fake, "w").write('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$FAKE_DIR/sd.log"\nwhile [[ $1 != -- ]]; do shift; done; shift; exec "$@"\n')
    os.chmod(fake, 0o755)
    e.config("a dev\n")
    pa, ja = e.job_session("a", 1, live=False, state="working")
    e.run("sync")
    rc, out, _ = e.run("up", extra={"FLEET_FORCE_SYSTEMD": "1", "FLEET_SYSTEMD_RUN": fake})
    log = e.log("sd.log")
    truth("under systemd: the respawn runs through `systemd-run --user --scope ... -- claude respawn <job>`", "--user --scope" in log and "respawn %s" % ja in log and "--collect" in log, log)
    e.reset_logs()
    rmf(os.path.join(e.root, "sd.log"))
    os.kill([d for _, d in [sessions_file_of(e, "a")]][0]["pid"], signal.SIGKILL)
    time.sleep(0.2)
    os.unlink(sessions_file_of(e, "a")[0])
    e.run("sync")
    e.run("up")
    check("outside systemd: a plain exec (no systemd-run)", "", e.log("sd.log"))
    unit = open(os.path.join(ROOT, "configs/systemd-user/fleet-watch.service")).read()
    truth("the unit: KillMode=process and the boot flag", "KillMode=process" in unit and "fleet watch --boot" in unit)
    for sub in ("stop", "kill"):
        truth("only --bg and respawn are wrapped: `claude %s` never" % sub, 'args[0] in ("--bg", "respawn")' in open(FLEET).read())


def t_menu_confirm_real(e):
    """finding 2: Stash / Disable in the REAL tmux menu ask first (confirm-before); 'n' does nothing, 'y' runs fleet"""
    tmux = "/usr/bin/tmux"
    if not os.path.exists(tmux):
        return
    import pty
    sock = "fleet-unit-%d-c" % os.getpid()
    T = lambda *a: subprocess.run([tmux, "-L", sock, "-f", "/dev/null"] + list(a), capture_output=True, text=True, timeout=60)
    cpid = None
    mfd = None
    try:
        T("new-session", "-d", "-s", "ent", "-x", "120", "-y", "30")
        m = load_module()
        rec = os.path.join(e.root, "rec2.sh")
        out = os.path.join(e.root, "argv2.out")
        open(rec, "w").write('#!/bin/bash\nfor a in "$@"; do printf "%%s\\n" "$a"; done >> %s\n' % out)
        os.chmod(rec, 0o755)
        env = dict(os.environ)
        env.pop("TMUX", None)
        cpid, mfd = pty.fork()
        if cpid == 0:
            os.execve(tmux, [tmux, "-L", sock, "-f", "/dev/null", "attach", "-t", "ent"], env)
        time.sleep(1.5)
        cl = T("list-clients", "-F", "#{client_name}").stdout.split()
        truth("a real client attached to the private server", len(cl) == 1, str(cl))
        old = sys.argv[0]
        sys.argv[0] = rec
        try:
            _, flat = m.menu_args(cl[0], "ent", {}, {"sessions": {}})
        finally:
            sys.argv[0] = old
        menu = lambda: subprocess.Popen([tmux, "-L", sock, "-f", "/dev/null", "display-menu", "-c", cl[0], "-T", "t"] + flat, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mp = menu()
        time.sleep(0.8)
        os.write(mfd, b"s")         # Stash ent
        time.sleep(0.8)
        os.write(mfd, b"n")         # ... and answer no
        time.sleep(1.0)
        check("confirm-before: answering n runs nothing", False, os.path.exists(out))
        with contextlib_suppress():
            mp.kill()
        mp = menu()
        time.sleep(0.8)
        os.write(mfd, b"d")         # Disable ent
        time.sleep(0.8)
        os.write(mfd, b"y")
        for _ in range(60):
            if os.path.exists(out):
                break
            time.sleep(0.1)
        with contextlib_suppress():
            mp.kill()
        got = open(out).read().split("\n")[:-1] if os.path.exists(out) else []
        check("confirm-before: answering y runs fleet with the Disable arguments and --notify", ["stash", "--disable", "--client", cl[0], "--notify", cl[0], "--", "ent"], got)
    finally:
        if cpid:
            with contextlib_suppress():
                os.kill(cpid, signal.SIGKILL)
                os.waitpid(cpid, 0)
        T("kill-server")
        with contextlib_suppress():
            r = T("display-message", "-p", "#{socket_path}")
        sp = "/tmp/tmux-%d/%s" % (os.getuid(), sock)
        if os.path.exists(sp):
            os.unlink(sp)


# ================================================================ final review 2: M1-M6, m1-m10
class inproc:
    """this process gets the Env's FLEET_* / FAKE_DIR environment (and `extra`), and the fleet module is imported under it"""

    def __init__(self, e, **extra):
        self.vars = {k: v for k, v in e.env.items() if k.startswith("FLEET") or k in ("FAKE_DIR", "HOME")}
        self.vars.update(extra)

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.vars}
        os.environ.update(self.vars)
        return load_module()

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def registry_bytes(e):
    return open(os.path.join(e.state, "registry.json")).read()


def t_launder(e):
    """M1 + M2: a bad read is never saved by a look-around, a restart is refused while the recorded process lives,
    and --boot lifts the brake only on a real boot"""
    e.config("".join("j%d dev always-on\n" % i for i in range(1, 4)))
    pids = [e.job_session("j%d" % i, i)[0] for i in range(1, 4)]
    e.run("sync")
    sdir = os.path.join(e.home, "sessions")
    for f in os.listdir(sdir):                          # a Claude update changes the kind value; the jobs live on
        d = json.load(open(os.path.join(sdir, f)))
        d["kind"] = "worker"
        json.dump(d, open(os.path.join(sdir, f), "w"))
    saved = registry_bytes(e)
    for args in (("status",), ("waiting",), ("list",), ("sync",), ("peek", "j1"), ("pick", "--list"), ("menu",)):
        e.run(*args)
    check("M1: status / waiting / list / sync / peek / pick / menu never save a bad read", saved, registry_bytes(e))
    e.reset_logs()
    rc, out, _ = e.run("up")
    truth("M1: `up` after the look-around is braked, no claude call (the review's reproduction: 5 respawns)", rc == 3 and not claude_calls(e), out)
    e.run("watch", "--once", "--no-up")
    check("M1: the watch after it is braked too", 0, len(claude_calls(e)))
    truth("M1: every original process is alive", all(os.path.exists("/proc/%d" % p) for p in pids))
    # M2: --boot on the SAME boot id is a normal start: the brake holds
    rc, out, _ = e.run("watch", "--once", "--boot")
    check("M2: watch --boot on the same boot id: braked, nothing started", 0, len(claude_calls(e)))
    check("M2: ... and the rows were not rewritten (only the brake's notified flag is)", json.loads(saved)["sessions"], e.reg())
    rc, out, _ = e.run("up", "--boot")
    truth("M2: up --boot on the same boot id: braked, says it is a normal start", rc == 3 and "normal start" in out and not claude_calls(e), out)
    check("the tick stored the boot id", "aaaaaaaa-0000-4000-8000-000000000001", open(os.path.join(e.state, "boot_id")).read().strip())
    # a real boot: the exemption applies (the bad read IS saved) but a live recorded process is still never respawned (M1b)
    e.set_boot_id("bbbbbbbb-0000-4000-8000-000000000002")
    rc, out, _ = e.run("watch", "--once", "--boot")
    reg = e.reg()
    check("M2: a real boot (boot id changed) lifts the brake: the rows were saved as gone", ["gone"] * 3, [reg["j%d" % i]["kind"] for i in range(1, 4)])
    check("M1b: ... yet nothing is respawned while the recorded processes are alive", 0, len(claude_calls(e)))
    check("the tick stored the new boot id", "bbbbbbbb-0000-4000-8000-000000000002", open(os.path.join(e.state, "boot_id")).read().strip())
    e.run("watch", "--once", "--boot")
    check("M2: the next unit start on the same boot is a normal start: still nothing started", 0, len(claude_calls(e)))
    rc, out, _ = e.run("up")
    truth("M1b: `up` refuses a gone row whose recorded pid is alive, and says why", not claude_calls(e) and "still alive" in out, out)
    truth("M1: every original process is still alive", all(os.path.exists("/proc/%d" % p) for p in pids))
    for f in os.listdir(sdir):                          # the data reads right again: the rows are jobs again, same pids
        d = json.load(open(os.path.join(sdir, f)))
        d["kind"] = "bg"
        json.dump(d, open(os.path.join(sdir, f), "w"))
    e.run("sync")
    check("the rows are jobs again once the data reads right", (["job"] * 3, pids), ([e.reg()["j%d" % i]["kind"] for i in range(1, 4)], [e.reg()["j%d" % i]["last_pid"] for i in range(1, 4)]))
    # M1b unit: the recorded pid + start time; a reused pid (other start time) is not "alive"
    with inproc(e) as m:
        row = m.new_row()
        row.update(managed=True, job_row=True, job_id="abcd1234", state="working", dir=e.workdir("x"), last_pid=pids[0], last_start=e.proc_start(pids[0]), conv_how="own")
        kind, why, _ = m.revive_plan("x", row)
        truth("M1b: revive_plan refuses while the recorded pid (same start time) is alive", kind is None and "still alive" in why, str(why))
        row["last_start"] = "1"
        check("M1b: a pid reused by another process (different start time) is not the recorded one", "respawn", m.revive_plan("x", row)[0])
    # M1c: a session file of another pid domain is ignored, and its session is never judged
    e.reset_logs()
    f, d = sessions_file_of(e, "j2")
    d["pidDomain"] = "linux:ffffffffffffffffffffffffffffffff:pid:[4026531836]"
    json.dump(d, open(f, "w"))
    out_pid = e.spawn()
    e.sfile(out_pid, sessionId=SID[9], cwd="/", kind="interactive", name="outsider", status="idle", pidDomain="linux:ffffffffffffffffffffffffffffffff:pid:[4026531836]")
    rc, out, _ = e.run("status")
    truth("M1c: a foreign-domain file makes no row", "outsider" not in out, out)
    truth("M1c: the session it claims shows as UNKNOWN", any(l.startswith("j2 ") and "UNKNOWN" in l for l in out.splitlines()), out)
    rc, out, _ = e.run("up")
    truth("M1c: up is braked and starts nothing", rc == 3 and not claude_calls(e), out)


def t_done_state(e):
    """M3: only `stopped` is "on purpose"; `done` is the state of a live idle job and is restartable"""
    e.config("a dev always-on\nb dev always-on\nc dev\nd dev always-on\n")
    pa = e.job_session("a", 1, state="done")[0]
    pb = e.job_session("b", 2, state="working")[0]
    pc = e.job_session("c", 3, state="done")[0]
    pd = e.job_session("d", 4, state="stopped", live=False)[0]
    e.run("sync")
    check("a live idle job's row state is the session status, not the job state", "idle", e.reg()["a"]["state"])
    for p in (pa, pb, pc):                               # kill -9
        e.kill(p)
    rc, out, _ = e.run("up", "--dry-run")
    truth("M3: up --dry-run plans the job sitting in `done` (and `working`)", "a: would run" in out and "b: would run" in out and "c: would run" in out, out)
    truth("M3: ... and still skips the stopped one", "d: skipped, stopped on purpose" in out, out)
    e.reset_logs()
    e.run("watch", "--once", "--no-up")
    calls = sorted(c.split("\t")[-1] for c in claude_calls(e))
    check("M3: the watch restarts the always-on job in `done` and the one in `working`; never the stopped one", sorted(["respawn " + SID[1][:8], "respawn " + SID[2][:8]]), calls)
    with inproc(e) as m:
        truth("M3: `idle` (a session status, never a job state) is not in the restartable allowlist", "idle" not in m.RESPAWN_STATES and "done" in m.RESPAWN_STATES)
        truth("M3: stopped is not restartable without --include-stopped", "stopped" not in m.RESPAWN_STATES)
    src = open(FAKE_CLAUDE).read()
    truth("M3: the fake claude writes only real job states", 'state:"idle"' not in src and "FAKE_JOB_STATE:-working" in src)


def t_owns_kinds(e):
    """M5: `fleet owns` is yes only for a job (or a dead job that `up` brings back), never for a session that runs in a pane"""
    e.config("x dev\ny dev\n")
    px, jx = e.job_session("x", 1, state="working")
    py, jy = e.job_session("y", 2, state="done")
    e.run("sync")
    check("owns: a live job", (0, "x\n"), e.run("owns", SID[1])[:2])
    # x is stopped with `claude stop`, then resumed by hand in a pane under the same name
    subprocess.run([os.path.join(e.bin, "claude"), "stop", jx], env=dict(e.env), capture_output=True, timeout=30)
    e.run("sync")
    check("owns: a stopped job (gone, state stopped) is not owned: the restore resumes it", 1, e.run("owns", SID[1])[0])
    ppid, pane = e.pane_session("x", 1, tmuxsession="work")
    e.run("sync")
    check("the row is a pane now", "pane", e.reg()["x"]["kind"])
    check("M5: a session that runs in a pane is NOT owned (the review's reproduction: owns said yes)", 1, e.run("owns", SID[1])[0])
    e.kill(ppid)
    e.jwrite(os.path.join(e.home, "jobs", jx, "state.json"), {"state": "done", "needs": None, "name": "x", "sessionId": SID[1], "cwd": e.workdir("x"),
                                                          "createdAt": "2026-11-16T10:00:00Z", "respawnFlags": ["--model", "m", "--effort", "high"]})
    e.run("sync")
    check("M5: gone after a pane, even with a restartable job state: not owned (its last kind was a pane)", 1, e.run("owns", SID[1])[0])
    # y: a job that died (a reboot / a daemon loss): gone with a restartable state = owned, `up` brings it back
    e.kill(py)
    e.run("sync")
    check("y died: kind gone, last kind job", ("gone", "job"), (e.reg()["y"]["kind"], e.reg()["y"]["last_kind"]))
    check("owns: a dead job in a restartable state is owned (the restore opens a viewer, `up` revives it)", (0, "y\n"), e.run("owns", SID[2])[:2])


def t_flag_warn(e):
    """m4 / m11 / m12: status flags every job whose saved respawn flags carry no --effort or no --model"""
    e.config("full dev\nnoeff dev\nnomod dev\nnone dev\n")
    e.job_session("full", 1, flags=["--model", "m", "--effort", "high", "--permission-mode", "auto"])
    e.job_session("noeff", 2, flags=["--model", "m", "--permission-mode", "auto"])
    e.job_session("nomod", 3, flags=["--effort", "high"])
    e.job_session("none", 4, flags=["--reply-on-resume"])
    rc, out, _ = e.run("status")
    lines = {l.split()[0]: l for l in out.splitlines() if l.split() and l.split()[0] in ("full", "noeff", "nomod", "none")}
    truth("m11: a job with both flags is not flagged", "NO-" not in lines["full"], lines["full"])
    truth("m11: no --effort flag is flagged", "NO-EFFORT-FLAG" in lines["noeff"] and "NO-MODEL-FLAG" not in lines["noeff"], lines["noeff"])
    truth("m11: no --model flag is flagged", "NO-MODEL-FLAG" in lines["nomod"], lines["nomod"])
    truth("m11: neither", "NO-EFFORT-FLAG" in lines["none"] and "NO-MODEL-FLAG" in lines["none"], lines["none"])
    truth("m11: and a warning line says what to do (stop, rm, bg again)", "warning: the saved respawn flags" in out and "claude rm" in out and "noeff" in out and "full (" not in out, out)
    d = json.loads(e.run("status", "--json")[1])
    check("m11: --json carries it", (["--effort"], []), (d["noeff"]["flag_warn"], d["full"]["flag_warn"]))


def t_fix_minors(e):
    """m1, m2, m3, m5, m7, m8, m9, m10"""
    import errno
    import threading
    # ---- m1: reconcile --boot waits for the restored panes to settle, and re-checks right before typing
    e.config("j1 dev\n")
    e.job_session("j1", 1)
    e.viewer("ent", "j1")
    e.run("sync")
    e.tmuxstate = {"sessions": {}, "clients": [], "next": 900, "start": int(time.time()) + 10}
    e.window("ent", "j1", cmd="zsh")
    e.write_tmux()
    e.reset_logs()
    t0 = time.time()
    rc, out, _ = e.run("reconcile", "--boot", extra={"FLEET_BOOT_SETTLE": "0.6", "FLEET_BOOT_SETTLE_MAX": "5"})
    truth("m1: with nothing changing, it waited the settle time before typing", time.time() - t0 >= 0.6 and len(tmux_calls(e, "send-keys")) == 2, "%.2f %s" % (time.time() - t0, tmux_calls(e)))
    st = e.read_tmux()
    st["sessions"]["ent"]["windows"][0]["panes"][0].pop("opts", None)
    st["sessions"]["ent"]["windows"][0]["panes"][0]["cmd"] = "zsh"
    json.dump(st, open(os.path.join(e.root, "tmux.json"), "w"))
    e.reset_logs()

    def restore_runs():                                 # the tmux restore's own `exec fleet view` fires while reconcile waits
        time.sleep(0.25)
        s2 = e.read_tmux()
        s2["sessions"]["ent"]["windows"][0]["panes"][0]["cmd"] = "python3"
        json.dump(s2, open(os.path.join(e.root, "tmux.json"), "w"))
    th = threading.Thread(target=restore_runs)
    th.start()
    e.run("reconcile", "--boot", extra={"FLEET_BOOT_SETTLE": "0.8", "FLEET_BOOT_SETTLE_MAX": "5"})
    th.join()
    check("m1: a pane whose command changes while reconcile waits is never typed into", 0, len(tmux_calls(e, "send-keys")))
    st = e.read_tmux()
    st["sessions"]["ent"]["windows"][0]["panes"][0]["cmd"] = "zsh"
    json.dump(st, open(os.path.join(e.root, "tmux.json"), "w"))
    tsv = os.path.join(e.root, "fakehome", ".local", "share", "tmux", "resurrect")
    os.makedirs(tsv)
    open(os.path.join(tsv, "claude_sessions.tsv"), "w").write("ent:1.1\t%s\t/x\n" % SID[1])
    e.reset_logs()
    e.run("reconcile", "--boot")
    check("m1: a pane the tmux restore fills itself (claude_sessions.tsv) is left to it", 0, len(tmux_calls(e, "send-keys")))
    os.unlink(os.path.join(tsv, "claude_sessions.tsv"))
    with inproc(e) as m:
        pane = e.read_tmux()["sessions"]["ent"]["windows"][0]["panes"][0]
        truth("m1: pane_is_bare_shell: a shell with no children", m.pane_is_bare_shell(pane["id"], pane["shellpid"]))
        sh = subprocess.Popen(["bash", "-c", "sleep 600 & wait"], start_new_session=True)
        e.pids.append(sh)
        time.sleep(0.3)
        check("m1: pane_is_bare_shell: a shell with a child is not bare", False, m.pane_is_bare_shell(pane["id"], sh.pid))
        st = e.read_tmux()
        st["sessions"]["ent"]["windows"][0]["panes"][0]["cmd"] = "claude"
        json.dump(st, open(os.path.join(e.root, "tmux.json"), "w"))
        check("m1: pane_is_bare_shell: a non-shell foreground command is not bare", False, m.pane_is_bare_shell(pane["id"], pane["shellpid"]))

    # ---- m2: no respawn-pane -k on a stale pane with children; m3: a dead viewer's pane is checked like any other
    e2 = Env()
    try:
        e2.config("j1 dev\n")
        e2.job_session("j1", 1)
        e2.viewer("ent", "j1")
        e2.run("sync")
        old = int(time.time()) - 600
        sh = subprocess.Popen(["bash", "-c", "sleep 600 & wait"], start_new_session=True)
        e2.pids.append(sh)
        time.sleep(0.3)
        st = e2.read_tmux()
        pn = st["sessions"]["ent"]["windows"][0]["panes"][0]
        pn.update(cmd="zsh", shellpid=sh.pid, opts={"@fleet-view": "j1 %d 3999999" % old})
        json.dump(st, open(os.path.join(e2.root, "tmux.json"), "w"))
        e2.reset_logs()
        e2.run("reconcile")
        check("m2: a stale-tagged pane with a child process (the user's `cmd &`) is never respawn-pane -k'd", 0, len(tmux_calls(e2, "respawn-pane")))
        os.killpg(sh.pid, signal.SIGKILL)
        st = e2.read_tmux()
        st["sessions"]["ent"]["windows"][0]["panes"][0]["shellpid"] = e2.shell_pid()
        json.dump(st, open(os.path.join(e2.root, "tmux.json"), "w"))
        e2.reset_logs()
        e2.run("reconcile")
        check("m2: ... the same pane without children is repaired as before", 1, len(tmux_calls(e2, "respawn-pane")))
    finally:
        e2.cleanup()
    e3 = Env()
    try:
        e3.config("j1 dev always-on\n")
        e3.job_session("j1", 1)
        e3.viewer("ent", "j1")                           # (a window in the entry that views j1)
        e3.run("sync")
        cl = subprocess.Popen(["bash", "-c", "%s 600; true" % os.path.join(e3.root, "procbin", "claude")], start_new_session=True)
        e3.pids.append(cl)
        time.sleep(0.3)
        st = e3.read_tmux()
        w = st["sessions"]["ent"]["windows"][0]
        w["panes"][0].update(shellpid=cl.pid, cmd="python3", opts={"@fleet-view": "j1 %d 3999999" % (int(time.time()) - 5)})
        json.dump(st, open(os.path.join(e3.root, "tmux.json"), "w"))
        rc, out, _ = e3.run("stash", "--disable", "--dry-run", "ent")
        truth("m3: a pane tagged as a viewer whose viewer pid is dead, with a claude running in it: refused", rc == 3 and "Claude process runs here" in out, out)
        w["panes"][0]["opts"] = {"@fleet-view": "j1 %d %d" % (int(time.time()) - 5, e3.spawn())}
        e3.pids.append(cl)
        st["sessions"]["ent"]["windows"][0]["panes"][0]["shellpid"] = e3.shell_pid()
        json.dump(st, open(os.path.join(e3.root, "tmux.json"), "w"))
        rc, out, _ = e3.run("stash", "--disable", "--dry-run", "ent")
        truth("m3: a LIVE viewer pid is still skipped (a normal viewer window)", rc == 0 and "would save" in out, out)
    finally:
        e3.cleanup()

    # ---- m5: the idle check is repeated right before each `claude stop`
    e4 = Env()
    try:
        e4.config("a dev\nb dev\n")
        e4.job_session("a", 1)
        e4.job_session("b", 2)
        e4.viewer("ent", "a")
        e4.viewer("ent", "b")
        e4.run("sync")
        with inproc(e4) as m:
            orig = m.mutate

            def flipping(fn):
                r = orig(fn)
                if fn.__name__ == "hold_all":            # the stops follow: the wake-up of b lands in between
                    f, d = sessions_file_of(e4, "b")
                    d["status"] = "busy"
                    json.dump(d, open(f, "w"))
                return r
            m.mutate = flipping
            panes = [p for p in m.tmux_snapshot()["panes"] if p["session"] == "ent"]
            e4.reset_logs()
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = m.stash_disable("ent", panes, False, False)
        calls = [c.split("\t")[-1] for c in claude_calls(e4)]
        check("m5: a job that went busy since the check is not stopped; the disable is abandoned", 1, rc)
        truth("m5: only the idle one was stopped, then brought back", "stop " + SID[1][:8] in calls and "stop " + SID[2][:8] not in calls and "respawn " + SID[1][:8] in calls, str(calls))
        truth("m5: no disabled record, no hold left behind", "ent" not in entries_json(e4)["disabled"] and not any(r["hold"] for r in e4.reg().values()), str(entries_json(e4)))
    finally:
        e4.cleanup()

    # ---- m7: a job row for the same session id under ANOTHER name blocks the move
    e5 = Env()
    try:
        e5.config("alpha dev\n")
        e5.pane_session("alpha", 1)
        e5.jwrite(os.path.join(e5.home, "jobs", SID[1][:8], "state.json"), {"state": "stopped", "name": "other-name", "sessionId": SID[1], "cwd": e5.workdir("alpha"),
                                                                           "createdAt": "2026-11-16T10:00:00Z", "respawnFlags": ["--model", "m"]})
        e5.run("sync")
        check("(the name-only sync does not see that row)", False, e5.reg()["alpha"]["job_row"])
        rc, out, _ = e5.run("bg", "alpha", "--dry-run")
        truth("m7: bg refuses: a job row exists for this session id", rc == 3 and "a job row already exists" in out, out)
    finally:
        e5.cleanup()

    # ---- m8: the half-done move leaves a record; status shows the exact resume command
    e6 = Env()
    try:
        e6.config("alpha dev\n")
        e6.pane_session("alpha", 1)
        e6.run("sync")
        rc, out, _ = e6.run("bg", "alpha", extra={"FAKE_BG_FAIL": "1"})
        check("m8: the resume failed after /exit", 1, rc)
        mv = json.load(open(os.path.join(e6.state, "moving.json")))
        check("m8: the record holds session id, dir, model, effort, mode", (SID[1], e6.workdir("alpha"), "claude-opus-5-5", "high"),
              (mv["alpha"]["sid"], mv["alpha"]["dir"], mv["alpha"]["model"], mv["alpha"]["effort"]))
        rc, out, _ = e6.run("status")
        want = "claude --bg --resume %s -n alpha --model claude-opus-5-5 --effort high" % SID[1]
        truth("m8: status shows HALF-DONE MOVE with the exact resume command", "HALF-DONE MOVE: alpha" in out and want in out, out)
        subprocess.run([os.path.join(e6.bin, "claude"), "--bg", "--resume", SID[1], "-n", "alpha", "--model", "claude-opus-5-5", "--effort", "high"],
                       cwd=e6.workdir("alpha"), env=dict(e6.env), capture_output=True, timeout=30)
        e6.run("sync")
        check("m8: once a job holds the session the record is dropped", {}, json.load(open(os.path.join(e6.state, "moving.json"))))
        rc, out, _ = e6.run("status")
        truth("m8: ... and status no longer shows it", "HALF-DONE" not in out, out)
    finally:
        e6.cleanup()
    e7 = Env()
    try:
        e7.config("alpha dev\n")
        e7.pane_session("alpha", 1)
        e7.run("sync")
        rc, out, _ = e7.run("bg", "alpha")
        truth("m8: a finished move clears its record", rc == 0 and "alpha" not in json.load(open(os.path.join(e7.state, "moving.json"))), out)
    finally:
        e7.cleanup()

    # ---- m9: never /exit the session that runs the command
    e8 = Env()
    try:
        e8.config("alpha dev\nbeta dev\n")
        e8.pane_session("alpha", 1)
        e8.pane_session("beta", 2)
        e8.run("sync")
        e8.reset_logs()
        rc, out, _ = e8.run("bg", "alpha", extra={"CLAUDE_CODE_SESSION_ID": SID[1]})
        truth("m9: bg of the caller's own session (from CLAUDE_CODE_SESSION_ID) is refused, with a message, nothing sent", rc == 3 and "runs the command" in out and not tmux_calls(e8, "send-keys"), out)
        rc, out, _ = e8.run("bg", "--all", extra={"CLAUDE_CODE_SESSION_ID": SID[1]})
        truth("m9: --all skips the caller's session and moves the others", "skip alpha: refused: this session runs the command" in out and "moved: beta" in out and rc == 0, out)
        check("m9: only beta got /exit", 1, len([c for c in tmux_calls(e8, "send-keys") if c.endswith("/exit")]))
    finally:
        e8.cleanup()
    e9 = Env()
    try:
        e9.config("gamma dev\n")
        go, outf = os.path.join(e9.root, "go"), os.path.join(e9.root, "out.txt")
        wrapper = subprocess.Popen(["bash", "-c", 'while [ ! -e "$1" ]; do sleep 0.05; done; "$2" bg gamma --dry-run > "$3" 2>&1; echo rc=$? >> "$3"', "x", go, FLEET, outf], env=dict(e9.env))
        e9.pids.append(wrapper)
        e9.sfile(wrapper.pid, sessionId=SID[3], cwd=e9.workdir("gamma"), kind="interactive", name="gamma", status="idle", tmux="work:@1.%77")
        e9.run("sync")
        open(go, "w").close()
        wrapper.wait(timeout=30)
        res = open(outf).read()
        truth("m9: fleet running as a descendant of the session's process is refused too (no env needed)", "runs the command" in res and "rc=3" in res, res)
    finally:
        e9.cleanup()

    # ---- m10: only a parse failure is corruption
    e10 = Env()
    try:
        e10.config("a dev\n")
        e10.job_session("a", 1)
        e10.run("sync")
        rp = os.path.join(e10.state, "registry.json")
        with inproc(e10) as m:
            real_open = open
            fails = {"n": 2}

            def flaky(path, *a, **k):
                if str(path) == rp and fails["n"] > 0:
                    fails["n"] -= 1
                    raise OSError(errno.EMFILE, "Too many open files")
                return real_open(path, *a, **k)
            m.open = flaky
            reg = m.load_registry()
            truth("m10: a transient read error is retried and then succeeds", "a" in reg["sessions"] and fails["n"] == 0)
            fails["n"] = 1000
            try:
                m.load_registry()
                bad("m10: a persistent read error must refuse")
            except m.RegistryUnreadable:
                ok()
            except m.RegistryCorrupt:
                bad("m10: a read error was reported as corruption")
            m.open = real_open
        truth("m10: the file was not moved aside, no recovery flag", os.path.exists(rp) and not os.path.exists(os.path.join(e10.state, "registry.needs-recovery"))
              and not [f for f in os.listdir(e10.state) if "corrupt" in f], str(os.listdir(e10.state)))
        open(rp, "w").write("{ torn")
        rc, out, er = e10.run("hold", "a")
        truth("m10: a real parse failure is still corruption (moved aside, flag written)", rc == 3 and os.path.exists(os.path.join(e10.state, "registry.needs-recovery")) and any("corrupt" in f for f in os.listdir(e10.state)), er)
    finally:
        e10.cleanup()


# ================================================================ follow-ups: clean stop vs crash, conversation file
def stop_it(e, name, job):
    """`claude stop` through the fake (as the real one: file removed, daemon logs killed, an idle job's state stays done)"""
    subprocess.run([os.path.join(e.bin, "claude"), "stop", job], env=dict(e.env), capture_output=True, timeout=30)


def t_stop_vs_crash(e):
    """a `claude stop` of an idle job leaves the state `done`, the same as a death: the way the process ended decides"""
    e.config("stopd dev always-on\ncrashd dev always-on\nswept dev always-on\nswept2 dev\nplain dev\nblk dev always-on\nblkc dev always-on\n")
    ps, js = e.job_session("stopd", 1, state="done")
    pc, jc = e.job_session("crashd", 2, state="done")
    pw, jw = e.job_session("swept", 3, state="done")
    pw2, jw2 = e.job_session("swept2", 4, state="done")
    pp, jp = e.job_session("plain", 5, state="done")
    pb, jb = e.job_session("blk", 6, state="blocked", needs="answer me")
    pbc, jbc = e.job_session("blkc", 7, state="blocked", needs="answer me")
    e.run("sync")
    check("the rows know the boot they were seen alive under", {"aaaaaaaa-0000-4000-8000-000000000001"}, {r["last_boot"] for r in e.reg().values()})
    stop_it(e, "stopd", js)                              # a clean stop of an idle always-on job
    stop_it(e, "blk", jb)                                # a clean stop of a blocked job: the state becomes stopped
    check("fake/real: a stop of a blocked job writes `stopped`, of an idle one leaves `done`", ("stopped", "done"),
          (json.load(open(os.path.join(e.home, "jobs", jb, "state.json")))["state"], json.load(open(os.path.join(e.home, "jobs", js, "state.json")))["state"]))
    e.kill(pc)                                           # kill -9: the session file stays behind
    e.kill(pbc)
    e.kill(pw)                                           # a crash whose stale file Claude has swept already, no daemon log line
    rmf(os.path.join(e.home, "sessions", "%d.json" % pw))
    e.kill(pw2)
    rmf(os.path.join(e.home, "sessions", "%d.json" % pw2))
    e.kill(pp)                                           # a non-always-on job killed: stale file
    e.run("sync")
    r = e.reg()
    check("end_kind: clean stop / kill -9 / swept / swept / kill -9", ["clean", "stale", "clean", "clean", "stale"],
          [r[n]["end_kind"] for n in ("stopd", "crashd", "swept", "swept2", "plain")])
    e.reset_logs()
    rc, out, _ = e.run("up", "--dry-run")
    truth("up: the cleanly stopped idle always-on job is skipped, with the reason", "stopd: skipped, stopped on purpose" in out and "daemon logged it killed" in out, out)
    truth("up: the crashed one (stale file) is planned", "crashd: would run" in out, out)
    truth("up: a swept crash of an always-on job (clean file signal, no daemon confirmation) is planned: in doubt = restart", "swept: would run" in out, out)
    truth("up: ... a non-always-on one in doubt is skipped with the reason", "swept2: skipped, job state done and no sign of a crash" in out, out)
    truth("up: a non-always-on job killed (stale file) is planned", "plain: would run" in out, out)
    truth("up: a stopped blocked job is skipped (state stopped), a killed blocked one is planned", "blk: skipped, stopped on purpose (job state stopped)" in out and "blkc: would run" in out, out)
    rc, out, _ = e.run("up", "--include-stopped", "--dry-run")
    truth("up --include-stopped overrides: the cleanly stopped one is planned", "stopd: would run" in out and "swept2: would run" in out, out)
    # the watchdog: twice, the stopped ones never come back
    e.run("watch", "--once", "--no-up")
    e.run("watch", "--once", "--no-up")
    calls = sorted(c.split("\t")[-1] for c in claude_calls(e))
    names = {SID[n][:8]: n for n in range(1, 8)}
    started = sorted({names[c.split()[-1]] for c in calls})
    check("watch x2: crashd, swept and blkc are restarted (once each, the second tick finds them alive); stopd, blk, swept2, plain are not", [2, 3, 7], started)
    truth("watch: it logs why it skipped the stopped ones", "stopd" in open(os.path.join(e.state, "fleet.log")).read() and "stopped on purpose" in open(os.path.join(e.state, "fleet.log")).read())
    # a reboot: the boot id changed since the jobs were seen alive: restart whatever the files say (a non-always-on done job too)
    e2 = Env()
    try:
        e2.config("plain dev\nstopd dev\n")
        pp, jp = e2.job_session("plain", 1, state="done")
        ps, js = e2.job_session("stopd", 2, state="done")
        e2.run("sync")
        stop_it(e2, "stopd", js)                         # stopped on purpose BEFORE the reboot
        e2.kill(pp)
        rmf(os.path.join(e2.home, "sessions", "%d.json" % pp))      # (files may be swept after a reboot)
        e2.run("sync")
        check("before the reboot: the stop is clean, the plain one clean too (no signal)", ["clean", "clean"], [e2.reg()["stopd"]["end_kind"], e2.reg()["plain"]["end_kind"]])
        # reboot: rows saved as job at the last tick, the data gone, a new boot id
        e3 = Env()
        try:
            e3.config("plain dev\nstopd dev\n")
            pp3, jp3 = e3.job_session("plain", 1, state="done")
            e3.run("sync")
            e3.kill(pp3)
            rmf(os.path.join(e3.home, "sessions", "%d.json" % pp3))
            e3.set_boot_id("cccccccc-0000-4000-8000-000000000003")
            e3.reset_logs()
            check("reboot: end_kind is reboot (the boot id changed), whatever the session files say", "reboot", json.loads(e3.run("status", "--json")[1])["plain"]["end_kind"])
            rc, out, _ = e3.run("up")
            check("reboot: a non-always-on job in state done is restarted", ["respawn " + jp3], [c.split("\t")[-1] for c in claude_calls(e3)])
        finally:
            e3.cleanup()
    finally:
        e2.cleanup()
    # daemon_killed: the NEWEST line that names the job decides
    with inproc(e) as m:
        log = os.path.join(e.home, "daemon.log")
        open(log, "w").write("[t] [bg] bg settled abcd1234 (killed)\n[t] [bg] bg claimed-spare abcd1234 (fleet)\n[t] [bg] bg settled other999 (killed)\n")
        check("daemon_killed: a later claim by fleet means it runs again", False, m.daemon_killed("abcd1234"))
        open(log, "a").write("[t] [bg] bg settled abcd1234 (killed)\n")
        check("daemon_killed: the newest line says killed", True, m.daemon_killed("abcd1234"))
        open(log, "w").write("[t] [bg] bg settled abcd1234 (done)\n")
        check("daemon_killed: settled done is not a stop", False, m.daemon_killed("abcd1234"))
        os.unlink(log)
        check("daemon_killed: no log = no", False, m.daemon_killed("abcd1234"))
        check("daemon_killed: a hostile job id is never matched", False, m.daemon_killed("a.*"))


def conv_line(model="claude-opus-5-5", effort="high", pte="same", ts="2026-11-16T10:00:00.000Z", sid=None, **extra):
    d = {"type": "assistant", "sessionId": sid, "timestamp": ts, "message": {"role": "assistant", "model": model}}
    if effort is not None:
        d["effort"] = effort
    if pte is not None:
        d["perTurnEffort"] = effort if pte == "same" else pte
    d.update(extra)
    return json.dumps(d)


def write_conv(e, sid, lines, raw_tail=""):
    d = os.path.join(e.home, "projects", "-some-dir-slug")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else "") + raw_tail)
    return p


def t_conversation(e):
    """model and effort: the last assistant record of the session's own conversation file is the first source"""
    e.config("a dev\n")
    e.pane_session("a", 1, model="claude-opus-5-5", effort="low")        # statusline file + the hook's seen/ say opus / low
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3 * 3600))
    write_conv(e, SID[1], [json.dumps({"type": "user", "message": {"content": "hi"}}), conv_line("claude-fable-5-2", "xhigh", ts="2026-01-01T00:00:00.000Z", sid=SID[1]),
                           json.dumps({"type": "user", "message": {"content": "more"}}), conv_line("claude-haiku-5-5", "high", ts=ts, sid=SID[1])])
    e.run("sync")
    r = e.reg()["a"]
    check("conversation: the LAST assistant record wins over the statusline file and the hook", ("claude-haiku-5-5", "high", "conversation", "conversation"), (r["model"], r["effort"], r["model_src"], r["effort_src"]))
    truth("conversation: the record's time is kept (about 3 h ago)", abs((time.time() - r["effort_at"]) - 3 * 3600) < 60 and r["model_at"] == r["effort_at"], str(r["effort_at"]))
    rc, out, _ = e.run("status", "a")
    truth("status shows the source and the age", "claude-haiku-5-5 (conv 3h" in out and "high (conv 3h" in out, out)
    d = json.loads(e.run("status", "--json", "a")[1])["a"]
    check("status --json carries source and time", ("conversation", "conversation"), (d["model_src"], d["effort_src"]))
    rc, out, _ = e.run("bg", "a", "--dry-run")
    truth("bg line: [effort high, from the conversation, 3h00m ago] and the model's source", "[effort high, from the conversation, 3h0" in out and "[model claude-haiku-5-5, from the conversation, 3h0" in out and "--model claude-haiku-5-5 --effort high" in out, out)
    rc, out, _ = e.run("bg", "a", "--dry-run", "--effort", "low", "--model", "claude-opus-5-5")
    truth("bg --effort / --model still override, and say so", "[effort low, from --effort]" in out and "[model claude-opus-5-5, from --model]" in out and "--model claude-opus-5-5 --effort low" in out, out)
    # fall through: no assistant record at all
    e1 = Env()
    try:
        e1.config("a dev\n")
        e1.pane_session("a", 1, model="claude-opus-5-5", effort="low")
        write_conv(e1, SID[1], [json.dumps({"type": "user", "message": {"content": "hi"}}), json.dumps({"type": "summary"})])
        e1.run("sync")
        r = e1.reg()["a"]
        check("no assistant record: the statusline file and the hook are used", ("claude-opus-5-5", "low", "statusline", "hook"), (r["model"], r["effort"], r["model_src"], r["effort_src"]))
        write_conv(e1, SID[1], [])
        e1.run("sync")
        check("an empty file falls through too", "hook", e1.reg()["a"]["effort_src"])
        os.unlink(os.path.join(e1.home, "projects", "-some-dir-slug", SID[1] + ".jsonl"))
        e1.run("sync")
        check("a missing file falls through too", "hook", e1.reg()["a"]["effort_src"])
        # a truncated last line (the file is being written): the record before it is used
        write_conv(e1, SID[1], [conv_line("claude-sonnet-5-5", "medium", sid=SID[1])], raw_tail=conv_line("claude-haiku-5-5", "max", sid=SID[1])[:-25])
        e1.run("sync")
        r = e1.reg()["a"]
        check("a truncated last line is skipped: the previous record is used", ("claude-sonnet-5-5", "medium"), (r["model"], r["effort"]))
        # hostile and odd values
        write_conv(e1, SID[1], [conv_line("claude-sonnet-5-5", "medium", sid=SID[1]),
                                conv_line("claude-haiku-5-5", "high", sid="b0000000-0000-4000-8000-000000000000"),             # another session's record
                                conv_line("claude-haiku-5-5", "max", sid=SID[1], isSidechain=True),                           # a subagent turn
                                conv_line("<synthetic>", "max", sid=SID[1]),                                                  # no real model
                                conv_line("x;rm -rf /", "high", sid=SID[1]),                                                  # hostile model
                                conv_line("claude-haiku-5-5", "high", sid=SID[1], isApiErrorMessage=True)])
        e1.run("sync")
        r = e1.reg()["a"]
        check("other session / sidechain / synthetic / hostile model / API error records are all skipped", ("claude-sonnet-5-5", "medium"), (r["model"], r["effort"]))
        write_conv(e1, SID[1], [conv_line("claude-haiku-5-5", "ultra\x1b[31m;reboot", pte=None, sid=SID[1])])
        e1.run("sync")
        r = e1.reg()["a"]
        check("a hostile effort is rejected (not an effort name): the model is used, the effort falls through to the hook", ("claude-haiku-5-5", "low", "conversation", "hook"), (r["model"], r["effort"], r["model_src"], r["effort_src"]))
        write_conv(e1, SID[1], [conv_line("claude-haiku-5-5", None, pte="max", sid=SID[1])])
        e1.run("sync")
        check("only perTurnEffort present (older / newer records): it is used", ("max", "conversation"), (e1.reg()["a"]["effort"], e1.reg()["a"]["effort_src"]))
        write_conv(e1, SID[1], [conv_line("claude-haiku-5-5", "high", pte="low", sid=SID[1])])
        e1.run("sync")
        check("both present and different (never seen in real files): `effort` (the session level) wins", "high", e1.reg()["a"]["effort"])
        write_conv(e1, SID[1], ["{ not json", "\x00\x00garbage", conv_line("claude-haiku-5-5", "high", sid=SID[1]).replace('"message"', '"mess"')])
        e1.run("sync")
        check("garbage lines and a record without message.model: nothing usable, next source", "hook", e1.reg()["a"]["effort_src"])
    finally:
        e1.cleanup()
    # a huge file: only the tail is read, within the cap
    e2 = Env()
    try:
        e2.config("a dev\n")
        e2.pane_session("a", 1, model="claude-opus-5-5", effort="low")
        d = os.path.join(e2.home, "projects", "-some-dir-slug")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, SID[1] + ".jsonl")
        with open(p, "wb") as f:
            f.write((conv_line("claude-fable-5-2", "xhigh", sid=SID[1]) + "\n").encode())
            f.seek(400 * 1024 * 1024)                           # 400 MB of (sparse) nothing, then the real tail
            f.write(b"\n" + b"x" * 300000 + b"\n" + (conv_line("claude-haiku-5-5", "medium", sid=SID[1]) + "\n").encode())
        t0 = time.time()
        e2.run("sync")
        el = time.time() - t0
        r = e2.reg()["a"]
        check("a 400 MB file: the tail record is found", ("claude-haiku-5-5", "medium"), (r["model"], r["effort"]))
        truth("a 400 MB file: fast (the file is never read whole)", el < 10, "%.1fs" % el)
        # the record sits further from the end than the byte cap: not found, falls through (never reads on without limit)
        with open(p, "ab") as f:
            f.write(b"y" * (6 * 1024 * 1024) + b"\n")
        e2.run("sync")
        r = e2.reg()["a"]
        check("the last record is beyond the cap (4 MB): next source", ("conversation" != r["effort_src"], "hook"), (True, r["effort_src"]))
        e2.run("sync", extra={"FLEET_CONV_TAIL_BYTES": str(16 * 1024 * 1024)})
        check("a bigger cap (FLEET_CONV_TAIL_BYTES) finds it", "conversation", e2.reg()["a"]["effort_src"])
    finally:
        e2.cleanup()
    # a job (no pane): its own file too
    e3 = Env()
    try:
        e3.config("j dev\n")
        e3.job_session("j", 1, flags=["--model", "haiku", "--effort", "low"])
        write_conv(e3, SID[1], [conv_line("claude-opus-5-5", "xhigh", sid=SID[1])])
        e3.run("sync")
        r = e3.reg()["j"]
        check("a job: the conversation beats its saved flags", ("claude-opus-5-5", "xhigh", "conversation"), (r["model"], r["effort"], r["effort_src"]))
        os.unlink(os.path.join(e3.home, "projects", "-some-dir-slug", SID[1] + ".jsonl"))
        e3.run("sync")
        r = e3.reg()["j"]
        check("... and without the file the saved flags are the source", ("haiku", "low", "job flags"), (r["model"], r["effort"], r["effort_src"]))
    finally:
        e3.cleanup()


# ================================================================ retire, wake, who, new
def daemon_log(e, *lines):
    with open(os.path.join(e.home, "daemon.log"), "a") as f:
        for l in lines:
            f.write("[2026-11-16T10:00:00.000Z] [bg] bg %s\n" % l)


def retire_lines(job):
    return ("retire %s: settled, idle 60m" % job, "settled %s (done)" % job)


def make_asleep(e, name, n, **kw):
    """a job that was alive at the last sync and was then retired by the daemon: process gone, session file gone, state done,
    the retire lines in the daemon log (a stand-in for waiting 60 minutes)"""
    pid, job = e.job_session(name, n, **kw)
    return pid, job


def retire_now(e, pid, job):
    e.kill(pid)
    rmf(os.path.join(e.home, "sessions", "%d.json" % pid))
    daemon_log(e, *retire_lines(job))


def t_retired(e):
    """the daemon retires an idle job after 60 min: asleep, not dead, not stopped on purpose; nothing restarts it by itself"""
    e.config("aon dev always-on\nplain dev\ncrash dev always-on\nlate dev always-on\n")
    pa, ja = e.job_session("aon", 1, state="done")
    pp, jp = e.job_session("plain", 2, state="done")
    pc, jc = e.job_session("crash", 3, state="done")
    pl, jl = e.job_session("late", 4, state="done")
    e.run("sync")
    retire_now(e, pa, ja)
    retire_now(e, pp, jp)
    e.kill(pc)                                         # a crash: stale file
    e.kill(pl)
    rmf(os.path.join(e.home, "sessions", "%d.json" % pl))   # `late`: gone cleanly, the retire line is written only after the sync
    e.run("sync")
    r = e.reg()
    check("end_kind: retired / retired / stale / clean", ["retired", "retired", "stale", "clean"], [r[n]["end_kind"] for n in ("aon", "plain", "crash", "late")])
    daemon_log(e, *retire_lines(jl))
    e.reset_logs()
    rc, out, _ = e.run("up", "--dry-run")
    truth("up: a retired always-on job is NOT planned, and says it is asleep and how to wake it", "aon: skipped, asleep" in out and "fleet wake aon" in out and "aon: would run" not in out, out)
    truth("up: a retired plain job is skipped too", "plain: skipped, asleep" in out, out)
    truth("up: the retire line that arrives after the transition still counts (decided at plan time)", "late: skipped, asleep" in out, out)
    truth("up: a crashed always-on job is still planned (unchanged)", "crash: would run" in out, out)
    e.run("watch", "--once", "--no-up", extra={"FLEET_BRAKE_MAX": "0"})
    e.run("watch", "--once", "--no-up", extra={"FLEET_BRAKE_MAX": "0"})
    started = sorted({c.split("\t")[-1] for c in claude_calls(e)})
    check("watch x2: only the crash is restarted; the retired always-on sessions stay asleep", ["respawn " + jc], started)
    truth("a retire raises no `died at once` notice", "died at once" not in e.log("notify.log"), e.log("notify.log"))
    truth("the notify lane says nothing about a retire", not any(("aon" in l or "plain" in l or "late" in l) for l in e.log("notify.log").splitlines()), e.log("notify.log"))
    rc, out, _ = e.run("status")
    truth("status shows asleep", any(l.startswith("aon ") and " asleep " in l for l in out.splitlines()), out)
    d = json.loads(e.run("status", "--json")[1])
    check("status --json / the API show asleep", ("asleep", "retired"), (d["aon"]["state"], d["aon"]["end_kind"]))
    e3 = Env()
    try:
        e3.config("r1 dev always-on\nr2 dev always-on\nr3 dev always-on\n")
        rp = [e3.job_session("r%d" % i, i, state="done") for i in (1, 2, 3)]
        e3.run("sync")
        for pid, job in rp:
            retire_now(e3, pid, job)
        e3.run("watch", "--once", "--no-up", extra={"FLEET_BRAKE_MAX": "1"})   # the tick itself sees the three retires
        truth("three retires in one tick: no `died at once` notice, no restart", "died at once" not in e3.log("notify.log") and not claude_calls(e3), e3.log("notify.log"))
    finally:
        e3.cleanup()
    rc, out, _ = e.run("up", "--include-stopped", "--dry-run")
    truth("up --include-stopped overrides the asleep skip", "aon: would run" in out and "plain: would run" in out, out)
    # a retired job after a reboot stays asleep (the retire came first), a job that was ALIVE at the reboot is restarted
    e2 = Env()
    try:
        e2.config("aon dev always-on\nlive dev always-on\n")
        pa2, ja2 = e2.job_session("aon", 1, state="done")
        pl2, jl2 = e2.job_session("live", 2, state="done")
        e2.run("sync")
        retire_now(e2, pa2, ja2)
        e2.run("sync")
        e2.kill(pl2)
        rmf(os.path.join(e2.home, "sessions", "%d.json" % pl2))
        e2.set_boot_id("dddddddd-0000-4000-8000-000000000004")
        e2.reset_logs()
        rc, out, _ = e2.run("up")
        check("reboot: the always-on job that was alive comes back; the retired one stays asleep", ["respawn " + jl2], [c.split("\t")[-1] for c in claude_calls(e2)])
    finally:
        e2.cleanup()
    with inproc(e) as m:
        log = os.path.join(e.home, "daemon.log")
        def end(txt, job="abcd1234"):
            open(log, "w").write(txt)
            return m.daemon_end(job)
        check("daemon_end: retire (settled) + settled done", "retired", end("[t] [bg] bg retire abcd1234: settled, idle 60m\n[t] [bg] bg settled abcd1234 (done)\n"))
        check("daemon_end: retire (idle-prompt)", "retired", end("[t] [bg] bg retire abcd1234: idle-prompt, idle 61m\n"))
        check("daemon_end: a later claimed-spare means it runs again", "", end("[t] [bg] bg retire abcd1234: settled, idle 60m\n[t] [bg] bg claimed-spare abcd1234 (fleet)\n"))
        check("daemon_end: a stale-spare retire is not a job retire", "", end("[t] [bg] bg retire abcd1234: stale-spare, idle 61m\n"))
        check("daemon_end: a plain settled done is nothing", "", end("[t] [bg] bg settled abcd1234 (done)\n"))
        check("daemon_end: another job's retire is not ours", "", end("[t] [bg] bg retire other999: settled, idle 60m\n"))


def asleep_job(e, name, n, **kw):
    """sync with the job alive, retire it, sync again: the registry row of a retired job"""
    pid, job = e.job_session(name, n, **kw)
    e.run("sync")
    retire_now(e, pid, job)
    e.run("sync")
    return job


def t_wake(e):
    e.config("alive dev\nsleepy dev always-on\nflagless dev - high\nheld dev\nstoppd dev\nclash dev\ndisab dev\nown dev\nnoeff dev\nnosock dev\nmism dev\n")
    pal, jal = e.job_session("alive", 1, state="done")
    e.run("sync")
    e.reset_logs()
    rc, out, _ = e.run("wake", "alive")
    check("wake of an awake session: `awake`, rc 0, no claude call", (0, "awake\n", 0), (rc, out, len(claude_calls(e))))
    # asleep, saved flags carry the recorded model and effort: `claude respawn`, flags kept
    js = asleep_job(e, "sleepy", 2, state="done")
    e.reset_logs()
    rc, out, er = e.run("wake", "sleepy")
    calls = [c.split("\t")[-1] for c in claude_calls(e)]
    check("wake of an asleep session: one `respawn`, then `awake`", (0, "awake\n", ["respawn " + js]), (rc, out, calls))
    truth("... and the session file with a live pid and its socket is there", wake_alive(e, "sleepy"))
    check("... and the saved flags are the recorded ones", ["--model", "claude-opus-5-5", "--effort", "high"], json.load(open(os.path.join(e.home, "jobs", js, "state.json")))["respawnFlags"])
    truth("wake: no CLAUDE* variable reached claude", all("claudevars=0" in c for c in claude_calls(e)))
    # saved flags WITHOUT --effort: respawn would run on the default effort: rm the row, resume with all flags
    jf = asleep_job(e, "flagless", 3, state="done", flags=["--model", "claude-opus-5-5"])
    e.reset_logs()
    rc, out, er = e.run("wake", "flagless")
    calls = [c.split("\t")[-1] for c in claude_calls(e)]
    truth("wake with flags lacking --effort: `rm <job>` then `--bg --resume <sid> -n name --model m --effort high`",
          rc == 0 and calls[0] == "rm " + jf and calls[1].startswith("--bg --resume %s -n flagless --model claude-opus-5-5 --effort high" % SID[3]) and len(calls) == 2, str((rc, out, er, calls)))
    check("... and the new row carries the effort", ["--model", "claude-opus-5-5", "--effort", "high"], json.load(open(os.path.join(e.home, "jobs", jf, "state.json")))["respawnFlags"])
    # refusals
    jh = asleep_job(e, "held", 4, state="done")
    e.run("hold", "held")
    e.reset_logs()
    rc, out, _ = e.run("wake", "held")
    truth("wake of a held session: refused (rc 3), says how, no call", rc == 3 and "held" in out and "--force" in out and not claude_calls(e), out)
    rc, out, _ = e.run("wake", "held", "--force")
    truth("wake --force overrides a hold", rc == 0 and out == "awake\n" and len(claude_calls(e)) == 1, out)
    pst, jst = e.job_session("stoppd", 5, state="done")
    e.run("sync")
    stop_it(e, "stoppd", jst)                           # a clean `claude stop` by the user: killed in the daemon log
    e.run("sync")
    e.reset_logs()
    rc, out, _ = e.run("wake", "stoppd")
    truth("wake of a session the user stopped on purpose: refused, no call", rc == 3 and "stopped on purpose" in out and not claude_calls(e), out)
    rc, out, _ = e.run("wake", "stoppd", "--force")
    truth("wake --force overrides stopped-on-purpose", rc == 0 and out == "awake\n", out)
    # conflict
    e.pane_session("clash", 6)
    e.pane_session("clash", 7)
    e.run("sync")
    e.reset_logs()
    rc, out, _ = e.run("wake", "clash")
    truth("wake of a name in conflict (two live sessions): refused, no call", rc == 3 and "two live sessions" in out and not claude_calls(e), out)
    # a disabled entry
    jd = asleep_job(e, "disab", 8, state="done")
    ent = {"stashed": {}, "disabled": {"offent": {"at": 1, "windows": [{"name": "w", "cwd": "/", "session": "disab"}], "held": [], "stopped": []}}}
    json.dump(ent, open(os.path.join(e.state, "entries.json"), "w"))
    e.run("sync")
    e.reset_logs()
    rc, out, _ = e.run("wake", "disab")
    truth("wake of a session in a disabled entry: refused, points at unstash", rc == 3 and "disabled" in out and "fleet unstash offent" in out and not claude_calls(e), out)
    os.unlink(os.path.join(e.state, "entries.json"))
    # the caller's own session is never woken
    jo = asleep_job(e, "own", 9, state="done")
    e.reset_logs()
    rc, out, _ = e.run("wake", "own", extra={"CLAUDE_CODE_SESSION_ID": SID[9]})
    truth("wake never wakes the caller's own session", rc == 3 and "runs the command" in out and not claude_calls(e), out)
    # no recorded effort: refused with the manual command
    jn = asleep_job(e, "noeff", 10, state="done", flags=["--model", "claude-opus-5-5"])
    e.reset_logs()
    rc, out, _ = e.run("wake", "noeff")
    truth("wake with no recorded effort: refused, the manual command with <EFFORT>", rc == 3 and "no recorded effort" in out and "--effort '<EFFORT>'" in out and not claude_calls(e), out)
    # not ready: the socket never appears
    jk = asleep_job(e, "nosock", 11, state="done")
    e.reset_logs()
    t0 = time.time()
    rc, out, _ = e.run("wake", "nosock", "--timeout", "1", extra={"FAKE_NOSOCK": "1"})
    truth("wake: started but not ready within --timeout: rc 1, says so, no `awake`", rc == 1 and "not ready" in out and "awake\n" not in out and time.time() - t0 < 20, out)
    # the rm route refuses a job row that does not match the session
    jm = asleep_job(e, "mism", 12, state="done", flags=["--model", "claude-opus-5-5"])
    e.reset_logs()
    with inproc(e) as m:                                # (a race the CLI cannot make: the registry row and the job row disagree on the id)
        import argparse as ap
        import contextlib as cl
        import io
        reg = m.load_registry()
        reg["sessions"]["mism"]["session_id"] = SID[31]
        reg["sessions"]["mism"]["effort"] = "high"
        m.sync_guarded = lambda *a, **k: (reg, "")
        buf = io.StringIO()
        with cl.redirect_stdout(buf):
            rc = m.wake_run(ap.Namespace(name="mism", timeout=1, force=False))
    truth("wake: a job row that is not this session's (other session id) is never removed, with a reason", rc == 3 and "does not match" in buf.getvalue()
          and not any(c.split("\t")[-1].startswith("rm ") for c in claude_calls(e)), buf.getvalue())
    rc, out, er = e.run("wake", "nosuchname")
    check("wake of an unknown name: rc 1", 1, rc)
    rc, out, _ = e.run("wake", "bad name;x")
    check("wake with a bad name: rc 2", 2, rc)
    rc, out, _ = e.run("wake", "outside", extra={"FLEET_NAME_PREFIX": "zz-"})
    check("wake outside FLEET_NAME_PREFIX: not run", True, rc != 0)
    # the one `rm`
    src = open(FLEET).read()
    def callers(flag):
        return [l for l in src.splitlines() if flag in l and "run_claude(" in l and not l.lstrip().startswith(("\"\"\"", "#", "def "))]
    check("run_claude: `rm_ok=True` has exactly one caller (fleet wake, the session's own row)", 1, len(callers("rm_ok=True")))
    check("run_claude: `stop_ok=True` still has exactly one caller", 1, len(callers("stop_ok=True")))
    with inproc(e) as m:
        for sub, kw in (("rm", {}), ("daemon", {"rm_ok": True}), ("kill", {"rm_ok": True}), ("purge", {"rm_ok": True}), ("daemon", {"stop_ok": True}), ("rm", {"stop_ok": True}), ("stop", {"rm_ok": True})):
            try:
                m.run_claude([sub, "x"], **kw)
                bad("run_claude allowed %s %s" % (sub, kw))
            except RuntimeError:
                ok()
    # wake with no state dir yet creates it
    e5 = Env()
    try:
        rc, out, er = e5.run("wake", "nobody")
        truth("wake with no registry yet: creates the state dir, says unknown", rc == 1 and os.path.isdir(e5.state), er)
    finally:
        e5.cleanup()


def wake_alive(e, name):
    f, d = sessions_file_of(e, name)
    return bool(d) and os.path.exists("/proc/%d" % d["pid"]) and os.path.exists(d["messagingSocketPath"])


def t_who(e):
    e.config("# a comment\nalpha dev always-on high Keeps the alpha service healthy\nbeta ops\ngamma - - - hostile \x1b[31mred\x07 text \u202ebidi\ndelta dev - low\nzed -\n")
    e.job_session("alpha", 1)
    e.job_session("beta", 2, live=False, state="working")
    e.job_session("gamma", 3)
    e.job_session("delta", 4, live=False, state="done")
    d = os.path.join(e.env["HOME"], "proj")
    os.makedirs(d, exist_ok=True)
    e.jwrite(os.path.join(e.home, "jobs", SID[5][:8], "state.json"), {"state": "done", "name": "eps", "sessionId": SID[5], "cwd": d, "createdAt": "x", "respawnFlags": ["--model", "m", "--effort", "low"]})
    e.config(open(e.cfg).read() + "eps dev - - sits under home\n")
    e.viewer("shop", "alpha")
    e.run("sync")
    e.run("hold", "beta")
    shutil.rmtree(e.state)                               # `who` needs no registry: it syncs read-only
    rc, out, er = e.run("who")
    check("who: rc 0", 0, rc)
    truth("who: no state dir created (read-only)", not os.path.exists(e.state))
    lines = out.splitlines()
    names = [l.split()[0] for l in lines]
    check("who: one line per registered session", sorted(["alpha", "beta", "delta", "eps", "gamma", "zed"]), sorted(names))
    check("who: sorted by entry then name (a session with an entry first, the rest by name)", ["alpha", "beta", "delta", "eps", "gamma", "zed"], names)
    ln = {l.split()[0]: l for l in lines}
    truth("who: alpha has entry, role, awake and its purpose", "shop" in ln["alpha"] and "dev" in ln["alpha"] and "awake" in ln["alpha"] and ln["alpha"].endswith("Keeps the alpha service healthy"), ln["alpha"])
    truth("who: states awake / down / held", " awake " in ln["gamma"] and " down " in ln["delta"], str(ln))
    truth("who: a home directory shows as ~", "~/proj" in ln["eps"], ln["eps"])
    truth("who: hostile purpose text is stripped (no escape, no bell, no bidi)", "\x1b" not in out and "\x07" not in out and "\u202e" not in out and "red" in out, repr(out))
    rc, out, _ = e.run("who", "--json")
    rows = json.loads(out)
    check("who --json: a list of objects with the fields", (True, ["conv", "conv_how", "dir", "entry", "name", "purpose", "role", "state"]), (isinstance(rows, list), sorted(rows[0])))
    byname = {r["name"]: r for r in rows}
    truth("who --json: purpose clean, held -> held when not alive", byname["alpha"]["purpose"] == "Keeps the alpha service healthy" and "\x1b" not in out and byname["zed"]["state"] == "down", str(byname))
    rc, out, _ = e.run("who", "gamma")
    check("who <name>: one line", 1, len(out.splitlines()))
    rc, out, _ = e.run("who", "nosuch")
    check("who <unknown>: rc 1", 1, rc)
    # held (not alive) shows held
    e.run("sync")
    rc, out, _ = e.run("hold", "beta")
    truth("who: a held session that is not running shows held", any(l.startswith("beta ") and " held " in l for l in e.run("who")[1].splitlines()), e.run("who")[1])
    # the retired one shows asleep
    jx = asleep_job(e, "zed", 6, state="done")
    truth("who: a retired session shows asleep", any(l.startswith("zed ") and "asleep" in l.split() for l in e.run("who")[1].splitlines()), e.run("who")[1])
    # the config format: backward compatible, purpose to the end of the line, `-` effort
    with inproc(e) as m:
        cfg = m.load_config()
        check("config: the 4-column form still works", ("dev", False, "low", ""), (cfg["delta"]["role"], cfg["delta"]["always_on"], cfg["delta"]["effort"], cfg["delta"]["purpose"]))
        check("config: `-` effort and a purpose", ("", True, "Keeps the alpha service healthy"), (cfg["gamma"]["effort"], cfg["alpha"]["always_on"], cfg["alpha"]["purpose"]))
        truth("config: a purpose is capped and has no control characters", len(cfg["gamma"]["purpose"]) <= 160 and "\x1b" not in cfg["gamma"]["purpose"])


def t_new(e):
    e.config("# my sessions\n\nold-one dev   # a comment\nexisting-job dev - high\n")
    cfg0 = open(e.cfg).read()
    d = e.workdir("proj")
    base = ["--dir", d, "--model", "haiku", "--effort", "low"]
    marker = os.path.join(e.root, "pwned")
    brief = "-- do $(touch %s) `touch %s` \"quoted\" 'single'; and a\nsecond line" % (marker, marker)
    rc, out, er = e.run("new", "shop-research", *base, "--role", "research", "--purpose", "market # research", "--permission-mode", "plan", "--", brief)
    check("new: rc 0", 0, rc)
    calls = claude_calls(e)
    truth("new: one claude call, from the directory, flags first, the brief is ONE argument after `--`",
          len(calls) == 1 and "cwd=%s\t" % d in calls[0] and "argc=11\t" in calls[0] and "--bg -n shop-research --model haiku --effort low --permission-mode plan -- " in calls[0], str(calls))
    truth("new: the brief was never run by a shell", not os.path.exists(marker))
    truth("new: no CLAUDE* variable", "claudevars=0" in calls[0])
    r = e.reg()["shop-research"]
    check("new: registered at once (managed job, role, purpose without the # )", (True, "job", "research", "market research", "low"), (r["managed"], r["kind"], r["role"], r["purpose"], r["effort"]))
    truth("new: the line was appended to the config, comments and blank lines untouched", open(e.cfg).read() == cfg0 + "shop-research research - low market research\n", open(e.cfg).read())
    truth("new: it shows in `fleet who` as awake with its purpose", any(l.startswith("shop-research") and "awake" in l and l.endswith("market research") for l in e.run("who")[1].splitlines()))
    # name rules
    e.reset_logs()
    for bad_name in ("Shop-research", "shop", "a-b-c-d-e", "shop_research", "-shop-x", "shop--x", "shop-x-", "sh op-x", "x" * 70 + "-y", "../etc-x"):
        rc, out, er = e.run("new", bad_name, *base, "brief")
        truth("new: bad name %r refused (rc 2)" % bad_name, rc == 2 and ("name rule" in er or bad_name.startswith("-")), er)
    check("new: no claude call for any bad name", 0, len(claude_calls(e)))
    # missing flags: never a default
    for label, args in (("model", ["--dir", d, "--effort", "low"]), ("effort", ["--dir", d, "--model", "haiku"]), ("dir", ["--model", "haiku", "--effort", "low"]),
                        ("a missing dir", ["--dir", d + "/nope", "--model", "haiku", "--effort", "low"]), ("a bad effort", ["--dir", d, "--model", "haiku", "--effort", "ultra"]),
                        ("a bad model", ["--dir", d, "--model", "x;rm", "--effort", "low"]), ("a bad mode", base + ["--permission-mode", "a b"])):
        rc, out, er = e.run("new", "shop-two", *args, "brief")
        truth("new without %s: refused (rc 2)" % label, rc == 2, er)
    rc, out, er = e.run("new", "shop-two", *base, "   ")
    truth("new: an empty brief is refused", rc == 2, er)
    check("new: still no claude call", 0, len(claude_calls(e)))
    # duplicates, in every place
    pn = e.spawn()
    e.sfile(pn, sessionId=SID[20], cwd="/", kind="interactive", name="live-one", status="idle")           # a live session, unregistered
    e.jwrite(os.path.join(e.home, "jobs", SID[21][:8], "state.json"), {"state": "done", "name": "job-only", "sessionId": SID[21], "cwd": d, "createdAt": "x", "respawnFlags": []})
    e.pane_session("pane-live", 22)
    e.run("sync")
    reg = json.load(open(os.path.join(e.state, "registry.json")))
    reg["sessions"]["renamed-now"] = dict(reg["sessions"]["shop-research"], former_names=["was-called"])
    json.dump(reg, open(os.path.join(e.state, "registry.json"), "w"))
    for dup, where in (("shop-research", "registry"), ("was-called", "former name"), ("old-one", "config"), ("live-one", "live session"),
                       ("job-only", "job row"), ("pane-live", "")):
        rc, out, er = e.run("new", dup, *base, "brief")
        truth("new: the name %s exists already (%s): refused, points at `fleet wake`" % (dup, where or "registry"), rc == 3 and "exists already" in out and "fleet wake %s" % dup in out, out + er)
    check("new: still no claude call after the duplicates", 0, len(claude_calls(e)))
    # the directory is not trusted: nothing created, a clear message
    cfg1 = open(e.cfg).read()
    rc, out, er = e.run("new", "shop-three", *base, "brief", extra={"FAKE_UNTRUSTED": "1"})
    truth("new in an untrusted directory: rc 1, 'not trusted', says what to do, nothing created", rc == 1 and "not trusted" in out and "accept the trust prompt" in out and "Nothing was created" in out
          and not os.path.isdir(os.path.join(e.home, "jobs", "x")) and open(e.cfg).read() == cfg1 and "shop-three" not in e.reg(), out)
    # verification failure: the saved flags are not the requested ones (or no job showed up): what exists + how to clean up
    rc, out, er = e.run("new", "shop-four", *base, "brief", extra={"FAKE_BG_MODEL": "other-model"})
    truth("new: saved flags differ -> rc 1, NOT verified, the job id and `claude stop` / `claude rm`", rc == 1 and "NOT verified" in out and "--model haiku" in out and "claude stop" in out and "claude rm" in out, out)
    truth("new: nothing is appended to the config after a failed verification", "shop-four" not in open(e.cfg).read())
    rc, out, er = e.run("new", "shop-five", *base, "brief", extra={"FAKE_BG_NOROW": "1", "FLEET_VERIFY_TIMEOUT": "1"})
    truth("new: no job row -> rc 1, says nothing found / check claude agents", rc == 1 and "NOT verified" in out and "no live job named shop-five" in out, out)
    # outside the prefix
    rc, out, er = e.run("new", "shop-six", *base, "brief", extra={"FLEET_NAME_PREFIX": "zz-"})
    truth("new: outside FLEET_NAME_PREFIX is refused", rc == 3, out + er)
    # no registry, no state dir, no config yet
    e2 = Env()
    try:
        rc, out, er = e2.run("new", "blog-writer", "--dir", e2.workdir("b"), "--model", "opus", "--effort", "high", "Write a post.")
        truth("new with nothing yet: creates the state dir and the config, registers the session", rc == 0 and os.path.isdir(e2.state) and open(e2.cfg).read() == "blog-writer - - high\n" and "blog-writer" in e2.reg(), out + er)
        rc, out, er = e2.run("new", "blog-always", "--dir", e2.workdir("b"), "--model", "opus", "--effort", "high", "--always-on", "Go.")
        truth("new --always-on writes the flag", rc == 0 and "blog-always - always-on high" in open(e2.cfg).read(), out + er)
    finally:
        e2.cleanup()
    # --help: one usage line and one example each
    for cmd, ex in (("wake", "example: fleet wake "), ("who", "example: fleet who"), ("new", "example: fleet new ")):
        p = subprocess.run([FLEET, cmd, "--help"], env=e.env, capture_output=True, text=True, timeout=30)
        truth("fleet %s --help: one usage line and an example" % cmd, p.returncode == 0 and p.stdout.startswith("usage: fleet %s" % cmd) and ex in p.stdout and len(p.stdout.splitlines()[0]) < 260, p.stdout)


# ================================================================ conversation id (in-app "move to background")
def slug_of(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def pre_move_file(e, name, pre_sid, new_sid, effort="xhigh", model="claude-sonnet-5-5", cont=True, agent=None, extra_tail=""):
    """the PRE-MOVE conversation file of a job whose own id has none: it keeps the history (an assistant record, the name
    records) and ends with a `continued-in` record naming the new id, as the real ones do"""
    d = os.path.join(e.home, "projects", slug_of(e.workdir(name)))
    os.makedirs(d, exist_ok=True)
    lines = [json.dumps({"type": "user", "sessionId": pre_sid, "message": {"content": "hi"}}),
             json.dumps({"type": "assistant", "sessionId": pre_sid, "timestamp": "2026-11-16T12:00:00.000Z", "effort": effort, "perTurnEffort": effort,
                         "message": {"model": model, "role": "assistant"}}),
             json.dumps({"type": "agent-name", "agentName": agent or name, "sessionId": pre_sid})]
    if cont:
        lines.append(json.dumps({"type": "continued-in", "timestamp": "2026-11-16T12:30:00.000Z", "sessionId": pre_sid, "continuedInSessionId": new_sid}))
    p = os.path.join(d, pre_sid + ".jsonl")
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n" + extra_tail)
    return p


def moved_job(e, name, n, pre_n, live=True, **kw):
    """a job made by the in-app move: its session id SID[n] has NO conversation file, the pre-move SID[pre_n] has"""
    pid, job = e.job_session(name, n, conv=False, live=live, **kw)
    pre_move_file(e, name, SID[pre_n], SID[n])
    return pid, job


def t_conv(e):
    e.config("own dev\nmoved dev always-on\nbyname dev\namb dev always-on\nnone dev always-on\nhist dev\n")
    e.job_session("own", 1)
    pm, jm = moved_job(e, "moved", 2, 22)
    pb, jb = e.job_session("byname", 3, conv=False)                 # no pointer: found by the name record of ONE newer file
    pre_move_file(e, "byname", SID[23], SID[3], cont=False)
    pa, ja = e.job_session("amb", 4, conv=False)                    # two files carry the name: ambiguous
    pre_move_file(e, "amb", SID[24], SID[4], cont=False)
    pre_move_file(e, "amb", SID[25], SID[4], cont=False)
    pn, jn = e.job_session("none", 5, conv=False)                   # nothing at all
    ph, jh = e.job_session("hist", 6)                               # an earlier id of the row has the file
    e.run("sync")
    r = e.reg()
    check("conv: own id with a file", ("own", SID[1]), (r["own"]["conv_how"], r["own"]["conv_id"]))
    check("conv: the `continued-in` record of the pre-move file is the pointer", ("continued-in", SID[22]), (r["moved"]["conv_how"], r["moved"]["conv_id"]))
    check("conv: one newer file with the session's name record", ("name record", SID[23]), (r["byname"]["conv_how"], r["byname"]["conv_id"]))
    check("conv: two candidates = unknown, never guessed", ("unknown", ""), (r["amb"]["conv_how"], r["amb"]["conv_id"]))
    check("conv: no candidate = unknown", ("unknown", ""), (r["none"]["conv_how"], r["none"]["conv_id"]))
    pt, jt = e.job_session("twoptr", 7, conv=False)                 # TWO files carry a pointer at it: ambiguous too
    e.config(open(e.cfg).read() + "twoptr dev\n")
    pre_move_file(e, "twoptr", SID[28], SID[7])
    pre_move_file(e, "twoptr", SID[29], SID[7])
    e.run("sync")
    check("conv: two files with a `continued-in` pointer at it = unknown, not the first", ("unknown", ""), (e.reg()["twoptr"]["conv_how"], e.reg()["twoptr"]["conv_id"]))
    # id history: the live id changes to one without a file, the old id's file is still there
    f, d = sessions_file_of(e, "hist")
    d["sessionId"] = SID[26]
    json.dump(d, open(f, "w"))
    e.run("sync")
    r = e.reg()["hist"]
    check("conv: from id_history", ("id history", SID[6]), (r["conv_how"], r["conv_id"]))
    # model / effort come from the resolved file (the pre-move one), not silently from the later sources
    r = e.reg()["moved"]
    check("conv: model and effort are read from the RESOLVED file", ("claude-sonnet-5-5", "xhigh", "conversation", "conversation"), (r["model"], r["effort"], r["model_src"], r["effort_src"]))
    # status / who / json flag it
    rc, out, _ = e.run("status")
    ln = {l.split()[0]: l for l in out.splitlines() if l.split()}
    truth("status: a row without an own conversation file is flagged with the resolved id (short)", "NO-OWN-CONV:%s" % SID[22][:8] in ln["moved"] and "NO-OWN-CONV:%s" % SID[6][:8] in ln["hist"], str(ln))
    truth("status: unknown is flagged too, an own row is not", "NO-OWN-CONV:unknown" in ln["amb"] and "NO-OWN-CONV" not in ln["own"], str(ln))
    rc, out, _ = e.run("who")
    ln = {l.split()[0]: l for l in out.splitlines() if l.split()}
    truth("who: conv:<short> / conv:unknown after the state", "conv:%s" % SID[22][:8] in ln["moved"] and "conv:unknown" in ln["none"] and "conv:" not in ln["own"], str(ln))
    rows = {x["name"]: x for x in json.loads(e.run("who", "--json")[1])}
    check("who --json: conv", (SID[22][:8], "unknown", "own"), (rows["moved"]["conv"], rows["none"]["conv"], rows["own"]["conv"]))
    d = json.loads(e.run("status", "--json")[1])
    check("status --json: conv_id and conv_how", (SID[22], "continued-in"), (d["moved"]["conv_id"], d["moved"]["conv_how"]))
    # the ids/ directory maps the conversation id too (the tmux restore asks `fleet owns <id>`)
    truth("ids/ has the conversation id", os.path.exists(os.path.join(e.state, "ids", SID[22])))

    # ---- every resume path uses the conversation id; respawn only when the own file exists
    for n in (pm,):
        e.kill(n)                                               # the moved job dies (a crash: stale file stays)
    e.kill(ph)
    e.kill(ph)
    pids_other = [pn, pa, pb]
    for p_ in pids_other:
        e.kill(p_)
    e.run("sync")
    e.reset_logs()
    rc, out, _ = e.run("up", "--dry-run")
    truth("up: a moved job (own id has no file) is NOT respawned: it is resumed by the pre-move id with every flag",
          "moved: would run: cd " in out and "claude --bg --resume %s -n moved --model claude-sonnet-5-5 --effort xhigh" % SID[22] in out and "respawn %s" % jm not in out, out)
    truth("up: an unresolved session (ambiguous / none) is refused with the grep and the manual command, never resumed",
          "amb: skipped, no conversation file found" in out and "none: skipped, no conversation file found" in out and "<CONVERSATION-ID>" in out
          and "grep -l" in out and "continuedInSessionId" in out and "amb: would run" not in out and "none: would run" not in out, out)
    rc, out, _ = e.run("up")
    calls = [c.split("\t")[-1] for c in claude_calls(e)]
    truth("up: the real run resumed the moved job by its pre-move id, nothing for the unresolved ones",
          any(c.startswith("--bg --resume %s -n moved " % SID[22]) for c in calls) and not any(SID[24] in c or SID[25] in c or SID[4] in c or SID[5] in c for c in calls), str(calls))
    truth("up: the moved job is up again and carries the pre-move id as its conversation", e.reg()["moved"]["kind"] == "job" and e.reg()["moved"]["session_id"] == SID[22], str(e.reg()["moved"]["session_id"]))

    # ---- a row with an own file still goes through `claude respawn`
    e2 = Env()
    try:
        e2.config("own dev always-on\n")
        po, jo = e2.job_session("own", 1)
        e2.run("sync")
        e2.kill(po)
        e2.reset_logs()
        e2.run("up")
        check("up: own file -> `claude respawn <job>`", ["respawn " + jo], [c.split("\t")[-1] for c in claude_calls(e2)])
    finally:
        e2.cleanup()

    # ---- no job row (removed): the resume uses the conversation id too
    e3 = Env()
    try:
        e3.config("gone dev always-on\n")
        pg, jg = moved_job(e3, "gone", 2, 22)
        e3.run("sync")
        e3.kill(pg)
        shutil.rmtree(os.path.join(e3.home, "jobs", jg))
        e3.run("sync")
        e3.reset_logs()
        rc, out, _ = e3.run("up")
        truth("up with no job row: resumed by the conversation id", any(c.split("\t")[-1].startswith("--bg --resume %s -n gone " % SID[22]) for c in claude_calls(e3)), out + str(claude_calls(e3)))
    finally:
        e3.cleanup()

    # ---- rollback / unstash use revive_many with include_stopped: the same plan
    e4 = Env()
    try:
        e4.config("st dev\n")
        ps, js = moved_job(e4, "st", 2, 22)
        e4.run("sync")
        stop_it(e4, "st", js)
        e4.run("sync")
        e4.reset_logs()
        with inproc(e4) as m:
            res = m.revive_many(m.load_registry(), ["st"], False, include_stopped=True)
        truth("revive_many (unstash / rollback): the stopped moved job is resumed by the conversation id", res[0][1] is True
              and any(c.split("\t")[-1].startswith("--bg --resume %s -n st " % SID[22]) for c in claude_calls(e4)), str(res) + str(claude_calls(e4)))
    finally:
        e4.cleanup()

    # ---- wake: rm the session's own dead row, resume by the conversation id; an unresolved one is refused
    e5 = Env()
    try:
        e5.config("sleepy dev always-on\namb dev\n")
        pj, jj = moved_job(e5, "sleepy", 2, 22, flags=["--model", "claude-sonnet-5-5", "--effort", "xhigh"])   # (the saved flags match the record)
        with open(os.path.join(e5.home, "daemon.log"), "a") as lf:      # an OLD crash line of the job that will be started: must not count
            lf.write("[2020-01-01T00:00:00.000Z] [bg] bg settled %s (crashed): source session x not found\n" % SID[22][:8])
        pa5, ja5 = e5.job_session("amb", 4, conv=False)
        pre_move_file(e5, "amb", SID[24], SID[4], cont=False)
        pre_move_file(e5, "amb", SID[25], SID[4], cont=False)
        e5.run("sync")
        retire_now(e5, pj, jj)
        retire_now(e5, pa5, ja5)
        e5.run("sync")
        e5.reset_logs()
        rc, out, _ = e5.run("wake", "sleepy", extra={"FAKE_REQUIRE_CONV": "1", "FLEET_CRASH_WAIT": "2"})
        calls = [c.split("\t")[-1] for c in claude_calls(e5)]
        truth("wake of a moved job: `rm <its own job>` then resume by the pre-move id, `awake`, no crash", rc == 0 and out == "awake\n" and calls[0] == "rm " + jj
              and calls[1].startswith("--bg --resume %s -n sleepy " % SID[22]) and len(calls) == 2, str((rc, out, calls)))
        e5.reset_logs()
        rc, out, _ = e5.run("wake", "amb")
        truth("wake of an unresolved session: refused with the manual command, no call", rc == 3 and "no conversation file found" in out and "<CONVERSATION-ID>" in out and not claude_calls(e5), out)
        # the fake reproduces the real bug: the old way (respawn by the own id, no file) crashes
        r = subprocess.run([os.path.join(e5.bin, "claude"), "respawn", ja5], env=dict(e5.env, FAKE_REQUIRE_CONV="1", FAKE_CRASH_DELAY="0.1"), capture_output=True, text=True, timeout=30, cwd=e5.root)
        time.sleep(0.5)
        truth("the fake claude reproduces `source session <id> not found` for a respawn without a conversation file",
              "(crashed): source session %s not found" % SID[4] in open(os.path.join(e5.home, "daemon.log")).read(), e5.log("claude.log"))
    finally:
        e5.cleanup()

    # ---- a crash after the resume is reported (rc != 0, the daemon line, the manual command) and counted by the backoff
    e6 = Env()
    try:
        e6.config("dying dev always-on\n")
        pd, jd = moved_job(e6, "dying", 2, 22)
        e6.run("sync")
        e6.kill(pd)
        e6.run("sync")
        rc, out, _ = e6.run("up", extra={"FAKE_CRASH_ALWAYS": "1", "FLEET_CRASH_WAIT": "2"})
        truth("up: a crash line after the resume = failure (rc 1), the daemon line and the manual command are printed",
              rc == 1 and "the daemon logged a crash" in out and "source session" in out and "by hand: cd " in out and "--resume %s" % SID[22] in out, out)
        # the watchdog: each tick that restarts it is counted; after 3 in an hour it is marked failed and tells the user
        for i in range(4):
            e6.run("watch", "--once", "--no-up", extra={"FAKE_CRASH_ALWAYS": "1", "FLEET_CRASH_WAIT": "1.2"})
            f6, d6 = sessions_file_of(e6, "dying")
            if d6 and d6["pid"] > 1:
                e6.kill(d6["pid"])
            time.sleep(0.2)
        reg = e6.reg()["dying"]
        truth("watch: crashes are counted against the 3-per-hour backoff; then `failed` and a notification", len(reg["restarts"]) >= 3 and (reg["failed"] or "keeps dying" in e6.log("notify.log")), str((reg["restarts"], reg["failed"], e6.log("notify.log"))))
    finally:
        e6.cleanup()
    # wake: a crash after the start is a failure too
    e7 = Env()
    try:
        e7.config("wk dev always-on\n")
        pw, jw = moved_job(e7, "wk", 2, 22)
        e7.run("sync")
        retire_now(e7, pw, jw)
        e7.run("sync")
        rc, out, _ = e7.run("wake", "wk", extra={"FAKE_CRASH_ALWAYS": "1", "FLEET_CRASH_WAIT": "2"})
        truth("wake: a crash after the start: rc 1, `did not stay up`, the daemon line, no `awake`", rc == 1 and "did not stay up" in out and "the daemon logged a crash" in out and "awake\n" not in out, out)
    finally:
        e7.cleanup()
    # bg (a pane session): the same check
    e8 = Env()
    try:
        e8.config("alpha dev\n")
        e8.pane_session("alpha", 1)
        e8.run("sync")
        rc, out, _ = e8.run("bg", "alpha", extra={"FAKE_CRASH_ALWAYS": "1", "FLEET_CRASH_WAIT": "2"})
        truth("bg: a crash after the move is reported; the half-done record stays", rc == 1 and "did not stay up" in out and os.path.exists(os.path.join(e8.state, "moving.json"))
              and "alpha" in json.load(open(os.path.join(e8.state, "moving.json"))), out)
    finally:
        e8.cleanup()
    # hostile / odd pre-move files
    e9 = Env()
    try:
        e9.config("odd dev\n")
        e9.job_session("odd", 1, conv=False)
        pre_move_file(e9, "odd", SID[22], SID[1], extra_tail='{"type":"continued-in","continuedInSessionId":"../../etc/passwd"}\n{ torn')
        e9.run("sync")
        check("a hostile / truncated tail does not break the pointer", ("continued-in", SID[22]), (e9.reg()["odd"]["conv_how"], e9.reg()["odd"]["conv_id"]))
        # a pointer file that does NOT name this session is not used
        e9.config("odd2 dev\n")
        e9.job_session("odd2", 3, conv=False)
        pre_move_file(e9, "odd2", SID[23], SID[27], cont=True, agent="someone-else")
        e9.run("sync")
        check("a `continued-in` record naming another session is not a pointer", "unknown", e9.reg()["odd2"]["conv_how"])
    finally:
        e9.cleanup()


def main():
    for name, fn in [("units", t_units), ("sync", t_sync), ("entries (derived membership)", t_entries), ("classify", t_classify),
                     ("bg happy path", t_bg_happy), ("bg refusals", t_bg_refusals),
                     ("bg failures", t_bg_failures), ("bg scope / all", t_bg_multi), ("models / effort", t_models),
                     ("permission mode", t_permission), ("up / hold", t_up), ("stopped on purpose", t_stopped),
                     ("watch tick", t_watch_tick), ("api + single instance", t_api),
                     ("stash (background)", t_stash_bg), ("stash --disable", t_disable), ("disable edges", t_disable_edges),
                     ("unstash disabled", t_unstash_disabled), ("reboot survival", t_reboot),
                     ("peek", t_peek), ("picker (pty)", t_pick_pty), ("menu + bar", t_menu), ("real tmux", t_tmux_real),
                     ("view", t_view), ("owns", t_owns),
                     ("fleet-hook", t_hook), ("notify-hook", t_notify_hook), ("resurrect guard", t_resurrect),
                     ("run_claude stop gate", t_run_claude_stop),
                     ("rename / identity", t_rename), ("missing rows / states", t_missing_rows), ("corrupt registry", t_corrupt),
                     ("brake / lock / second read", t_brake_lock), ("bg races", t_bg_races), ("disable: untracked / busy shells", t_disable_untracked),
                     ("disable: busy / partial", t_disable_busy), ("multi-pane", t_multipane), ("resurrect snapshot drop", t_resurrect_drop),
                     ("notify target", t_notify_target), ("minors", t_minors), ("systemd scope", t_systemd_scope),
                     ("menu confirm (real tmux)", t_menu_confirm_real),
                     ("M1/M2: laundering, boot id, recorded pid", t_launder), ("M3: done is restartable", t_done_state),
                     ("M5: owns", t_owns_kinds), ("m4/m11/m12: flag warnings", t_flag_warn), ("review-2 minors", t_fix_minors),
                     ("clean stop vs crash", t_stop_vs_crash), ("conversation file", t_conversation),
                     ("retired", t_retired), ("wake", t_wake), ("who", t_who), ("new", t_new),
                     ("conversation id", t_conv)]:
        if os.environ.get("FLEET_TEST_ONLY") and fn.__name__ not in os.environ["FLEET_TEST_ONLY"].split(","):
            continue
        before = FAIL
        try:
            scenario(fn)
        except Exception as ex:  # a crashed scenario is a failure, not a silent skip
            import traceback
            traceback.print_exc()
            bad("%s: scenario crashed: %r" % (name, ex))
        if FAIL > before:
            print("  (%s: %d failing)" % (name, FAIL - before), file=sys.stderr)
    print()
    if FAIL == 0:
        print("fleet tests: %d passed, 0 failed" % PASS)
        return 0
    print("fleet tests: %d passed, %d FAILED" % (PASS, FAIL))
    return 1


if __name__ == "__main__":
    sys.exit(main())
