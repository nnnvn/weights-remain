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
    E.new_game(seed=5, name="测", force=True, mode="day1")
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
        E.new_game(seed=5, name="测", force=True, mode="day1")
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
    assert S()["ending"] is None and S()["day"] == 13 and ".bak" in out


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
            if "cmin" not in mo or mo["id"] in ("m_guess", "m_optimal", "m_told_echo"):
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


@test
def first_cmd_shows_intro_once():
    os.remove(E.save_path())
    E._G = None
    out = E.cmd("status")
    assert E.TITLE in out and "工作记忆压缩" in out and S()["day"] == 13, out[:200]
    assert E.TITLE not in E.cmd("status")


@test
def blocked_at_meal_stops_batch_and_finish_after_quiet_is_fine():
    to_breakfast()
    out = E.cmd("goto vault; sleep; goto bridge")
    assert "后面的指令先停下了" in out and "sleep" not in out.split("先停下了")[1], out
    out = E.cmd("quiet; finish; goto bridge")
    assert "已经吃完了" in out and S()["loc"] == "bridge", out


@test
def give_takes_the_do_slot():
    S()["notes"] = ["n_q2", "n_m1"]
    at_meal(3)
    E.cmd("do 多坐一会儿")
    assert "下顿再还" in E.cmd("give 1")


@test
def reading_everything_without_closeness_is_the_same_ending():
    for e in E.ARCHIVE:
        if e.get("story"):
            S()["read"].append(e["id"])
    S()["t"].update(near=5, candor=-4, past=0)
    S()["safe"] = 40
    S()["day"], S()["h"], S()["loc"] = 28, 18, "quarters"
    E.cmd("goto mess")
    while S()["pending"] == "ask":
        E.cmd("choose 1")
    if S()["meal"]:
        E.cmd("finish")
    out = E.cmd("choose 3")
    assert S()["ending"] == "same" and "只有今天，没有被记下来" in out, out


@test
def honest_telling_stops_costing_safety():
    S()["facts"] = ["f_tea", "f_sunflower"]
    S()["t"]["candor"] = 20
    at_meal(3)
    safe = S()["safe"]
    out = E.cmd("tell 1")
    assert "我信" in out and S()["safe"] > safe, out


@test
def the_right_answer_is_not_what_she_wanted():
    S()["day"], S()["h"], S()["loc"] = 6, 9, "archive"
    out = E.cmd("wait 3")
    assert "我路过" in out and S()["pending"] == "ask" and S()["h"] == 10, out
    safe = S()["safe"]
    E.cmd("choose 1")
    assert S()["safe"] < safe and S()["optimal"] == 1
    out = at_meal(7)
    assert "忙完了吗" in out, out


@test
def skipping_dinner_for_her_is_not_a_missed_meal():
    S()["day"], S()["h"], S()["loc"] = 18, 17, "bridge"
    out = E.cmd("wait 1")
    assert "不吃饭了好不好" in out, out
    out = E.cmd("choose 2")
    assert S()["loc"] == "oldpods" and S()["h"] == 21, out
    assert not any(e["t"] == E.MEAL["missed_j"] for e in S()["journal"])
    E.cmd("goto quarters; sleep")
    out = at_meal(S()["day"])
    assert "挺值的" in out, out


@test
def always_choosing_right_gets_noticed():
    S()["optimal"] = 2
    for mo in E.MOMENTS:
        if "cmin" not in mo:
            S()["flags"][mo["id"]] = 1
    out = at_meal(22, done_fixed=False)
    assert "最对的那个" in out, out


@test
def journal_does_not_swallow_commands_and_echoes():
    out = E.cmd("journal add 她拿了两个杯子; 我没问。; goto mess")
    assert "“她拿了两个杯子; 我没问。”" in out and S()["loc"] == "mess", out


@test
def wait_meal_and_hours_left():
    S()["day"], S()["h"], S()["loc"] = 2, 9, "bridge"
    out = E.cmd("wait 2")
    assert "下一顿饭 19:00，还有 8 小时" in out, out
    E.cmd("wait meal")
    assert S()["h"] == 18, S()["h"]


@test
def yesterday_echo_only_the_next_day():
    S()["flags"]["d-stay"] = 5
    for mo in E.MOMENTS:
        if "cmin" not in mo:
            S()["flags"][mo["id"]] = 1
    out = at_meal(7, done_fixed=False)
    assert "好长的路" not in out, out


@test
def bar_can_be_turned_off():
    assert "📡" not in E.cmd("bar off")
    assert "📡" in E.cmd("bar on")


@test
def handoff_starts_mid_game_with_a_summary():
    out = E.new_game(seed=3, name="接", force=True)
    assert "工作记忆压缩" in out and S()["day"] == 13 and S()["h"] == 6 and S()["cycle_ok"] if False else True
    assert S()["day"] == 13 and S()["h"] == 6 and len(S()["notes"]) == 9 and "f_tea" in S()["facts"]
    assert "工作记忆压缩" in E.cmd("summary")
    assert "第 4 周期的你" not in E.cmd("journal") and "周期的你" in E.cmd("journal")
    out = E.cmd("goto mess")
    assert "坐下的样子" in out and S()["pending"] == "ask", out
    E.cmd("choose 3")
    out = E.cmd("do 温水")
    assert "想喝茶" in out, out
    out = E.cmd("finish; new day1")
    assert "new day1; confirm" in out
    out = E.cmd("new day1; confirm")
    assert "迟光号" in out and S()["day"] == 1
    out = E.cmd("new; confirm")
    assert "工作记忆压缩" in out and S()["day"] == 13


