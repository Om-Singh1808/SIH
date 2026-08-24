/* eslint-disable */
// GENERATED FILE - do not edit. engine=npx contracts=1.0.0 schema=119c4cc7368a
// Regenerate: python tools/gen_ts_types.py   (source: packages/contracts/retailsense_contracts/*.py)

export const CONTRACTS_VERSION = "1.0.0";

/**
 * What the shopkeeper did. On WhatsApp digit *i* maps to ``Alert.actions[i-1]``.
 */
export type AckAction =
  "restocked" | "order" | "false_positive" | "opened_counter" | "ignore" | "checked" | "investigate";
export type AckBy = "whatsapp" | "whatsapp_sim" | "board" | "auto" | "telegram";
export type Origin = "edge" | "cloud";
export type AlertKind =
  | "shelf_gap"
  | "queue_long"
  | "queue_forecast"
  | "camera_down"
  | "sync_backlog"
  | "device_offline"
  | "shrink_suspect"
  | "footfall_spike";
export type Severity = "info" | "warn" | "high" | "critical";
export type AlertStatus = "open" | "acked" | "resolved";
export type AlertDetails =
  | StockoutAlert
  | QueueAlertDetails
  | CameraAlertDetails
  | SyncAlertDetails
  | DeviceAlertDetails
  | ShrinkAlertDetails
  | FootfallAlertDetails;
/**
 * Which point of a track's bbox represents the person on the floor.
 */
export type Anchor = "bottom_center" | "center";
export type DetectorKind = "auto" | "synthetic" | "onnx" | "ultralytics" | "fake";
export type Lang = "hi" | "en";
export type UplinkMode = "http" | "mqtt" | "none";
export type LinkState = "up" | "down";
/**
 * Line-crossing direction; see geometry.side_of_line for the normative rule.
 */
export type Direction = "in" | "out";
export type EventType =
  | "footfall.crossing"
  | "zone.occupancy"
  | "dwell.sample"
  | "heatmap.tiles"
  | "queue.snapshot"
  | "queue.forecast"
  | "shelf.scan"
  | "shelf.state"
  | "alert.raised"
  | "alert.acked"
  | "alert.resolved"
  | "device.heartbeat"
  | "stock.reconciled"
  | "order.requested"
  | "config.applied"
  | "sim.truth";
/**
 * Storage/sync class of an event; drives outbox expiry + eviction (topics.EXPIRY_S).
 */
export type EventClass = "telemetry" | "aggregate" | "alert" | "txn" | "config";
export type Payload =
  | FootfallCrossing
  | ZoneOccupancy
  | DwellSample
  | HeatmapTiles
  | QueueSnapshot
  | QueueForecast
  | ShelfScan
  | ShelfStateChange
  | AlertRaised
  | AlertAcked
  | AlertResolved
  | DeviceHeartbeat
  | StockReconciled
  | OrderRequested
  | ConfigApplied
  | SimTruth;
export type LineKind = "entrance" | "counter" | "custom";
export type ZoneKind = "aisle" | "queue" | "entrance" | "counter" | "store" | "custom";
export type ShelfState = "stocked" | "partial" | "empty" | "unknown";
export type WsKind =
  "hello" | "event" | "alert" | "kpi" | "health" | "sync" | "scenario" | "notification" | "device" | "forecast";

/**
 * Index of every RetailSense contract model (contracts v1.0.0).
 */
