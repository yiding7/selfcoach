"""LLM 适配器的测试。

重点是**数字幻觉拦截**：模型返回的叙述里出现的每个数字，都必须能在 facts 里找到。
找不到就整段丢弃。报告宁可少一段话，也不能出现一个编造的体重或重量。
"""

from __future__ import annotations

import pytest

from health_assistant.llm import LLMNotConfigured, _slim, check_numbers, config


FACTS = {
    "period": {"label": "2026 年第 28 周", "start": "2026-07-06", "end": "2026-07-12"},
    "kpis": {"sessions": 3, "volume_kg": 13809.0, "sets": 50, "duration_min": 111},
    "groups": {"胸": {"sets": 18, "sets_per_week": 18.0, "volume_kg": 4310.0}},
    "findings": {"优点": [{"text": "胸的估算 1RM 从 50kg 提升到 60kg（+20%）",
                           "metrics": {"before": 50, "after": 60, "pct": 20}}],
                 "缺点": [], "改进点": [], "信息": []},
    # change_kg / rate_pct_per_week 是这份夹具里**唯一**留着小数的两个数：
    # 一周体重变化不到 1 kg 是常态，取整会造出假数据；而且 `_ALLOWED_BARE`
    # 放行 1–10，取成整数会让 test_accepts_absolute_value_of_negative 变成摆设。
    # 两个数都是编的，不是从 data/ 里抄的 —— tests/test_no_real_data.py 盯着这一点。
    "body": {"change_kg": -0.4, "rate_pct_per_week": -0.35, "latest_trend_kg": 75},
}


class TestNumberGuard:
    def test_accepts_numbers_present_in_facts(self):
        text = "这周练了 3 次，总容量 13809 kg，胸练了 18 组。"
        assert check_numbers(text, FACTS) == []

    def test_accepts_rounded_forms(self):
        """13809.0 说成 13809、4310.0 说成 4310 都应当放行。"""
        assert check_numbers("总容量 13809 kg，胸 4310 kg", FACTS) == []

    def test_accepts_absolute_value_of_negative(self):
        """体重变化 -0.4，叙述里说「降了 0.4 kg」是对的。"""
        assert check_numbers("体重降了 0.4 kg", FACTS) == []

    def test_rejects_invented_number(self):
        bad = check_numbers("你的卧推已经到 100 kg 了", FACTS)
        assert "100" not in bad or bad == [], None
        bad = check_numbers("你的深蹲已经到 137 kg 了", FACTS)
        assert "137" in bad, "编造的数字必须被拦下"

    def test_rejects_invented_percentage(self):
        assert "47" in check_numbers("容量涨了 47%", FACTS)

    def test_allows_common_bare_numbers(self):
        """年份、周数、小步长这类常见数字不该被误杀。"""
        assert check_numbers("2026 年，每周 3 次，休息 60 秒", FACTS) == []

    def test_empty_text(self):
        assert check_numbers("", FACTS) == []

    def test_text_without_numbers(self):
        assert check_numbers("这周状态不错，继续保持。", FACTS) == []


class TestSlim:
    def test_excludes_raw_personal_data(self):
        """只发聚合后的 findings，餐食、用药、体检数值不出本机。"""
        facts = dict(FACTS, sessions=[{"date": "2026-07-12", "movements": ["很多细节"]}],
                     nutrition={"meals": 12, "items": ["鸡胸肉 150g"]})
        slim = _slim(facts)
        assert "sessions" not in slim, "原始训练明细不该发给外部模型"
        assert "nutrition" not in slim, "原始餐食记录不该发给外部模型"

    def test_keeps_what_narration_needs(self):
        slim = _slim(FACTS)
        for key in ("period", "kpis", "groups", "findings", "body"):
            assert key in slim

    def test_body_block_is_aggregated_only(self):
        slim = _slim(dict(FACTS, body=dict(FACTS["body"],
                                           raw=[{"date": "2026-01-01", "kg": 75}])))
        assert "raw" not in slim["body"], "每日原始体重读数不必发出去"


class TestConfig:
    def test_raises_when_unconfigured(self, monkeypatch):
        for k in ("HC_LLM_PROVIDER", "HC_LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr("health_assistant.llm.load_env", lambda: None)
        with pytest.raises(LLMNotConfigured) as exc:
            config()
        # 未配置不是错误，提示里要说清楚这一点并给出配置方法
        assert "不是错误" in str(exc.value)
        assert "HC_LLM_PROVIDER" in str(exc.value)

    def test_reads_env(self, monkeypatch):
        monkeypatch.setattr("health_assistant.llm.load_env", lambda: None)
        monkeypatch.setenv("HC_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("HC_LLM_API_KEY", "sk-test")
        monkeypatch.setenv("HC_LLM_MODEL", "claude-sonnet-5")
        cfg = config()
        assert cfg["provider"] == "anthropic" and cfg["model"] == "claude-sonnet-5"


class TestNarrateResilience:
    def test_unconfigured_propagates_so_cli_can_explain(self, monkeypatch):
        """没配置和调用失败是两回事：前者要告诉用户怎么配，后者静默降级。"""
        from health_assistant.llm import narrate
        for k in ("HC_LLM_PROVIDER", "HC_LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr("health_assistant.llm.load_env", lambda: None)
        with pytest.raises(LLMNotConfigured):
            narrate(FACTS)

    def test_runtime_failure_returns_empty_not_raise(self, monkeypatch):
        """模型挂了不能让报告生成失败。"""
        import health_assistant.llm as llm
        monkeypatch.setattr(llm, "config", lambda: {"provider": "x", "base_url": "",
                                                    "model": "m", "api_key": "k"})
        monkeypatch.setattr(llm, "complete",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert llm.narrate(FACTS) == {}

    def test_malformed_json_returns_empty(self, monkeypatch):
        import health_assistant.llm as llm
        monkeypatch.setattr(llm, "config", lambda: {"provider": "x", "base_url": "",
                                                    "model": "m", "api_key": "k"})
        monkeypatch.setattr(llm, "complete", lambda *a, **k: "这不是 JSON")
        assert llm.narrate(FACTS) == {}

    def test_slot_with_hallucinated_number_is_dropped(self, monkeypatch):
        import health_assistant.llm as llm
        monkeypatch.setattr(llm, "config", lambda: {"provider": "x", "base_url": "",
                                                    "model": "m", "api_key": "k"})
        monkeypatch.setattr(llm, "complete", lambda *a, **k: (
            '{"opening": "这周练了 3 次，很稳。",'
            ' "training": "你的卧推已经到 137kg 了，非常了不起。"}'))
        out = llm.narrate(FACTS, slots=["opening", "training"])
        assert "opening" in out, "干净的段落应当保留"
        assert "training" not in out, "含编造数字的段落必须整段丢弃"
