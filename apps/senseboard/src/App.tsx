import React, { useEffect, useState } from "react";
import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import { useT } from "@/i18n/useT";
import { useSettings } from "@/store/settings";
import { useLive, selectOpenAlerts, selectEdgeOnline } from "@/store/live";

export function NavBar() {
  const { t, lang } = useT();
  const toggleLang = useSettings((s) => s.toggleLang);
  const alerts = useLive((s) => s.alerts);
  const openAlerts = Object.values(alerts).filter((a) => a.status !== "resolved");
  const edgeOnline = useLive(selectEdgeOnline);
  const sync = useLive((s) => s.sync);

  return (
    <header className="sticky top-0 z-50 bg-slate-900 border-b border-slate-800 text-slate-100 shadow-md">
      <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-orange-500 flex items-center justify-center font-bold text-white shadow">
              RS
            </div>
            <span className="font-extrabold text-lg tracking-tight bg-gradient-to-r from-orange-400 to-amber-200 bg-clip-text text-transparent">
              RetailSense
            </span>
          </div>

          <nav className="hidden md:flex items-center gap-1">
            <NavLink
              to="/owner"
              className={({ isActive }) =>
                `px-3 py-1.5 rounded-md text-sm font-semibold transition ${
                  isActive ? "bg-orange-500/20 text-orange-400 border border-orange-500/30" : "text-slate-300 hover:bg-slate-800"
                }`
              }
            >
              {t("nav.owner", { defaultValue: "आज का हिसाब" })}
            </NavLink>
            <NavLink
              to="/ops"
              className={({ isActive }) =>
                `px-3 py-1.5 rounded-md text-sm font-semibold transition flex items-center gap-2 ${
                  isActive ? "bg-orange-500/20 text-orange-400 border border-orange-500/30" : "text-slate-300 hover:bg-slate-800"
                }`
              }
            >
              <span>{t("nav.ops", { defaultValue: "ऑप्स" })}</span>
              {openAlerts.length > 0 && (
                <span className="px-1.5 py-0.5 text-xs rounded-full bg-rose-500 text-white font-bold animate-pulse">
                  {openAlerts.length}
                </span>
              )}
            </NavLink>
            <NavLink
              to="/insights"
              className={({ isActive }) =>
                `px-3 py-1.5 rounded-md text-sm font-semibold transition ${
                  isActive ? "bg-orange-500/20 text-orange-400 border border-orange-500/30" : "text-slate-300 hover:bg-slate-800"
                }`
              }
            >
              {t("nav.insights", { defaultValue: "इनसाइट्स" })}
            </NavLink>
            <NavLink
              to="/chain"
              className={({ isActive }) =>
                `px-3 py-1.5 rounded-md text-sm font-semibold transition ${
                  isActive ? "bg-orange-500/20 text-orange-400 border border-orange-500/30" : "text-slate-300 hover:bg-slate-800"
                }`
              }
            >
              {t("nav.chain", { defaultValue: "चेन" })}
            </NavLink>
            <NavLink
              to="/zones"
              className={({ isActive }) =>
                `px-3 py-1.5 rounded-md text-sm font-semibold transition ${
                  isActive ? "bg-orange-500/20 text-orange-400 border border-orange-500/30" : "text-slate-300 hover:bg-slate-800"
                }`
              }
            >
              {t("nav.zones", { defaultValue: "ज़ोन" })}
            </NavLink>
          </nav>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-slate-800 border border-slate-700 rounded-full px-3 py-1 text-xs">
            <span
              className={`w-2 h-2 rounded-full ${
                sync?.link === "down"
                  ? "bg-amber-400"
                  : edgeOnline
                  ? "bg-emerald-400 animate-ping"
                  : "bg-emerald-500"
              }`}
            />
            <span className="text-slate-300 font-medium">
              {sync?.link === "down"
                ? "Offline · Store-and-Forward Active"
                : "Live LAN Connection"}
            </span>
          </div>

          <button
            onClick={toggleLang}
            className="px-2.5 py-1 text-xs font-bold rounded-md bg-slate-800 hover:bg-slate-700 text-orange-400 border border-slate-700 transition"
          >
            {lang === "hi" ? "ENG 🌐" : "हिंदी 🇮🇳"}
          </button>
        </div>
      </div>
    </header>
  );
}

