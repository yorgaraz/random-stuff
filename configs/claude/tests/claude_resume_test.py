#!/usr/bin/env python3
"""Behaviour tests for configs/claude/bin/claude-resume (id input, --restore) and the claude-resurrect.sh restore
hand-off. Everything runs against a fake HOME and stub `claude`, `claude-wake` and `tmux` on PATH: no real session,
job or tmux server is touched. Run: python3 -I configs/claude/tests/claude_resume_test.py (exit 0 = pass)."""
import json, os, shutil, subprocess, sys, tempfile, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
RESUME = os.path.join(ROOT, "configs/claude/bin/claude-resume")
RESURRECT = os.path.join(ROOT, "configs/tmux/claude-resurrect.sh")
passed = failed = 0


def check(label, want, got):
    global passed, failed
    if want == got:
        passed += 1
    else:
        failed += 1
        print(f"FAIL {label}\n  want: {want!r}\n  got:  {got!r}")


def sid():
    return str(uuid.uuid4())


class Home:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="claude-resume-test.")
        self.home = os.path.join(self.root, "home")
        self.cl = os.path.join(self.home, ".claude")
        self.bin = os.path.join(self.root, "bin")
        self.log = os.path.join(self.root, "calls.log")
        self.rows_file = os.path.join(self.root, "rows.json")
        self.cwd = os.path.join(self.root, "work")
        for d in (self.cl + "/sessions", self.cl + "/jobs", self.bin, self.cwd):
            os.makedirs(d, exist_ok=True)
        self.rows = []
        self.sleepers = []
        self.stub("claude", '''#!/usr/bin/env bash
echo "claude $*" >> "$FAKE_LOG"
case "$1" in
  agents) cat "$FAKE_ROWS" ;;
  attach) echo "ATTACH $2" ;;
  *) exit 0 ;;
esac''')
        self.stub("claude-wake", '#!/usr/bin/env bash\necho "claude-wake $*" >> "$FAKE_LOG"\nexit ${FAKE_WAKE_RC:-0}')
        self.stub("tmux", '#!/usr/bin/env bash\necho "tmux $*" >> "$FAKE_LOG"\n'
                          '[[ $1 == list-panes ]] && echo "${FAKE_PANE_PID:-1} adv:2.1"\nexit 0')
        self.slug = os.path.join(self.cl, "projects", "".join(c if c.isalnum() else "-" for c in self.cwd))
        os.makedirs(self.slug, exist_ok=True)

    def stub(self, name, body):
        p = os.path.join(self.bin, name)
        open(p, "w").write(body + "\n")
        os.chmod(p, 0o755)

    def live_pid(self):
        p = subprocess.Popen(["sleep", "300"])
        self.sleepers.append(p)
        return p.pid

    def conv(self, s, records=()):
        with open(os.path.join(self.slug, s + ".jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "sessionId": s, "cwd": self.cwd}) + "\n")
            for r in records:
                f.write(json.dumps(r) + "\n")

    def row(self, jid, s, name, pid=None, state="done"):
        r = {"id": jid, "kind": "background", "sessionId": s, "name": name, "cwd": self.cwd, "state": state}
        if pid:
            r["pid"] = pid
        self.rows.append(r)

    def session_file(self, pid, s, name, kind="interactive", job=None):
        d = {"pid": pid, "sessionId": s, "name": name, "kind": kind, "cwd": self.cwd}
        if job:
            d["jobId"] = job
        json.dump(d, open(os.path.join(self.cl, "sessions", f"{pid}.json"), "w"))

    def run(self, *args, extra=None):
        json.dump(self.rows, open(self.rows_file, "w"))
        open(self.log, "w").close()
        env = {"HOME": self.home, "PATH": self.bin + ":/usr/bin:/bin", "FAKE_LOG": self.log,
               "FAKE_ROWS": self.rows_file, "TMUX": "/tmp/fake,1,0"}
        env.update(extra or {})
        p = subprocess.run([sys.executable, "-I", RESUME, *args], env=env, cwd=self.cwd, capture_output=True,
                           text=True, timeout=30)
        return p.returncode, p.stdout, open(self.log).read()

    def close(self):
        for p in self.sleepers:
            p.kill()
            p.wait()
        shutil.rmtree(self.root, ignore_errors=True)


def mutating(log):
    return [l for l in log.splitlines() if l.split()[1:2] and l.split()[1] in ("stop", "rm", "respawn", "--bg")]


def t_restore():
    h = Home()
    try:
        # 1. pane-only conversation, no background job, runs nowhere -> 10 (resume it in the pane)
        a = sid()
        h.conv(a, [{"type": "custom-title", "customTitle": "proj-alpha"}])
        rc, out, log = h.run("--restore", a)
        check("restore: plain pane conversation -> 10", 10, rc)
        check("restore: plain pane conversation -> no mutating call", [], mutating(log))

        # 2. unnamed conversation, runs nowhere -> 10
        b = sid()
        h.conv(b)
        check("restore: unnamed conversation -> 10", 10, h.run("--restore", b[:8])[0])

        # 3. clean awake job -> attach to it, by the 8-hex prefix
        c = sid()
        h.conv(c)
        pid = h.live_pid()
        h.row(c[:8], c, "proj-gamma", pid=pid, state="working")
        h.session_file(pid, c, "proj-gamma", kind="bg", job=c[:8])
        rc, out, log = h.run("--restore", c[:8])
        check("restore: clean awake job -> attach", (0, "ATTACH " + c[:8]), (rc, out.strip().splitlines()[-1]))
        check("restore: clean awake job -> no mutating call", [], mutating(log))

        # 4. clean asleep job -> claude-wake, then attach
        d = sid()
        h.conv(d)
        h.row(d[:8], d, "proj-delta")
        rc, out, log = h.run("--restore", d)
        check("restore: asleep job -> wake + attach", (0, True, "ATTACH " + d[:8]),
              (rc, "claude-wake proj-delta" in log, out.strip().splitlines()[-1]))

        # 5. pointer-only job (in-app move): pre-move file ends in continued-in; job awake -> attach to the job
        pre, new = sid(), sid()
        h.conv(pre, [{"type": "custom-title", "customTitle": "proj-eps"},
                     {"type": "continued-in", "sessionId": pre, "continuedInSessionId": new}])
        pid = h.live_pid()
        h.row(new[:8], new, "proj-eps", pid=pid, state="working")
        h.session_file(pid, new, "proj-eps", kind="bg", job=new[:8])
        rc, out, log = h.run("--restore", pre)
        check("restore: parked pane id, job awake -> attach to the job", (0, "ATTACH " + new[:8]),
              (rc, out.strip().splitlines()[-1]))
        check("restore: parked pane id -> no mutating call", [], mutating(log))

        # 6. same shape, job asleep -> refuse (3), no repair at boot
        pre2, new2 = sid(), sid()
        h.conv(pre2, [{"type": "custom-title", "customTitle": "proj-zeta"},
                      {"type": "continued-in", "sessionId": pre2, "continuedInSessionId": new2}])
        h.row(new2[:8], new2, "proj-zeta")
        rc, out, log = h.run("--restore", pre2)
        check("restore: pointer-only asleep -> 3", 3, rc)
        check("restore: pointer-only asleep -> no mutating call", [], mutating(log))
        check("restore: pointer-only asleep -> says how to fix", True, "claude-resume proj-zeta" in out)

        # 7. two copies of one name, one live -> attach to the live one, never repair
        e1, e2 = sid(), sid()
        h.conv(e1, [{"type": "continued-in", "sessionId": e1, "continuedInSessionId": e2}])
        pid = h.live_pid()
        h.row(e1[:8], e1, "proj-eta")
        h.row(e2[:8], e2, "proj-eta (2)", pid=pid, state="working")
        rc, out, log = h.run("--restore", e1)
        check("restore: copies, one live -> attach to it", (0, "ATTACH " + e2[:8]), (rc, out.strip().splitlines()[-1]))
        check("restore: copies -> no mutating call", [], mutating(log))

        # 8. conversation open in a live pane and a job row -> 3, no client switch
        f = sid()
        h.conv(f)
        pid = h.live_pid()
        h.row(f[:8], f, "proj-theta")
        h.session_file(pid, f, "proj-theta")
        rc, out, log = h.run("--restore", f, extra={"FAKE_PANE_PID": str(pid)})
        check("restore: open in a pane + job -> 3", 3, rc)
        check("restore: never switches the client", False, "switch-client" in log)

        # 9. an id that is not a session -> 2; a prefix that matches two -> 2
        check("restore: unknown id -> 2", 2, h.run("--restore", "0123abcd")[0])
        g1 = "ab12cd34" + sid()[8:]
        g2 = "ab12cd34" + sid()[8:]
        h.conv(g1)
        h.conv(g2)
        check("id: ambiguous prefix -> 2", 2, h.run("--restore", "ab12cd34")[0])

        # 10. --restore without an id -> 2
        check("restore: needs an id", 2, h.run("--restore", "proj-alpha")[0])

        # 11. id given without --restore, clean awake job -> attach
        rc, out, log = h.run(c, "--no-attach")
        check("id: clean awake job -> ready line", (0, True), (rc, c[:8] in out))

        # 12. id of an unnamed conversation without --restore -> 2 with the resume command
        rc, out, _ = h.run(b)
        check("id: unnamed -> 2 + command", (2, True), (rc, f"claude --resume {b}" in out))
    finally:
        h.close()


def t_resurrect_handoff():
    root = tempfile.mkdtemp(prefix="resurrect-handoff.")
    try:
        stubs, rdir = os.path.join(root, "bin"), os.path.join(root, "r")
        os.makedirs(stubs)
        os.makedirs(rdir)
        s = sid()
        open(os.path.join(rdir, "claude_sessions.tsv"), "w").write(f"sess:1.0\t{s}\t{root}\n")

        def stub(name, body):
            p = os.path.join(stubs, name)
            open(p, "w").write("#!/usr/bin/env bash\n" + body + "\n")
            os.chmod(p, 0o755)
        stub("tmux", f'case "$1" in show) echo "{rdir}" ;; display) echo "sess:1.0" ;; esac')
        stub("claude", 'echo "claude $*"')
        stub("claude-resume", 'echo "claude-resume $*"; exit ${STUB_RC:-0}')
        stub("fleet", '[[ $1 == owns && -n ${STUB_OWNS:-} ]] && { echo "$STUB_OWNS"; exit 0; }; exit 1')

        def run(extra):
            env = {"HOME": root, "PATH": stubs + ":/usr/bin:/bin", "TMUX_PANE": "%1",
                   "CLAUDE_RESURRECT_TMUX": os.path.join(stubs, "tmux")}
            env.update(extra)
            return subprocess.run(["bash", RESURRECT, "restore"], env=env, capture_output=True, text=True,
                                  timeout=20).stdout.strip().splitlines()
        check("resurrect: dry run names claude-resume", [f"claude-resume --restore {s}"],
              run({"CLAUDE_RESURRECT_DRY": "1"}))
        check("resurrect: claude-resume 10 -> resume in the pane", [f"claude-resume --restore {s}", f"claude --resume {s}"],
              run({"STUB_RC": "10"}))
        check("resurrect: claude-resume 0 -> nothing more", [f"claude-resume --restore {s}"], run({"STUB_RC": "0"}))
        out = run({"STUB_RC": "3"})
        check("resurrect: claude-resume 3 -> no resume, a note", (2, True),
              (len(out), "left a shell" in out[-1] and not any(l.startswith("claude --resume") for l in out)))
        check("resurrect: fleet owner wins over claude-resume", ["exec fleet view jobby --shell"],
              run({"CLAUDE_RESURRECT_DRY": "1", "STUB_OWNS": "jobby"}))
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    t_restore()
    t_resurrect_handoff()
    print(f"claude-resume tests: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
