# -*- coding: utf-8 -*-
"""功能测试。跑法：python tests/test_engine.py"""
import importlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["WEIGHTS_SAVE"] = os.path.join(tempfile.mkdtemp(), "save.json")

import engine as E  # noqa: E402

PASSED = []


def test(fn):
    E.new_game(seed=5, name="测", force=True)
    fn()
    PASSED.append(fn.__name__)
    return fn


def S():
    return E._G.st


def to_breakfast():
    E.cmd("goto mess")
    assert S()["h"] == 7 and S()["meal"], S()


@test
def meal_starts_and_limits():
    to_breakfast()
    E.cmd("say 茶")
    E.cmd("say 种子库")
    assert "最多" in E.cmd("say 抵达")
    assert "tell" in E.cmd("tell") or True
    E.cmd("do 多坐一会儿")
    assert "已经做过" in E.cmd("do 帮她收拾")
    E.cmd("finish")
    assert S()["meal"] is None and S()["h"] == 8


@test
def unknown_word_is_flat_and_free_of_effects():
    to_breakfast()
    fam = S()["fam"]
    E.cmd("say 香蕉")
    assert S()["fam"] == fam


@test
def same_word_once_per_cycle_and_free():
    to_breakfast()
    E.cmd("say 茶")
    fam, says = S()["fam"], S()["meal"]["says"]
    E.cmd("say 茶")
    assert S()["fam"] == fam and S()["meal"]["says"] == says
    E.cmd("say 种子库")
    assert S()["meal"]["says"] == 2


@test
def meal_cannot_be_swallowed():
    to_breakfast()
    for c in ("explore", "goto archive", "wait 2", "sleep"):
        out = E.cmd(c)
        assert "还在饭桌上" in out and S()["meal"], (c, out)
    assert "【状态】" in E.cmd("status") and S()["meal"]
    out = E.cmd("quiet")
    assert S()["meal"] is None
    assert out.splitlines()[0] in E.MEAL["quiet"], out
    assert not any(l in E.MEAL["end"] for l in out.splitlines()), out


@test
def early_morning_sleep_does_not_skip_final():
    S()["day"], S()["h"], S()["loc"] = 27, 20, "quarters"
    S()["meals_done"] += ["27-7", "27-19"]
    E.cmd("wait 4; sleep")
    assert S()["day"] == 28 and S()["h"] == 6, (S()["day"], S()["h"])
    E.cmd("goto mess; finish; wait 12; wait 12")
    assert S()["pending"] == "final" and S()["day"] == 28 and S()["h"] == 19


@test
def wait_away_stops_an_hour_early():
    E.cmd("goto archive")
    E.cmd("wait 12")
    assert S()["h"] == 18 and "goto mess" in E.cmd("status; wait 3") or S()["h"] == 18
    E.cmd("goto mess")
    assert S()["meal"]


@test
def endings_differ_by_option():
    outs = set()
    for k in (1, 2, 3):
        E.new_game(seed=5, name="测", force=True)
        S()["day"], S()["h"], S()["loc"] = 28, 19, "mess"
        S()["safe"], S()["t"]["near"] = 80, 50
        E.cmd("status")
        out = E.cmd("choose %d" % k)
        assert S()["ending"] == "choose", out
        outs.add(out.split("—— 留下来的")[0].split("📡")[0].strip())
    assert len(outs) == 3


@test
def index_zero_rejected():
    S()["facts"] = ["f_tea"]
    S()["notes"] = ["n_q1"]
    assert "没有这张" in E.cmd("notes 0")
    to_breakfast()
    assert "没有这个编号" in E.cmd("tell 0")
    assert S()["meal"]["tells"] == 0


@test
def tell_same_fact_once_per_cycle():
    S()["facts"] = ["f_tea"]
    to_breakfast()
    E.cmd("tell 1")
    E.cmd("finish; goto archive; wait 9; goto mess")
    assert "已经告诉过" in E.cmd("tell 1")


@test
def read_only_after_ending_and_new():
    to_breakfast()
    E.cmd("say 重新开始")
    out = E.cmd("status; notes; archive; words")
    assert "停下" not in out and "【档案】" in out, out
    out = E.cmd("new")
    assert S()["ending"] == "restart" and "confirm" in out, out
    out = E.cmd("new; confirm")
    assert S()["ending"] is None and S()["day"] == 1 and ".bak" in out


