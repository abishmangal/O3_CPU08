"""Cycle-level OOO backend model. Engine only; experiments live in run.py.

Per-cycle order:  1 complete -> 2 wakeup -> 3 select/issue -> 4 dispatch
Latency L: issue at C -> completes at C+L -> consumer can issue at C+L.
A uop dispatched in cycle C can first be selected in C+1.
"""
import re, csv
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from enum import Enum


# ---------- config ----------
@dataclass(frozen=True)
class OpInfo:
    needs: tuple          # resource classes held at issue (store needs two)
    latency: int
    writes: bool = True   # has a destination register


def _default_ops():
    ops = {o: OpInfo(("ALU",), 1) for o in ("ADD", "SUB", "AND", "OR", "XOR")}
    ops.update(IMUL=OpInfo(("IMUL",), 4),
               FADD=OpInfo(("FADD",), 3), VADD=OpInfo(("FADD",), 3),
               FMUL=OpInfo(("FMUL",), 4), VMUL=OpInfo(("FMUL",), 4),
               LOAD=OpInfo(("LOAD",), 5),
               STORE=OpInfo(("STADDR", "STDATA"), 1, writes=False),
               BRANCH=OpInfo(("BRANCH",), 1, writes=False))
    return ops


@dataclass(frozen=True)
class Config:
    name: str = "baseline"
    dispatch_width: int = 6
    scheduler_capacity: int = 24
    issue_width: int = 6
    wakeup_bandwidth: int = 6
    pipelined: bool = True
    resources: dict = field(default_factory=lambda: dict(
        ALU=3, IMUL=1, FADD=2, FMUL=2, LOAD=2, STADDR=1, STDATA=1, BRANCH=1))
    ops: dict = field(default_factory=_default_ops)

    def tweak(self, name, **res):
        """Copy with some resource counts changed: cfg.tweak('C', ALU=4)."""
        return replace(self, name=name, resources={**self.resources, **res})


# ---------- uops ----------
class State(Enum):
    NEW = 0; WAITING = 1; READY = 2; SELECTED = 3; ISSUED = 4; COMPLETED = 5


@dataclass
class Uop:
    uid: int
    name: str
    op: str
    dst: str
    srcs: list
    needs: tuple
    latency: int
    producers: dict = field(default_factory=dict)
    consumers: list = field(default_factory=list)
    waiting_on: set = field(default_factory=set)
    state: State = State.NEW
    dispatch: int = None
    ready: int = None
    select: int = None
    issue: int = None
    complete: int = None
    units: list = field(default_factory=list)
    last_block: str = ""
    wait_cycles: int = 0


REG = re.compile(r"\b[A-Za-z]+\d+\b")


