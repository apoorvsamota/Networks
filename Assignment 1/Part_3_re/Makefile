PY ?= python3

server:      ; $(PY) server.py
run-fcfs:    ; $(PY) runner_once.py
plot:        ; $(PY) runner.py
clean:       ; rm -f time_results.csv time_plot.png
stop-server: ; pkill -f 'python3 server.py' || true
