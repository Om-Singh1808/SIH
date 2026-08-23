# Thin wrappers over `python -m retailsense <cmd>` (GNU make is NOT installed on the stage
# laptop - every target works without make: just run the python command shown).
PY ?= python

.PHONY: help setup demo smoke test lint types video models up down ci

help:
	@echo "targets: setup demo smoke test lint types video models up down ci"

setup:        ## editable-install every package + npm install
	$(PY) -m retailsense setup

demo:         ## boot cloud + tally mock + edge + chain sims + board
	$(PY) -m retailsense demo --open

smoke:        ## CI gate: boot everything headless, verify, exit
	$(PY) -m retailsense demo --smoke --no-board

test:         ## pytest (all packages + integration) then vitest
	$(PY) -m retailsense test -m "not slow and not gpu"

lint:
	$(PY) -m retailsense lint

types:        ## regenerate packages/contracts/ts/types.gen.ts
	$(PY) -m retailsense types

video:        ## render var/demo_store.mp4
	$(PY) -m retailsense video

models:       ## fetch YOLO11n ONNX weights (internet once)
	$(PY) -m retailsense fetch-models

up:           ## docker compose (add `--profile broker --profile pg` for MQTT/Timescale)
	docker compose up --build -d

down:
	docker compose down -v

ci: lint test smoke
