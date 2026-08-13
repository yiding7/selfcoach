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
import re
import subprocess

import pytest

from health_assistant import persona
from health_assistant.config import KNOWLEDGE_DIR, ROOT

# ── 「knowledge/ 不许有身份信息」守卫用的三块 ──────────────────────────
#
# 这条守卫是整个测试套里唯一挡在「真名进公开仓库」前面的东西，所以它
# **不许 fail-open**。首选判据是 git（它认 .gitignore，不会和白名单漂开），
# git 不可用时退回下面这份内置清单 —— 少扫几份可以，整条跳过不行。

IDENTITY_RE = re.compile(r"\bTim(othy)?\b", re.IGNORECASE)

# .gitignore 里 `knowledge/` 根白名单的影子。会漂开，所以
# `test_the_fallback_never_scans_more_than_git_would` 拿真 git 校准它。
VERSIONED_DIRS = ("coach/", "measurement/", "movements/", "training/",
                  "nutrition/")
VERSIONED_FILES = ("README.md", "library/README.md", "library/INDEX.example.md")


def _knowledge_md():
    """knowledge/ 下所有 .md，返回（绝对路径, 相对仓库根的路径）两份等长列表。"""
    candidates = sorted(KNOWLEDGE_DIR.rglob("*.md"))
    if not candidates:
        pytest.skip("knowledge/ 里没有 .md")
    return candidates, [str(p.relative_to(ROOT)) for p in candidates]


def _git_ignored(rels: list[str]) -> set[str] | None:
    """git 眼里哪些路径被忽略。**git 不可用返回 None** —— 调用方必须退回保守判定。

    用 check-ignore 而不是 ls-files，是为了连**还没 git add 的新文件**也查到。
    -z 是必须的：默认 core.quotePath=true 会把中文路径转义成 \\346\\233\\277…，
    跟这边的原始字符串永远对不上，结果是所有中文路径都被当成「没被忽略」而去扫。
    """
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"], cwd=ROOT,
            input="\0".join(rels), capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    # 退出码 0=有被忽略的 1=一个都没被忽略，都算正常；128 才是真出错
    if proc.returncode not in (0, 1):
        return None
    return {s for s in proc.stdout.split("\0") if s}


def _assumed_private(rel: str) -> bool:
    """没有 git 时的保守判定：白名单之外的一律当私人资料。

    宁可少扫也不能误扫 —— 误扫别人整套私人知识库会满屏假阳性，
    那种测试三天就会被人加 `-k` 跳过，等于没有。
    """
    inside = rel.replace("\\", "/").removeprefix("knowledge/")
    return not (inside in VERSIONED_FILES
                or inside.startswith(VERSIONED_DIRS))


def _assert_no_identity(candidates, rels, ignored: set[str]) -> None:
    checked = 0
    for p, rel in zip(candidates, rels):
        if rel in ignored:            # 私人资料，本来就不进版本库
            continue
        checked += 1
        assert not IDENTITY_RE.search(p.read_text(encoding="utf-8")), \
            f"{rel} 会进版本库，但里面出现了身份信息 —— 那属于 profile/"
    # 过滤器写错时会把所有文件都排除掉，测试就变成永远通过的摆设
    assert checked, "一个会进版本库的 knowledge/*.md 都没查到，过滤器写反了"


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


