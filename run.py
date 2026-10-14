"""Experiment driver.

Commands:
  base       full run, metrics, writes traces/<stem>_{cycle,uops}.csv
  ready      cycles with 2+ ready uops, full state
  contention demand per resource class + ready-but-unselected counts
  sched      scheduler capacity 12/24/48
  width      issue/wakeup 3/3 vs 6/6
  machines   Machines A-F
  crime      frozen backend state + backward trace (--cycle N --uop I14)
  inv1       dependency chains + independent work
  inv4       dependency-heavy vs more-independent (--wl ignored; uses both files)
  inv7       A-F with predictions printed
  inv12      proposed modification vs baseline
  inv13      counterexample workload with proposed modification
  all        run every experiment in sequence

Flags:
  --wl FILE       workload file (default workload.uops)
  --cycle N       cycle for crime
  --uop I14       uop name for crime
  --unpipelined   a unit stays busy for the whole latency
"""
import argparse, os, sys
from ooo import *


A = Config(name="A")
MACHINES = {
    "A": A,
    "B": replace(A, name="B", scheduler_capacity=48),
    "C": A.tweak("C", ALU=4),
    "D": A.tweak("D", IMUL=2),
    "E": A.tweak("E", LOAD=3),
    "F": replace(A, name="F", issue_width=8),
}

# Investigation 12: our proposed modification.  Change this to any tweak.
PROPOSAL = A.tweak("proposed", LOAD=3)
PROPOSAL_DESC = "3 LOAD/AGU units (instead of 2)"


def go(text, cfg):
    res = run(text, cfg)
    check(res)
    return res


def table(text, cfgs, header=True):
    if header:
        print(f"{'config':<12}{'cycles':>7}{'IPC':>6}{'occ':>6}{'ready':>6}"
              f"{'issue':>6}{'unsel':>6}  bottleneck")
    for c in cfgs:
        m = metrics(go(text, c))
        print(f"{c.name:<12}{m['cycles']:>7}{m['ipc']:>6.2f}{m['occ']:>6.1f}"
              f"{m['ready']:>6.1f}{m['issue']:>6.2f}{m['unsel']:>6}  {m['bottleneck']}  "
              f"(crit path {m['crit_path']})")


def interesting(res, k=3):
    rows = [r for r in res.trace if len(r.ready) >= 2 and r.unselected]
    if not rows:
        rows = sorted(res.trace, key=lambda r: -len(r.ready))
        return rows[:k]
    return sorted(rows, key=lambda r: -len(r.unselected))[:k]


# ---------------- Investigations ----------------

def inv1(res):
    """Investigation 1: where does parallelism come from?"""
    print("== Investigation 1: dependency chains and independent work ==\n")
    ch = chains(res, k=3)
    for i, c in enumerate(ch, 1):
        print(f"Chain {i} ({len(c)} uops, {sum(res.uops[u].latency for u in c)} cycles):")
        for uid in c:
            u = res.uops[uid]
            print(f"   {u.name:<6} {u.op:<5} ready C{u.ready} issue C{u.issue} "
                  f"complete C{u.complete}  producers: "
                  f"{[res.uops[p].name for p in u.producers.values()] or '-'}")
        print()
    # Independent work while the longest chain stalls
    c0 = ch[0]
    for i in range(len(c0) - 1):
        a, b = c0[i], c0[i + 1]
        ua, ub = res.uops[a], res.uops[b]
        gap = ub.ready - ua.complete
        if gap <= 0:
            continue
        print(f"Chain stall: {ua.name} completes C{ua.complete}, "
              f"{ub.name} not ready until C{ub.ready} (gap {gap} cycle(s))")
        # Which other uops issued during that gap?
        during = [uid for uid in range(len(res.uops))
                  if ua.complete <= res.uops[uid].issue < ub.ready]
        print(f"   Independent uops issued during the gap: "
              f"{[res.uops[u].name for u in during]}")
        break
    print()