export interface RetailSenseContracts {
  AckAction?: AckAction;
  AckBy?: AckBy;
  Alert?: Alert;
  AlertAckRequest?: AlertAckRequest;
  AlertAcked?: AlertAcked;
  AlertDetails?: AlertDetails;
  AlertKind?: AlertKind;
  AlertRaised?: AlertRaised;
  AlertResolved?: AlertResolved;
  AlertStatus?: AlertStatus;
  Anchor?: Anchor;
  CameraAlertDetails?: CameraAlertDetails;
  CameraConfig?: CameraConfig;
  CameraHealth?: CameraHealth;
  ChainRank?: ChainRank;
  ChainRankRow?: ChainRankRow;
  ChaosRequest?: ChaosRequest;
  Command?: Command;
  ConfigApplied?: ConfigApplied;
  Counter?: Counter;
  DailyReport?: DailyReport;
  DailySummary?: DailySummary;
  DeliveryReceipt?: DeliveryReceipt;
  DemoConfig?: DemoConfig;
  DetectorKind?: DetectorKind;
  DeviceAlertDetails?: DeviceAlertDetails;
  DeviceConfig?: DeviceConfig;
  DeviceHeartbeat?: DeviceHeartbeat;
  DeviceStatus?: DeviceStatus;
  Direction?: Direction;
  DwellSample?: DwellSample;
  ErrorResponse?: ErrorResponse;
  Event?: Event;
  EventClass?: EventClass;
  EventType?: EventType;
  FitReport?: FitReport;
  FleetView?: FleetView;
  Floorplan?: Floorplan;
  FootfallAlertDetails?: FootfallAlertDetails;
  FootfallCrossing?: FootfallCrossing;
  FootfallForecast?: FootfallForecast;
  FootfallForecastDay?: FootfallForecastDay;
  HealthStatus?: HealthStatus;
  HeatCell?: HeatCell;
  HeatmapResponse?: HeatmapResponse;
  HeatmapTile?: HeatmapTile;
  HeatmapTiles?: HeatmapTiles;
  HomographyConfig?: HomographyConfig;
  ImpactConfig?: ImpactConfig;
  ImpactInr?: ImpactInr;
  IngestAck?: IngestAck;
  IngestBatch?: IngestBatch;
  IntegrationsConfig?: IntegrationsConfig;
  IntegrationsStatus?: IntegrationsStatus;
  KpiDaily?: KpiDaily;
  KpiRange?: KpiRange;
  KpiToday?: KpiToday;
  Lang?: Lang;
  Line?: Line;
  LineKind?: LineKind;
  LinkRequest?: LinkRequest;
  LinkState?: LinkState;
  ManifestPublishRequest?: ManifestPublishRequest;
  ModelEntry?: ModelEntry;
  ModelIO?: ModelIO;
  ModelManifest?: ModelManifest;
  ModelStatus?: ModelStatus;
  MqttConfig?: MqttConfig;
  Observation?: Observation;
  OndcAck?: OndcAck;
  OndcConfig?: OndcConfig;
  OndcPublishRequest?: OndcPublishRequest;
  OrderRequested?: OrderRequested;
  Origin?: Origin;
  OutboundMessage?: OutboundMessage;
  Payload?: Payload;
  PrivacyConfig?: PrivacyConfig;
  PrivacyManifest?: PrivacyManifest;
  QueueAlertDetails?: QueueAlertDetails;
  QueueForecast?: QueueForecast;
  QueueSnapshot?: QueueSnapshot;
  QueueView?: QueueView;
  ReconcileReport?: ReconcileReport;
  ReconcileRow?: ReconcileRow;
  ReorderSuggestion?: ReorderSuggestion;
  RetentionPolicy?: RetentionPolicy;
  RolloutPolicy?: RolloutPolicy;
  RolloutRequest?: RolloutRequest;
  RulesConfig?: RulesConfig;
  SKU?: SKU;
  ScenarioRequest?: ScenarioRequest;
  ScenarioStatus?: ScenarioStatus;
  Series?: Series;
  SeriesPoint?: SeriesPoint;
  Severity?: Severity;
  ShelfPolygon?: ShelfPolygon;
  ShelfReference?: ShelfReference;
  ShelfScan?: ShelfScan;
  ShelfState?: ShelfState;
  ShelfStateChange?: ShelfStateChange;
  ShelfStateView?: ShelfStateView;
  ShelvesUpdate?: ShelvesUpdate;
  ShrinkAlertDetails?: ShrinkAlertDetails;
  SimTruth?: SimTruth;
  SkuEnrolResponse?: SkuEnrolResponse;
  StockReconciled?: StockReconciled;
  StockoutAlert?: StockoutAlert;
  Store?: Store;
  StoreConfig?: StoreConfig;
  StoreInfo?: StoreInfo;
  SyncAlertDetails?: SyncAlertDetails;
  SyncStatus?: SyncStatus;
  TallyConfig?: TallyConfig;
  UplinkConfig?: UplinkConfig;
  UplinkMode?: UplinkMode;
  WhatsAppConfig?: WhatsAppConfig;
  WhatsAppReply?: WhatsAppReply;
  WsKind?: WsKind;
  WsMessage?: WsMessage;
  Zone?: Zone;
  ZoneKind?: ZoneKind;
  ZoneOccupancy?: ZoneOccupancy;
  ZonesUpdate?: ZonesUpdate;
}
export interface Alert {
  alert_id: string;
  store_id: string;
  device_id: string;
  origin: Origin;
  kind: AlertKind;
  severity: Severity;
  status: AlertStatus & string;
  subject_id: string;
  title_en: string;
  title_hi: string;
  message_en: string;
  message_hi: string;
  details: AlertDetails;
  impact: ImpactInr | null;
  actions: AckAction[];
  raised_ts: number;
  acked_ts: number | null;
  resolved_ts: number | null;
  ack_action: AckAction | null;
  ack_by: AckBy | null;
}
/**
 * Details for kind=shelf_gap.
 */