class TestAddresses:
    """称呼。要害只有一个：**空是答案，不是缺失。**

    有人不喜欢被叫名字。如果空被当成「还没填」，教练要么反复催他填，
    要么自己想一个 —— 后者比不叫更冒犯。
    """

    def test_empty_when_never_asked(self, sandbox):
        assert persona.addresses() == []

    def test_reads_the_list_in_order(self, sandbox):
        _write_profile(sandbox["profile"], {"address": ["Tim", "Timothy"]})
        assert persona.addresses() == ["Tim", "Timothy"], "顺序有意义，第一个是默认"

    def test_explicit_empty_stays_empty(self, sandbox):
        """明确填了空 = 不要称呼。绝不能在这里补一个默认值。"""
        _write_profile(sandbox["profile"], {"address": []})
        assert persona.addresses() == []

    def test_a_bare_string_is_accepted(self, sandbox):
        """手改 json 的人十有八九会写成字符串，认它比报错有用。"""
        _write_profile(sandbox["profile"], {"address": "Tim"})
        assert persona.addresses() == ["Tim"]

    @pytest.mark.parametrize("bad", [123, {"first": "Tim"}, None])
    def test_garbage_degrades_to_empty_instead_of_raising(self, sandbox, bad):
        """一个称呼字段填坏了，不该让整个教练用不了。"""
        _write_profile(sandbox["profile"], {"address": bad})
        assert persona.addresses() == []

    @pytest.mark.parametrize("bad", [123, {"formal": "王先生", "casual": "老王"}])
    def test_garbage_is_reported_instead_of_silently_dropped(self, sandbox, bad):
        """容错**不等于**沉默。

        手改成 `{"formal": …}` 时 `addresses()` 返回 []，调用方只看「有没有值」
        就会打出「不要称呼（已确认）」—— 用户填了两个称呼，工具却说系统已确认
        他不要称呼。和忌口那一层「解析失败要红着脸报」是同一条纪律。
        """
        _write_profile(sandbox["profile"], {"address": bad})
        warn = persona.address_warning()
        assert warn and "没有生效" in warn
        assert warn in persona.warnings()
        assert "没有生效" in persona.render_list()

    def test_a_partly_broken_list_says_what_was_dropped(self, sandbox):
        _write_profile(sandbox["profile"], {"address": ["Tim", 7]})
        assert persona.addresses() == ["Tim"]
        assert "没有生效" in (persona.address_warning() or "")

    @pytest.mark.parametrize("fine", [None, [], ["Tim"], "Tim", ["Tim", "  "]])
    def test_the_normal_shapes_do_not_warn(self, sandbox, fine):
        """空、单字符串、带空白项都是正常输入，不许制造噪音。"""
        _write_profile(sandbox["profile"], {"address": fine} if fine is not None else {})
        assert persona.address_warning() is None
        assert persona.warnings() == []

    def test_blanks_and_padding_are_dropped(self, sandbox):
        _write_profile(sandbox["profile"], {"address": ["  Tim  ", "", "   ", 7]})
        assert persona.addresses() == ["Tim"]

    def test_render_list_says_when_there_is_no_address(self, sandbox):
        """`hc persona` 得能区分「没设」和「设成了不要称呼」，否则用户没法核对。"""
        _write_profile(sandbox["profile"], {"address": []})
        assert "不用称呼" in persona.render_list()


class TestCoachName:
    """教练自己的名字。称呼的另一半 —— 那边是它怎么叫你，这边是你怎么叫它。

    契约和 `address` **逐条对齐**（空是答案、单字符串也认、坏值报出来不静默），
    因为两个字段在 `hc doctor` 和 `scripts/coach` 里走的是同一套判据。
    对齐是有意的：两边一旦漂开，其中一个的失败模式就没人测了。

    另外多一条 `address` 没有的义务：这个字段还要让教练**认出自己被叫到了**，
    所以别名必须能填多个，而且读函数要返回**全部**别名而不只是第一个。
    """

    def test_empty_when_never_asked(self, sandbox):
        assert persona.coach_names() == []

    def test_reads_the_list_in_order(self, sandbox):
        _write_profile(sandbox["profile"], {"coach_name": ["阿尺", "Chi"]})
        assert persona.coach_names() == ["阿尺", "Chi"], "第一个是自称用的"

    def test_explicit_empty_stays_empty(self, sandbox):
        """明确填了空 = 不起名字。绝不能在这里补一个默认值。"""
        _write_profile(sandbox["profile"], {"coach_name": []})
        assert persona.coach_names() == []

    def test_a_bare_string_is_accepted(self, sandbox):
        _write_profile(sandbox["profile"], {"coach_name": "阿尺"})
        assert persona.coach_names() == ["阿尺"]

    @pytest.mark.parametrize("bad", [123, {"first": "阿尺"}, None])
    def test_garbage_degrades_to_empty_instead_of_raising(self, sandbox, bad):
        _write_profile(sandbox["profile"], {"coach_name": bad})
        assert persona.coach_names() == []

    @pytest.mark.parametrize("bad", [123, {"full": "阿尺", "short": "尺"}])
    def test_garbage_is_reported_instead_of_silently_dropped(self, sandbox, bad):
        _write_profile(sandbox["profile"], {"coach_name": bad})
        warn = persona.coach_name_warning()
        assert warn and "没有生效" in warn
        assert warn in persona.warnings()
        assert "没有生效" in persona.render_list()

    @pytest.mark.parametrize("fine", [None, [], ["阿尺"], "阿尺", ["阿尺", "  "]])
    def test_the_normal_shapes_do_not_warn(self, sandbox, fine):
        _write_profile(sandbox["profile"],
                       {"coach_name": fine} if fine is not None else {})
        assert persona.coach_name_warning() is None
        assert persona.warnings() == []

    def test_blanks_and_padding_are_dropped(self, sandbox):
        _write_profile(sandbox["profile"], {"coach_name": ["  阿尺  ", "", "  ", 7]})
        assert persona.coach_names() == ["阿尺"]

    def test_render_list_says_when_there_is_no_name(self, sandbox):
        _write_profile(sandbox["profile"], {"coach_name": []})
        assert "没起名字" in persona.render_list()

    def test_render_list_shows_every_alias(self, sandbox):
        """别名是识别用的，`hc persona` 只显示第一个的话用户没法核对。"""
        _write_profile(sandbox["profile"], {"coach_name": ["阿尺", "Chi"]})
        out = persona.render_list()
        assert "阿尺" in out and "Chi" in out

    def test_a_broken_name_does_not_also_break_the_address(self, sandbox):
        """两个字段共用一套读法，别让其中一个的坏值污染另一个。"""
        _write_profile(sandbox["profile"],
                       {"address": ["Tim"], "coach_name": {"x": 1}})
        assert persona.addresses() == ["Tim"]
        assert persona.address_warning() is None
        assert persona.coach_name_warning() is not None


