# Edge AI Retail CV Stack - Research Findings (2025-2026)

Primary sources: Ultralytics docs, GitHub READMEs (ByteTrack, BoT-SORT, OC-SORT, BoxMOT, supervision, Frigate), NVIDIA DeepStream docs, Intel OpenVINO reference kits, Hailo model zoo, Raspberry Pi docs.

## Detectors (COCO, imgsz 640)
- YOLOv8n: 37.3 mAP50-95, 80.4 ms CPU ONNX, 0.99 ms A100 TRT, 3.2M params, 8.7 GFLOPs.
- YOLO11n: 39.5 mAP, 56.1 ms CPU ONNX, 1.5 ms T4 TRT10, 2.6M params, 6.5 GFLOPs. YOLO11s: 47.0 mAP, 90 ms CPU, 2.5 ms T4.
- YOLO26n (Jan 2026, NMS-free end-to-end): 40.9 mAP, 38.9 ms CPU ONNX (~43% faster CPU than YOLO11n), 1.7 ms T4, 2.4M params, 5.4 GFLOPs. Ultralytics solutions now default to yolo26n.pt.
- RT-DETR-L: 53.0 mAP @ 114 FPS T4 TRT — GPU-class edge only, no CPU/Pi story.
- YOLO-NAS (INT8-friendly, ~0.5 mAP INT8 drop): S 47.5 mAP / 3.21 ms T4. Frigate uses YOLO-NAS-s on RK3588 NPU at ~25 ms.
- Intel person-detection-retail-0013 (OpenVINO OMZ): 320x544 input, 2.30 GFLOPs, 0.72M params, AP 88.6% on retail set, handles 50% occlusion — classic CPU retail person detector.

## Trackers
- ByteTrack (ECCV 2022): associates low-score boxes; MOT17 80.3 MOTA / 77.3 IDF1 / 63.1 HOTA @ 29.6 FPS V100; NO appearance model -> no ReID -> privacy-friendly, ideal for static retail cams.
- BoT-SORT: camera-motion compensation + refined Kalman; 80.6 MOTA / 79.5 IDF1. Use `gmc_method: none` for static cameras.
- OC-SORT (CVPR 2023): observation-centric; HOTA 63.2; ~700 FPS association-only on CPU with precomputed detections.
- BoxMOT library: OccluBoost, BoT-SORT, BoostTrack, StrongSORT, DeepOCSORT, ByteTrack, HybridSORT, OC-SORT (AGPL-3.0).
- Ultralytics `model.track()` ships bytetrack.yaml (lightest), botsort.yaml, ocsort, deepocsort, fasttrack, tracktrack. Docs recommend ByteTrack/BoT-SORT with gmc none for static cameras.

## Analytics primitives
- Ultralytics Solutions: ObjectCounter (2-point region = line IN/OUT; 3+ = polygon), Heatmap (circular intensity accumulation per tracked bbox, COLORMAP_DEEPGREEN default), QueueManager (count tracked objects inside polygon), TrackZone, RegionCounter, ObjectBlurrer.
- Roboflow supervision (MIT, 49.7k stars): `sv.LineZone(start, end, triggering_anchors, minimum_crossing_threshold)` -> crossed_in/crossed_out; `sv.PolygonZone`; `sv.ByteTrack`; annotators HeatMapAnnotator, TraceAnnotator, BlurAnnotator, PixelateAnnotator. LineZone REQUIRES tracker_id.
- supervision `examples/time_in_zone`: canonical dwell-time reference — FPS-based timer for files, clock-based for live; anchor BOTTOM_CENTER; ByteTrack IoU 0.4, min consecutive frames 2, lost-track buffer 60, activation 0.3.
- NVIDIA DeepStream `Gst-nvdsanalytics`: ROI filtering, overcrowding, direction detection, line crossing (modes strict/balanced/loose); outputs objLCCumCnt, objLCCurrCnt, objInROIcnt; requires tracker IDs. deepstream-occupancy-analytics publishes tripwire counts to Kafka.
- Intel OpenVINO ref kits: `intelligent_queue_management` (YOLOv8m -> IR FP16/INT8, zones.json polygons, --customers_limit alert), `automated_self_checkout`.
- Frigate NVR: motion-gated detection, polygon zones evaluated on bottom-center with `inertia` (1-3 frames) and `loitering_time`, MQTT `events` topic with entered_zones; backends Coral, Hailo-8/8L, OpenVINO, TensorRT, RKNN.