export interface StockoutAlert {
  shelf_id: string;
  sku_id: string | null;
  sku_name: string;
  gap_minutes: number;
  coverage: number;
  facings: number;
  min_facings: number;
  consecutive_empty_scans: number;
}
/**
 * Details for kind=queue_long / queue_forecast.
 */
export interface QueueAlertDetails {
  counter_id: string;
  counter_name: string;
  count: number;
  est_wait_s: number;
  forecast: number | null;
  horizon_min: number | null;
  threshold: number;
}
export interface CameraAlertDetails {
  camera_id: string;
  status: string;
  last_frame_age_s: number;
}
export interface SyncAlertDetails {
  backlog: number;
  down_since_ts: number;
}
export interface DeviceAlertDetails {
  device_id: string;
  last_seen_ts: number;
}
export interface ShrinkAlertDetails {
  sku_id: string;
  sku_name: string;
  visual_units: number;
  system_units: number;
  delta_units: number;
  delta_inr: number;
}
/**
 * Details for kind=footfall_spike (P1).
 */
export interface FootfallAlertDetails {
  count: number;
  baseline: number;
  factor: number;
  window_min: number;
}
/**
 * Rupee impact with its derivation. Produced only by ``impact.py``.
 */
export interface ImpactInr {
  lost_sales_inr: number;
  lost_margin_inr: number;
  basis: string;
  factor: number;
  source: string;
}
export interface AlertAckRequest {
  action: AckAction;
  by: AckBy & string;
  note: string | null;
}
export interface AlertAcked {
  type: "alert.acked";
  alert_id: string;
  action: AckAction;
  by: AckBy;
  note: string | null;
}
export interface AlertRaised {
  type: "alert.raised";
  alert: Alert;
}
export interface AlertResolved {
  type: "alert.resolved";
  alert_id: string;
  reason: "condition_cleared" | "restocked_observed" | "false_positive" | "superseded" | "timeout" | "device_back";
  final_gap_minutes: number | null;
  impact_final: ImpactInr | null;
  recovered: ImpactInr | null;
}
export interface CameraConfig {
  camera_id: string;
  source: string;
  width: number;
  height: number;
  fps_sample: number;
  detector: DetectorKind & string;
  anchor: Anchor & string;
  shelf_scan_interval_s: number;
  homography: HomographyConfig | null;
  preview_blur_people: boolean;
  loop_file: boolean;
}
/**
 * >= 4 image/floor point pairs; ``None`` on the camera means identity.
 */
