import { useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type ScenarioId = "baseline" | "queue" | "stockout" | "weekend";
type ViewId = "live" | "weekly" | "privacy";

type Track = {
  id: number;
  x: number;
  y: number;
  width: number;
  height: number;
  confidence: number;
  zone: string;
};

const scenarioConfig: Record<
  ScenarioId,
  {
    label: string;
    eyebrow: string;
    description: string;
    color: string;
    soft: string;
    queueBase: number;
    arrivalRate: number;
    serviceRate: number;
    coverage: number;
    revenueFactor: number;
  }
> = {
  baseline: {
    label: "Normal trading",
    eyebrow: "CALIBRATED BASELINE",
    description: "Balanced footfall, healthy shelves and one active checkout.",
    color: "#14B8A6",
    soft: "#CCFBF1",
    queueBase: 3,
    arrivalRate: 1.08,
    serviceRate: 1.42,
    coverage: 92,
    revenueFactor: 1,
  },
  queue: {
    label: "Queue overload",
    eyebrow: "SERVICE PRESSURE",
    description: "Evening rush pushes arrivals above checkout capacity.",
    color: "#F97316",
    soft: "#FFEDD5",
    queueBase: 8,
    arrivalRate: 2.18,
    serviceRate: 1.31,
    coverage: 87,
    revenueFactor: 1.19,
  },
  stockout: {
    label: "Shelf stockout",
    eyebrow: "AVAILABILITY RISK",
    description: "Beverage shelf coverage collapses across three scans.",
    color: "#F43F5E",
    soft: "#FFE4E6",
    queueBase: 4,
    arrivalRate: 1.32,
    serviceRate: 1.45,
    coverage: 18,
    revenueFactor: 0.91,
  },
  weekend: {
    label: "Weekend surge",
    eyebrow: "HIGH CONVERSION",
    description: "High traffic and strong basket value with two counters.",
    color: "#8B5CF6",
    soft: "#EDE9FE",
    queueBase: 6,
    arrivalRate: 1.84,
    serviceRate: 2.46,
    coverage: 74,
    revenueFactor: 1.36,
  },
};

const weeklyBase = [
  { day: "Mon", revenue: 28400, footfall: 312, conversion: 61, lost: 680 },
  { day: "Tue", revenue: 31200, footfall: 338, conversion: 64, lost: 520 },
  { day: "Wed", revenue: 29800, footfall: 326, conversion: 62, lost: 890 },
  { day: "Thu", revenue: 34600, footfall: 371, conversion: 67, lost: 410 },
  { day: "Fri", revenue: 38900, footfall: 424, conversion: 69, lost: 740 },
  { day: "Sat", revenue: 51200, footfall: 566, conversion: 73, lost: 1180 },
  { day: "Sun", revenue: 47600, footfall: 521, conversion: 71, lost: 960 },
];

const categoryRows = [
  { category: "Packaged foods", sales: 72480, share: 27.7, trend: 8.4, availability: 96 },
  { category: "Dairy & chilled", sales: 58320, share: 22.3, trend: -2.1, availability: 84 },
  { category: "Beverages", sales: 46940, share: 17.9, trend: 12.6, availability: 91 },
  { category: "Household care", sales: 37860, share: 14.5, trend: 4.8, availability: 98 },
  { category: "Personal care", sales: 28300, share: 10.8, trend: 3.2, availability: 94 },
  { category: "Impulse & confectionery", sales: 17600, share: 6.8, trend: 15.1, availability: 89 },
];

const queueSeries = [
  { minute: "−10", actual: 2, forecast: null },
  { minute: "−8", actual: 3, forecast: null },
  { minute: "−6", actual: 3, forecast: null },
  { minute: "−4", actual: 4, forecast: null },
  { minute: "−2", actual: 5, forecast: null },
  { minute: "Now", actual: 6, forecast: 6 },
  { minute: "+5", actual: null, forecast: 7.2 },
  { minute: "+10", actual: null, forecast: 8.1 },
  { minute: "+15", actual: null, forecast: 8.7 },
];

const currency = (value: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(value);

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

function Signal({ color = "#14B8A6", pulse = false }: { color?: string; pulse?: boolean }) {
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {pulse && <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60" style={{ background: color }} />}
      <span className="relative inline-flex h-2.5 w-2.5 rounded-full border border-slate-950" style={{ background: color }} />
    </span>
  );
}

function MiniMetric({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: string }) {
  return (
    <div className="rounded-xl border-2 border-slate-950 bg-white p-3 shadow-[2px_2px_0_#0F172A]">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[10px] font-black uppercase tracking-[0.13em] text-slate-500">{label}</span>
        <span className="h-2 w-2 rounded-full border border-slate-950" style={{ background: tone }} />
      </div>
      <div className="font-mono text-2xl font-black tracking-tight text-slate-950">{value}</div>
      <div className="mt-1 text-[10px] font-semibold text-slate-500">{detail}</div>
    </div>
  );
}

function StatusPill({ children, tone = "#CCFBF1" }: { children: React.ReactNode; tone?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-950 px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.08em]" style={{ background: tone }}>
      {children}
    </span>
  );
}

function VideoOverlay({ tracks, scenario }: { tracks: Track[]; scenario: ScenarioId }) {
  const accent = scenarioConfig[scenario].color;
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1000 567" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <pattern id="privacy-grid" width="6" height="6" patternUnits="userSpaceOnUse">
          <rect width="3" height="3" fill="#0F172A" opacity=".9" />
          <rect x="3" y="3" width="3" height="3" fill="#64748B" opacity=".9" />
        </pattern>
        <linearGradient id="queue-zone" x1="0" x2="1">
          <stop offset="0" stopColor={accent} stopOpacity=".06" />
          <stop offset="1" stopColor={accent} stopOpacity=".25" />
        </linearGradient>
      </defs>

      <polygon points="75,120 480,90 550,430 110,505" fill="#38BDF8" fillOpacity=".06" stroke="#38BDF8" strokeWidth="2" strokeDasharray="9 7" />
      <text x="92" y="143" fill="#E0F2FE" fontSize="12" fontWeight="900" letterSpacing="1.8">AISLE / DWELL ZONE</text>

      <polygon points="585,90 915,82 940,410 540,390" fill="url(#queue-zone)" stroke={accent} strokeWidth="2.5" strokeDasharray="10 7" />
      <text x="604" y="116" fill="#FFFFFF" fontSize="12" fontWeight="900" letterSpacing="1.8">CHECKOUT QUEUE ROI</text>

      <line x1="230" y1="498" x2="760" y2="507" stroke="#FACC15" strokeWidth="3" strokeDasharray="12 8" />
      <rect x="235" y="472" width="142" height="22" rx="4" fill="#0F172A" fillOpacity=".82" stroke="#FACC15" />
      <text x="246" y="487" fill="#FEF08A" fontSize="11" fontWeight="900">ENTRY CROSSING LINE</text>

      {tracks.map((track) => (
        <g key={track.id}>
          <rect
            x={track.x}
            y={track.y}
            width={track.width}
            height={track.height}
            rx="5"
            fill="none"
            stroke={track.zone === "queue" ? accent : "#67E8F9"}
            strokeWidth="3"
          />
          <path d={`M ${track.x} ${track.y + 18} L ${track.x} ${track.y} L ${track.x + 18} ${track.y}`} fill="none" stroke="#FFFFFF" strokeWidth="3" />
          <rect x={track.x} y={track.y - 25} width="108" height="23" rx="4" fill="#020617" fillOpacity=".9" stroke={track.zone === "queue" ? accent : "#67E8F9"} />
          <text x={track.x + 7} y={track.y - 9} fill="#FFFFFF" fontSize="11" fontFamily="monospace" fontWeight="800">
            P-{String(track.id).padStart(3, "0")} · {Math.round(track.confidence * 100)}%
          </text>
          <rect x={track.x + track.width * 0.28} y={track.y + 4} width={track.width * 0.44} height={track.height * 0.2} rx="3" fill="url(#privacy-grid)" stroke="#FFFFFF" strokeWidth="1" />
          <circle cx={track.x + track.width / 2} cy={track.y + track.height + 5} r="3.5" fill={track.zone === "queue" ? accent : "#67E8F9"} stroke="#020617" />
        </g>
      ))}
    </svg>
  );
}

