"""有氧心率分析、苹果健康导入、数据新鲜度的测试。"""

from __future__ import annotations

import json
import zipfile

import pytest

from health_assistant.analytics import cardio as C


@pytest.fixture(autouse=True)
def _profile(tmp_path, monkeypatch):
    """固定生理参数，让心率区间可预测。"""
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"birth_year": 1993, "height_cm": 179, "sex": "male"}),
                 encoding="utf-8")
    monkeypatch.setattr(C, "PROFILE_PATH", p)
    C._profile.cache_clear()
    C._zones_config.cache_clear()
    yield
    C._profile.cache_clear()


class TestHRMax:
    def test_tanaka_formula(self, monkeypatch):
        """Tanaka（208 − 0.7×年龄）对成年人比 220−年龄 更准。"""
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        hm, src = C.hr_max()
        assert hm == 185
        assert "Tanaka" in src

    def test_measured_overrides_formula(self, tmp_path, monkeypatch):
        p = tmp_path / "p.json"
        p.write_text(json.dumps({"birth_year": 1993, "hr_max_override": 192}),
                     encoding="utf-8")
        monkeypatch.setattr(C, "PROFILE_PATH", p)
        C._profile.cache_clear()
        hm, src = C.hr_max()
        assert hm == 192 and src == "实测"

    def test_missing_birth_year(self, tmp_path, monkeypatch):
        p = tmp_path / "p.json"
        p.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(C, "PROFILE_PATH", p)
        C._profile.cache_clear()
        assert C.hr_max()[0] is None


class TestZones:
    @pytest.mark.parametrize("bpm,expect", [
        (100, "Z1"), (121, "Z2"), (140, "Z3"), (153, "Z4"), (175, "Z5"),
    ])
    def test_zone_classification(self, bpm, expect, monkeypatch):
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        z, _ = C.zone_of(bpm)
        assert z.startswith(expect)

    def test_zone_table_capped_at_hrmax(self, monkeypatch):
        """Z5 上界不能超过最大心率本身。"""
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        table = C.zone_table()
        assert table[-1][2] == 185

    def test_none_bpm(self):
        assert C.zone_of(None) is None


class TestBouts:
    RAW = [{
        "date": "2026-08-04", "movements": [{
            "name": "爬楼梯", "exetype": "cardio",
            "sets": [{"done": True,
                      "hr": {"avg": 153.0, "max": 174.0, "step_s": 18.0,
                             "values": [150] * 47},
                      "metrics": {"kcal": 168.0}}]}]}]

    def test_extracts_cardio_only(self, monkeypatch):
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        raw = self.RAW + [{"date": "2026-08-03", "movements": [
            {"name": "杠铃卧推", "exetype": None, "sets": [{"done": True}]}]}]
        bouts = C.extract_bouts([], raw)
        assert len(bouts) == 1 and bouts[0].name == "爬楼梯"

    def test_duration_from_hr_buckets(self, monkeypatch):
        """没有 time_s 时用心率分桶数 × 步长推算时长。"""
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        b = C.extract_bouts([], self.RAW)[0]
        assert b.minutes == pytest.approx(47 * 18 / 60, abs=0.1)

    def test_high_intensity_flag(self, monkeypatch):
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        b = C.extract_bouts([], self.RAW)[0]
        assert b.is_high_intensity          # 153/185 = 83%
        assert b.zone.startswith("Z4")


class TestWeeklyEvaluation:
    def _bout(self, date, minutes, avg):
        z = C.zone_of(avg)
        return C.CardioBout(date=date, name="x", minutes=minutes, avg_hr=avg,
                            max_hr=None, kcal=None,
                            zone=z[0] if z else None, pct_hrmax=z[1] if z else None)

    def test_window_scales_weekly_target(self, monkeypatch):
        """周目标要按窗口天数缩放，否则 14 天窗口拿周目标比会误判。"""
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        bouts = [self._bout("2026-08-01", 100, 120)]
        wk = C.summarize(bouts, "2026-08-04", window_days=14)
        findings = C.evaluate(wk, bouts)
        # 14 天折算目标是 120–300 分钟，100 分钟应当判为偏少
        assert any("低于建议区间" in f["text"] for f in findings)

    def test_high_intensity_cap(self, monkeypatch):
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        bouts = [self._bout("2026-08-01", 50, 160), self._bout("2026-08-02", 50, 160)]
        wk = C.summarize(bouts, "2026-08-04", window_days=7)
        assert any(f["kind"] == "warn" for f in C.evaluate(wk, bouts))

    def test_no_cardio(self, monkeypatch):
        monkeypatch.setattr(C, "age_now", lambda today=None: 33)
        wk = C.summarize([], "2026-08-04")
        assert C.evaluate(wk, [])[0]["kind"] == "info"


