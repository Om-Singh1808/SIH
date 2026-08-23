"""THE rupee formula and the Hindi/English templates."""

import pytest

from retailsense_contracts.alerts import ACTIONS_BY_KIND, AlertKind, ImpactInr
from retailsense_contracts.enums import AckAction, Lang
from retailsense_contracts.i18n import TEMPLATES, action_label, fmt_inr, fmt_num, render
from retailsense_contracts.impact import (
    ImpactConfig,
    lost_sales,
    queue_abandon_risk,
    rate_per_hour,
    recovered,
    zero_impact,
)
from retailsense_contracts.testing import sample_alert


def test_impact_formula(cfg):
    amul = cfg.sku("AMUL-TAAZA-500")
    cfgi = cfg.impact
    imp = lost_sales(amul, 20, cfgi)
    assert isinstance(imp, ImpactInr)
    assert imp.lost_sales_inr == pytest.approx(27 * 18 * (20 / 60) * 0.31, abs=0.005)
    assert imp.lost_sales_inr == 50.22
    assert imp.lost_margin_inr == pytest.approx(50.22 * 0.08, abs=0.01)
    assert "0.31" in imp.basis and "27" in imp.basis and "18" in imp.basis
    assert imp.factor == 0.31 and "Gruen" in imp.source
    assert rate_per_hour(amul, cfgi) == pytest.approx(27 * 18 * 0.31)
    # editable factor flows through
    custom = ImpactConfig(lost_sale_factor=0.5)
    assert lost_sales(amul, 60, custom).lost_sales_inr == pytest.approx(27 * 18 * 0.5, abs=0.01)
    assert "0.5" in lost_sales(amul, 60, custom).basis
    # negative gaps are clamped
    assert lost_sales(amul, -5, cfgi).lost_sales_inr == 0.0


def test_recovered_and_queue_risk(cfg):
    amul = cfg.sku("AMUL-TAAZA-500")
    rec = recovered(amul, 20, cfg.impact)  # baseline 120 min unattended
    assert rec.lost_sales_inr == pytest.approx(27 * 18 * 0.31 * (100 / 60), abs=0.01)
    assert recovered(amul, 500, cfg.impact).lost_sales_inr == 0.0
    risk = queue_abandon_risk(6, 4, cfg.impact)
    assert risk.lost_sales_inr == pytest.approx(3 * 0.32 * 180)
    assert risk.factor == 0.32 and "0.32" in risk.basis and "180" in risk.basis
    assert queue_abandon_risk(2, 4, cfg.impact).lost_sales_inr == 0.0
    z = zero_impact(cfg.impact)
    assert z.lost_sales_inr == 0.0 and z.basis


def test_fmt_inr_indian_grouping():
    assert fmt_inr(1234567.8) == "12,34,568"
    assert fmt_inr(50.22) == "50"
    assert fmt_inr(999) == "999"
    assert fmt_inr(1000) == "1,000"
    assert fmt_inr(100000) == "1,00,000"
    assert fmt_inr(12345678901) == "12,34,56,78,901"
    assert fmt_inr(-1234) == "-1,234"
    assert fmt_inr(None) == "?" and fmt_inr(float("nan")) == "?" and fmt_inr("x") == "?"
    assert fmt_num(3.0) == "3" and fmt_num(3.47) == "3.5" and fmt_num(7) == "7" and fmt_num("a") == "a"


def test_i18n_all_keys_both_langs():
    assert len(TEMPLATES) >= 24
    for key, entry in TEMPLATES.items():
        assert set(entry) == {Lang.HI, Lang.EN}, key
        assert entry[Lang.HI].strip() and entry[Lang.EN].strip(), key
    for kind in AlertKind:
        assert f"{kind}.title" in TEMPLATES and f"{kind}.msg" in TEMPLATES
    for action in AckAction:
        assert f"action.{action}" in TEMPLATES
    # every message with a digit menu lists as many digits as ACTIONS_BY_KIND has actions
    for kind, actions in ACTIONS_BY_KIND.items():
        if actions:
            msg = TEMPLATES[f"{kind}.msg"][Lang.EN]
            for i in range(1, len(actions) + 1):
                assert f"{i} =" in msg, (kind, i)
    # Hindi strings really are Devanagari
    assert "शेल्फ" in TEMPLATES["shelf_gap.title"][Lang.HI]  # "शेल्फ"


def test_render_never_raises():
    s = render("shelf_gap.msg", "hi")  # no params at all
    assert "?" in s and "₹?" in s
    s = render(
        "shelf_gap.msg",
        Lang.EN,
        sku_name="Amul Taaza 500ml",
        gap_min=3.0,
        lost_inr=50.22,
        basis="₹27 × 18/hr × 0.33 h × 0.31",
    )
    assert (
        "Amul Taaza 500ml shelf has been empty for 3 min" in s and "₹50" in s and "0.31" in s and "1 = restocked" in s
    )
    assert render("no.such.key", "hi", x=1) == "no.such.key"
    assert render("shelf_gap.title", "xx", sku_name="A") == "A shelf empty"  # unknown lang -> en
    assert (
        render("queue_long.msg", "hi", count=5, counter_name="Main", wait_min=3.75, risk_inr=1234567).count("12,34,567")
        == 1
    )
    assert render("device_offline.msg", "en", device_id=None, since=None) == "🔌 ? offline since ?."
    assert action_label(AckAction.RESTOCKED, "hi") == "भर दिया" and action_label("order", Lang.EN) == "Order"


@pytest.mark.parametrize("kind", list(AlertKind))
def test_sample_alert_rendered_both_langs(kind):
    a = sample_alert(kind)
    assert a.kind == kind and a.title_hi and a.title_en and a.message_hi and a.message_en
    assert "?" not in a.message_en.replace("Open a second counter?", "").replace("तैयार", "")
    assert a.actions == ACTIONS_BY_KIND[kind]
    if kind in (AlertKind.SHELF_GAP, AlertKind.QUEUE_LONG):
        assert a.impact is not None and a.impact.lost_sales_inr > 0
        assert "₹" in a.message_hi and "₹" in a.message_en
    for i, action in enumerate(a.actions, start=1):
        assert a.action_for_digit(i) == action
    assert a.action_for_digit(9) is None
