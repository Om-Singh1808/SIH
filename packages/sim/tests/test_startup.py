import numpy as np
from retailsense_contracts.registry import resolve
from retailsense_contracts.testing import sample_store_config


def test_synthetic_camera_starts_and_renders_without_fallback():
    cfg = sample_store_config()
    source_cls = resolve("frame_source.synthetic", allow_fake=False)
    source = source_cls(camera=cfg.synthetic_camera, cfg=cfg, pace=False)
    source.open()
    try:
        frame = source.read()
        assert frame.camera_id == cfg.synthetic_camera.camera_id
        assert frame.image.shape == (cfg.synthetic_camera.height, cfg.synthetic_camera.width, 3)
        assert frame.image.dtype == np.uint8
        assert frame.image.std() > 10
    finally:
        source.close()
