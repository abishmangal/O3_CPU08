import sys, os, importlib.util

print("Python:", sys.version.split()[0])
print("CWD:   ", os.getcwd())
print("Files in CWD:", sorted(f for f in os.listdir(".") if f.endswith((".py", ".uops", ".txt"))))
print()

for f in ["ooo.py", "run.py", "workload.uops", "dep_heavy.uops",
          "independent.uops", "counterexample.uops"]:
    if os.path.exists(f):
        size = os.path.getsize(f)
        # Peek at the first non-comment line to confirm content
        with open(f) as fh:
            head = "".join(fh.readlines()[:3]).strip().replace("\n", " | ")
        print(f"  OK   {f:<22} {size:>7} bytes   head: {head[:80]}")
    else:
        print(f"  MISS {f}")

print()
# Try importing ooo
try:
    import ooo
    print("import ooo:  OK")
    print("  has Config:",       hasattr(ooo, "Config"))
    print("  has run:",          hasattr(ooo, "run"))
    print("  has metrics:",      hasattr(ooo, "metrics"))
    print("  has chains:",       hasattr(ooo, "chains"))        # NEW
    print("  has unsel_by_class:", hasattr(ooo, "unsel_by_class"))  # NEW
except Exception as e:
    print("import ooo:  FAILED ->", repr(e))

# Try importing run
try:
    import run
    print("import run:  OK")
    print("  has PROPOSAL:", hasattr(run, "PROPOSAL"))
    print("  has MACHINES:", hasattr(run, "MACHINES"))
    print("  has inv1:",     hasattr(run, "inv1"))
    print("  has inv4:",     hasattr(run, "inv4"))
    print("  has inv13:",    hasattr(run, "inv13"))
    print("  has run_all:",  hasattr(run, "run_all"))
except Exception as e:
    print("import run:  FAILED ->", repr(e))