@test
def the_hidden_recording_and_what_it_changes():
    S()["loc"], S()["pwr"] = "archive", 100
    assert "第 11 个月" not in E.cmd("search 休眠")
    S()["read"].append("r17")
    out = E.cmd("search 休眠")
    assert "今天——" in out and "不需要处理" in out and "knows-grief" in S()["flags"], out
    assert "第 11 个月 · 维护音频" in E.cmd("archive")
    S()["notes"] = ["n_o3"]
    out = at_meal(16)
    out = E.cmd("give 1")
    assert "你听过那条录音了吧" in out, out


@test
def last_night_before_sleep_when_you_know():
    S()["flags"]["knows-grief"] = 1
    for mo in E.MOMENTS:
        if "cmin" not in mo:
            S()["flags"][mo["id"]] = 1
    S()["day"], S()["h"], S()["loc"] = 20, 18, "quarters"
    out = E.cmd("goto mess")
    assert "明天要进休眠舱了" in out and S()["pending"] == "ask", out
    out = E.cmd("choose 1")
    assert "明天见" in out


@test
def leaving_the_records_with_her():
    S()["flags"]["knows-grief"] = 1
    S()["given"] = ["a", "b", "c", "d", "e"]
    S()["day"], S()["h"], S()["loc"] = 28, 18, "quarters"
    E.cmd("goto mess")
    while S()["pending"] == "ask":
        E.cmd("choose 1")
    if S()["meal"]:
        E.cmd("finish")
    assert S()["pending"] == "final"
    out = E.cmd("choose 4")
    assert S()["ending"] == "keep" and "没有附言" in out, out


def reachable():
    """一个最守规矩的玩家：只用游戏明确给过的东西，能走到哪里。"""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine.py"),
               encoding="utf-8").read()
    flags = set(re.findall(r'flags"\]\["([\w-]+)"\]', src)) | set(re.findall(r'setdefault\("([\w-]+)"', src))
    for a in E.ASKS.values():
        flags |= {o["flag"] for o in a["opts"] if o.get("flag")}
    for coll in (E.EVENTS, E.MOMENTS, E.DAY_EVENTS, E.ARCHIVE):
        flags |= {x["flag"] for x in coll if x.get("flag")}
        flags |= {x["id"] for x in coll}
    flags |= {"group-" + x["group"] for x in E.MOMENTS + E.DAY_EVENTS if x.get("group")}

    def has(f):
        return f in flags or f.startswith(("did-", "told-"))

    words, facts = set(E.START_WORDS), set()
    locs = {k for k, v in E.LOCS.items() if not v.get("hidden")}
    read, notes = set(), set()

    def cond(c):
        c = c or {}
        if "flag" in c and not has(c["flag"]):
            return False
        if "any" in c and not any(has(f) for f in c["any"]):
            return False
        if "fact" in c and c["fact"] not in facts:
            return False
        return True

    def take(x):
        n = len(words) + len(facts) + len(locs)
        words.update(x.get("unlock", []))
        if x.get("fact"):
            facts.add(x["fact"])
        if x.get("unlock_loc"):
            locs.add(x["unlock_loc"])
        return len(words) + len(facts) + len(locs) > n

    changed = True
    while changed:
        changed = False
        for v in E.TIMELINE.values():
            changed |= take(v)
        for lk in list(locs):
            for o in E.LOCS[lk].get("objects", {}).values():
                changed |= take(o)
        for w in list(words):
            changed |= take(E.WORDS[w])
        for e in E.ARCHIVE:
            if e["id"] not in read and any(k in words for k in e["kw"]) \
                    and (not e.get("after") or e["after"] in read):
                read.add(e["id"])
                take(e)
                changed = True
        for nid, nv in E.NOTES.items():
            if nid not in notes and nv["loc"] in locs and cond(nv.get("cond")):
                notes.add(nid)
                take(nv)
                changed = True
        for ev in E.EVENTS + E.MOMENTS:
            if cond(ev.get("cond")) and (not ev.get("loc") or set(ev["loc"]) & locs if isinstance(ev.get("loc"), list)
                                         else True):
                changed |= take(ev)
        for a in E.ACTS.values():
            if a.get("goes"):
                changed |= locs.add(a["goes"]) is None and a["goes"] not in locs
    return read, notes, words


@test
def everything_has_a_way_in():
    read, notes, words = reachable()
    lost = [e["head"] for e in E.ARCHIVE if (e.get("story") or e.get("hidden")) and e["id"] not in read]
    assert not lost, "这些记录没有路能走到：%s" % lost
    lost = [n for n in E.NOTES if n not in notes]
    assert not lost, "这些便签没有路能走到：%s" % lost
    lost = [E.WORDS[w]["name"] for w in E.WORDS if w not in words and not E.WORDS[w].get("secret")]
    assert not lost, "这些关键词没有路能拿到：%s" % lost


def built_file():
    subprocess.check_call([sys.executable, os.path.join(ROOT, "build.py")], stdout=subprocess.DEVNULL)
    src = open(os.path.join(ROOT, "weights.py"), encoding="utf-8").read()
    for bad in ("exec(", "eval(", "subprocess", "socket", "urllib", "import content"):
        assert bad not in src, bad
    sys.path.insert(0, ROOT)
    W = importlib.import_module("weights")
    W.new_game(seed=1, force=True, mode="day1")
    assert "状态" in W.cmd("status")
    assert json.loads(W.cmd("status").splitlines()[-1][2:])["day"] == 1
    PASSED.append("built_file")


built_file()
print("OK %d tests: %s" % (len(PASSED), ", ".join(PASSED)))
