"""Hindi / English templates for alerts, WhatsApp messages and the daily summary.

Rendering happens on the edge at alert time (so messages work offline) and
again on the cloud for the daily report.  ``render()`` never raises: a missing
parameter renders as ``"?"`` rather than crashing a rule engine at 2 am.

Number formatting: any parameter whose name ends in ``_inr`` is formatted with
Indian digit grouping (``12,34,568``); other floats are shown with at most one
decimal (``3.5``) and as integers when whole (``3``).
"""

import math
from typing import Any

from .enums import AckAction, Lang

TEMPLATES: dict[str, dict[Lang, str]] = {
    "shelf_gap.title": {Lang.EN: "{sku_name} shelf empty", Lang.HI: "{sku_name} की शेल्फ खाली"},
    "shelf_gap.msg": {
        Lang.EN: "⚠️ {sku_name} shelf has been empty for {gap_min} min. Est. lost sales ₹{lost_inr} ({basis}). Reply 1 = restocked, 2 = order from distributor, 3 = false alert",
        Lang.HI: "⚠️ {sku_name} की शेल्फ {gap_min} मिनट से खाली है। अनुमानित नुकसान ₹{lost_inr}। जवाब दें: 1 = भर दिया, 2 = डिस्ट्रीब्यूटर को ऑर्डर, 3 = गलत अलर्ट",
    },
    "queue_long.title": {Lang.EN: "Long queue at {counter_name}", Lang.HI: "{counter_name} पर लंबी लाइन"},
    "queue_long.msg": {
        Lang.EN: "🧾 {count} customers waiting at {counter_name} (~{wait_min} min). Risk ₹{risk_inr}. Open a second counter? Reply 1 = opened, 2 = ignore",
        Lang.HI: "🧾 {counter_name} पर {count} ग्राहक लाइन में (~{wait_min} मिनट)। जोखिम ₹{risk_inr}। दूसरा काउंटर खोलें? जवाब: 1 = खोल दिया, 2 = रहने दो",
    },
    "queue_forecast.title": {Lang.EN: "Queue build-up expected", Lang.HI: "लाइन बढ़ने वाली है"},
    "queue_forecast.msg": {
        Lang.EN: "⏱️ Queue at {counter_name} expected to reach {forecast} in {horizon} min. Get a second counter ready. Reply 1 = ready, 2 = ignore",
        Lang.HI: "⏱️ {horizon} मिनट में {counter_name} पर लाइन {forecast} तक पहुँच सकती है। दूसरा काउंटर तैयार रखें। जवाब: 1 = तैयार, 2 = रहने दो",
    },
    "camera_down.title": {Lang.EN: "Camera {camera_id} down", Lang.HI: "कैमरा {camera_id} बंद"},
    "camera_down.msg": {
        Lang.EN: "📷 Camera {camera_id} is not sending frames. Check power/cable. Reply 1 = checked",
        Lang.HI: "📷 कैमरा {camera_id} से वीडियो नहीं आ रहा। बिजली/केबल जाँचें। जवाब: 1 = देख लिया",
    },
    "sync_backlog.title": {Lang.EN: "Internet down, data safe", Lang.HI: "इंटरनेट बंद, डेटा सुरक्षित"},
    "sync_backlog.msg": {
        Lang.EN: "📡 Internet down for {minutes} min; {backlog} records saved locally, nothing lost.",
        Lang.HI: "📡 इंटरनेट {minutes} मिनट से बंद; {backlog} रिकॉर्ड लोकल सेव हैं, कुछ नहीं खोया।",
    },
    "device_offline.title": {Lang.EN: "Edge device offline", Lang.HI: "एज डिवाइस ऑफ़लाइन"},
    "device_offline.msg": {
        Lang.EN: "🔌 {device_id} offline since {since}.",
        Lang.HI: "🔌 {device_id} {since} से ऑफ़लाइन है।",
    },
    "shrink_suspect.title": {Lang.EN: "Stock mismatch: {sku_name}", Lang.HI: "स्टॉक में अंतर: {sku_name}"},
    "shrink_suspect.msg": {
        Lang.EN: "🔎 {sku_name}: system shows {system_units}, shelf shows {visual_units}. Gap worth ₹{delta_inr}. Reply 1 = investigate, 2 = false alert",
        Lang.HI: "🔎 {sku_name}: सिस्टम में {system_units} यूनिट, शेल्फ पर {visual_units}। ₹{delta_inr} का अंतर। जवाब: 1 = जाँच करो, 2 = गलत अलर्ट",
    },
    "footfall_spike.title": {Lang.EN: "Footfall spike", Lang.HI: "भीड़ बढ़ी"},
    "footfall_spike.msg": {
        Lang.EN: "📈 {count} visitors in the last 15 min ({factor}x usual).",
        Lang.HI: "📈 पिछले 15 मिनट में {count} ग्राहक (सामान्य से {factor}x)।",
    },
    "daily_summary.msg": {
        Lang.EN: "📊 {store_name} {date}: {footfall_in} visitors, {visual_transactions} billed ({conversion_pct}%), OSA {osa_pct}%, avg wait {avg_wait_min} min, lost ₹{lost_inr}, saved ₹{recovered_inr}. Tomorrow's order: {order_lines}",
        Lang.HI: "📊 {store_name} {date}: {footfall_in} ग्राहक आए, {visual_transactions} बिल ({conversion_pct}%), OSA {osa_pct}%, औसत इंतज़ार {avg_wait_min} मिनट, नुकसान ₹{lost_inr}, बचाया ₹{recovered_inr}। कल का ऑर्डर: {order_lines}",
    },
    "action.restocked": {Lang.EN: "Restocked", Lang.HI: "भर दिया"},
    "action.order": {Lang.EN: "Order", Lang.HI: "ऑर्डर करो"},
    "action.false_positive": {Lang.EN: "False alert", Lang.HI: "गलत अलर्ट"},
    "action.opened_counter": {Lang.EN: "Opened counter", Lang.HI: "काउंटर खोल दिया"},
    "action.ignore": {Lang.EN: "Ignore", Lang.HI: "रहने दो"},
    "action.checked": {Lang.EN: "Checked", Lang.HI: "देख लिया"},
    "action.investigate": {Lang.EN: "Investigate", Lang.HI: "जाँच करो"},
}