function LiveFloor({
  scenario,
  currentTime,
  tracks,
  queueLength,
  arrivalRate,
  serviceRate,
  waitMinutes,
  shelfCoverage,
}: {
  scenario: ScenarioId;
  currentTime: number;
  tracks: Track[];
  queueLength: number;
  arrivalRate: number;
  serviceRate: number;
  waitMinutes: number;
  shelfCoverage: number;
}) {
  const cfg = scenarioConfig[scenario];
  const utilization = Math.round((arrivalRate / serviceRate) * 100);
  const lostPerHour = Math.round(Math.max(0, waitMinutes - 2.5) * 186 + Math.max(0, 65 - shelfCoverage) * 11);
  const forecast = Math.max(0, queueLength + (arrivalRate - serviceRate) * 7.5);

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.58fr)_minmax(320px,.72fr)]">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <MiniMetric label="People in frame" value={String(tracks.length)} detail="anonymous tracks" tone="#22D3EE" />
          <MiniMetric label="Queue length" value={`${queueLength}`} detail={`${forecast.toFixed(1)} in 15 min`} tone={cfg.color} />
          <MiniMetric label="Wait estimate" value={`${waitMinutes.toFixed(1)}m`} detail="L ÷ service rate" tone={waitMinutes > 4 ? "#F43F5E" : "#14B8A6"} />
          <MiniMetric label="Shelf available" value={`${shelfCoverage}%`} detail="3-scan confidence" tone={shelfCoverage < 35 ? "#F43F5E" : "#A3E635"} />
        </div>

        <section className="overflow-hidden rounded-2xl border-[2.5px] border-slate-950 bg-slate-950 shadow-[5px_5px_0_#0F172A]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-700 bg-slate-900 px-4 py-3 text-white">
            <div className="flex items-center gap-3">
              <div className="flex gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-rose-500" /><span className="h-2.5 w-2.5 rounded-full bg-amber-400" /><span className="h-2.5 w-2.5 rounded-full bg-emerald-400" /></div>
              <div className="font-mono text-[11px] font-bold tracking-wide text-slate-300">CAM-01 · RETAIL FLOOR · PRIVACY PREVIEW</div>
            </div>
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-wider">
              <StatusPill tone="#0F766E"><span className="text-white"><Signal pulse /> Live inference</span></StatusPill>
              <span className="font-mono text-slate-400">{new Date(currentTime * 1000).toISOString().slice(14, 19)} / 01:38</span>
            </div>
          </div>

          <div className="relative aspect-[1000/567] w-full overflow-hidden bg-slate-900">
            <video
              id="vision-demo-video"
              className="h-full w-full object-cover"
              src="/demo/retail-floor-privacy-preview.mp4"
              controls
              muted
              loop
              autoPlay
              playsInline
              preload="metadata"
              aria-label="Anonymized retail floor CCTV demonstration"
            />
            <VideoOverlay tracks={tracks} scenario={scenario} />
            <div className="pointer-events-none absolute left-3 top-3 flex flex-col gap-1.5">
              <span className="w-fit rounded border border-cyan-300 bg-slate-950/85 px-2 py-1 font-mono text-[9px] font-black uppercase tracking-widest text-cyan-200">YOLO11n · ByteTrack-style IoU · 640²</span>
              <span className="w-fit rounded border border-slate-400 bg-slate-950/85 px-2 py-1 font-mono text-[9px] font-bold uppercase tracking-widest text-white">Simulated detections · source preview pixelated</span>
            </div>
            <div className="pointer-events-none absolute bottom-12 right-3 rounded-lg border border-white/50 bg-slate-950/80 p-2.5 text-right font-mono text-[9px] font-bold uppercase tracking-wide text-slate-200 backdrop-blur-sm">
              <div className="text-emerald-300">Faces: not detected</div>
              <div>Raw frames: RAM only</div>
              <div>Cloud payload: aggregates</div>
            </div>
          </div>

          <div className="grid grid-cols-3 divide-x divide-slate-700 bg-slate-900 text-white sm:grid-cols-6">
            {[
              ["Inference", "4.1 fps"],
              ["P95 latency", "67 ms"],
              ["Confidence", "91.8%"],
              ["Resolution", "640 × 640"],
              ["Dropped", "0.3%"],
              ["Edge temp", "53°C"],
            ].map(([label, value]) => (
              <div className="px-3 py-2.5" key={label}>
                <div className="text-[8px] font-black uppercase tracking-widest text-slate-500">{label}</div>
                <div className="mt-0.5 font-mono text-xs font-bold">{value}</div>
              </div>
            ))}
          </div>
        </section>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-2xl border-2 border-slate-950 bg-white p-4 shadow-[4px_4px_0_#0F172A]">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div><h3 className="font-black text-slate-950">Queue pressure curve</h3><p className="text-[11px] font-medium text-slate-500">Observed track count + damped 15-minute forecast</p></div>
              <StatusPill tone={utilization > 100 ? "#FFE4E6" : "#CCFBF1"}>{utilization}% utilized</StatusPill>
            </div>
            <div className="h-44">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={queueSeries} margin={{ top: 5, right: 10, left: -28, bottom: 0 }}>
                  <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="minute" tick={{ fontSize: 10, fontWeight: 700 }} axisLine={false} tickLine={false} />
                  <YAxis domain={[0, 10]} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ border: "2px solid #0F172A", borderRadius: 10, fontSize: 11, boxShadow: "3px 3px 0 #0F172A" }} />
                  <Line type="monotone" dataKey="actual" stroke="#0F172A" strokeWidth={3} dot={{ r: 3, fill: "#FFF", strokeWidth: 2 }} connectNulls={false} />
                  <Line type="monotone" dataKey="forecast" stroke={cfg.color} strokeWidth={3} strokeDasharray="7 5" dot={{ r: 3, fill: cfg.color }} connectNulls={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section className="rounded-2xl border-2 border-slate-950 bg-[#F8FAFC] p-4 shadow-[4px_4px_0_#0F172A]">
            <div className="mb-3 flex items-start justify-between">
              <div><h3 className="font-black text-slate-950">Little's Law matrix</h3><p className="text-[11px] font-medium text-slate-500">Transparent queue calculation, no black box</p></div>
              <span className="font-mono text-[10px] font-black text-slate-500">L = λW</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {[
                ["L · queue now", `${queueLength} people`, "Measured"],
                ["λ · arrivals", `${arrivalRate.toFixed(2)}/min`, "5-min roll"],
                ["μ · service", `${serviceRate.toFixed(2)}/min`, "10 serves"],
                ["ρ · pressure", `${utilization}%`, utilization > 100 ? "Unstable" : "Stable"],
                ["W · observed", `${(queueLength / Math.max(arrivalRate, .2)).toFixed(1)} min`, "L ÷ λ"],
                ["Wq · join now", `${waitMinutes.toFixed(1)} min`, "L ÷ μ"],
              ].map(([label, value, detail]) => (
                <div className="rounded-lg border border-slate-300 bg-white p-2.5" key={label}>
                  <div className="text-[9px] font-black uppercase tracking-wider text-slate-500">{label}</div>
                  <div className="mt-1 font-mono text-sm font-black text-slate-950">{value}</div>
                  <div className="text-[9px] font-bold text-slate-400">{detail}</div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>

      <aside className="space-y-4">
        <section className="rounded-2xl border-2 border-slate-950 p-4 shadow-[4px_4px_0_#0F172A]" style={{ background: cfg.soft }}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-500">Active simulation</div>
              <h3 className="mt-1 text-lg font-black text-slate-950">{cfg.label}</h3>
            </div>
            <span className="mt-1 h-3 w-3 rounded-full border-2 border-slate-950" style={{ background: cfg.color }} />
          </div>
          <p className="mt-2 text-xs font-semibold leading-relaxed text-slate-600">{cfg.description}</p>
          <div className="mt-4 rounded-xl border-2 border-slate-950 bg-white p-3">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Estimated value at risk</span>
              <span className="font-mono text-lg font-black text-rose-600">{currency(lostPerHour)}/h</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full border border-slate-950 bg-slate-100">
              <div className="h-full" style={{ width: `${clamp(lostPerHour / 10, 4, 100)}%`, background: cfg.color }} />
            </div>
          </div>
        </section>

        <section className="rounded-2xl border-2 border-slate-950 bg-white p-4 shadow-[4px_4px_0_#0F172A]">
          <div className="mb-3 flex items-center justify-between"><h3 className="font-black text-slate-950">Decision engine</h3><Signal color={cfg.color} pulse /></div>
          <div className="space-y-2.5">
            {(scenario === "queue"
              ? [
                  ["CRITICAL", "Open Counter 2 within 3 min", "Avoid ~₹1,240 abandonment loss"],
                  ["FORECAST", "Queue likely to reach 9 people", "Arrival rate exceeds service rate"],
                  ["AUTOMATION", "Staff nudge prepared in Hindi", "No image attached to alert"],
                ]
              : scenario === "stockout"
                ? [
                    ["CRITICAL", "Restock beverage shelf A3", "Coverage persisted <25% for 3 scans"],
                    ["REORDER", "Move 12 units from backroom", "Expected 78 min until full stockout"],
                    ["IMPACT", "₹1,086 sales at risk today", "Demand × OOS substitution factor"],
                  ]
                : scenario === "weekend"
                  ? [
                      ["OPPORTUNITY", "Keep Counter 2 active", "Conversion is 6.2 pts above baseline"],
                      ["REPLENISH", "Top-up impulse rack", "Velocity +15.1% week over week"],
                      ["FORECAST", "Peak expected at 19:10", "Prepare float and bags now"],
                    ]
                  : [
                      ["HEALTHY", "No immediate intervention", "Service capacity exceeds arrivals"],
                      ["WATCH", "Dairy coverage trending down", "Review again after next 3 scans"],
                      ["OPPORTUNITY", "Impulse dwell is +18 sec", "Move ₹10 packs to eye level"],
                    ]
            ).map(([tag, title, detail], index) => (
              <div className="flex gap-3 rounded-xl border border-slate-200 p-3" key={title}>
                <div className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-slate-950 text-[10px] font-black" style={{ background: index === 0 ? cfg.soft : "#F8FAFC" }}>{index + 1}</div>
                <div className="min-w-0">
                  <div className="text-[8px] font-black uppercase tracking-[0.14em]" style={{ color: index === 0 ? cfg.color : "#64748B" }}>{tag}</div>
                  <div className="text-xs font-black text-slate-900">{title}</div>
                  <div className="mt-0.5 text-[10px] font-medium leading-snug text-slate-500">{detail}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border-2 border-slate-950 bg-slate-950 p-4 text-white shadow-[4px_4px_0_#475569]">
          <div className="flex items-center justify-between"><h3 className="font-black">Event stream</h3><span className="font-mono text-[9px] font-bold text-emerald-300">EDGE LOCAL</span></div>
          <div className="mt-3 space-y-3 font-mono text-[9px]">
            {[
              ["NOW", `queue.snapshot · L=${queueLength} · μ=${serviceRate.toFixed(2)}`],
              ["−03s", `zone.occupancy · floor=${tracks.length}`],
              ["−08s", `shelf.scan · coverage=${shelfCoverage}%`],
              ["−14s", "footfall.crossing · direction=IN"],
              ["−21s", "heatmap.tiles · bucket=1h · cells=24"],
            ].map(([time, event]) => (
              <div className="flex gap-3 border-b border-slate-800 pb-2 last:border-0 last:pb-0" key={event}>
                <span className="w-8 shrink-0 text-slate-500">{time}</span><span className="text-slate-200">{event}</span>
              </div>
            ))}
          </div>
        </section>
      </aside>
    </div>
  );
}

function WeeklyReport({ scenario }: { scenario: ScenarioId }) {
  const cfg = scenarioConfig[scenario];
  const data = weeklyBase.map((row, index) => ({
    ...row,
    revenue: Math.round(row.revenue * cfg.revenueFactor * (index > 4 && scenario === "weekend" ? 1.08 : 1)),
    lost: Math.round(row.lost * (scenario === "stockout" ? 1.8 : scenario === "queue" ? 1.45 : 1)),
  }));
  const totals = data.reduce((acc, row) => ({ revenue: acc.revenue + row.revenue, footfall: acc.footfall + row.footfall, lost: acc.lost + row.lost }), { revenue: 0, footfall: 0, lost: 0 });
  const avgConversion = data.reduce((sum, row) => sum + row.conversion, 0) / data.length;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MiniMetric label="Net sales · 7 days" value={currency(totals.revenue)} detail="+8.7% vs prior week" tone="#8B5CF6" />
        <MiniMetric label="Store visits" value={totals.footfall.toLocaleString("en-IN")} detail="+231 week over week" tone="#22D3EE" />
        <MiniMetric label="Conversion" value={`${avgConversion.toFixed(1)}%`} detail="+2.4 percentage points" tone="#14B8A6" />
        <MiniMetric label="Preventable loss" value={currency(totals.lost)} detail="OOS + queue abandonment" tone="#F43F5E" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_.85fr]">
        <section className="rounded-2xl border-2 border-slate-950 bg-white p-5 shadow-[4px_4px_0_#0F172A]">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div><div className="text-[9px] font-black uppercase tracking-[.16em] text-violet-600">PERFORMANCE</div><h3 className="text-lg font-black text-slate-950">Sales and missed opportunity</h3><p className="text-[11px] font-medium text-slate-500">POS revenue reconciled with visual operating signals</p></div>
            <div className="flex gap-3 text-[10px] font-bold"><span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-violet-500" />Net sales</span><span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-rose-400" />Loss</span></div>
          </div>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 11, fontWeight: 800 }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={(v) => `₹${v / 1000}k`} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} width={42} />
                <Tooltip formatter={(value) => currency(Number(value))} contentStyle={{ border: "2px solid #0F172A", borderRadius: 10, fontSize: 11, boxShadow: "3px 3px 0 #0F172A" }} />
                <Bar dataKey="revenue" radius={[6, 6, 0, 0]} maxBarSize={42}>{data.map((row) => <Cell key={row.day} fill={row.day === "Sat" ? cfg.color : "#8B5CF6"} />)}</Bar>
                <Bar dataKey="lost" fill="#FB7185" radius={[4, 4, 0, 0]} maxBarSize={16} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="rounded-2xl border-2 border-slate-950 bg-[#ECFEFF] p-5 shadow-[4px_4px_0_#0F172A]">
          <div className="mb-4"><div className="text-[9px] font-black uppercase tracking-[.16em] text-cyan-700">FUNNEL HEALTH</div><h3 className="text-lg font-black text-slate-950">Footfall → conversion</h3></div>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 5, right: 5, left: -18, bottom: 0 }}>
                <defs><linearGradient id="footfallFill" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#06B6D4" stopOpacity={.4} /><stop offset="95%" stopColor="#06B6D4" stopOpacity={0} /></linearGradient></defs>
                <CartesianGrid stroke="#BAE6FD" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 10, fontWeight: 700 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 9 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ border: "2px solid #0F172A", borderRadius: 10, fontSize: 11 }} />
                <Area type="monotone" dataKey="footfall" stroke="#0891B2" strokeWidth={3} fill="url(#footfallFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-cyan-900/30 bg-white p-3"><div className="text-[9px] font-black uppercase text-slate-500">Peak window</div><div className="font-mono text-sm font-black">18:30–20:00</div></div>
            <div className="rounded-lg border border-cyan-900/30 bg-white p-3"><div className="text-[9px] font-black uppercase text-slate-500">Best day</div><div className="font-mono text-sm font-black">Saturday · 73%</div></div>
          </div>
        </section>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.4fr_.6fr]">
        <section className="overflow-hidden rounded-2xl border-2 border-slate-950 bg-white shadow-[4px_4px_0_#0F172A]">
          <div className="flex items-center justify-between border-b-2 border-slate-950 p-4"><div><h3 className="font-black text-slate-950">Category performance</h3><p className="text-[10px] font-medium text-slate-500">Tally sales × shelf availability</p></div><StatusPill tone="#EDE9FE">7-day rollup</StatusPill></div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px] text-left text-xs">
              <thead className="bg-slate-950 text-[9px] uppercase tracking-widest text-slate-300"><tr><th className="px-4 py-3">Category</th><th className="px-4 py-3">Net sales</th><th className="px-4 py-3">Mix</th><th className="px-4 py-3">WoW</th><th className="px-4 py-3">On-shelf</th></tr></thead>
              <tbody className="divide-y divide-slate-200">
                {categoryRows.map((row) => <tr className="hover:bg-slate-50" key={row.category}><td className="px-4 py-3 font-black text-slate-900">{row.category}</td><td className="px-4 py-3 font-mono font-bold">{currency(row.sales * cfg.revenueFactor)}</td><td className="px-4 py-3 font-mono">{row.share}%</td><td className={`px-4 py-3 font-mono font-black ${row.trend < 0 ? "text-rose-600" : "text-emerald-700"}`}>{row.trend > 0 ? "+" : ""}{row.trend}%</td><td className="px-4 py-3"><div className="flex items-center gap-2"><div className="h-2 w-16 overflow-hidden rounded-full bg-slate-200"><div className="h-full bg-lime-500" style={{ width: `${scenario === "stockout" && row.category === "Dairy & chilled" ? 18 : row.availability}%` }} /></div><span className="font-mono font-bold">{scenario === "stockout" && row.category === "Dairy & chilled" ? 18 : row.availability}%</span></div></td></tr>)}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rounded-2xl border-2 border-slate-950 bg-[#FEF3C7] p-5 shadow-[4px_4px_0_#0F172A]">
          <div className="text-[9px] font-black uppercase tracking-[.16em] text-amber-700">WEEKLY ACTION PLAN</div>
          <h3 className="mt-1 text-lg font-black text-slate-950">Three moves for next week</h3>
          <div className="mt-4 space-y-3">
            {[
              ["01", "Add second cashier Fri–Sun", "17:45–20:15 · projected save ₹3,420"],
              ["02", "Raise dairy safety stock", "+18 units Amul Taaza before 16:00"],
              ["03", "Expand impulse facing", "Confectionery velocity is +15.1%"],
            ].map(([n, title, detail]) => <div className="flex gap-3 rounded-xl border-2 border-slate-950 bg-white p-3" key={n}><span className="font-mono text-lg font-black text-amber-500">{n}</span><div><div className="text-xs font-black text-slate-900">{title}</div><div className="mt-0.5 text-[10px] font-medium text-slate-500">{detail}</div></div></div>)}
          </div>
          <div className="mt-4 border-t border-amber-700/30 pt-3 text-[10px] font-semibold leading-relaxed text-slate-600">Report joins anonymous visual aggregates with POS totals. It contains no images, paths, identities, demographics or biometric attributes.</div>
        </section>
      </div>
    </div>
  );
}