export interface HomographyConfig {
  image_points: number[][];
  floor_points: number[][];
}
export interface CameraHealth {
  camera_id: string;
  status: "ok" | "stale" | "black" | "error";
  fps: number;
  last_frame_age_s: number;
  detector: string;
}
export interface ChainRank {
  metric: string;
  date: string;
  rows: ChainRankRow[];
}
export interface ChainRankRow {
  store_id: string;
  name: string;
  value: number;
  rank: number;
  footfall_in: number;
  normalised: number | null;
}
export interface ChaosRequest {
  kind: "freeze" | "drop" | "blackout" | "noise";
  enabled: boolean;
  seconds: number | null;
  p: number | null;
}
/**
 * Cloud -> device instruction, piggybacked on IngestAck (HTTP) or cmd topic (MQTT).
 */
export interface Command {
  command_id: string;
  device_id: string;
  kind: "ack_alert" | "apply_config" | "set_link" | "set_scenario" | "model_update" | "ping";
  payload: {
    [k: string]: unknown;
  };
  created_ts: number;
}
export interface ConfigApplied {
  type: "config.applied";
  config_version: number;
  config_hash: string;
}
export interface Counter {
  counter_id: string;
  name: string;
  queue_zone_id: string;
  counter_line_id: string;
  max_queue: number;
  default_service_s: number;
}
export interface DailyReport {
  store_id: string;
  date: string;
  kpis: KpiDaily;
  top_alerts: Alert[];
  gap_minutes_by_shelf: {
    [k: string]: number;
  };
  queue_by_hour: {
    [k: string]: number;
  };
  forecast_mae: number | null;
  whatsapp_text_hi: string;
  whatsapp_text_en: string;
}
export interface KpiDaily {
  store_id: string;
  date: string;
  footfall_in: number;
  footfall_out: number;
  visual_transactions: number;
  conversion_pct: number | null;
  atv_inr: number | null;
  osa_pct: number;
  gap_minutes_total: number;
  avg_wait_s: number | null;
  max_wait_s: number | null;
  abandoned: number;
  lost_sales_inr: number;
  recovered_inr: number;
  shrink_inr: number;
  alerts_total: number;
}
export interface DailySummary {
  store_id: string;
  date: string;
  lang: Lang;
  text: string;
  kpis: KpiToday;
}
export interface KpiToday {
  store_id: string;
  date: string;
  as_of_ts: number;
  footfall_in: number;
  footfall_out: number;
  occupancy_now: number;
  visual_transactions: number;
  conversion_pct: number | null;
  atv_inr: number | null;
  osa_pct: number;
  gap_minutes_total: number;
  avg_wait_s: number | null;
  max_wait_s: number | null;
  abandoned: number;
  lost_sales_inr: number;
  lost_margin_inr: number;
  recovered_inr: number;
  alerts_open: number;
  alerts_today: number;
  deltas: {
    [k: string]: number | null;
  };
}
export interface DeliveryReceipt {
  message_id: string;
  status: "sent" | "failed";
  detail: string | null;
}
export interface DemoConfig {
  enabled: boolean;
  clock_factor: number;
  default_scenario: string;
  start_time: string;
  seed_history_days: number;
  auto_calibrate_first_scan: boolean;
}
export interface DeviceConfig {
  device_id: string;
  token: string;
  edge_port: number;
  cloud_url: string;
  db_path: string;
  uplink: UplinkConfig;
}
export interface UplinkConfig {
  mode: UplinkMode & string;
  batch_size: number;
  interval_s: number;
  heartbeat_s: number;
  max_outbox_rows: number;
  mqtt: MqttConfig;
}
export interface MqttConfig {
  host: string;
  port: number;
  ws_port: number;
  username: string | null;
  password: string | null;
  session_expiry_s: number;
}
export interface DeviceHeartbeat {
  type: "device.heartbeat";
  uptime_s: number;
  fps: number;
  infer_ms_p50: number;
  infer_ms_p95: number;
  detector: string;
  model_version: string;
  backlog: number;
  link: LinkState;
  cameras: CameraHealth[];
  contracts_version: string;
  clock_factor: number;
  sim_ts: number | null;
  cpu_pct: number | null;
  mem_mb: number | null;
}
export interface DeviceStatus {
  device_id: string;
  store_id: string;
  status: "online" | "offline" | "never";
  last_seen_ts: number | null;
  model_version: string | null;
  assigned_version: string | null;
  version_drift: boolean;
  fps: number | null;
  backlog: number | null;
  link: LinkState | null;
  uptime_s: number | null;
}
/**
 * A finished visit to a zone. Deliberately carries no track id (privacy).
 */