def inv4(wl_dep, wl_ind):
    """Investigation 4: dependency-heavy vs independent."""
    print("== Investigation 4: dependency structure ==\n")
    rows = []
    for label, path in (("dep-heavy", wl_dep), ("independent", wl_ind)):
        text = open(path).read()
        res = go(text, A)
        m = metrics(res)
        rows.append((label, m))
        print(f"--- {label} ({path}) ---")
        print(f"  cycles={m['cycles']}  IPC={m['ipc']:.2f}  "
              f"occ={m['occ']:.1f}  ready={m['ready']:.1f}  "
              f"issue/cyc={m['issue']:.2f}  unsel={m['unsel']}")
        print(f"  utilization: " +
              ", ".join(f"{k}={v:.2f}" for k, v in m['util'].items()))
        print(f"  bottleneck: {m['bottleneck']}  (crit path {m['crit_path']})")
        print()
    (la, ma), (lb, mb) = rows
    print("Comparison:")
    print(f"  cycles      {ma['cycles']:>4}  vs  {mb['cycles']:>4}")
    print(f"  occupancy   {ma['occ']:>4.1f}  vs  {mb['occ']:>4.1f}")
    print(f"  ready/cyc   {ma['ready']:>4.1f}  vs  {mb['ready']:>4.1f}")
    print(f"  issue/cyc   {ma['issue']:>4.2f}  vs  {mb['issue']:>4.2f}")
    print(f"  unselected  {ma['unsel']:>4}  vs  {mb['unsel']:>4}")
    print(f"  crit path   {ma['crit_path']:>4}  vs  {mb['crit_path']:>4}")
    print()


def inv7(text):
    """Investigation 7: bottleneck moves."""
    print("== Investigation 7: bottleneck moves ==\n")
    print("Predictions (written before running):")
    print("  A (baseline):    bottleneck = dependency chains, ~38 cycles")
    print("  B (48 sched):    no change if A never fills the scheduler")
    print("  C (4 ALU):       no change if ALU isn't the contested class")
    print("  D (2 IMUL):      no change if IMUL isn't the contested class")
    print("  E (3 LOAD):      should reduce LOAD contention if LOAD dominates")
    print("  F (8-wide issue):no change if issue width was never full\n")
    table(text, MACHINES.values())
    print()
    base = metrics(go(text, MACHINES["A"]))
    print("Actual bottleneck per machine (vs baseline cycles):")
    for name, cfg in MACHINES.items():
        m = metrics(go(text, cfg))
        delta = m['cycles'] - base['cycles']
        print(f"  {name}: {m['bottleneck']:<45} "
              f"cycles {m['cycles']:>3} ({delta:+d})")
    print()


def inv12(text, proposal, desc):
    print(f"== Investigation 12: proposed modification ==\n")
    print(f"Proposal: {desc}\n")
    table(text, [A, proposal])
    print()


def inv13(text, proposal, desc):
    print(f"== Investigation 13: counterexample for '{desc}' ==\n")
    table(text, [A, proposal])
    print("If the proposal shows no improvement, the counterexample works.\n")