class TestAppleHealthImport:
    XML = """<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="zh_CN">
 <Record type="HKQuantityTypeIdentifierBodyMass" startDate="2026-08-02 08:10:00 +0800" endDate="2026-08-02 08:10:00 +0800" value="80.4" unit="kg"/>
 <Record type="HKQuantityTypeIdentifierBodyMass" startDate="2026-08-02 21:00:00 +0800" endDate="2026-08-02 21:00:00 +0800" value="81.2" unit="kg"/>
 <Record type="HKQuantityTypeIdentifierNumberOfAlcoholicBeverages" startDate="2026-07-28 20:00:00 +0800" endDate="2026-07-28 20:00:00 +0800" value="3"/>
 <Record type="HKQuantityTypeIdentifierNumberOfAlcoholicBeverages" startDate="2026-07-28 22:00:00 +0800" endDate="2026-07-28 22:00:00 +0800" value="3"/>
 <Record type="HKQuantityTypeIdentifierStepCount" startDate="2026-08-03 09:00:00 +0800" endDate="2026-08-03 10:00:00 +0800" value="3200"/>
 <Record type="HKQuantityTypeIdentifierStepCount" startDate="2026-08-03 18:00:00 +0800" endDate="2026-08-03 19:00:00 +0800" value="4100"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" value="HKCategoryValueSleepAnalysisAsleepCore" startDate="2026-08-02 23:40:00 +0800" endDate="2026-08-03 03:00:00 +0800"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" value="HKCategoryValueSleepAnalysisAsleepREM" startDate="2026-08-03 03:00:00 +0800" endDate="2026-08-03 06:30:00 +0800"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" value="HKCategoryValueSleepAnalysisAwake" startDate="2026-08-03 06:30:00 +0800" endDate="2026-08-03 07:00:00 +0800"/>
</HealthData>
"""

    @pytest.fixture
    def xml_file(self, tmp_path):
        p = tmp_path / "导出.xml"
        p.write_text(self.XML, encoding="utf-8")
        return p

    def test_weight_takes_morning_reading(self, xml_file):
        """晚上称会比晨起高 1kg 以上，必须取当天最早那条。"""
        from health_assistant.apple_health import parse
        d = parse(xml_file, log=lambda *a: None)
        assert d["weight"]["2026-08-02"] == 80.4

    def test_alcohol_summed_per_day(self, xml_file):
        from health_assistant.apple_health import parse
        assert parse(xml_file, log=lambda *a: None)["alcohol"]["2026-07-28"] == 6.0

    def test_steps_summed(self, xml_file):
        from health_assistant.apple_health import parse
        assert parse(xml_file, log=lambda *a: None)["steps"]["2026-08-03"] == 7300

    def test_sleep_excludes_awake_and_lands_on_wake_day(self, xml_file):
        from health_assistant.apple_health import parse
        d = parse(xml_file, log=lambda *a: None)
        # 3:20 + 3:30 = 6.83h，清醒段不计；跨夜归到起床那天
        assert d["sleep_h"]["2026-08-03"] == pytest.approx(6.83, abs=0.02)
        assert "2026-08-02" not in d["sleep_h"]

    def test_reads_zip(self, tmp_path, xml_file):
        from health_assistant.apple_health import parse
        z = tmp_path / "导出.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.write(xml_file, "apple_health_export/导出.xml")
        assert parse(z, log=lambda *a: None)["weight"]["2026-08-02"] == 80.4

    def test_since_filter(self, xml_file):
        from health_assistant.apple_health import parse
        d = parse(xml_file, since="2026-08-01", log=lambda *a: None)
        assert "alcohol" not in d          # 7/28 早于 since
        assert "weight" in d

    def test_english_export_name(self, tmp_path):
        from health_assistant.apple_health import parse
        p = tmp_path / "export.xml"
        p.write_text(self.XML, encoding="utf-8")
        assert parse(p, log=lambda *a: None)["weight"]


class TestFreshness:
    def test_reports_missing_recent_days(self, monkeypatch, tmp_path):
        import datetime as dt

        from health_assistant import autosync, store
        monkeypatch.setattr(store, "load_index",
                            lambda year: {"2026-08-01": {"status": "ok"}})
        monkeypatch.setattr(store, "load_sessions", lambda *a, **k: [])
        monkeypatch.setattr(store, "load_body", lambda *a, **k: [])
        f = autosync.freshness(today=dt.date(2026, 8, 4))
        assert "2026-08-04" in f["missing_recent"]
        assert "2026-08-01" not in f["missing_recent"]

    def test_estimate_matches_rate_limit(self):
        from health_assistant.autosync import estimate_minutes
        # 30 秒限频 + 1 秒余量
        assert estimate_minutes(30) == pytest.approx(15.5, abs=0.5)