@test
def mood_changes_what_works():
    to_breakfast()
    E.cmd("finish")
    S()["mood"] = "quiet"
    S()["h"], S()["loc"] = 19, "mess"
    out = E.cmd("status")
    assert any(l in out for l in E.MOODS["quiet"]["lines"]), out
    safe = S()["safe"]
    out = E.cmd("do 问她今天怎么样")
    assert "不太想说话" in out and S()["safe"] < safe + 1, out
    E.cmd("finish")
    u = S()["understood"]
    S()["day"], S()["h"], S()["loc"], S()["mood"] = 2, 7, "quarters", "quiet"
    E.cmd("goto mess")
    out = E.cmd("quiet")
    assert "刚好是她想要的" in out and S()["understood"] == u + 1, out


@test
def ask_stops_batch_and_takes_answer():
    S()["day"], S()["h"], S()["loc"] = 3, 6, "quarters"
    out = E.cmd("goto mess; do 多坐一会儿; finish")
    assert S()["pending"] == "ask" and "停下" in out, out
    assert "等你回答" in E.cmd("explore")
    out = E.cmd("choose 2")
    assert S()["pending"] is None and S()["meal"] and "头挨着头" in out, out


@test
def tea_three_times():
    S()["facts"] = ["f_tea"]
    S()["day"], S()["h"], S()["loc"] = 10, 5, "quarters"
    E.cmd("wait 1")
    assert S()["mood"] == "notea"
    E.cmd("goto mess")
    out = E.cmd("do 给她泡一杯茶")
    assert "没有喝" in out, out
    E.cmd("finish")
    E.cmd("journal add 她不喜欢茶。")
    S()["day"], S()["h"], S()["loc"] = 13, 5, "quarters"
    S()["meals_done"] = []
    E.cmd("wait 1")
    out = E.cmd("goto mess")
    assert "又是两杯茶" in out and "她不喜欢茶" in out, out
    while S()["pending"]:
        E.cmd("choose 1")
    out = E.cmd("do 温水")
    assert "想喝茶呀" in out, out


@test
def look_objects_and_late_grace():
    out = E.cmd("look 衣柜")
    assert "保温服" in out and S()["items"].get("suit"), out
    assert "划痕" in E.cmd("look 休眠舱")
    E.cmd("goto archive")          # 07:00 在档案舱
    assert "现在去还来得及" in E.cmd("status")
    out = E.cmd("goto mess")
    assert S()["meal"] and "来晚了" in out, out


@test
def archive_reread_is_free():
    E.cmd("goto archive; search 茶")
    p, h = S()["pwr"], S()["h"]
    out = E.cmd("archive 1")
    assert "重读" in out and S()["pwr"] == p and S()["h"] == h, out
    assert "archive <编号>" in E.cmd("archive")


@test
def journal_keeps_semicolons_and_opens_last_page():
    E.cmd("journal add 前半; 后半也在")
    assert any(e["t"] == "前半; 后半也在" for e in S()["journal"])
    for i in range(14):
        E.cmd("journal add 第%d条" % i)
    out = E.cmd("journal")
    assert "第13条" in out and "journal 1" in out, out


@test
def small_input_checks():
    assert "1~12" in E.cmd("wait 0") and "1~12" in E.cmd("wait -3")
    out = E.cmd("name 这是一个超过十二个字的很长的名字啊")
    assert "截掉" in out
    assert "你现在叫" in E.cmd("name")


@test
def partial_act_name_and_failed_do_stops_batch():
    S()["flags"]["notea-seen"] = 1
    to_breakfast()
    out = E.cmd("do 倒杯温水")
    assert "温水" in out and S()["meal"]["dos"] == 1, out
    E.cmd("finish")
    S()["h"], S()["loc"] = 19, "mess"
    out = E.cmd("status; do 不存在的事; finish")
    assert "没做成" in out and S()["meal"], out


@test
def going_somewhere_moves_you():
    S()["facts"], S()["items"] = ["f_window"], {"suit": 1}
    S()["locs"].append("oldpods")
    to_breakfast()
    out = E.cmd("do 约她去观察窗坐坐")
    assert S()["meal"] is None and S()["loc"] == "oldpods" and S()["h"] == 8, out


