import React, { useEffect, useState } from "react";
import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import { useT } from "@/i18n/useT";
import { useSettings } from "@/store/settings";
import { useLive, selectEdgeOnline } from "@/store/live";

export function NavBar() {
  const { t, lang } = useT();
  const toggleLang = useSettings((s) => s.toggleLang);
  const alerts = useLive((s) => s.alerts);
  const openAlerts = Object.values(alerts).filter((a) => a.status !== "resolved");
  const edgeOnline = useLive(selectEdgeOnline);
  const sync = useLive((s) => s.sync);

  return (
    <header className="sticky top-0 z-50 bg-white border-b-[2.5px] border-slate-900 shadow-[0_3px_0_#0F172A] text-slate-900">
      <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
        <div className="flex items-center gap-5">
          {/* Brand Logo */}
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-[#FEF08A] border-[2.5px] border-slate-900 shadow-[2px_2px_0px_#0F172A] flex items-center justify-center font-black text-slate-900 text-sm">
              RS
            </div>
            <div className="flex flex-col">
              <span className="font-extrabold text-xl tracking-tight text-slate-900">
                RetailSense
              </span>
              <span className="text-[9px] font-mono font-bold tracking-widest text-slate-500 uppercase -mt-1">
                ✦ AI Operations Board
              </span>
            </div>
          </div>

          {/* Navigation Items (Soothing Muted Tabs) */}
          <nav className="hidden md:flex items-center gap-1.5 bg-[#FAF7F2] p-1 rounded-xl border border-slate-300">
            <NavLink
              to="/owner"
              className={({ isActive }) =>
                `px-3.5 py-1.5 rounded-lg text-xs font-bold tracking-tight transition-all duration-150 ${
                  isActive
                    ? "bg-[#FEF3C7] text-slate-900 border-[2px] border-slate-900 shadow-[2px_2px_0px_#0F172A]"
                    : "text-slate-700 hover:text-slate-900 hover:bg-white border border-transparent"
                }`
              }
            >
              {t("nav.owner", { defaultValue: "आज का हिसाब" })}
            </NavLink>
            <NavLink
              to="/ops"
              className={({ isActive }) =>
                `px-3.5 py-1.5 rounded-lg text-xs font-bold tracking-tight transition-all duration-150 flex items-center gap-2 ${
                  isActive
                    ? "bg-[#FEF3C7] text-slate-900 border-[2px] border-slate-900 shadow-[2px_2px_0px_#0F172A]"
                    : "text-slate-700 hover:text-slate-900 hover:bg-white border border-transparent"
                }`
              }
            >
              <span>{t("nav.ops", { defaultValue: "ऑप्स" })}</span>
              {openAlerts.length > 0 && (
                <span className="px-1.5 py-0.2 text-[10px] font-bold rounded-full bg-[#E11D48] text-white border border-slate-900">
                  {openAlerts.length}
                </span>
              )}
            </NavLink>
            <NavLink
              to="/insights"
              className={({ isActive }) =>
                `px-3.5 py-1.5 rounded-lg text-xs font-bold tracking-tight transition-all duration-150 ${
                  isActive
                    ? "bg-[#FEF3C7] text-slate-900 border-[2px] border-slate-900 shadow-[2px_2px_0px_#0F172A]"
                    : "text-slate-700 hover:text-slate-900 hover:bg-white border border-transparent"
                }`
              }
            >
              {t("nav.insights", { defaultValue: "इनसाइट्स" })}
            </NavLink>
            <NavLink
              to="/chain"
              className={({ isActive }) =>
                `px-3.5 py-1.5 rounded-lg text-xs font-bold tracking-tight transition-all duration-150 ${
                  isActive
                    ? "bg-[#FEF3C7] text-slate-900 border-[2px] border-slate-900 shadow-[2px_2px_0px_#0F172A]"
                    : "text-slate-700 hover:text-slate-900 hover:bg-white border border-transparent"
                }`
              }
            >
              {t("nav.chain", { defaultValue: "चेन" })}
            </NavLink>
            <NavLink
              to="/zones"
              className={({ isActive }) =>
                `px-3.5 py-1.5 rounded-lg text-xs font-bold tracking-tight transition-all duration-150 ${
                  isActive
                    ? "bg-[#FEF3C7] text-slate-900 border-[2px] border-slate-900 shadow-[2px_2px_0px_#0F172A]"
                    : "text-slate-700 hover:text-slate-900 hover:bg-white border border-transparent"
                }`
              }
            >
              {t("nav.zones", { defaultValue: "ज़ोन" })}
            </NavLink>
          </nav>
        </div>

        {/* Right Header Status Controls */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-[#E2F1E7] text-slate-900 font-mono border-[2px] border-slate-900 shadow-[2px_2px_0px_#0F172A] px-3 py-1 rounded-lg font-bold text-xs">
            <span
              className={`w-2 h-2 rounded-full border border-slate-900 ${
                sync?.link === "down" ? "bg-[#F59E0B]" : edgeOnline ? "bg-[#0284C7] animate-ping" : "bg-slate-900"
              }`}
            />
            <span>
              {sync?.link === "down"
                ? "Offline · Store-and-Forward Active"
                : "Live LAN Connection"}
            </span>
          </div>

          <button
            onClick={toggleLang}
            className="px-3 py-1 text-xs font-bold rounded-lg bg-[#FFEDD5] hover:bg-[#FED7AA] text-slate-900 border-[2px] border-slate-900 shadow-[2px_2px_0px_#0F172A] active:translate-x-[1px] active:translate-y-[1px] active:shadow-none transition-all"
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
      {/* Soothing Hero Banner */}
      <div className="bg-[#FEF3C7] border-[2.5px] border-slate-900 shadow-[5px_5px_0px_#0F172A] rounded-2xl p-6 text-slate-900">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-5">
          <div>
            <div className="inline-block bg-white text-slate-900 border border-slate-900 font-mono font-bold text-xs px-2.5 py-0.5 rounded shadow-[1.5px_1.5px_0px_#0F172A] uppercase mb-2">
              ✦ REAL-TIME RETAIL TELEMETRY ENGINE
            </div>
            <h1 className="text-2xl md:text-3xl font-extrabold text-slate-900 tracking-tight">
              {t("owner.summary", { defaultValue: "आज का हिसाब — Ramesh General Store" })}
            </h1>
            <p className="text-xs font-mono font-bold text-slate-700 mt-1.5 flex flex-wrap items-center gap-2">
              <span className="bg-white border border-slate-800 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A]">
                Store ID: <strong>STR-DL-001</strong>
              </span>
              <span className="bg-white border border-slate-800 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A]">
                Edge ID: <strong>EDGE-001</strong>
              </span>
              <span className="bg-[#E2F1E7] border border-slate-800 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A]">
                CCTV RTSP Pipeline
              </span>
            </p>
          </div>

          {/* Scenario Buttons */}
          <div className="flex flex-wrap gap-2.5">
            <button
              onClick={() => triggerScenario("evening_rush")}
              className="px-3.5 py-2 rounded-xl bg-[#F59E0B] hover:bg-[#D97706] text-white border-[2px] border-slate-900 shadow-[3px_3px_0px_#0F172A] font-bold text-xs active:translate-x-[2px] active:translate-y-[2px] active:shadow-none transition-all"
            >
              🔥 Rush Hour Demo
            </button>
            <button
              onClick={() => triggerScenario("stockout", { shelf_id: "shelf-A" })}
              className="px-3.5 py-2 rounded-xl bg-[#E11D48] hover:bg-[#BE123C] text-white border-[2px] border-slate-900 shadow-[3px_3px_0px_#0F172A] font-bold text-xs active:translate-x-[2px] active:translate-y-[2px] active:shadow-none transition-all"
            >
              🥛 Amul Stockout Demo
            </button>
            <button
              onClick={() => triggerScenario("baseline")}
              className="px-3.5 py-2 rounded-xl bg-white hover:bg-slate-50 text-slate-900 border-[2px] border-slate-900 shadow-[3px_3px_0px_#0F172A] font-bold text-xs active:translate-x-[2px] active:translate-y-[2px] active:shadow-none transition-all"
            >
              ↺ Reset Scenario
            </button>
          </div>
        </div>
      </div>

      {/* Soothing Muted Data Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        {/* Card 1: Saved Revenue (Muted Sage) */}
        <div className="bg-[#E2F1E7] border-[2.5px] border-slate-900 shadow-[4px_4px_0px_#0F172A] rounded-2xl p-5 hover:translate-y-[-2px] hover:shadow-[6px_6px_0px_#0F172A] transition-all">
          <div className="inline-block bg-white text-slate-900 font-mono text-[10px] font-bold uppercase px-2 py-0.5 rounded border border-slate-900 shadow-[1px_1px_0px_#0F172A]">
            {t("owner.saved", { defaultValue: "₹ बचाया आज" })}
          </div>
          <div className="text-4xl font-black text-slate-900 mt-2.5 tabular tracking-tight">
            ₹{kpi?.recovered_inr ?? 281}
          </div>
          <p className="text-xs font-semibold text-slate-700 mt-1.5 font-sans">
            Prevented stockout & queue loss
          </p>
        </div>

        {/* Card 2: Risk Amount (Soft Pastel Rose) */}
        <div className="bg-[#FDE8E8] border-[2.5px] border-slate-900 shadow-[4px_4px_0px_#0F172A] rounded-2xl p-5 hover:translate-y-[-2px] hover:shadow-[6px_6px_0px_#0F172A] transition-all">
          <div className="inline-block bg-white text-rose-900 font-mono text-[10px] font-bold uppercase px-2 py-0.5 rounded border border-slate-900 shadow-[1px_1px_0px_#0F172A]">
            {t("owner.lost", { defaultValue: "₹ नुकसान आज" })}
          </div>
          <div className="text-4xl font-black text-rose-900 mt-2.5 tabular tracking-tight">
            ₹{kpi?.lost_sales_inr ?? 173}
          </div>
          <p className="text-xs font-semibold text-slate-700 mt-1.5 font-sans">
            Based on Corsten 0.31 stockout factor
          </p>
        </div>

        {/* Card 3: Footfall (Muted Sky Tint) */}
        <div className="bg-[#E0F2FE] border-[2.5px] border-slate-900 shadow-[4px_4px_0px_#0F172A] rounded-2xl p-5 hover:translate-y-[-2px] hover:shadow-[6px_6px_0px_#0F172A] transition-all">
          <div className="inline-block bg-white text-slate-900 font-mono text-[10px] font-bold uppercase px-2 py-0.5 rounded border border-slate-900 shadow-[1px_1px_0px_#0F172A]">
            {t("kpi.footfall", { defaultValue: "ग्राहक आए" })}
          </div>
          <div className="text-4xl font-black text-slate-900 mt-2.5 tabular tracking-tight flex items-baseline gap-1.5">
            <span>{kpi?.footfall_in ?? 142}</span>
            <span className="text-xs font-bold text-slate-600 font-sans">visitors</span>
          </div>
          <p className="text-xs font-semibold text-slate-700 mt-1.5 font-sans">
            Real-time entrance line crossings
          </p>
        </div>

        {/* Card 4: OSA Shelf Fill (Soft Warm Sand) */}
        <div className="bg-[#FEF3C7] border-[2.5px] border-slate-900 shadow-[4px_4px_0px_#0F172A] rounded-2xl p-5 hover:translate-y-[-2px] hover:shadow-[6px_6px_0px_#0F172A] transition-all">
          <div className="inline-block bg-white text-slate-900 font-mono text-[10px] font-bold uppercase px-2 py-0.5 rounded border border-slate-900 shadow-[1px_1px_0px_#0F172A]">
            {t("kpi.osa", { defaultValue: "शेल्फ भरी" })}
          </div>
          <div className="text-4xl font-black text-slate-900 mt-2.5 tabular tracking-tight">
            {kpi?.osa_pct ?? 94.2}%
          </div>
          <p className="text-xs font-semibold text-slate-700 mt-1.5 font-sans">
            3-scan temporal persistence active
          </p>
        </div>
      </div>

      {/* Main Content Layout Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* WhatsApp Vernacular Alerts Center */}
        <div className="lg:col-span-2 bg-white border-[2.5px] border-slate-900 shadow-[5px_5px_0px_#0F172A] rounded-2xl p-6 space-y-5">
          <div className="flex items-center justify-between border-b border-slate-200 pb-3.5">
            <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
              <span>💬 Live WhatsApp Vernacular Alerts</span>
            </h2>
            <span className="bg-[#E2F1E7] text-slate-900 border border-slate-900 font-mono font-bold text-xs px-2.5 py-0.5 rounded-md shadow-[1px_1px_0px_#0F172A]">
              Meta Cloud API Ready
            </span>
          </div>

          {openAlerts.length === 0 ? (
            <div className="p-6 bg-[#FEF9C3] border border-slate-900 rounded-xl text-slate-900 shadow-[2px_2px_0px_#0F172A] font-medium text-center space-y-1.5">
              <div className="w-9 h-9 rounded-full bg-[#E2F1E7] border border-slate-900 flex items-center justify-center mx-auto text-slate-900 font-extrabold text-base shadow-[1px_1px_0px_#0F172A]">
                ✓
              </div>
              <p className="text-slate-900 text-sm font-semibold">
                सब ठीक है! कोई सक्रिय अलर्ट नहीं (All systems normal. No active alerts).
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {openAlerts.map((alert) => (
                <div
                  key={alert.alert_id}
                  className="bg-[#FAF7F2] border-[2px] border-slate-900 rounded-xl p-4 space-y-3 shadow-[3px_3px_0px_#0F172A]"
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <span className="px-2.5 py-0.5 text-[10px] font-mono font-bold rounded uppercase bg-[#FDE8E8] text-rose-900 border border-slate-900">
                        {alert.kind}
                      </span>
                      <h3 className="text-base font-extrabold text-slate-900 mt-1.5">
                        {alert.rendered_hi ?? alert.rendered_en}
                      </h3>
                    </div>
                    {alert.impact && (
                      <span className="text-xs font-mono font-bold text-slate-900 bg-[#E2F1E7] border border-slate-900 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A]">
                        ₹{alert.impact.lost_sales_inr} at risk
                      </span>
                    )}
                  </div>

                  <p className="text-xs font-mono text-slate-700 bg-white p-2.5 rounded border border-slate-300">
                    Basis: {alert.impact?.basis ?? "Little's Law calculation"}
                  </p>

                  <div className="flex items-center gap-2.5 pt-2 border-t border-slate-200">
                    <button
                      onClick={async () => {
                        setActiveReply(alert.alert_id);
                        await fetch("http://localhost:8001/demo/whatsapp/reply", {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ alert_id: alert.alert_id, reply: "1" }),
                        });
                      }}
                      className="px-3.5 py-2 rounded-lg bg-[#E2F1E7] hover:bg-[#C8E6D0] text-slate-900 border-[2px] border-slate-900 shadow-[2.5px_2.5px_0px_#0F172A] font-bold text-xs active:translate-x-[1px] active:translate-y-[1px] active:shadow-none transition-all"
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
                      className="px-3.5 py-2 rounded-lg bg-white hover:bg-slate-50 text-slate-900 border-[2px] border-slate-900 shadow-[2.5px_2.5px_0px_#0F172A] font-bold text-xs active:translate-x-[1px] active:translate-y-[1px] active:shadow-none transition-all"
                    >
                      3 = गलत अलर्ट (False alert)
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Store Overview Panel */}
        <div className="bg-[#F3E8FF] border-[2.5px] border-slate-900 shadow-[5px_5px_0px_#0F172A] rounded-2xl p-6 space-y-5">
          <h2 className="text-lg font-bold text-slate-900 border-b border-slate-900/40 pb-3 flex items-center justify-between">
            <span>📌 Store Overview</span>
            <span className="bg-white text-slate-900 font-mono text-[11px] px-2 py-0.5 rounded border border-slate-900 font-bold shadow-[1px_1px_0px_#0F172A]">
              ACTIVE
            </span>
          </h2>
          <div className="space-y-3 text-xs font-mono text-slate-900 font-bold">
            <div className="flex justify-between items-center py-2 border-b border-slate-300">
              <span className="text-slate-600 font-sans">Store Name</span>
              <span className="bg-white border border-slate-900 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A] font-sans">
                Ramesh General Store
              </span>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-slate-300">
              <span className="text-slate-600 font-sans">Location</span>
              <span className="bg-white border border-slate-900 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A] font-sans">
                Karol Bagh, Delhi
              </span>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-slate-300">
              <span className="text-slate-600 font-sans">Active Cameras</span>
              <span className="bg-[#E2F1E7] border border-slate-900 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A]">
                2 Streams (RTSP)
              </span>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-slate-300">
              <span className="text-slate-600 font-sans">Inference Device</span>
              <span className="bg-white border border-slate-900 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A] font-sans">
                Raspberry Pi 5 (CPU ONNX)
              </span>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-slate-300">
              <span className="text-slate-600 font-sans">Tally Reconciliation</span>
              <span className="bg-[#FFEDD5] border border-slate-900 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A]">
                Connected (:9000)
              </span>
            </div>
            <div className="flex justify-between items-center py-2">
              <span className="text-slate-600 font-sans">ONDC Catalog Publisher</span>
              <span className="bg-[#E0F2FE] border border-slate-900 px-2 py-0.5 rounded shadow-[1px_1px_0px_#0F172A]">
                Active
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function OpsPage() {
  const { t } = useT();

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <div className="flex items-center justify-between border-b-[2.5px] border-slate-900 pb-3.5">
        <h1 className="text-2xl font-extrabold text-slate-900 tracking-tight">
          Operations Console <span className="text-xs font-mono font-normal text-slate-600">(/ops)</span>
        </h1>
        <span className="bg-[#E2F1E7] text-slate-900 border border-slate-900 px-3 py-1 rounded-lg font-bold text-xs shadow-[2px_2px_0px_#0F172A]">
          Real-time Edge Workers Active
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Active Queue Intelligence */}
        <div className="bg-[#E0F2FE] border-[2.5px] border-slate-900 shadow-[4px_4px_0px_#0F172A] rounded-2xl p-6 space-y-3.5">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-extrabold text-slate-900 tracking-tight">Active Queue Intelligence</h2>
            <span className="bg-white text-slate-900 font-mono text-[10px] px-2 py-0.5 rounded border border-slate-900 font-bold shadow-[1px_1px_0px_#0F172A]">
              Little's Law
            </span>
          </div>
          <p className="text-xs font-sans text-slate-700">Little's Law Wait Time Estimation & Forecasts</p>
          
          <div className="p-4 bg-white rounded-xl border border-slate-900 shadow-[2px_2px_0px_#0F172A] space-y-3 font-mono font-bold text-xs">
            <div className="flex justify-between">
              <span className="text-slate-600 font-sans">Counter 1 Queue Length</span>
              <span className="bg-[#FEF3C7] border border-slate-900 px-2 py-0.5 rounded">4 Persons</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-600 font-sans">Estimated Wait Time</span>
              <span className="bg-[#E2F1E7] border border-slate-900 px-2 py-0.5 rounded">~2.8 Minutes</span>
            </div>
            <div className="flex justify-between pt-2.5 border-t border-slate-200">
              <span className="text-slate-600 font-sans">15-Min Forecast (HGB Model)</span>
              <span className="bg-[#FFEDD5] border border-slate-900 px-2 py-0.5 rounded">↑ 6 Persons (MAE 0.8)</span>
            </div>
          </div>
        </div>

        {/* Active Shelf Monitoring */}
        <div className="bg-[#FEF3C7] border-[2.5px] border-slate-900 shadow-[4px_4px_0px_#0F172A] rounded-2xl p-6 space-y-3.5">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-extrabold text-slate-900 tracking-tight">Active Shelf Monitoring</h2>
            <span className="bg-white text-slate-900 font-mono text-[10px] px-2 py-0.5 rounded border border-slate-900 font-bold shadow-[1px_1px_0px_#0F172A]">
              3-Scan Persistence
            </span>
          </div>
          <p className="text-xs font-sans text-slate-700">Classical Coverage Estimator + 3-Scan Persistence</p>
          
          <div className="p-4 bg-white rounded-xl border border-slate-900 shadow-[2px_2px_0px_#0F172A] space-y-3 font-mono font-bold text-xs">
            <div className="flex justify-between">
              <span className="text-slate-600 font-sans">Shelf A (Amul Milk)</span>
              <span className="bg-[#E2F1E7] border border-slate-900 px-2 py-0.5 rounded">Stocked (92% Coverage)</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-600 font-sans">Shelf B (Biscuits)</span>
              <span className="bg-[#E2F1E7] border border-slate-900 px-2 py-0.5 rounded">Stocked (88% Coverage)</span>
            </div>
            <div className="flex justify-between pt-2.5 border-t border-slate-200">
              <span className="text-slate-600 font-sans">Shelf C (Oil & Ghee)</span>
              <span className="bg-[#E2F1E7] border border-slate-900 px-2 py-0.5 rounded">Stocked (95% Coverage)</span>
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
      <div className="flex items-center justify-between border-b-[2.5px] border-slate-900 pb-3.5">
        <h1 className="text-2xl font-extrabold text-slate-900 tracking-tight">
          Store Insights & Shrink <span className="text-xs font-mono font-normal text-slate-600">(/insights)</span>
        </h1>
        <span className="bg-[#E0F2FE] text-slate-900 border border-slate-900 px-3 py-1 rounded-lg font-bold text-xs shadow-[2px_2px_0px_#0F172A]">
          Tally ERP Synchronized
        </span>
      </div>

      <div className="bg-white border-[2.5px] border-slate-900 shadow-[5px_5px_0px_#0F172A] rounded-2xl p-6 space-y-5">
        <div>
          <h2 className="text-lg font-bold text-slate-900">Visual-vs-Tally Inventory Shrinkage</h2>
          <p className="text-xs font-sans text-slate-600 mt-1">
            Compares physical shelf facings observed by edge cameras against Tally ERP stock summary records.
          </p>
        </div>

        <div className="overflow-x-auto rounded-xl border border-slate-900 shadow-[2px_2px_0px_#0F172A]">
          <table className="w-full text-left text-xs font-mono border-collapse">
            <thead>
              <tr className="bg-slate-900 text-white text-xs font-bold uppercase tracking-wider">
                <th className="py-3 px-4 font-sans">Item Name</th>
                <th className="py-3 px-4">Tally ERP Qty</th>
                <th className="py-3 px-4">Camera Qty</th>
                <th className="py-3 px-4">Discrepancy (Δ)</th>
                <th className="py-3 px-4">Shrink Amount (₹)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 font-bold bg-white text-slate-900">
              <tr className="hover:bg-[#FEF9C3] transition">
                <td className="py-3.5 px-4 font-semibold text-sm font-sans">Amul Taaza 500ml</td>
                <td className="py-3.5 px-4">48</td>
                <td className="py-3.5 px-4">41</td>
                <td className="py-3.5 px-4">
                  <span className="bg-[#FDE8E8] text-rose-900 border border-slate-900 px-2 py-0.5 rounded">
                    -7
                  </span>
                </td>
                <td className="py-3.5 px-4">
                  <span className="bg-[#FDE8E8] text-rose-900 border border-slate-900 px-2 py-0.5 rounded">
                    ₹189
                  </span>
                </td>
              </tr>
              <tr className="hover:bg-[#FEF9C3] transition">
                <td className="py-3.5 px-4 font-semibold text-sm font-sans">Parle-G 70g</td>
                <td className="py-3.5 px-4">120</td>
                <td className="py-3.5 px-4">120</td>
                <td className="py-3.5 px-4">
                  <span className="bg-[#E2F1E7] text-slate-900 border border-slate-900 px-2 py-0.5 rounded">
                    0
                  </span>
                </td>
                <td className="py-3.5 px-4">
                  <span className="bg-[#E2F1E7] text-slate-900 border border-slate-900 px-2 py-0.5 rounded">
                    ₹0
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function ChainPage() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <div className="flex items-center justify-between border-b-[2.5px] border-slate-900 pb-3.5">
        <h1 className="text-2xl font-extrabold text-slate-900 tracking-tight">
          Fleet & Multi-Store Chain View <span className="text-xs font-mono font-normal text-slate-600">(/chain)</span>
        </h1>
        <span className="bg-[#FEF3C7] text-slate-900 border border-slate-900 px-3 py-1 rounded-lg font-bold text-xs shadow-[2px_2px_0px_#0F172A]">
          3 Devices Online
        </span>
      </div>

      <div className="bg-white border-[2.5px] border-slate-900 shadow-[5px_5px_0px_#0F172A] rounded-2xl p-6 space-y-5">
        <h2 className="text-lg font-bold text-slate-900">Edge Fleet Status</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 font-mono">
          <div className="p-4 bg-[#FAF7F2] rounded-xl border border-slate-900 shadow-[3px_3px_0px_#0F172A]">
            <div className="text-xs font-bold text-slate-700 font-sans">EDGE-001 (Karol Bagh, Delhi)</div>
            <div className="mt-2.5 flex items-center justify-between">
              <span className="bg-[#E2F1E7] text-slate-900 border border-slate-900 font-bold px-2.5 py-0.5 text-xs rounded">
                Online (3.99 fps)
              </span>
            </div>
          </div>
          <div className="p-4 bg-[#FAF7F2] rounded-xl border border-slate-900 shadow-[3px_3px_0px_#0F172A]">
            <div className="text-xs font-bold text-slate-700 font-sans">EDGE-002 (Koramangala, BLR)</div>
            <div className="mt-2.5 flex items-center justify-between">
              <span className="bg-[#E2F1E7] text-slate-900 border border-slate-900 font-bold px-2.5 py-0.5 text-xs rounded">
                Online (4.12 fps)
              </span>
            </div>
          </div>
          <div className="p-4 bg-[#FAF7F2] rounded-xl border border-slate-900 shadow-[3px_3px_0px_#0F172A]">
            <div className="text-xs font-bold text-slate-700 font-sans">EDGE-003 (Andheri, Mumbai)</div>
            <div className="mt-2.5 flex items-center justify-between">
              <span className="bg-[#E2F1E7] text-slate-900 border border-slate-900 font-bold px-2.5 py-0.5 text-xs rounded">
                Online (3.95 fps)
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ZonesPage() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <div className="flex items-center justify-between border-b-[2.5px] border-slate-900 pb-3.5">
        <h1 className="text-2xl font-extrabold text-slate-900 tracking-tight">
          Zone & Camera Calibration <span className="text-xs font-mono font-normal text-slate-600">(/zones)</span>
        </h1>
        <span className="bg-[#FFEDD5] text-slate-900 border border-slate-900 px-3 py-1 rounded-lg font-bold text-xs shadow-[2px_2px_0px_#0F172A]">
          Interactive Polygon Editor
        </span>
      </div>

      <div className="bg-[#FEF3C7] border-[2.5px] border-slate-900 shadow-[5px_5px_0px_#0F172A] rounded-2xl p-6 text-slate-900 space-y-2 font-mono font-bold">
        <p className="text-sm font-extrabold uppercase">Zero-Hardware Polygon Zone Editor for RTSP Camera Streams.</p>
        <p className="text-xs text-slate-700 font-sans font-normal">
          Draw entrance lines, counter polygons, and shelf regions over the live CCTV preview.
        </p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-[#FAF7F2] text-slate-900 font-sans antialiased selection:bg-[#FEF08A] selection:text-slate-900">
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