def fmt_inr(x: float | int | None) -> str:
    """Indian digit grouping of the rounded amount: 1234567.8 -> '12,34,568'. No ₹ sign."""
    if x is None:
        return "?"
    try:
        value = float(x)
    except (TypeError, ValueError):
        return "?"
    if math.isnan(value) or math.isinf(value):
        return "?"
    n = int(round(abs(value)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return ("-" if value < 0 else "") + s


def fmt_num(v: Any) -> str:
    """Compact number for message text: 3.0 -> '3', 3.47 -> '3.5', other types -> str()."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return "?"
        if float(v).is_integer():
            return str(int(v))
        return f"{v:.1f}"
    return str(v)


class _SafeParams(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def _lang(lang: Lang | str) -> Lang:
    try:
        return Lang(str(lang))
    except ValueError:
        return Lang.EN


def _format_params(params: dict[str, Any]) -> _SafeParams:
    out = _SafeParams()
    for k, v in params.items():
        if v is None:
            out[k] = "?"
        elif k.endswith("_inr") and isinstance(v, int | float):
            out[k] = fmt_inr(v)
        else:
            out[k] = fmt_num(v)
    return out


def render(key: str, lang: Lang | str, **params: Any) -> str:
    """Render ``TEMPLATES[key][lang]`` with ``params``. Never raises; missing -> '?'."""
    lang_ = _lang(lang)
    entry = TEMPLATES.get(key)
    if entry is None:
        return key
    template = entry.get(lang_) or entry.get(Lang.EN) or next(iter(entry.values()), key)
    try:
        return template.format_map(_format_params(params))
    except Exception:  # malformed template or exotic format spec: degrade, never crash
        out = template
        for k, v in _format_params(params).items():
            out = out.replace("{" + k + "}", v)
        return out


def action_label(action: AckAction | str, lang: Lang | str) -> str:
    """Button label for an AckAction in the given language."""
    return render(f"action.{str(action)}", lang)


def action_labels(actions: list[AckAction], lang: Lang | str) -> list[str]:
    return [action_label(a, lang) for a in actions]


def has_key(key: str) -> bool:
    return key in TEMPLATES


__all__ = ["TEMPLATES", "action_label", "action_labels", "fmt_inr", "fmt_num", "has_key", "render"]
