"""ImpactCalculator wrapper, rules_default.yaml loader and bilingual rendering."""

import pytest
from retailsense_contracts.config import RulesConfig
from retailsense_contracts.enums import AlertKind
from retailsense_contracts.impact import lost_sales, queue_abandon_risk

from retailsense_edgerules import ImpactCalculator, load_rules_yaml, open_hours_per_day, render_alert, rules_default_path


def test_impact_calculator_matches_contracts(cfg):
    calc = ImpactCalculator(cfg)
    sku = cfg.sku("AMUL-TAAZA-500")
    assert calc.lost_sales("AMUL-TAAZA-500", 30) == lost_sales(sku, 30, cfg.impact)
    assert calc.queue_abandon_risk(6, 4) == queue_abandon_risk(6, 4, cfg.impact)
    assert calc.rate_per_hour("AMUL-TAAZA-500") == pytest.approx(27 * 18 * 0.31)
    assert calc.rate_per_hour("NOPE") == 0.0
    z = calc.lost_sales("NOPE", 30)
    assert z.lost_sales_inr == 0.0 and "no SKU" in z.basis
    assert calc.recovered("NOPE", 5) is None
    assert calc.recovered("AMUL-TAAZA-500", 5).lost_sales_inr > 0


def test_impact_calculator_atv_from_tally(cfg):
    calc = ImpactCalculator(cfg)
    base = calc.queue_abandon_risk(6, 4).lost_sales_inr
    assert calc.set_atv_from_sales(sales_inr=0, transactions=0) is None  # no bills yet -> keep config
    assert calc.atv_inr == cfg.impact.atv_inr
    assert calc.set_atv_from_sales(sales_inr=36_000, transactions=100) == 360.0
    live = calc.queue_abandon_risk(6, 4)
    assert live.lost_sales_inr == pytest.approx(base * 2)
    assert "360" in live.basis
    calc.set_atv(None)
    assert calc.queue_abandon_risk(6, 4).lost_sales_inr == base
    with pytest.raises(ValueError):
        calc.set_atv(-1)
    assert cfg.impact.atv_inr == 180.0  # the store config is never mutated


def test_suggest_order_qty(cfg):
    calc = ImpactCalculator(cfg)
    assert open_hours_per_day(cfg) == 14.0
    assert calc.suggest_order_qty("AMUL-TAAZA-500") == 252  # 18 x 14 x 1
    assert calc.suggest_order_qty("FORTUNE-OIL-1L") == 84  # 2 x 14 x 3 = 84, units_per_facing 2
    assert calc.suggest_order_qty("PARLE-G-70") == 224  # 8 x 14 x 2
    assert calc.suggest_order_qty(None) == 0 and calc.order_cost_inr(None, 5) is None
    assert calc.order_cost_inr("PARLE-G-70", 10) == pytest.approx(10 * 10 * 0.88)


def test_open_hours_overnight_and_bad_values(cfg):
    night = cfg.model_copy(update={"store": cfg.store.model_copy(update={"open_hours": ("20:00", "02:00")})})
    assert open_hours_per_day(night) == 6.0
    bad = cfg.model_copy(update={"store": cfg.store.model_copy(update={"open_hours": ("x", "y")})})
    assert open_hours_per_day(bad) == 14.0


def test_rules_default_yaml_mirrors_rulesconfig():
    path = rules_default_path()
    assert path.exists()
    loaded = load_rules_yaml(path)
    assert loaded == RulesConfig()
    assert load_rules_yaml() == RulesConfig()
    text = path.read_text(encoding="utf-8")
    for field in RulesConfig.model_fields:
        assert f"\n{field}:" in text, f"{field} missing from rules_default.yaml"
    assert text.count("#") >= len(RulesConfig.model_fields)  # every rule is commented


def test_load_rules_yaml_store_file_and_errors(tmp_path):
    p = tmp_path / "store.yaml"
    p.write_text("store: {x: 1}\nrules: {queue_long_count: 7}\n", encoding="utf-8")
    assert load_rules_yaml(p).queue_long_count == 7
    flat = tmp_path / "flat.yaml"
    flat.write_text("queue_long_s: 90\n", encoding="utf-8")
    assert load_rules_yaml(flat).queue_long_s == 90
    typo = tmp_path / "typo.yaml"
    typo.write_text("queue_long_cnt: 7\n", encoding="utf-8")
    with pytest.raises(Exception):
        load_rules_yaml(typo)
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert load_rules_yaml(empty) == RulesConfig()
    bad = tmp_path / "list.yaml"
    bad.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules_yaml(bad)


def test_render_alert_bilingual():
    t = render_alert(AlertKind.SHELF_GAP, {"sku_name": "Parle-G", "gap_min": 12, "lost_inr": 1234.5, "basis": "b"}, {"sku_name": "पारले-जी", "gap_min": 12, "lost_inr": 1234.5})
    assert t.title_en == "Parle-G shelf empty"
    assert "पारले-जी" in t.title_hi and "Parle-G" not in t.title_hi
    assert "₹1,235" in t.message_en and "₹1,235" in t.message_hi
    same = render_alert(AlertKind.CAMERA_DOWN, {"camera_id": "cam-1"})
    assert "cam-1" in same.message_en and "cam-1" in same.message_hi