export interface DwellSample {
  type: "dwell.sample";
  zone_id: string;
  dwell_s: number;
  entered_ts: number;
  exited_ts: number;
}
/**
 * Uniform error body (FastAPI ``detail`` compatible).
 */
export interface ErrorResponse {
  detail: string;
  code: string | null;
}
/**
 * The wire/storage envelope. Immutable once stamped.
 */
export interface Event {
  event_id: string;
  store_id: string;
  device_id: string;
  camera_id: string | null;
  ts: number;
  hlc: string;
  seq: number;
  type: EventType;
  cls: EventClass;
  version: number;
  payload: Payload;
  created_ts: number;
}
export interface FootfallCrossing {
  type: "footfall.crossing";
  line_id: string;
  line_kind: LineKind;
  direction: Direction;
  count: number;
}
export interface ZoneOccupancy {
  type: "zone.occupancy";
  zone_id: string;
  zone_kind: ZoneKind;
  count: number;
  window_s: number;
}
/**
 * Deltas since the last flush, in floorplan cell coordinates.
 */
export interface HeatmapTiles {
  type: "heatmap.tiles";
  cell_px: number;
  width_cells: number;
  height_cells: number;
  tiles: HeatmapTile[];
}
export interface HeatmapTile {
  cell_x: number;
  cell_y: number;
  hour_bucket: number;
  dwell_s: number;
  visits: number;
}
export interface QueueSnapshot {
  type: "queue.snapshot";
  counter_id: string;
  zone_id: string;
  count: number;
  avg_dwell_s: number;
  max_dwell_s: number;
  arrival_rate_pm: number;
  service_rate_pm: number;
  est_wait_s: number;
  method: "little_service" | "observed_wait" | "default_service";
  served_window: number;
  abandoned_window: number;
  window_s: number;
  served_total: number;
  abandoned_total: number;
  long_since_ts: number | null;
}
export interface QueueForecast {
  type: "queue.forecast";
  counter_id: string;
  made_ts: number;
  horizons: {
    [k: string]: number;
  };
  model: "edge_trend" | "cloud_gbm";
  mae_recent: number | null;
}
export interface ShelfScan {
  type: "shelf.scan";
  shelf_id: string;
  sku_id: string | null;
  coverage: number;
  facings: number;
  capacity_facings: number;
  state_raw: ShelfState;
  occluded: boolean;
  method: string;
  thumb_b64: string | null;
}
export interface ShelfStateChange {
  type: "shelf.state";
  shelf_id: string;
  sku_id: string | null;
  from_state: ShelfState;
  to_state: ShelfState;
  gap_started_ts: number | null;
  gap_minutes: number | null;
  consecutive_empty_scans: number;
  impact: ImpactInr | null;
}
export interface StockReconciled {
  type: "stock.reconciled";
  sku_id: string;
  shelf_id: string | null;
  visual_units: number;
  system_units: number;
  delta_units: number;
  delta_inr: number;
  source: "tally" | "zoho" | "manual" | "mock";
}
export interface OrderRequested {
  type: "order.requested";
  sku_id: string;
  qty: number;
  channel: AckBy;
  alert_id: string | null;
  est_cost_inr: number | null;
}
/**
 * Ground truth emitted by the synthetic store so tests can assert accuracy.
 */