function PrivacyPanel() {
  const pipeline = [
    ["01", "Frame sampled in RAM", "2–5 fps from existing RTSP camera; never written to disk."],
    ["02", "Person box only", "YOLO11n class=person. No face, age, gender, emotion or clothing inference."],
    ["03", "Appearance-free tracking", "Motion + IoU association. Ephemeral integer IDs; no ReID embedding."],
    ["04", "Immediate aggregation", "Line crossings, dwell seconds, queue count, shelf coverage and heat cells."],
    ["05", "Anonymous numbers sync", "Cloud receives aggregated JSON. Raw frames and track IDs stay on edge."],
  ];
  const controls = [
    ["Raw video retention", "0 seconds", "Frame reference dropped after inference"],
    ["Ephemeral track IDs", "RAM only", "Lifetime measured in seconds/minutes"],
    ["Operational aggregates", "30 days", "Hourly retention purge"],
    ["Heatmap cells", "90 days", "Hourly 20 px buckets; paths unrecoverable"],
    ["Daily KPI rollups", "Indefinite", "Only 17 non-personal numeric fields"],
  ];

  return (
    <div className="grid gap-4 xl:grid-cols-[1.05fr_.95fr]">
      <section className="rounded-2xl border-2 border-slate-950 bg-white p-5 shadow-[4px_4px_0_#0F172A]">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-[9px] font-black uppercase tracking-[.17em] text-emerald-700">PRIVACY BY ARCHITECTURE</div><h3 className="text-xl font-black text-slate-950">From pixels to anonymous operations</h3><p className="mt-1 max-w-xl text-xs font-medium text-slate-500">Designed around data minimisation, purpose limitation and enforced retention—not post-hoc anonymisation.</p></div><StatusPill tone="#D1FAE5"><Signal pulse /> DPDP safeguards active</StatusPill></div>
        <div className="relative mt-6 space-y-3 before:absolute before:bottom-7 before:left-[19px] before:top-7 before:w-0.5 before:bg-slate-200">
          {pipeline.map(([n, title, detail], index) => <div className="relative flex gap-4 rounded-xl border border-slate-200 bg-slate-50 p-3.5" key={n}><div className="z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border-2 border-slate-950 font-mono text-xs font-black shadow-[2px_2px_0_#0F172A]" style={{ background: index === pipeline.length - 1 ? "#CCFBF1" : "#FFFFFF" }}>{n}</div><div><div className="text-sm font-black text-slate-900">{title}</div><div className="mt-0.5 text-[11px] font-medium leading-relaxed text-slate-500">{detail}</div></div></div>)}
        </div>
      </section>

      <div className="space-y-4">
        <section className="rounded-2xl border-2 border-slate-950 bg-slate-950 p-5 text-white shadow-[4px_4px_0_#64748B]">
          <div className="flex items-center justify-between"><div><div className="text-[9px] font-black uppercase tracking-[.17em] text-emerald-300">DATA CONTRACT</div><h3 className="text-lg font-black">What leaves the edge</h3></div><span className="font-mono text-[10px] font-black text-emerald-300">JSON · ≈1 kbit/s</span></div>
          <pre className="mt-4 overflow-x-auto rounded-xl border border-slate-700 bg-slate-900 p-4 font-mono text-[10px] leading-relaxed text-slate-300"><code>{`{
  "event": "queue.snapshot",
  "camera_id": "CAM-01",
  "count": 6,
  "arrival_rate_pm": 2.18,
  "service_rate_pm": 1.31,
  "wait_estimate_s": 275,
  "frame": null,
  "track_id": null
}`}</code></pre>
          <div className="mt-3 flex flex-wrap gap-2"><StatusPill tone="#064E3B"><span className="text-emerald-200">No image</span></StatusPill><StatusPill tone="#064E3B"><span className="text-emerald-200">No identity</span></StatusPill><StatusPill tone="#064E3B"><span className="text-emerald-200">No biometrics</span></StatusPill></div>
        </section>

        <section className="overflow-hidden rounded-2xl border-2 border-slate-950 bg-white shadow-[4px_4px_0_#0F172A]">
          <div className="border-b-2 border-slate-950 bg-[#E0F2FE] p-4"><h3 className="font-black text-slate-950">Retention & purpose controls</h3><p className="text-[10px] font-semibold text-slate-500">Code-enforced defaults for the pilot</p></div>
          <div className="divide-y divide-slate-200">
            {controls.map(([data, retention, mechanism]) => <div className="grid grid-cols-[1fr_auto] gap-3 p-3.5" key={data}><div><div className="text-xs font-black text-slate-900">{data}</div><div className="mt-0.5 text-[10px] font-medium text-slate-500">{mechanism}</div></div><span className="self-center rounded-md border border-slate-950 bg-slate-50 px-2 py-1 font-mono text-[10px] font-black">{retention}</span></div>)}
          </div>
        </section>

        <div className="rounded-xl border-2 border-amber-800 bg-amber-50 p-4 text-[11px] font-semibold leading-relaxed text-amber-950"><strong>Compliance note:</strong> This is an engineering demonstration of DPDP-aligned safeguards, not legal advice. Production deployment still requires store signage, a documented lawful purpose, grievance contact and counsel review.</div>
      </div>
    </div>
  );
}