class TestSetCoachNames:
    def test_writes_the_list_and_keeps_other_fields(self, sandbox):
        _write_profile(sandbox["profile"], {"persona": "strict", "height_cm": 179})
        assert persona.set_coach_names(["阿尺", "Chi"]) == ["阿尺", "Chi"]
        data = json.loads(sandbox["profile"].read_text(encoding="utf-8"))
        assert data["coach_name"] == ["阿尺", "Chi"]
        assert data["persona"] == "strict" and data["height_cm"] == 179

    def test_leaves_a_comment_explaining_the_field(self, sandbox):
        persona.set_coach_names(["阿尺"])
        data = json.loads(sandbox["profile"].read_text(encoding="utf-8"))
        assert "_coach_name_comment" in data

    def test_empty_is_saved_as_a_decision_not_skipped(self, sandbox):
        """「不起名字」必须落成 `[]` —— 否则和「还没问过」分不开，教练会一直催。"""
        assert persona.set_coach_names([]) == []
        assert persona.as_dict()["coach_name_set"] is True
        assert persona.as_dict()["coach_names"] == []

    def test_a_bare_string_and_none_are_accepted(self, sandbox):
        assert persona.set_coach_names("阿尺") == ["阿尺"]
        assert persona.set_coach_names(None) == []

    def test_duplicates_collapse_but_order_survives(self, sandbox):
        assert persona.set_coach_names(["阿尺", "Chi", "阿尺"]) == ["阿尺", "Chi"]

    def test_non_text_is_rejected_without_writing(self, sandbox):
        _write_profile(sandbox["profile"], {"coach_name": ["阿尺"]})
        with pytest.raises(ValueError):
            persona.set_coach_names(["阿尺", 7])
        assert persona.coach_names() == ["阿尺"], "报错的那次不该写坏原值"

    def test_round_trip(self, sandbox):
        persona.set_coach_names(["阿尺"])
        assert persona.coach_names() == ["阿尺"]
        assert persona.coach_name_warning() is None