export interface SimTruth {
  type: "sim.truth";
  in_store: number;
  queue_counts: {
    [k: string]: number;
  };
  shelf_units: {
    [k: string]: number;
  };
  shelf_facings: {
    [k: string]: number;
  };
  served_total: number;
  abandoned_total: number;
  footfall_in_total: number;
  scenario: string;
}
export interface FitReport {
  model: string;
  target: string;
  trained_ts: number;
  n_rows: number;
  mae_holdout: number;
  mae_baseline: number;
  features: string[];
  horizons: number[];
}
export interface FleetView {
  devices: DeviceStatus[];
  online: number;
  offline: number;
  manifest_version: string | null;
}
export interface Floorplan {
  width_px: number;
  height_px: number;
  scale_m_per_px: number;
  image: string | null;
  heat_cell_px: number;
}
export interface FootfallForecast {
  store_id: string;
  made_ts: number;
  days: FootfallForecastDay[];
  mae_holdout: number | null;
}
export interface FootfallForecastDay {
  date: string;
  predicted: number;
  lower: number;
  upper: number;
  is_festival: boolean;
  festival_name: string | null;
  days_to_festival: number | null;
}
export interface HealthStatus {
  status: "ok" | "degraded" | "starting";
  store_id: string;
  device_id: string;
  uptime_s: number;
  contracts_version: string;
  detector: string;
  model_version: string;
  cameras: CameraHealth[];
  sync: SyncStatus;
  sim_ts: number | null;
  clock_factor: number;
  fps: number;
  infer_ms_p50: number;
}
export interface SyncStatus {
  link: LinkState;
  uplink: UplinkMode;
  cloud_reachable: boolean;
  backlog: number;
  backlog_by_class: {
    [k: string]: number;
  };
  last_ack_ts: number | null;
  last_ack_seq: number | null;
  replayed_since_restore: number;
  replay_total_at_restore: number;
  seq_ok: boolean;
  down_since_ts: number | null;
}
export interface HeatCell {
  x: number;
  y: number;
  dwell_s: number;
  visits: number;
}
export interface HeatmapResponse {
  camera_id: string | null;
  cell_px: number;
  width_cells: number;
  height_cells: number;
  from_ts: number;
  to_ts: number;
  cells: HeatCell[];
  max_dwell_s: number;
}
export interface ImpactConfig {
  lost_sale_factor: number;
  lost_sale_source: string;
  queue_abandon_factor: number;
  queue_abandon_source: string;
  atv_inr: number;
  baseline_unattended_gap_min: number;
}
export interface IngestAck {
  batch_id: string;
  accepted: number;
  duplicates: number;
  rejected: {
    [k: string]: string;
  }[];
  last_seq: number | null;
  seq_ok: boolean;
  seq_gaps: number[];
  commands: Command[];
  server_ts: number;
}
export interface IngestBatch {
  batch_id: string;
  device_id: string;
  store_id: string;
  sent_ts: number;
  cursor: number;
  /**
   * @maxItems 500
   */
  events: Event[];
  backlog: number;
  contracts_version: string;
}
export interface IntegrationsConfig {
  tally: TallyConfig;
  ondc: OndcConfig;
  whatsapp: WhatsAppConfig;
}
export interface TallyConfig {
  enabled: boolean;
  url: string;
  company: string | null;
}
export interface OndcConfig {
  enabled: boolean;
  gateway_url: string;
  bpp_id: string;
  signing: "none" | "ed25519";
}
export interface WhatsAppConfig {
  mode: "simulator" | "cloud_api" | "telegram" | "none";
  to: string | null;
  phone_number_id: string | null;
  token_env: string;
  telegram_chat_id: string | null;
  telegram_token_env: string;
}
export interface IntegrationsStatus {
  tally: {
    [k: string]: unknown;
  };
  ondc: {
    [k: string]: unknown;
  };
  whatsapp: {
    [k: string]: unknown;
  };
}
export interface KpiRange {
  today: KpiToday;
  daily: KpiDaily[];
}
/**
 * Directed line; +1 side (left of start->end, y down) is IN. See geometry.py.
 */
