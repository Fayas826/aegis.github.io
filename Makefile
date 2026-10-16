PYTHON ?= python3
PYTEST := $(PYTHON) -m pytest

PYTHON_OFFLINE_TESTS := \
	tests/smoke/test_phase_r_voice.py \
	tests/smoke/test_phase_o_computer_use.py \
	tests/smoke/test_phase1_hybrid_brain.py \
	tests/test_phase4_5_vision_integrity.py \
	tests/test_screen_endpoint_truthfulness.py \
	tests/test_tts_style_controls.py \
	tests/smoke/test_phase_s_realtime.py \
	tests/test_shared_task_junction.py \
	tests/test_browser_security_policy.py

.PHONY: test test-python test-node build

test: test-python test-node build

test-python:
	$(PYTEST) $(PYTHON_OFFLINE_TESTS) -q

test-node:
	npm test -- --runInBand

build:
	npm run build