def run_all(args):
    text = open(args.wl).read()
    stem = os.path.splitext(os.path.basename(args.wl))[0]
    os.makedirs("traces", exist_ok=True)

    print("### BASE ###\n")
    res = go(text, A)
    write_csv(res, f"traces/{stem}")
    m = metrics(res)
    print(f"{len(res.uops)} uops, {m['cycles']} cycles, IPC {m['ipc']:.2f}, "
          f"critical path {m['crit_path']}")
    print("avg occupancy %.1f  ready %.1f  waiting %.1f  issue/cycle %.2f" %
          (m['occ'], m['ready'], m['waiting'], m['issue']))
    print("utilization:", {k: round(v, 2) for k, v in m['util'].items()})
    print("limits:", dict(m['limits']))
    print()

    print("### INVESTIGATION 1 ###\n")
    inv1(res)

    print("### INVESTIGATION 2 (ready-but-unselected cycles) ###\n")
    for r in interesting(res):
        show_cycle(res, r.t); print()

    print("### INVESTIGATION 3 (resource contention) ###\n")
    d = demand(res)
    print("demand per resource class (uops / units):")
    for r_, n in d.most_common():
        print(f"  {r_:<7}{n:>3} uops / {A.resources[r_]} unit(s) = "
              f"{n / A.resources[r_]:.1f}")
    print("ready-but-unselected by blocking class:", dict(unsel_by_class(res)))
    print()

    print("### INVESTIGATION 4 ###\n")
    inv4("dep_heavy.uops", "independent.uops")

    print("### INVESTIGATION 5 (scheduler capacity) ###\n")
    table(text, [replace(A, name=f"sched_{n}", scheduler_capacity=n)
                 for n in (12, 24, 48)])
    print()

    print("### INVESTIGATION 6 (issue/wakeup width) ###\n")
    table(text, [replace(A, name="3/3", issue_width=3, wakeup_bandwidth=3),
                 replace(A, name="6/6")])
    print()

    print("### INVESTIGATION 7 ###\n")
    inv7(text)

    print("### INVESTIGATION 11 (crime scene) ###\n")
    picks = interesting(res, 1)
    t = picks[0].t if picks else 0
    show_cycle(res, t)
    print()
    r = res.trace[t]
    if r.unselected:
        why(res, r.unselected[0][0])
    print()

    print("### INVESTIGATION 12 ###\n")
    inv12(text, PROPOSAL, PROPOSAL_DESC)

    print("### INVESTIGATION 13 ###\n")
    inv13(open("counterexample.uops").read(), PROPOSAL, PROPOSAL_DESC)

    print("Done. CSVs in traces/.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--wl", default="workload.uops")
    ap.add_argument("--cycle", type=int)
    ap.add_argument("--uop")
    ap.add_argument("--unpipelined", action="store_true")
    a = ap.parse_args()

    global A, MACHINES, PROPOSAL
    if a.unpipelined:
        A = replace(A, pipelined=False)
        MACHINES = {k: replace(v, pipelined=False) for k, v in MACHINES.items()}
        PROPOSAL = replace(PROPOSAL, pipelined=False)

    text = open(a.wl).read()
    stem = os.path.splitext(os.path.basename(a.wl))[0]

    if a.cmd == "base":
        res = go(text, A)
        os.makedirs("traces", exist_ok=True)
        write_csv(res, f"traces/{stem}")
        m = metrics(res)
        print(f"{len(res.uops)} uops, {m['cycles']} cycles, IPC {m['ipc']:.2f}, "
              f"critical path {m['crit_path']}")
        print("avg occupancy %.1f  ready %.1f  waiting %.1f  issue/cycle %.2f" %
              (m['occ'], m['ready'], m['waiting'], m['issue']))
        print("utilization:", {k: round(v, 2) for k, v in m['util'].items()})
        print("limits:", dict(m['limits']))
        print(f"traces written to traces/{stem}_cycle.csv and _uops.csv")

    elif a.cmd == "ready":
        res = go(text, A)
        for r in interesting(res):
            show_cycle(res, r.t); print()

    elif a.cmd == "contention":
        res = go(text, A)
        d = demand(res)
        print("demand per resource class (uops / units):")
        for r_, n in d.most_common():
            print(f"  {r_:<7}{n:>3} uops / {A.resources[r_]} unit(s) = "
                  f"{n / A.resources[r_]:.1f}")
        print("ready-but-unselected by blocking class:", dict(unsel_by_class(res)))

    elif a.cmd == "sched":
        table(text, [replace(A, name=f"sched_{n}", scheduler_capacity=n)
                     for n in (12, 24, 48)])

    elif a.cmd == "width":
        table(text, [replace(A, name="3/3", issue_width=3, wakeup_bandwidth=3),
                     replace(A, name="6/6")])

    elif a.cmd == "machines":
        table(text, MACHINES.values())

    elif a.cmd == "crime":
        res = go(text, A)
        t = a.cycle if a.cycle is not None else interesting(res, 1)[0].t
        show_cycle(res, t)
        print()
        r = res.trace[t]
        if a.uop:
            uid = next(i for i, u in enumerate(res.uops) if u.name == a.uop)
        elif r.unselected:
            uid = r.unselected[0][0]
        else:
            return print("\nNothing was left unselected this cycle; try --unpipelined.")
        why(res, uid)

    elif a.cmd == "inv1":
        inv1(go(text, A))
    elif a.cmd == "inv4":
        inv4("dep_heavy.uops", "independent.uops")
    elif a.cmd == "inv7":
        inv7(text)
    elif a.cmd == "inv12":
        inv12(text, PROPOSAL, PROPOSAL_DESC)
    elif a.cmd == "inv13":
        inv13(open("counterexample.uops").read(), PROPOSAL, PROPOSAL_DESC)
    elif a.cmd == "all":
        run_all(a)
    else:
        ap.error("unknown command")

if __name__ == "__main__":
    main()