export interface Line {
  line_id: string;
  camera_id: string;
  kind: LineKind;
  start: number[];
  end: number[];
  name: string | null;
}
export interface LinkRequest {
  state: LinkState;
}
export interface ManifestPublishRequest {
  manifest: ModelManifest;
}
export interface ModelManifest {
  manifest_version: number;
  version: string;
  generated_ts: number;
  models: ModelEntry[];
  rollout: RolloutPolicy;
}
export interface ModelEntry {
  model_id: string;
  version: string;
  task: "person_detect" | "shelf_gap" | "sku_embed";
  format: "onnx" | "pt" | "tflite";
  file: string;
  sha256: string;
  size_bytes: number;
  input: ModelIO;
  output_format: "yolov8" | "yolox" | "synthetic" | "none";
  classes: string[];
  source_url: string | null;
  license: string;
  min_runtime: string;
  notes: string;
}
export interface ModelIO {
  shape: number[];
  layout: string;
  normalize: string;
  letterbox: boolean;
}
export interface RolloutPolicy {
  channel: "canary" | "stable";
  canary_pct: number;
  abort_failure_pct: number;
  pinned_devices: {
    [k: string]: string;
  };
}
export interface ModelStatus {
  local: ModelManifest | null;
  remote: ModelManifest | null;
  active_model_id: string;
  active_version: string;
  update_available: boolean;
  assigned_version: string | null;
}
/**
 * What a producer hands to ``EdgeStore.append()``; not yet stamped.
 */
export interface Observation {
  type: EventType;
  ts: number;
  camera_id: string | null;
  payload: Payload;
}
export interface OndcAck {
  ok: boolean;
  message_id: string;
  item_id: string;
  available: boolean;
  ts: number;
  signed: boolean;
}
export interface OndcPublishRequest {
  sku_id: string;
  available: boolean;
  qty: number | null;
}
export interface OutboundMessage {
  message_id: string;
  channel: string;
  to: string;
  text: string;
  buttons: string[];
  alert_id: string | null;
  store_id: string;
  created_ts: number;
  status: "queued" | "sent" | "delivered" | "failed";
  delivered_ts: number | null;
}
export interface PrivacyConfig {
  preview_blur_people: boolean;
  shelf_thumbnails: boolean;
  retention: RetentionPolicy;
  statement: string;
}
/**
 * How long each data class lives before the purge job deletes it.
 */
export interface RetentionPolicy {
  telemetry_hours: number;
  aggregate_days: number;
  thumbnails_days: number;
  heatmap_days: number;
  alerts_days: number;
  sent_outbox_hours: number;
}
/**
 * Machine-readable statement of what RetailSense does and does not collect.
 */
export interface PrivacyManifest {
  face_recognition: boolean;
  raw_video_persisted: boolean;
  track_ids_leave_edge: boolean;
  biometric_templates: boolean;
  shelf_thumbnails: boolean;
  thumbnail_max_px: number;
  thumbnail_scope: string;
  preview_blur_people: boolean;
  data_leaving_edge: string[];
  retention: RetentionPolicy;
  lawful_basis: string;
  statement: string;
}
export interface QueueView {
  counter_id: string;
  name: string;
  snapshot: QueueSnapshot | null;
  forecast: QueueForecast | null;
  open_alert_id: string | null;
}
export interface ReconcileReport {
  store_id: string;
  ts: number;
  source: string;
  rows: ReconcileRow[];
  shrink_inr_total: number;
  alerts_raised: number;
}
export interface ReconcileRow {
  sku_id: string;
  name: string;
  shelf_id: string | null;
  visual_units: number;
  system_units: number;
  delta_units: number;
  delta_inr: number;
  flagged: boolean;
}
export interface ReorderSuggestion {
  sku_id: string;
  name_en: string;
  name_hi: string;
  system_units: number | null;
  visual_units: number | null;
  forecast_units_lead: number;
  safety_stock: number;
  suggest_qty: number;
  est_cost_inr: number;
  reason: string;
}
export interface RolloutRequest {
  model_id: string;
  version: string;
  canary_pct: number;
}
export interface RulesConfig {
  shelf_partial_coverage: number;
  shelf_empty_coverage: number;
  persistence_scans: number;
  max_persistence_scans: number;
  queue_long_count: number;
  queue_long_s: number;
  queue_resolve_s: number;
  queue_forecast_threshold: number;
  queue_forecast_horizon_min: number;
  queue_min_age_s: number;
  queue_window_s: number;
  snapshot_interval_s: number;
  occupancy_interval_s: number;
  heat_flush_s: number;
  camera_down_s: number;
  black_frame_std: number;
  sync_backlog_warn: number;
  sync_backlog_after_s: number;
  shrink_min_units: number;
  shrink_min_inr: number;
  occlusion_skip_overlap: number;
  footfall_spike_factor: number;
}
export interface SKU {
  sku_id: string;
  name_en: string;
  name_hi: string;
  mrp_inr: number;
  margin_pct: number;
  velocity_units_per_hr: number;
  units_per_facing: number;
  lead_time_days: number;
  tally_item_name: string | null;
  ondc_item_id: string | null;
  enrolled_images: number;
}
export interface ScenarioRequest {
  name: string;
  params: {
    [k: string]: unknown;
  };
}
export interface ScenarioStatus {
  active: string;
  since_ts: number;
  params: {
    [k: string]: unknown;
  };
  available: string[];
  clock_factor: number;
  sim_ts: number;
}
export interface Series {
  metric: string;
  bucket_s: number;
  points: SeriesPoint[];
}
export interface SeriesPoint {
  ts: number;
  value: number;
}
export interface ShelfPolygon {
  shelf_id: string;
  camera_id: string;
  name: string;
  polygon: number[][];
  sku_id: string | null;
  capacity_facings: number;
  min_facings: number;
  facing_width_px: number | null;
  reference: ShelfReference | null;
}
/**
 * Calibration snapshot of a full shelf (what 100 % coverage looks like).
 */
