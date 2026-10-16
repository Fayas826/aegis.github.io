from core.execution.universal_desktop_layer import DesktopActionLayer
from core.perception.universal_desktop_layer import (
    BLOCKED,
    SUCCESS,
    DesktopPerception,
    DesktopObservation,
    build_manifest,
    denormalize_coordinates,
    fuse_elements,
    normalize_coordinates,
)


def _capture():
    return {
        "width": 100,
        "height": 80,
        "image_b64": "fixture",
        "elements": [{"id": "button", "text": "Open", "bbox": [10, 10, 30, 30], "clickable": True}],
    }


def test_normalized_coordinates_round_trip_and_reject_out_of_range():
    normalized = normalize_coordinates(99, 79, 100, 80)
    assert normalized == (1.0, 1.0)
    assert denormalize_coordinates(*normalized, 100, 80) == (99, 79)
    try:
        denormalize_coordinates(1.01, 0.5, 100, 80)
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range normalized coordinate was accepted")


def test_uia_elements_are_fused_and_manifest_numbers_are_one_based():
    elements = fuse_elements(
        [{"id": "visual", "text": "", "bbox": [10, 10, 30, 30]}],
        [{"id": "uia", "text": "Open", "role": "Button", "bbox": [11, 11, 29, 29], "source": "uia"}],
    )
    manifest = build_manifest(elements, 100, 80, "fixture-manifest")
    assert manifest.manifest_id == "fixture-manifest"
    assert manifest.marks[0].number == 1
    assert manifest.marks[0].label == "Open"


def test_actions_capture_fresh_before_and_after_and_bound_invalid_click():
    perception = DesktopPerception(capture=_capture, uia_provider=lambda: [])
    calls = []

    class Backend:
        name = "fixture"

        def click(self, x, y):
            calls.append((x, y))

    layer = DesktopActionLayer(perception=perception, backend=Backend())
    result = layer.click_element(1)
    assert result.status == SUCCESS
    assert result.before and result.after
    assert calls == [(20, 20)]

    blocked = layer.click_normalized(1.2, 0.5)
    assert blocked.status == BLOCKED
    assert not blocked.success


def test_unavailable_capture_is_truthful():
    perception = DesktopPerception(capture=lambda: (_ for _ in ()).throw(RuntimeError("no display")))
    result = DesktopActionLayer(perception=perception, backend=object()).scroll(1)
    assert result.status == "UNAVAILABLE"
    assert result.before and "no display" in (result.before.error or "")