class TestMachineReadableSnapshot:
    """`as_dict()` 是给 `scripts/coach` 这类调用方用的**接口**。

    它存在的理由就是别让人再去 sed `render_list()` 那份给人看的清单 ——
    措辞一调就抠不到，而抠不到时 sed 只会安静地给出空串。
    所以这里把键名当契约钉住：改名等于悄悄弄坏调用方。
    """

    def test_carries_the_fields_the_launcher_needs(self, sandbox):
        _write_profile(sandbox["profile"], {"persona": "strict", "address": ["老王"]})
        d = persona.as_dict()
        assert d["tone"] == "strict"
        assert d["tone_label"] == persona.label("strict")
        assert d["addresses"] == ["老王"]
        assert d["address_set"] is True and d["address_ok"] is True
        assert d["warnings"] == []

    def test_distinguishes_never_asked_from_declined(self, sandbox):
        """两种情况 addresses 都是空的，给 agent 的指示却不一样。"""
        _write_profile(sandbox["profile"], {})
        assert persona.as_dict()["address_set"] is False
        _write_profile(sandbox["profile"], {"address": []})
        assert persona.as_dict()["address_set"] is True

    def test_carries_the_coach_name_fields(self, sandbox):
        """`scripts/coach` 靠这三个键分「有名字 / 明确不起 / 没问过」三种状态。"""
        _write_profile(sandbox["profile"], {"coach_name": ["阿尺", "Chi"]})
        d = persona.as_dict()
        assert d["coach_names"] == ["阿尺", "Chi"]
        assert d["coach_name_set"] is True and d["coach_name_ok"] is True
        _write_profile(sandbox["profile"], {})
        assert persona.as_dict()["coach_name_set"] is False
        _write_profile(sandbox["profile"], {"coach_name": []})
        assert persona.as_dict()["coach_name_set"] is True
        _write_profile(sandbox["profile"], {"coach_name": {"x": 1}})
        assert persona.as_dict()["coach_name_ok"] is False

    def test_a_broken_address_shows_up_as_not_ok(self, sandbox):
        _write_profile(sandbox["profile"], {"address": {"formal": "王先生"}})
        d = persona.as_dict()
        assert d["address_ok"] is False
        assert d["addresses"] == []
        assert any("没有生效" in w for w in d["warnings"])

    def test_paths_are_relative_to_the_repo(self):
        """这些字符串会进 system prompt —— 绝对路径既长又把用户名带出去。

        不用 sandbox：那边的路径本来就在仓库外，`_rel()` 按约定原样返回绝对路径。
        """
        d = persona.as_dict()
        assert not d["tone_file"].startswith("/")
        assert not d["core_file"].startswith("/")


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
        assert "knowledge/coach/persona.md" in data["_persona_comment"]

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
        assert "knowledge/coach/personas/" in core

    @pytest.mark.parametrize("mode", ["git", "fallback"])
    def test_knowledge_dir_carries_no_identity(self, mode):
        """**会进版本库的** knowledge/ 文件里不能有名字 —— 仓库是公开的。

        判据是「git 会不会收它」，不是「在不在 knowledge/ 下面」。
        `knowledge/` 根目录是 .gitignore 白名单：整套私人知识库
        （买来的课程包、聊天记录归档）就该原样丢在那一层，
        它们**永远不进版本库**，扫它们只会扫出假阳性。

        两种模式都跑，因为这条守卫**绝不能 fail-open**：它存在的唯一目的
        就是拦住真名进公开仓库，而「git 跑不了」（最小 CI 容器、源码 tarball）
        恰恰是没人盯着的那一次。所以 fallback 模式不依赖 git，永远真的扫。
        """
        candidates, rels = _knowledge_md()
        if mode == "git":
            ignored = _git_ignored(rels)
            if ignored is None:
                pytest.skip("这台机器跑不了 git check-ignore，fallback 那条用例仍然在扫")
        else:
            ignored = {rel for rel in rels if _assumed_private(rel)}
        _assert_no_identity(candidates, rels, ignored)

    def test_the_fallback_only_trusts_the_gitignore_whitelist(self):
        """保守判定的方向必须是「不确定就当私有」，但白名单那几个区一个都不能漏。"""
        for rel in ("knowledge/coach/persona.md",
                    "knowledge/nutrition/notes.md",
                    "knowledge/README.md",
                    "knowledge/library/README.md"):
            assert not _assumed_private(rel), f"{rel} 会进版本库，必须扫"
        for rel in ("knowledge/某某知识库/减脂.md",
                    "knowledge/library/营养/一份讲义.md",
                    "knowledge/聊天记录.md"):
            assert _assumed_private(rel), f"{rel} 不进版本库，扫它只会出假阳性"

    def test_the_fallback_never_scans_more_than_git_would(self):
        """校准：内置白名单会和 .gitignore 漂开，所以拿真实的 git 结果对一次。

        方向不对称 —— fallback 可以**少**扫（保守），但不能把 git 眼里
        私有的文件当成公开的去扫，那会在别人的私人知识库上报假阳性。
        """
        candidates, rels = _knowledge_md()
        ignored = _git_ignored(rels)
        if ignored is None:
            pytest.skip("这台机器跑不了 git check-ignore")
        leaked = [rel for rel in rels
                  if rel in ignored and not _assumed_private(rel)]
        assert not leaked, f"内置白名单和 .gitignore 漂开了：{leaked}"