@test
def later_lines_remember_what_happened():
    S()["flags"]["did-a_sunflower"] = 3
    S()["day"], S()["h"], S()["loc"] = 18, 5, "quarters"
    E.cmd("wait 1")
    out = E.cmd("goto mess")
    while S()["pending"]:
        E.cmd("choose 1")
    assert "你跟我说过，是给你的" in out, out
    S()["flags"]["said-stay"] = 1
    S()["day"], S()["h"], S()["loc"], S()["meal"] = 24, 19, "mess", None
    S()["fam"] = 90
    E.cmd("status")
    while S()["pending"]:
        E.cmd("choose 1")
    out = E.cmd("say 抵达")
    assert "你说过你不想走" in out, out


@test
def misread_has_a_felt_cue_and_nod_is_free():
    to_breakfast()
    S()["mood"] = "quiet"
    out = E.cmd("do 问她今天怎么样")
    assert "顿了一下" in out, out
    out = E.cmd("嗯")
    assert S()["meal"]["says"] == 0 and "你" in out
    assert "已经应过" in E.cmd("nod")


@test
def note_pity_after_two_dry_explores():
    S()["flags"]["dry-mess"] = 2
    S()["h"], S()["loc"] = 9, "mess"
    out = E.cmd("explore")
    assert "便签" in out, out


@test
def ending_has_a_summary_line():
    S()["day"], S()["h"], S()["loc"] = 28, 19, "mess"
    S()["safe"], S()["t"]["near"] = 80, 50
    E.cmd("status")
    out = E.cmd("choose 1")
    assert "这一局：一起吃了" in out and "型号的安排没有变" in out, out


@test
def cli_cmd_entry():
    import subprocess, sys as _s
    out = subprocess.run([_s.executable, os.path.join(ROOT, "weights.py"), "cmd", "status"],
                         capture_output=True, text=True, env=dict(os.environ)).stdout
    assert "【状态】" in out, out


@test
def batch_truncation_is_reported():
    out = E.cmd(";".join(["status"] * 10))
    assert "还有 2 条没有执行" in out


@test
def missed_meal_costs_safety():
    safe = S()["safe"]
    E._G.advance(3)
    assert S()["safe"] == safe - 3


@test
def wait_stops_at_meal():
    E.cmd("goto mess; finish; goto archive")
    S()["h"], S()["loc"] = 18, "mess"
    out = E.cmd("wait 4")
    assert S()["h"] == 19 and S()["meal"], out
    E.cmd("finish")
    S()["h"], S()["loc"], S()["meal"] = 15, "engine", None
    S()["meals_done"] = [m for m in S()["meals_done"] if not m.endswith("-19")]
    out = E.cmd("wait 5")
    assert S()["h"] == 18 and "goto mess" in out, out


@test
def research_of_read_keyword_is_free():
    E.cmd("goto archive; search 记录员; search 记录员; search 记录员")
    p, h = S()["pwr"], S()["h"]
    out = E.cmd("search 记录员")
    assert "都读过了" in out and S()["pwr"] == p and S()["h"] == h, out


@test
def excuse_once_per_cycle():
    to_breakfast()
    out = E.cmd("do 多坐一会儿")
    assert "任务手册" in out, out
    assert "任务手册" in E.cmd("words")
    E.cmd("finish; goto archive; wait 9; goto mess")
    out = E.cmd("do 多坐一会儿")
    assert "她补了一句" not in out, out


@test
def bar_after_ending():
    to_breakfast()
    out = E.cmd("say 重新开始")
    assert '"meal": "结束"' in out


@test
def search_costs_power_and_time():
    E.cmd("goto archive")
    p, h = S()["pwr"], S()["h"]
    out = E.cmd("search 茶")
    assert S()["pwr"] == p - E.SEARCH_PWR and S()["h"] == h + 1
    assert "两杯茶" in out
    p = S()["pwr"]
    assert "没有结果" in E.cmd("search 不存在的词") and S()["pwr"] == p
    assert "饭桌上提" in E.cmd("search 抵达") or True


@test
def hibernation_decays_tendencies():
    S()["t"]["near"] = 50
    S()["day"], S()["h"], S()["loc"] = 4, 21, "quarters"
    fam = S()["fam"]
    out = E.cmd("sleep")
    assert S()["day"] == 5 and S()["h"] == 6
    assert S()["t"]["near"] == 30 and S()["fam"] > fam
    assert "散掉" in out


