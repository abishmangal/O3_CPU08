PY=python

all:            base inv1 inv4 inv7 crime inv12 inv13
base:           ; $(PY) run.py base
ready:          ; $(PY) run.py ready
contention:     ; $(PY) run.py contention
sched:          ; $(PY) run.py sched
width:          ; $(PY) run.py width
machines:       ; $(PY) run.py machines
crime:          ; $(PY) run.py crime
inv1:           ; $(PY) run.py inv1
inv4:           ; $(PY) run.py inv4
inv7:           ; $(PY) run.py inv7
inv12:          ; $(PY) run.py inv12
inv13:          ; $(PY) run.py inv13
everything:     ; $(PY) run.py all
unpipe-base:    ; $(PY) run.py base --unpipelined
unpipe-all:     ; $(PY) run.py all --unpipelined
clean:          ; rm -rf traces/*.csv