## Hardware benchmarks
- Raspberry Pi 5 CPU (Ultralytics, YOLO26n @640 FP32): ONNX 126 ms, OpenVINO 104.6 ms, NCNN 67 ms (~15 FPS); YOLO11n ONNX 147 ms. Independent (LearnOpenCV, YOLO11n): OpenVINO 12.4 FPS, ONNX 6.4 FPS; 25+ FPS at 240x240. Practical: 6-15 FPS nano @640, 20-30 FPS @320.
- Pi 5 + AI HAT+ (Hailo-8L 13 TOPS): yolov8n 202 FPS, yolov11n 157 FPS, yolov8s 110 FPS (batch 1, 640). hailo-rpi5-examples GStreamer pipeline exposes per-person track IDs.
- Jetson Orin Nano Super (TensorRT, YOLO26n @640): FP32 7.53 ms, FP16 4.57 ms (~219 FPS), INT8 3.80 ms; 67 TOPS, 7-25 W, $249.
- Intel x86 + OpenVINO (YOLO26n @640): Core Ultra 7 258V 3.33 ms, Core Ultra 7 155H 9.13 ms; N100 mini-PC iGPU YOLO11n 21 FPS (2-3x over CPU).
- Frigate inference reference: Intel iGPU OpenVINO MobileNetV2 15-35 ms; Hailo-8L YOLOv6n 7-11 ms; RTX 3070 YOLOv9-t 320 6 ms; Jetson 20-40 ms; RK3588 ~20 ms.
- Datacenter: YOLO11n 1.5 ms T4 (~650 FPS). Any desktop RTX runs 10-30 nano streams; tracker association is sub-ms on CPU.

## Privacy techniques
1. Appearance-free tracking (ByteTrack / OC-SORT / BoT-SORT with_reid=False): only ephemeral integer track IDs; no biometric embedding ever computed. Disable ReID backbones (StrongSORT, DeepOCSORT, OMZ reid-0277).
2. On-device anonymization before storage/egress: Ultralytics ObjectBlurrer (`yolo solutions blur classes=[0]`), supervision Blur/Pixelate annotators; pattern camera -> edge -> detector+tracker -> aggregates -> only numbers leave; raw frames discarded from RAM.
3. Regulatory framing (EDPB Guidelines 3/2019; India DPDP Act 2023): facial recognition = biometric (explicit consent); statistical counting with no identifiable footage retained = legitimate interest with data minimisation, ~72 h retention ceiling, signage, privacy-by-design.

## Recommended tiers
- (a) Pi 5 bare CPU: YOLO26n/YOLO11n NCNN/OpenVINO @320-640 + ByteTrack + LineZone/PolygonZone: 10-15 FPS, 1 camera.
- (b) Pi 5 + Hailo-8L: 100-200 FPS model throughput, 2-4 cameras.
- (c) Intel N100/NUC + OpenVINO iGPU: YOLO26n 3-10 ms, 4-8 cameras.
- (d) Jetson Orin Nano Super + TensorRT FP16/INT8 (3.8-4.6 ms) or DeepStream nvdsanalytics: 8-16 cameras.
- (e) Any RTX GPU + TensorRT: 16+ streams.
All tiers share one analytics layer: tracker IDs -> line crossing -> polygon occupancy -> time-in-zone dwell -> heatmap grid.

## Sources
- https://docs.ultralytics.com/models/yolo11/ ; /models/yolo26/ ; /models/rtdetr/ ; /models/yolo-nas/ ; /modes/track/ ; /solutions/ ; /guides/object-counting/ ; /guides/heatmaps/ ; /guides/queue-management/ ; /guides/trackzone/ ; /guides/object-blurring/ ; /guides/raspberry-pi/ ; /guides/nvidia-jetson/ ; /integrations/openvino/
- https://learnopencv.com/yolo11-on-raspberry-pi/
- https://www.digikey.com/en/maker/tutorials/2025/yolo11-speed-boosted-by-7x-with-openvino-on-lattepanda-mu
- https://github.com/roboflow/supervision ; https://supervision.roboflow.com/latest/detection/tools/line_zone/ ; https://github.com/roboflow/supervision/tree/develop/examples/time_in_zone ; https://blog.roboflow.com/dwell-time-and-zone-analytics/
- https://github.com/ifzhang/ByteTrack ; https://github.com/NirAharon/BoT-SORT ; https://github.com/noahcao/OC_SORT ; https://github.com/mikel-brostrom/boxmot
- https://github.com/NVIDIA-AI-IOT/deepstream-occupancy-analytics ; https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvdsanalytics.html
- https://github.com/openvinotoolkit/openvino_build_deploy/tree/master/ai_ref_kits/intelligent_queue_management ; https://github.com/openvinotoolkit/open_model_zoo/blob/master/models/intel/person-detection-retail-0013/README.md
- https://github.com/blakeblackshear/frigate ; https://docs.frigate.video/configuration/zones ; https://docs.frigate.video/frigate/hardware
- https://www.raspberrypi.com/documentation/computers/ai.html ; https://github.com/hailo-ai/hailo-rpi5-examples ; https://github.com/hailo-ai/hailo_model_zoo
- https://www.edpb.europa.eu/our-work-tools/our-documents/guidelines/guidelines-32019-processing-personal-data-through-video_en