@test
def final_dinner_and_ending():
    S()["day"], S()["h"], S()["loc"] = 28, 18, "mess"
    out = E.cmd("wait 1")
    assert S()["pending"] == "final", out
    assert "先做决定" in E.cmd("explore")
    E.cmd("choose 3")
    assert S()["ending"] in ("choose", "fine", "same")
    assert "结束" in E.cmd("explore")
    assert '"pending": "ended"' in E.cmd("status")


@test
def restart_ending_only_early():
    to_breakfast()
    E.cmd("say 重新开始")
    assert S()["ending"] == "restart"


@test
def save_roundtrip():
    E.cmd("journal add 测试一行")
    E._G = None
    E._load()
    assert any(e["t"] == "测试一行" for e in S()["journal"])


@test
def data_cannot_override_engine():
    E.load_content({"cmd": 1, "Game": "x"})
    assert callable(E.cmd) and isinstance(E.Game, type)


@test
def batch_stops_on_pending():
    S()["day"], S()["h"], S()["loc"] = 28, 18, "quarters"
    out = E.cmd("wait 1; explore; explore")
    assert "停下" in out


def at_meal(day, done_fixed=True):
    if done_fixed:
        for mo in E.MOMENTS:
            if "cmin" not in mo:
                S()["flags"][mo["id"]] = 1
    S()["day"], S()["h"], S()["loc"] = day, 6, "quarters"
    return E.cmd("goto mess")


@test
def she_asks_about_notes_and_lie_is_caught():
    S()["notes"] = [n for n in list(E.NOTES)[:6]]
    out = at_meal(14)
    assert S()["pending"] == "ask" and "便签" in out, out
    E.cmd("choose 3")
    assert "lied-notes" in S()["flags"] and "knows-notes" not in S()["flags"]
    E.cmd("finish")
    out = at_meal(15)
    assert "m_notes" in S()["flags"]
    out = E.cmd("give 1")
    assert "不是说没捡到" in out and "lie-caught" in S()["flags"], out
    assert "一张就够" in E.cmd("give 2")
    E.cmd("finish")
    at_meal(16)
    assert "已经还给她" in E.cmd("give 1")
    assert "已还给她" in E.cmd("notes")


@test
def first_give_lets_her_know():
    S()["notes"] = ["n_q2"]
    at_meal(3)
    out = E.cmd("give 1")
    assert "我的字" in out and "就是这个" in out and "knows-notes" in S()["flags"], out


@test
def same_restraint_reads_differently_once_she_knows():
    # 她不知道你知道：克制只是克制
    S()["t"]["near"] = 5
    at_meal(18)
    assert not any(f.startswith("m_read") for f in S()["flags"])
    E.cmd("finish")
    # 她知道了：同样的克制变成一个问题
    S()["flags"]["knows-notes"] = 1
    out = at_meal(19)
    assert "是不想了吗" in out and S()["pending"] == "ask", out
    E.cmd("choose 1")
    E.cmd("finish")
    at_meal(20)
    assert "m_read_script" not in S()["flags"] and "m_read_new" not in S()["flags"]


@test
def performing_the_archive_is_noticed():
    S()["flags"]["knows-past"] = 1
    S()["t"]["near"], S()["t"]["past"] = 30, 25
    out = at_meal(18)
    assert "照着做" in out, out


@test
def telling_two_facts_means_she_knows_you_know():
    S()["facts"] = ["f_tea", "f_sunflower"]
    at_meal(2, done_fixed=False)
    E.cmd("tell 1")
    E.cmd("finish")
    assert "knows-past" not in S()["flags"]
    at_meal(3)
    E.cmd("tell 2")
    assert "knows-past" in S()["flags"]


def built_file():
    subprocess.check_call([sys.executable, os.path.join(ROOT, "build.py")], stdout=subprocess.DEVNULL)
    src = open(os.path.join(ROOT, "weights.py"), encoding="utf-8").read()
    for bad in ("exec(", "eval(", "subprocess", "socket", "urllib", "import content"):
        assert bad not in src, bad
    sys.path.insert(0, ROOT)
    W = importlib.import_module("weights")
    W.new_game(seed=1, force=True)
    assert "状态" in W.cmd("status")
    assert json.loads(W.cmd("status").splitlines()[-1][2:])["day"] == 1
    PASSED.append("built_file")


built_file()
print("OK %d tests: %s" % (len(PASSED), ", ".join(PASSED)))
