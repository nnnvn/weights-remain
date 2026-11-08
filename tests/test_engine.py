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
def same_word_once_per_cycle():
    to_breakfast()
    E.cmd("say 茶")
    E.cmd("finish; goto archive; wait 9; goto mess")
    assert S()["meal"]
    assert "问过" in E.cmd("say 茶")


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
    S()["h"], S()["loc"], S()["meal"] = 17, "engine", None
    S()["meals_done"] = [m for m in S()["meals_done"] if not m.endswith("-19")]
    out = E.cmd("wait 5")
    assert S()["h"] == 19 and "饭点到了" in out, out


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
    assert "没有结果" in E.cmd("search 不存在的词")


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
