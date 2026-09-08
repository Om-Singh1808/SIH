from types import SimpleNamespace

import numpy as np
from retailsense_contracts.interfaces import Frame

from senseedge.preview import PreviewStreamer


def test_preview_redacts_before_annotation_with_no_tracks(monkeypatch):
    image = np.full((32, 32, 3), 80, dtype=np.uint8)
    frame = Frame(0, "cam", image, 1)
    calls = []

    def redactor(source, tracks, factor):
        assert source is image and tracks == []
        calls.append("redact")
        out = source.copy()
        out[4:12, 4:12] = 0
        return out

    def annotator(source, tracks, view, blur_people):
        assert not source[4:12, 4:12].any()
        assert blur_people is False
        calls.append("annotate")
        return source

    monkeypatch.setattr("senseedge.preview.resolve", lambda key: redactor)
    state = SimpleNamespace(
        latest=SimpleNamespace(get=lambda camera_id: (frame, [])),
        cfg=SimpleNamespace(
            privacy=SimpleNamespace(preview_blur_people=True),
            camera=lambda camera_id: SimpleNamespace(preview_blur_people=True),
        ),
        wiring=SimpleNamespace(annotator=annotator),
        cfg_view=lambda camera_id: {},
    )
    preview = PreviewStreamer(state)
    result = preview.still("cam")
    assert calls == ["redact", "annotate"]
    assert (result[16:] == 80).all()
    assert (image == 80).all()
    calls.clear()
    assert not preview.still("cam", annotate=False)[4:12, 4:12].any()
    assert calls == ["redact"]


def test_preview_routes_use_streamer_and_keep_privacy_enabled(monkeypatch):
    from fastapi.testclient import TestClient
    from retailsense_contracts.testing import sample_store_config

    from senseedge.app import create_app
    from senseedge.preview import BOUNDARY

    calls = []

    class Preview:
        def still(self, camera_id, *, annotate):
            calls.append(("still", camera_id, annotate))
            return np.zeros((16, 16, 3), dtype=np.uint8)

        async def mjpeg(self, camera_id):
            calls.append(("stream", camera_id))
            yield f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n\r\ntest\r\n".encode()

    cfg = sample_store_config()
    state = SimpleNamespace(cfg=cfg, preview=Preview())
    monkeypatch.setattr("senseedge.app.Wiring.from_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("senseedge.app.EdgeState", lambda *args: state)
    with TestClient(create_app(cfg, start_background=False)) as client:
        camera_id = cfg.cameras[0].camera_id
        response = client.get(f"/preview/{camera_id}.jpg?annotate=0&blur=false")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        assert calls.pop() == ("still", camera_id, False)
        for suffix in (".mjpg", ""):
            response = client.get(f"/preview/{camera_id}{suffix}?blur=false")
            assert response.status_code == 200
            assert f"boundary={BOUNDARY}" in response.headers["content-type"]
            assert response.content.startswith(f"--{BOUNDARY}".encode())
            assert calls.pop() == ("stream", camera_id)
        for suffix in (".jpg", ".mjpg", ""):
            assert client.get(f"/preview/unknown{suffix}").status_code == 404