export function VisionDemoPage() {
  const [scenario, setScenario] = useState<ScenarioId>("baseline");
  const [view, setView] = useState<ViewId>("live");
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const video = document.getElementById("vision-demo-video") as HTMLVideoElement | null;
    if (!video) return;
    const update = () => {
      setCurrentTime(video.currentTime || 0);
      setIsPlaying(!video.paused);
      rafRef.current = requestAnimationFrame(update);
    };
    rafRef.current = requestAnimationFrame(update);
    return () => { if (rafRef.current != null) cancelAnimationFrame(rafRef.current); };
  }, [view]);

  const cfg = scenarioConfig[scenario];
  const dynamic = Math.sin(currentTime * 0.43) * 0.7 + Math.sin(currentTime * 0.13) * 0.45;
  const queueLength = clamp(Math.round(cfg.queueBase + dynamic), 1, 10);
  const arrivalRate = Math.max(0.2, cfg.arrivalRate + Math.sin(currentTime * 0.19) * 0.12);
  const serviceRate = Math.max(0.2, cfg.serviceRate + Math.cos(currentTime * 0.11) * 0.08);
  const waitMinutes = queueLength / serviceRate;
  const shelfCoverage = clamp(Math.round(cfg.coverage + Math.sin(currentTime * 0.17) * 2), 8, 99);
  const tracks = useMemo<Track[]>(() => {
    const count = clamp(queueLength + (scenario === "queue" ? 2 : 3), 4, 10);
    return Array.from({ length: count }, (_, index) => {
      const inQueue = index < queueLength;
      if (inQueue) {
        return {
          id: 31 + index,
          x: 585 + (index % 3) * 98 + Math.sin(currentTime * .4 + index) * 12,
          y: 126 + Math.floor(index / 3) * 112 + Math.cos(currentTime * .32 + index) * 8,
          width: 66,
          height: 116,
          confidence: .88 + (index % 5) * .018,
          zone: "queue",
        };
      }
      return {
        id: 31 + index,
        x: 115 + ((index * 137 + currentTime * (10 + index)) % 400),
        y: 165 + ((index * 83 + currentTime * 7) % 190),
        width: 62 + (index % 2) * 8,
        height: 108 + (index % 3) * 8,
        confidence: .86 + (index % 6) * .02,
        zone: "aisle",
      };
    });
  }, [currentTime, queueLength, scenario]);

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#F1F5F9] text-slate-950">
      <div className="border-b-2 border-slate-950 bg-white">
        <div className="mx-auto max-w-[1500px] px-4 py-5 sm:px-6">
          <div className="flex flex-col justify-between gap-4 xl:flex-row xl:items-end">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border-2 border-slate-950 bg-cyan-200 font-mono text-sm font-black shadow-[3px_3px_0_#0F172A]">CV</div>
              <div>
                <div className="flex flex-wrap items-center gap-2"><span className="text-[10px] font-black uppercase tracking-[.18em] text-cyan-700">RETAILSENSE VISION LAB</span><StatusPill tone="#E0F2FE">Simulation</StatusPill></div>
                <h1 className="mt-1 text-2xl font-black tracking-tight sm:text-3xl">Anonymous retail intelligence, live.</h1>
                <p className="mt-1 max-w-2xl text-xs font-medium text-slate-500">Playable CCTV simulation with visual detections, queue science, shelf risk, operational actions and weekly business outcomes.</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill tone="#D1FAE5"><Signal pulse /> Edge online</StatusPill>
              <StatusPill tone="#E0F2FE">No face recognition</StatusPill>
              <StatusPill tone={isPlaying ? "#FEF3C7" : "#E2E8F0"}>{isPlaying ? "Stream playing" : "Stream paused"}</StatusPill>
            </div>
          </div>

          <div className="mt-5 flex flex-col gap-3 border-t border-slate-200 pt-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex w-fit rounded-xl border-2 border-slate-950 bg-slate-100 p-1 shadow-[2px_2px_0_#0F172A]">
              {(["live", "weekly", "privacy"] as ViewId[]).map((item) => <button key={item} onClick={() => setView(item)} className={`rounded-lg px-3 py-2 text-[11px] font-black transition ${view === item ? "bg-slate-950 text-white" : "text-slate-600 hover:bg-white"}`}>{item === "live" ? "Live floor" : item === "weekly" ? "Weekly report" : "Privacy & model"}</button>)}
            </div>
            <div className="flex items-center gap-2 overflow-x-auto pb-1 lg:pb-0">
              <span className="shrink-0 text-[9px] font-black uppercase tracking-[.15em] text-slate-400">Scenario</span>
              {(Object.keys(scenarioConfig) as ScenarioId[]).map((id) => {
                const item = scenarioConfig[id];
                return <button key={id} onClick={() => setScenario(id)} className={`shrink-0 rounded-lg border-2 border-slate-950 px-3 py-2 text-[10px] font-black shadow-[2px_2px_0_#0F172A] transition active:translate-x-0.5 active:translate-y-0.5 active:shadow-none ${scenario === id ? "text-white" : "bg-white text-slate-700 hover:-translate-y-0.5"}`} style={{ background: scenario === id ? item.color : undefined }}><span className="mr-1.5 inline-block h-2 w-2 rounded-full border border-slate-950" style={{ background: item.color }} />{item.label}</button>;
              })}
            </div>
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-[1500px] px-4 py-5 sm:px-6">
        {view === "live" && <LiveFloor scenario={scenario} currentTime={currentTime} tracks={tracks} queueLength={queueLength} arrivalRate={arrivalRate} serviceRate={serviceRate} waitMinutes={waitMinutes} shelfCoverage={shelfCoverage} />}
        {view === "weekly" && <WeeklyReport scenario={scenario} />}
        {view === "privacy" && <PrivacyPanel />}
        <footer className="mt-7 flex flex-col justify-between gap-2 border-t border-slate-300 pt-4 text-[10px] font-semibold text-slate-500 sm:flex-row"><span>Demo telemetry is deterministic and synchronized to the supplied footage; it is not a claim of frame-level model output.</span><span className="font-mono">RetailSense Edge CV · demo build 2026.09</span></footer>
      </main>
    </div>
  );
}