export function OwnerPage() {
  const { t } = useT();
  const kpi = useLive((s) => s.kpi);
  const alerts = useLive((s) => s.alerts);
  const openAlerts = Object.values(alerts).filter((a) => a.status !== "resolved");
  const [activeReply, setActiveReply] = useState<string | null>(null);

  const triggerScenario = async (name: string, params: any = {}) => {
    try {
      await fetch("http://localhost:8001/demo/scenario", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, params }),
      });
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <div className="bg-gradient-to-r from-slate-900 to-slate-800 border border-slate-700/80 rounded-2xl p-6 shadow-lg">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-extrabold text-white tracking-tight">
              {t("owner.summary", { defaultValue: "आज का हिसाब — Ramesh General Store" })}
            </h1>
            <p className="text-sm text-slate-400 mt-1">
              Store ID: STR-DL-001 · Edge ID: EDGE-001 · CCTV RTSP Pipeline
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => triggerScenario("evening_rush")}
              className="px-3 py-1.5 rounded-lg bg-orange-600 hover:bg-orange-500 text-white text-xs font-bold shadow transition"
            >
              🔥 Rush Hour Demo
            </button>
            <button
              onClick={() => triggerScenario("stockout", { shelf_id: "shelf-A" })}
              className="px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold shadow transition"
            >
              🥛 Amul Stockout Demo
            </button>
            <button
              onClick={() => triggerScenario("baseline")}
              className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white text-xs font-bold transition"
            >
              ↺ Reset Scenario
            </button>
          </div>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            {t("owner.saved", { defaultValue: "₹ बचाया आज" })}
          </div>
          <div className="text-3xl font-black text-emerald-400 mt-2">
            ₹{kpi?.recovered_inr ?? 281}
          </div>
          <p className="text-xs text-slate-400 mt-1">Prevented stockout & queue loss</p>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            {t("owner.lost", { defaultValue: "₹ नुकसान आज" })}
          </div>
          <div className="text-3xl font-black text-rose-400 mt-2">
            ₹{kpi?.lost_sales_inr ?? 173}
          </div>
          <p className="text-xs text-slate-400 mt-1">Based on Corsten 0.31 stockout factor</p>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            {t("kpi.footfall", { defaultValue: "ग्राहक आए" })}
          </div>
          <div className="text-3xl font-black text-blue-400 mt-2">
            {kpi?.footfall_in ?? 142} <span className="text-sm font-normal text-slate-400">visitors</span>
          </div>
          <p className="text-xs text-slate-400 mt-1">Real-time entrance line crossings</p>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            {t("kpi.osa", { defaultValue: "शेल्फ भरी" })}
          </div>
          <div className="text-3xl font-black text-amber-400 mt-2">
            {kpi?.osa_pct ?? 94.2}%
          </div>
          <p className="text-xs text-slate-400 mt-1">3-scan temporal persistence active</p>
        </div>
      </div>

      {/* WhatsApp Simulator / Phone Panel View */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow space-y-4">
          <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
            <span>💬 Live WhatsApp Vernacular Alerts</span>
            <span className="text-xs font-normal text-emerald-400 bg-emerald-950 border border-emerald-800 px-2.5 py-0.5 rounded-full">
              Meta Cloud API Ready
            </span>
          </h2>

          {openAlerts.length === 0 ? (
            <div className="p-8 text-center bg-slate-950/50 border border-dashed border-slate-800 rounded-xl">
              <p className="text-slate-400 text-sm">
                सब ठीक है! कोई सक्रिय अलर्ट नहीं (All systems normal. No active alerts).
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {openAlerts.map((alert) => (
                <div
                  key={alert.alert_id}
                  className="bg-slate-950 border border-orange-500/30 rounded-xl p-4 space-y-3 shadow-md"
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <span className="px-2 py-0.5 text-xs font-bold rounded bg-rose-500/20 text-rose-400 border border-rose-500/30">
                        {alert.kind}
                      </span>
                      <h3 className="text-base font-bold text-white mt-1">
                        {alert.rendered_hi ?? alert.rendered_en}
                      </h3>
                    </div>
                    {alert.impact && (
                      <span className="text-sm font-extrabold text-emerald-400 bg-emerald-950 border border-emerald-800 px-2 py-1 rounded">
                        ₹{alert.impact.lost_sales_inr} at risk
                      </span>
                    )}
                  </div>

                  <p className="text-xs text-slate-400 font-mono">
                    Basis: {alert.impact?.basis ?? "Little's Law calculation"}
                  </p>

                  <div className="flex items-center gap-2 pt-2 border-t border-slate-800">
                    <button
                      onClick={async () => {
                        setActiveReply(alert.alert_id);
                        await fetch("http://localhost:8001/demo/whatsapp/reply", {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ alert_id: alert.alert_id, reply: "1" }),
                        });
                      }}
                      className="px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-bold transition"
                    >
                      1 = भर दिया (Restocked)
                    </button>
                    <button
                      onClick={async () => {
                        await fetch("http://localhost:8001/demo/whatsapp/reply", {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ alert_id: alert.alert_id, reply: "3" }),
                        });
                      }}
                      className="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition"
                    >
                      3 = गलत अलर्ट (False alert)
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Store Information Panel */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow space-y-4">
          <h2 className="text-lg font-bold text-slate-100">📌 Store Overview</h2>
          <div className="space-y-3 text-xs text-slate-300">
            <div className="flex justify-between py-1.5 border-b border-slate-800">
              <span className="text-slate-400">Store Name</span>
              <span className="font-semibold text-white">Ramesh General Store</span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-slate-800">
              <span className="text-slate-400">Location</span>
              <span className="font-semibold text-white">Karol Bagh, Delhi</span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-slate-800">
              <span className="text-slate-400">Active Cameras</span>
              <span className="font-semibold text-emerald-400">2 Streams (RTSP)</span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-slate-800">
              <span className="text-slate-400">Inference Device</span>
              <span className="font-semibold text-white">Raspberry Pi 5 (CPU ONNX)</span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-slate-800">
              <span className="text-slate-400">Tally Reconciliation</span>
              <span className="font-semibold text-orange-400">Connected (:9000)</span>
            </div>
            <div className="flex justify-between py-1.5">
              <span className="text-slate-400">ONDC Catalog Publisher</span>
              <span className="font-semibold text-emerald-400">Active</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function OpsPage() {
  const { t } = useT();
  const openAlerts = useLive(selectOpenAlerts);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold text-white">Operations Console (/ops)</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow space-y-3">
          <h2 className="text-base font-bold text-orange-400">Active Queue Intelligence</h2>
          <p className="text-xs text-slate-400">Little's Law Wait Time Estimation & Forecasts</p>
          <div className="p-4 bg-slate-950 rounded-lg border border-slate-800 space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-slate-300">Counter 1 Queue Length</span>
              <span className="font-bold text-white">4 Persons</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-slate-300">Estimated Wait Time</span>
              <span className="font-bold text-emerald-400">~2.8 Minutes</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-slate-300">15-Min Forecast (HGB Model)</span>
              <span className="font-bold text-amber-400">↑ 6 Persons (MAE 0.8)</span>
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow space-y-3">
          <h2 className="text-base font-bold text-orange-400">Active Shelf Monitoring</h2>
          <p className="text-xs text-slate-400">Classical Coverage Estimator + 3-Scan Persistence</p>
          <div className="p-4 bg-slate-950 rounded-lg border border-slate-800 space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-slate-300">Shelf A (Amul Milk)</span>
              <span className="font-bold text-emerald-400">Stocked (92% Coverage)</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-slate-300">Shelf B (Biscuits)</span>
              <span className="font-bold text-emerald-400">Stocked (88% Coverage)</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-slate-300">Shelf C (Oil & Ghee)</span>
              <span className="font-bold text-emerald-400">Stocked (95% Coverage)</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function InsightsPage() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold text-white">Store Insights & Shrink (/insights)</h1>
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow">
        <h2 className="text-lg font-bold text-orange-400 mb-2">Visual-vs-Tally Inventory Shrinkage</h2>
        <p className="text-xs text-slate-400 mb-4">
          Compares physical shelf facings observed by edge cameras against Tally ERP stock summary records.
        </p>
        <table className="w-full text-left text-xs text-slate-300 border-collapse">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400">
              <th className="py-2">Item Name</th>
              <th className="py-2">Tally ERP Qty</th>
              <th className="py-2">Camera Qty</th>
              <th className="py-2">Discrepancy (Δ)</th>
              <th className="py-2">Shrink Amount (₹)</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-slate-800/50">
              <td className="py-2.5 font-semibold text-white">Amul Taaza 500ml</td>
              <td className="py-2.5">48</td>
              <td className="py-2.5">41</td>
              <td className="py-2.5 text-rose-400 font-bold">-7</td>
              <td className="py-2.5 text-rose-400 font-bold">₹189</td>
            </tr>
            <tr className="border-b border-slate-800/50">
              <td className="py-2.5 font-semibold text-white">Parle-G 70g</td>
              <td className="py-2.5">120</td>
              <td className="py-2.5">120</td>
              <td className="py-2.5 text-emerald-400 font-bold">0</td>
              <td className="py-2.5 text-emerald-400">₹0</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ChainPage() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold text-white">Fleet & Multi-Store Chain View (/chain)</h1>
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow space-y-4">
        <h2 className="text-base font-bold text-orange-400">Edge Fleet Status</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="p-4 bg-slate-950 rounded-lg border border-slate-800">
            <div className="text-xs text-slate-400">EDGE-001 (Karol Bagh, Delhi)</div>
            <div className="text-sm font-bold text-emerald-400 mt-1">Online (3.99 fps)</div>
          </div>
          <div className="p-4 bg-slate-950 rounded-lg border border-slate-800">
            <div className="text-xs text-slate-400">EDGE-002 (Koramangala, BLR)</div>
            <div className="text-sm font-bold text-emerald-400 mt-1">Online (4.12 fps)</div>
          </div>
          <div className="p-4 bg-slate-950 rounded-lg border border-slate-800">
            <div className="text-xs text-slate-400">EDGE-003 (Andheri, Mumbai)</div>
            <div className="text-sm font-bold text-emerald-400 mt-1">Online (3.95 fps)</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ZonesPage() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold text-white">Zone & Camera Calibration (/zones)</h1>
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow text-slate-300 text-sm space-y-3">
        <p>Zero-Hardware Polygon Zone Editor for RTSP Camera Streams.</p>
        <p className="text-xs text-slate-400">
          Draw entrance lines, counter polygons, and shelf regions over the live CCTV preview.
        </p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans antialiased selection:bg-orange-500 selection:text-white">
      <NavBar />
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/owner" replace />} />
          <Route path="/owner" element={<OwnerPage />} />
          <Route path="/ops" element={<OpsPage />} />
          <Route path="/insights" element={<InsightsPage />} />
          <Route path="/chain" element={<ChainPage />} />
          <Route path="/zones" element={<ZonesPage />} />
          <Route path="*" element={<Navigate to="/owner" replace />} />
        </Routes>
      </main>
    </div>
  );
}
