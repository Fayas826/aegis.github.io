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
	tests/test_browser_security_policy.py \
	tests/test_booking_portal_policy.py \
	tests/test_web_truthful_runtime.py \
	tests/test_neural_extensions_integration.py \
	tests/test_verified_desktop_task.py \
	tests/test_autonomy_capability_contract.py \
	tests/test_universal_desktop_layer.py \
	tests/test_universal_gui_worker.py \
	tests/test_universal_runtime_safety.py \
	tests/test_circuit_breaker.py \
	tests/test_local_desktop_provider.py \
	tests/test_local_desktop_worker.py \
	tests/test_storage_task_lookup.py \
	tests/test_verification_planner.py \
	tests/test_multimodal_runtime.py \
	tests/test_gemini_computer_use_agent.py

.PHONY: test test-python test-node build

test: test-python test-node build

test-python:
	$(PYTEST) $(PYTHON_OFFLINE_TESTS) -q

test-node:
	npm test -- --runInBand

build:
	npm run build
