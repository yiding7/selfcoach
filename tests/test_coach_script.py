"""`scripts/coach` 注入语气/称呼那一段的回归测试。

这一段的危险不在于会算错，而在于**会安静地消失**：原来它用 sed 从
`hc persona` 的展示文本里抠「当前：」和「称呼：」，那份清单是给人看的、
措辞随时会调，抠不到时 sed 只给出空串 —— 注入的称呼提示整段没了，
屏幕上一个字的错都没有，agent 于是退回「不知道该怎么称呼」甚至自己编一个，
正是那段代码想防的事。

所以这里盯三件事：

1. 走机器可读接口（`hc persona --json`），不再解析人类可读输出
2. 拿不到就**出声**，不许静默降级
3. 「有称呼 / 明确不要称呼 / 还没问过」三种状态给 agent 的指示各不相同 ——
   空是一个合法答案，不是「什么都不说」
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from health_assistant.config import ROOT

COACH = ROOT / "scripts" / "coach"


@pytest.fixture()
def fake_repo(tmp_path):
    """把 scripts/coach 单独摘出来跑，hc 换成一个可编排的假货。"""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(COACH, scripts / "coach")
    return tmp_path


def _fake_hc(repo: Path, *, payload: dict | None = None, fail: bool = False) -> None:
    hc = repo / "scripts" / "hc"
    if fail:
        body = "#!/bin/sh\nexit 1\n"
    else:
        body = ("#!/bin/sh\ncat <<'JSON'\n"
                + json.dumps(payload, ensure_ascii=False) + "\nJSON\n")
    hc.write_text(body, encoding="utf-8")
    hc.chmod(0o755)


def _hint(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/coach", "--persona-hint"],
                          cwd=repo, capture_output=True, text=True)


class TestPersonaHint:
    def test_reads_the_json_interface(self, fake_repo):
        _fake_hc(fake_repo, payload={
            "tone": "strict", "tone_label": "严厉严肃",
            "addresses": ["老王", "王先生"], "address_set": True,
            "address_ok": True, "warnings": []})
        proc = _hint(fake_repo)
        assert proc.returncode == 0, proc.stderr
        assert "严厉严肃" in proc.stdout
        assert "老王、王先生" in proc.stdout

    def test_a_broken_interface_is_never_silent(self, fake_repo):
        """整段提示消失且不报错，是这一段最糟的失败模式。"""
        _fake_hc(fake_repo, fail=True)
        proc = _hint(fake_repo)
        assert proc.stdout.strip() == "", "拿不到数据却编出了提示"
        assert "⚠️" in proc.stderr and "doctor" in proc.stderr

    def test_garbage_output_is_treated_as_a_failure(self, fake_repo):
        """接口哪天改了格式，也必须是「报出来」而不是「静默变空」。"""
        hc = fake_repo / "scripts" / "hc"
        hc.write_text("#!/bin/sh\necho 这不是 json\n", encoding="utf-8")
        hc.chmod(0o755)
        proc = _hint(fake_repo)
        assert proc.stdout.strip() == ""
        assert "⚠️" in proc.stderr

    def test_an_explicitly_empty_address_still_instructs_the_agent(self, fake_repo):
        """空是一个合法答案。什么都不注入的话 agent 会自己编一个称呼。"""
        _fake_hc(fake_repo, payload={
            "tone_label": "平和", "addresses": [], "address_set": True,
            "address_ok": True, "warnings": []})
        out = _hint(fake_repo).stdout
        assert "不要称呼" in out
        assert "不要自己想一个" in out

    def test_never_asked_is_not_the_same_as_declined(self, fake_repo):
        """「还没问过」和「明确不要」给 agent 的指示不一样，不能合成一句。"""
        _fake_hc(fake_repo, payload={
            "tone_label": "平和", "addresses": [], "address_set": False,
            "address_ok": True, "warnings": []})
        out = _hint(fake_repo).stdout
        assert "还没问过" in out
        assert "明确" not in out

    def test_config_warnings_reach_the_screen_not_the_prompt(self, fake_repo):
        """address 填坏了要让**人**看见，而不是塞给模型。"""
        _fake_hc(fake_repo, payload={
            "tone_label": "平和", "addresses": [], "address_set": True,
            "address_ok": False,
            "warnings": ["data/profile.json 的 address 是 dict 类型，没有生效"]})
        proc = _hint(fake_repo)
        assert "没有生效" in proc.stderr
        assert "没有生效" not in proc.stdout


class TestNoScrapingOfHumanOutput:
    def test_the_script_does_not_sed_the_persona_listing(self):
        """结构性守卫：谁都不许再回去解析 `hc persona` 的展示文本。"""
        text = COACH.read_text(encoding="utf-8")
        assert "persona --json" in text
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            assert not ("sed" in line and "persona" in line.lower()), line
            assert "当前：" not in line, "这是 render_list() 的措辞，不是接口"
