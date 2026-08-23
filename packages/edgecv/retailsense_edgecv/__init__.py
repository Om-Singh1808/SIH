"""RetailSense edge perception (``retailsense_edgecv``).

Everything that turns pixels into tracks lives here:

* :mod:`source`             - frame sources (file / RTSP / webcam / synthetic) and ``open_source()``
* :mod:`detector_synthetic` - weight-free HSV colour-blob person detector for the synthetic store
* :mod:`detector_onnx`      - YOLOv8/11-style ONNX person detector (CPU or CUDA execution provider)
* :mod:`detector_ultralytics` - optional fallback that lazy-imports ``ultralytics``
* :mod:`kalman` / :mod:`tracker` - ByteTrack-lite multi-object tracker (Kalman + Hungarian + centroid gate)
* :mod:`homography`         - image -> floorplan ``PointMapper``
* :mod:`annotate`           - privacy-preserving preview overlay (people pixelated, never written to disk)
* :mod:`pipeline`           - ``CvPipeline`` thread loop producing ``FrameResult``
* :mod:`models`             - ``ModelManager`` (manifest load / sha verify / OTA compare)

Other packages never import these modules directly; they reach them through
``retailsense_contracts.registry.resolve("tracker")`` etc.  Heavy dependencies
(onnxruntime, ultralytics) are imported lazily so the package imports cleanly
on a bare edge box running only the synthetic demo.
"""

VERSION = "1.0.0"

__all__ = ["VERSION"]
