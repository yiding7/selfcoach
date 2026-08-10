"""人格分层的回归测试。

这一层的价值全在「换语气不改结论」上，所以测试盯四件事：

1. **核心永远在前，且永远在。** 语气文件丢了顶多说话干巴，核心丢了就不是这个教练了
2. **坏配置不能让教练不可用。** 认不出的语气退回默认并报出来，不抛异常
3. **每份语气文件都带护栏。** 「这个语气最容易漂成什么」那一节必须存在 ——
   严厉和活力最容易漂，没有护栏的语气文件不该被合进来
4. **knowledge/ 里不许有身份信息。** 那是公开目录，名字属于 profile/
"""

from __future__ import annotations

import json

import pytest

from health_assistant import persona


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把核心、语气目录、profile.json 全指到 tmp，互不干扰。"""
    knowledge = tmp_path / "knowledge"
    personas = knowledge / "personas"
    personas.mkdir(parents=True)
    core = knowledge / "persona.md"
    core.write_text("# 核心\n\n数字归脚本。\n", encoding="utf-8")
    for slug in persona.TONES:
        (personas / f"{slug}.md").write_text(f"# 语气 {slug}\n", encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(persona, "CORE_PATH", core)
    monkeypatch.setattr(persona, "PERSONAS_DIR", personas)
    monkeypatch.setattr(persona, "PROFILE_PATH", profile)
    return {"core": core, "personas": personas, "profile": profile}


def _write_profile(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestComposition:
    def test_core_comes_before_tone(self, sandbox):
        _write_profile(sandbox["profile"], {"persona": "strict"})
        text = persona.load()
        assert text.index("# 核心") < text.index("# 语气 strict"), \
            "核心必须在前 —— 顺序反了模型会以为后面那份优先级更高"

    def test_missing_tone_file_still_yields_the_core(self, sandbox):
        _write_profile(sandbox["profile"], {"persona": "calm"})
        (sandbox["personas"] / "calm.md").unlink()
        text = persona.load()
        assert "# 核心" in text
        assert "语气" not in text.replace("# 核心", "")

    def test_missing_core_falls_back_to_a_minimal_prompt(self, sandbox):
        sandbox["core"].unlink()
        text = persona.load()
        # 兜底文案必须仍然带上数字纪律 —— 那是这个项目的立身之本
        assert "数字" in text and "脚本" in text

    def test_explicit_slug_overrides_the_stored_one(self, sandbox):
        _write_profile(sandbox["profile"], {"persona": "warm"})
        assert "# 语气 energetic" in persona.load("energetic")


class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        ("warm", "warm"),
        ("STRICT", "strict"),
        ("  calm  ", "calm"),
        ("亲切客观", "warm"),
        ("严厉严肃", "strict"),
        ("平和", "calm"),
        ("充满活力", "energetic"),
    ])
    def test_accepts_slug_and_chinese_label(self, raw, expected):
        assert persona.normalize(raw) == expected

    @pytest.mark.parametrize("raw", ["", "凶巴巴", None, 3, [], "warmish"])
    def test_rejects_everything_else(self, raw):
        assert persona.normalize(raw) is None


class TestBadConfigDegradesGracefully:
    def test_unknown_value_falls_back_to_default_and_warns(self, sandbox):
        _write_profile(sandbox["profile"], {"persona": "凶巴巴"})
        assert persona.current() == persona.DEFAULT_TONE
        assert any("凶巴巴" in w for w in persona.warnings())

    def test_unset_value_is_not_a_warning(self, sandbox):
        _write_profile(sandbox["profile"], {})
        assert persona.current() == persona.DEFAULT_TONE
        assert persona.warnings() == []

    def test_corrupt_profile_json_does_not_raise(self, sandbox):
        sandbox["profile"].write_text("{ 这不是 json", encoding="utf-8")
        assert persona.current() == persona.DEFAULT_TONE
        assert persona.load()  # 仍然拼得出来

    def test_missing_tone_file_is_reported(self, sandbox):
        _write_profile(sandbox["profile"], {"persona": "calm"})
        (sandbox["personas"] / "calm.md").unlink()
        assert any("calm" in w for w in persona.warnings())


class TestSetTone:
    def test_writes_slug_and_keeps_other_fields(self, sandbox):
        _write_profile(sandbox["profile"], {"height_cm": 179, "diet": {"phase": "减脂"}})
        persona.set_tone("严厉严肃")
        data = json.loads(sandbox["profile"].read_text(encoding="utf-8"))
        assert data["persona"] == "strict"
        assert data["height_cm"] == 179
        assert data["diet"] == {"phase": "减脂"}

    def test_leaves_a_comment_explaining_the_field(self, sandbox):
        persona.set_tone("calm")
        data = json.loads(sandbox["profile"].read_text(encoding="utf-8"))
        assert "knowledge/persona.md" in data["_persona_comment"]

    def test_rejects_unknown_tone_without_writing(self, sandbox):
        _write_profile(sandbox["profile"], {"persona": "warm"})
        with pytest.raises(ValueError):
            persona.set_tone("凶巴巴")
        data = json.loads(sandbox["profile"].read_text(encoding="utf-8"))
        assert data["persona"] == "warm"

    def test_round_trip_through_every_tone(self, sandbox):
        for slug in persona.TONES:
            persona.set_tone(slug)
            assert persona.current() == slug


class TestShippedFiles:
    """这些跑在真实的 knowledge/ 上 —— 它们是内容契约，不是逻辑测试。"""

    def test_every_tone_has_a_file_with_content(self):
        for slug in persona.TONES:
            p = persona.tone_path(slug)
            assert p.exists(), f"缺 {p}"
            assert len(p.read_text(encoding="utf-8")) > 400, f"{slug} 太短，像是占位文件"

    def test_every_tone_declares_its_own_failure_mode(self):
        """没有护栏的语气文件不该被合进来 —— 严厉和活力最容易漂。"""
        for slug in persona.TONES:
            text = persona.tone_path(slug).read_text(encoding="utf-8")
            assert "最容易漂成什么" in text, f"{slug} 缺「这个语气最容易漂成什么」一节"

    def test_every_tone_says_it_cannot_change_conclusions(self):
        for slug in persona.TONES:
            text = persona.tone_path(slug).read_text(encoding="utf-8")
            assert "同一个结论" in text, f"{slug} 没写明换语气不改结论"

    def test_core_points_at_the_tone_layer(self):
        core = persona.CORE_PATH.read_text(encoding="utf-8")
        assert "knowledge/personas/" in core

    def test_knowledge_dir_carries_no_identity(self):
        """knowledge/ 进版本库且仓库公开，名字只能待在 profile/。"""
        import re

        from health_assistant.config import KNOWLEDGE_DIR

        banned = re.compile(r"\bTim(othy)?\b", re.IGNORECASE)
        for p in KNOWLEDGE_DIR.rglob("*.md"):
            if "library" in p.parts:      # 用户的私人资料，本来就不进版本库
                continue
            assert not banned.search(p.read_text(encoding="utf-8")), \
                f"{p} 里出现了身份信息 —— 那属于 profile/"
