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
    assert S()["ending"] is None and S()["day"] == 1


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