def parse(text, cfg):
    uops, last_writer = [], {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = line.split(None, 2)
        name, op, rest = parts[0], parts[1], (parts[2] if len(parts) > 2 else "")
        info = cfg.ops[op]
        regs = REG.findall(rest)
        dst = regs[0] if info.writes else None
        srcs = list(dict.fromkeys(regs[1:] if info.writes else regs))
        u = Uop(len(uops), name, op, dst, srcs, info.needs, info.latency)
        for r in srcs:
            if r in last_writer:
                p = last_writer[r]
                u.producers[r] = p
                if u.uid not in uops[p].consumers:
                    uops[p].consumers.append(u.uid)
        if dst:
            last_writer[dst] = u.uid
        uops.append(u)
    return uops


# ---------- simulation ----------
@dataclass
class Cycle:
    t: int
    waiting: int = 0
    ready: list = field(default_factory=list)
    selected: list = field(default_factory=list)
    unselected: list = field(default_factory=list)
    completed: list = field(default_factory=list)
    dispatched: list = field(default_factory=list)
    dispatch_stall: bool = False
    free_before: dict = field(default_factory=dict)
    busy: dict = field(default_factory=dict)
    wakeup_used: int = 0
    wakeup_backlog: int = 0


@dataclass
class Result:
    cfg: Config
    uops: list
    trace: list


def run(text, cfg, max_cycles=5000):
    uops = parse(text, cfg)
    free_at = {r: [0] * n for r, n in cfg.resources.items()}
    owner = {r: [None] * n for r, n in cfg.resources.items()}
    sched, inflight, pending = [], [], deque()
    nxt, done, t, trace = 0, 0, 0, []

    while done < len(uops):
        if t > max_cycles:
            raise RuntimeError("deadlock / runaway")
        row = Cycle(t)

        # 1. complete
        for u in [u for u in inflight if u.complete == t]:
            inflight.remove(u)
            u.state = State.COMPLETED
            done += 1
            row.completed.append(u.uid)
            for c in u.consumers:
                if uops[c].state == State.WAITING:
                    pending.append((uops[c], u))

        # 2. wakeup (bandwidth-limited; leftovers stay queued)
        while pending and row.wakeup_used < cfg.wakeup_bandwidth:
            c, p = pending.popleft()
            row.wakeup_used += 1
            c.waiting_on.discard(p.uid)
            if not c.waiting_on:
                c.state, c.ready = State.READY, t
        row.wakeup_backlog = len(pending)

        # 3. select + issue (oldest first)
        ready = sorted((u for u in sched if u.state == State.READY), key=lambda u: u.uid)
        row.waiting = sum(u.state == State.WAITING for u in sched)
        row.ready = [u.uid for u in ready]
        row.free_before = {r: sum(f <= t for f in fa) for r, fa in free_at.items()}
        for u in ready:
            if len(row.selected) >= cfg.issue_width:
                u.last_block = f"issue width {cfg.issue_width} full"
                row.unselected.append((u.uid, "ISSUE_WIDTH", u.last_block))
                u.wait_cycles += 1
                continue
            short = next((r for r in u.needs
                          if not any(f <= t for f in free_at[r])), None)
            if short:
                held = [uops[o].name for o in owner[short] if o is not None
                        and free_at[short][owner[short].index(o)] > t]
                u.last_block = f"all {len(free_at[short])} {short} unit(s) busy (held by {', '.join(held)})"
                row.unselected.append((u.uid, short, u.last_block))
                u.wait_cycles += 1
                continue
            u.state, u.select = State.SELECTED, t
            for r in u.needs:
                i = next(i for i, f in enumerate(free_at[r]) if f <= t)
                free_at[r][i] = t + (1 if cfg.pipelined else u.latency)
                owner[r][i] = u.uid
                u.units.append((r, i))
            u.state, u.issue, u.complete = State.ISSUED, t, t + u.latency
            sched.remove(u)
            inflight.append(u)
            row.selected.append(u.uid)
        row.busy = {r: sum(f > t for f in fa) for r, fa in free_at.items()}

        # 4. dispatch in program order; stalls if scheduler full
        while nxt < len(uops) and len(row.dispatched) < cfg.dispatch_width:
            if len(sched) >= cfg.scheduler_capacity:
                row.dispatch_stall = True
                break
            u = uops[nxt]; nxt += 1
            u.dispatch = t
            u.waiting_on = {p for p in u.producers.values()
                            if uops[p].state != State.COMPLETED}
            if u.waiting_on:
                u.state = State.WAITING
            else:
                u.state, u.ready = State.READY, t + 1
            sched.append(u)
            row.dispatched.append(u.uid)

        trace.append(row)
        t += 1
    return Result(cfg, uops, trace)


# ---------- checks, metrics, output ----------
def check(res):
    cfg, U = res.cfg, res.uops
    for u in U:
        assert u.dispatch < u.ready <= u.issue == u.select, u.name
        assert u.complete == u.issue + u.latency, u.name
        for p in u.producers.values():
            assert U[p].complete <= u.issue, f"{u.name} issued before {U[p].name} done"
    for row in res.trace:
        assert len(row.selected) <= cfg.issue_width
        assert row.wakeup_used <= cfg.wakeup_bandwidth
        assert row.waiting + len(row.ready) <= cfg.scheduler_capacity
        assert all(row.busy[r] <= n for r, n in cfg.resources.items())
    assert all(u.state == State.COMPLETED for u in U)


def critical_path(res):
    fin = {}
    for u in res.uops:
        fin[u.uid] = u.latency + max((fin[p] for p in u.producers.values()), default=0)
    return max(fin.values())


def longest_chain(res):
    """Return list of uids forming the longest producer chain."""
    fin, back = {}, {}
    for u in res.uops:
        if u.producers:
            p = max(u.producers.values(), key=lambda x: fin[x])
            fin[u.uid] = fin[p] + u.latency
            back[u.uid] = p
        else:
            fin[u.uid] = u.latency
            back[u.uid] = None
    end = max(fin, key=fin.get)
    chain = []
    while end is not None:
        chain.append(end); end = back[end]
    return list(reversed(chain))


def chains(res, k=3):
    """Return up to k distinct long chains, greedy by length."""
    seen, out = set(), []
    U = res.uops
    for _ in range(k):
        fin, back = {}, {}
        for u in U:
            if u.uid in seen:
                continue
            cands = [p for p in u.producers.values() if p not in seen]
            if cands:
                p = max(cands, key=lambda x: fin.get(x, 0))
                fin[u.uid] = fin.get(p, 0) + u.latency
                back[u.uid] = p
            else:
                fin[u.uid] = u.latency
                back[u.uid] = None
        if not fin:
            break
        end = max(fin, key=fin.get)
        chain = []
        while end is not None:
            chain.append(end); end = back.get(end)
        chain = list(reversed(chain))
        out.append(chain)
        seen.update(chain)
    return out


def demand(res):
    return Counter(r for u in res.uops for r in u.needs)


def metrics(res):
    cfg, T, n = res.cfg, len(res.trace), len(res.uops)
    mean = lambda f: sum(f(r) for r in res.trace) / T
    util = {r: sum(row.busy[r] for row in res.trace) / (c * T)
            for r, c in cfg.resources.items()}
    limits = Counter()
    for row in res.trace:
        for _, code, _ in row.unselected:
            limits["ready lost: " + code] += 1
        limits["dispatch stalled: scheduler full"] += row.dispatch_stall
        limits["wakeup backlog"] += row.wakeup_backlog > 0
    limits = Counter({k: v for k, v in limits.items() if v})
    cp = critical_path(res)
    lost = Counter({k: v for k, v in limits.items() if k.startswith("ready lost")})
    top = lost.most_common(1)[0][0] if lost else "dependency chains"
    if limits.get("dispatch stalled: scheduler full"):
        top += " (+scheduler full)"
    return dict(cycles=T, ipc=n / T, occ=mean(lambda r: r.waiting + len(r.ready)),
                ready=mean(lambda r: len(r.ready)), waiting=mean(lambda r: r.waiting),
                issue=mean(lambda r: len(r.selected)), util=util, limits=limits,
                crit_path=cp, bottleneck=top,
                unsel=sum(len(r.unselected) for r in res.trace))


def unsel_by_class(res):
    """Count ready-but-unselected events grouped by blocking resource class."""
    c = Counter()
    for row in res.trace:
        for _, code, _ in row.unselected:
            c[code] += 1
    return c


def names(res, ids):
    return " ".join(res.uops[i].name for i in ids) or "-"


def show_cycle(res, t):
    r, cfg = res.trace[t], res.cfg
    print(f"Cycle {t}")
    print(f"  Scheduler: occupied={r.waiting + len(r.ready)}/{cfg.scheduler_capacity} "
          f"ready={len(r.ready)} waiting={r.waiting}")
    print(f"  Ready:      {names(res, r.ready)}")
    print(f"  Selected:   {names(res, r.selected)}")
    for uid, _, why_ in r.unselected:
        print(f"  Unselected: {res.uops[uid].name} needs {'+'.join(res.uops[uid].needs)}: {why_}")
    print(f"  Resources free before select: {r.free_before}")
    print(f"  Resources busy after select:  {r.busy}")
    print(f"  Issue slots: used={len(r.selected)} remaining={cfg.issue_width - len(r.selected)}")
    print(f"  Wakeup: used={r.wakeup_used} available={cfg.wakeup_bandwidth - r.wakeup_used} "
          f"backlog={r.wakeup_backlog}")
    print(f"  Completed: {names(res, r.completed)}   Dispatched: {names(res, r.dispatched)}"
          f"{'  (STALLED: scheduler full)' if r.dispatch_stall else ''}")


def why(res, uid, t=None):
    """Backward trace for a uop that was ready but waited."""
    U = res.uops
    u = U[uid]
    print(f"{u.name} ready at C{u.ready}, issued at C{u.issue} "
          f"(lost selection {u.wait_cycles} cycle(s))")
    print(f"  last blocked: needs {'+'.join(u.needs)} -> {u.last_block}")
    tt = u.issue - 1
    span = lambda h: 1 if res.cfg.pipelined else h.latency
    held = [h for h in U if h is not u and set(h.needs) & set(u.needs)
            and h.issue <= tt < h.issue + span(h)]
    for h in held[:1]:
        print(f"  C{tt}: {h.name} held the unit (issued C{h.issue})")
        while h.producers:
            p = max(h.producers.values(), key=lambda x: U[x].complete)
            print(f"    {h.name} became ready C{h.ready} when {U[p].name} "
                  f"completed C{U[p].complete}")
            h = U[p]
        print(f"    {h.name} has no producers (starts the chain)")


def write_csv(res, prefix):
    with open(prefix + "_cycle.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cycle", "waiting", "ready", "selected", "unselected", "completed",
                    "dispatched", "dispatch_stall", "wakeup_used", "wakeup_backlog", "busy"])
        for r in res.trace:
            w.writerow([r.t, r.waiting, names(res, r.ready), names(res, r.selected),
                        " ".join(f"{res.uops[u].name}:{c}" for u, c, _ in r.unselected),
                        names(res, r.completed), names(res, r.dispatched),
                        int(r.dispatch_stall), r.wakeup_used, r.wakeup_backlog, r.busy])
    with open(prefix + "_uops.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["uid", "op", "dst", "srcs", "needs", "producers", "dispatch",
                    "ready", "select", "issue", "complete", "units", "ready_wait"])
        for u in res.uops:
            w.writerow([u.name, u.op, u.dst, " ".join(u.srcs), "+".join(u.needs),
                        " ".join(res.uops[p].name for p in u.producers.values()),
                        u.dispatch, u.ready, u.select, u.issue, u.complete,
                        " ".join(f"{r}{i}" for r, i in u.units), u.issue - u.ready])