export interface ShelfReference {
  shelf_id: string;
  calibrated_ts: number;
  raw_coverage_full: number;
  backing_bgr: number[];
  profile: number[] | null;
  method: string;
}
export interface ShelfStateView {
  shelf_id: string;
  name: string;
  sku_id: string | null;
  sku_name: string;
  state: ShelfState;
  coverage: number;
  facings: number;
  capacity_facings: number;
  min_facings: number;
  consecutive_empty_scans: number;
  persistence_required: number;
  gap_started_ts: number | null;
  gap_minutes: number | null;
  last_scan_ts: number | null;
  occluded: boolean;
  impact_open: ImpactInr | null;
  has_reference: boolean;
}
export interface ShelvesUpdate {
  shelves: ShelfPolygon[];
}
export interface SkuEnrolResponse {
  sku_id: string;
  enrolled: number;
  backend: string;
}
export interface Store {
  store_id: string;
  name: string;
  tier: string;
  lang: Lang;
  tz: string;
  device_ids: string[];
  registered_ts: number;
  config: StoreConfig | null;
}
export interface StoreConfig {
  schema_version: number;
  config_version: number;
  store: StoreInfo;
  device: DeviceConfig;
  floorplan: Floorplan;
  cameras: CameraConfig[];
  zones: Zone[];
  lines: Line[];
  counters: Counter[];
  shelves: ShelfPolygon[];
  skus: SKU[];
  rules: RulesConfig;
  impact: ImpactConfig;
  privacy: PrivacyConfig;
  integrations: IntegrationsConfig;
  demo: DemoConfig;
}
export interface StoreInfo {
  store_id: string;
  name: string;
  lang: Lang & string;
  tz: string;
  tier: "kirana" | "mini" | "chain";
  owner_whatsapp: string;
  /**
   * @minItems 2
   * @maxItems 2
   */
  open_hours: [string, string];
  address: string | null;
}
export interface Zone {
  zone_id: string;
  camera_id: string;
  kind: ZoneKind;
  polygon: number[][];
  name: string | null;
}
export interface WhatsAppReply {
  alert_id: string;
  digit: number;
  from_number: string | null;
}
export interface WsMessage {
  kind: WsKind;
  ts: number;
  store_id: string | null;
  data: {
    [k: string]: unknown;
  };
}
export interface ZonesUpdate {
  zones: Zone[];
  lines: Line[];
  counters: Counter[];
}
