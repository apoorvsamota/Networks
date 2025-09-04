CXX=g++
CXXFLAGS=-O2 -std=gnu++17 -Wall -Wextra
PY=python3

all: build
build: server client

server: server.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

client: client.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

plot: build
	$(PY) runner.py

clean:
	rm -f server client p1_results.csv p1_plot.png
