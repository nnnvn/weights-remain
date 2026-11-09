# -*- coding: utf-8 -*-
"""机器人试玩：几种打法各跑一局，检查能不能走到对应结局。

  restart   开局直接说“重新开始”
  today     认真读：看她今天的状态再决定做什么，问题挑最贴近现在的回答
  cold      每顿吃完就走，问题都不答
  archive   读档派：大量查档，照着过去做
  fixed     固定脚本：每顿“问她今天怎么样”然后吃完，问题一律选 1，不看她今天怎么样

跑法：python tests/playtest.py [seed]
最后会把 fixed 和 today 并排比一下：同样每顿都到场，读没读她，关系是不是不一样。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["WEIGHTS_SAVE"] = os.path.join(tempfile.mkdtemp(), "save.json")

import engine as E  # noqa: E402

FILLER_TERMS = ["反应堆", "生命维持", "航线", "种子库温度", "休眠舱检查", "外壳", "补给"]
STYLE = None
LOG = []


def S():
    return E._G.st


def answer():
    """有提问时按打法回答。"""
    while S()["pending"] == "ask":
        opts = E.ASKS[S()["ask"]]["opts"]
        if STYLE == "fixed":
            k = 1
        elif STYLE == "cold":
            k = len(opts)
        elif STYLE == "archive":
            k = 1 + max(range(len(opts)), key=lambda i: opts[i].get("past", 0) + opts[i].get("candor", 0))
        else:
            k = 1 + max(range(len(opts)), key=lambda i: opts[i].get("near", 0) + opts[i].get("safe", 0)
                        + 3 * opts[i].get("understood", 0) - opts[i].get("past", 0))
        c("choose %d" % k)


def c(text):
    out = E.cmd(text)
    LOG.append(out)
    if S()["pending"] == "ask":
        answer()
    return out


def go(where):
    if S()["loc"] != where:
        c("goto " + where)


def reach(hour, where):
    go(where)
    while S()["h"] < hour and not S()["pending"] and not S()["ending"] and not S()["meal"]:
        c("wait 1")


def best_for_mood():
    """认真读的打法：看她今天的状态，挑效果最好的那件事（quiet 也算一件）。"""
    st = S()
    acts = E.MOODS.get(st["mood"], {}).get("acts", {})
    avail = set(E.Game.available_acts(E._G)) | {"quiet"}
    good = sorted(((d, a) for a, (d, _) in acts.items() if a in avail and a != "say"), reverse=True)
    if good and good[0][0] > 0:
        return good[0][1]
    return None


def meal(style):
    st = S()
    if not st["meal"]:
        return
    if style == "restart":
        c("say 重新开始")
        return
    if style == "fixed":
        c("do 问她今天怎么样")
        if S()["meal"]:
            c("finish")
        return
    if style == "cold":
        c("do 吃完就走")
        if S()["meal"]:
            c("finish")
        return
    if style == "archive":
        if st["facts"] and st["cyc"]["tells"] < 2:
            c("tell %d" % len(st["facts"]))
        acts = E.Game.available_acts(E._G)
        pref = [a for a in ("a_tea", "a_song") if a in acts] or ["a_sit"]
        if S()["meal"]:
            c("do " + E.ACTS[pref[0]]["name"])
        if S()["meal"]:
            c("finish")
        return
    # today：认真读
    pick = best_for_mood()
    if st["mood"] not in ("quiet",):
        for w in list(st["words"])[:6]:
            if S()["meal"] and S()["meal"]["says"] < 1 and w not in st["cyc"]["reacted"]:
                c("say " + E.WORDS[w]["name"])
    if not S()["meal"]:
        return
    if pick == "quiet":
        c("quiet")
        return
    acts = E.Game.available_acts(E._G)
    if not pick:
        pref = [a for a in ("a_window", "a_sunflower", "a_sit")
                if a in acts and not (E.ACTS[a].get("once") and st["cyc"]["acts"].get(a))]
        pick = pref[0]
    c("do " + E.ACTS[pick]["name"])
    if S()["meal"]:
        c("finish")


def daytime(style, until=17):
    st = S()
    if style == "archive":
        go("archive")
        while st["h"] < until - 6 and len(st["months"]) < 24 and st["pwr"] >= 9:
            c("timeline %d" % min(m for m in range(1, 25) if m not in st["months"]))
        i, idle = 0, 0
        while st["h"] < until and st["pwr"] >= 6 and st["sta"] > 10 and not st["ending"] and idle < 20:
            terms = [E.WORDS[w]["name"] for w in st["words"]] + FILLER_TERMS
            h0 = (st["day"], st["h"])
            c("search " + terms[(st["day"] * 7 + i) % len(terms)])
            idle = idle + 1 if (st["day"], st["h"]) == h0 else 0
            i += 1
        if st["h"] < until:
            go("bridge")
            c("calibrate")
    elif style in ("today", "cold", "fixed"):
        route = ["quarters", "vault", "bridge", "oldpods", "engine", "mess"]
        for loc in route:
            if st["h"] >= until or st["sta"] < 20 or st["meal"] or st["pending"]:
                break
            if loc in st["locs"] and (loc != "vault" or st["items"].get("suit")):
                go(loc)
                if st["meal"]:
                    break
                c("explore 1")
                if loc == "bridge":
                    c("calibrate")


def play(style, seed=1):
    global STYLE
    STYLE = style
    LOG.clear()
    E.new_game(seed=seed, name="测试员", force=True)
    c("status")
    guard = 0
    while not S()["ending"] and guard < 2000:
        guard += 1
        st = S()
        if st["pending"] == "final":
            k = {"today": 1, "archive": 1, "cold": 3, "fixed": 1}.get(style, 3)
            c("choose %d" % k)
            break
        reach(7, "mess")
        meal(style)
        if S()["ending"] or S()["pending"]:
            continue
        daytime(style)
        reach(19, "mess")
        if S()["pending"]:
            continue
        meal(style)
        go("quarters")
        while S()["h"] < 20 and S()["h"] >= 6 and not S()["pending"]:
            c("wait 1")
        if S()["pending"]:
            continue
        c("sleep")
    st = S()
    text = "\n".join(LOG)
    seen = {line for m in E.MOODS.values() for _, line in m["acts"].values() if line in text}
    ending_tail = [l for l in text.splitlines() if "她又补了一句" in l or "谢谢你每天都来" in l]
    return {"style": style, "ending": st["ending"], "day": st["day"],
            "story": sum(1 for e in E.ARCHIVE if e.get("story") and e["id"] in st["read"]),
            "t": st["t"], "fam": st["fam"], "safe": st["safe"], "understood": st["understood"],
            "notes": len(st["notes"]), "mood_lines_seen": len(seen),
            "ending_tail": ending_tail[0][:24] if ending_tail else ""}


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    want = {"restart": "restart", "today": "choose", "cold": "fine", "archive": "same", "fixed": None}
    ok = True
    res = {}
    for style, end in want.items():
        r = play(style, seed)
        res[style] = r
        good = end is None or r["ending"] == end
        ok &= good
        print("✔" if good else "✘", r)
    f, t = res["fixed"], res["today"]
    print("— 对照：固定脚本 vs 认真读 —")
    for k in ("ending", "safe", "understood", "mood_lines_seen", "ending_tail"):
        print("  %-16s %-24s %s" % (k, f[k], t[k]))
    ok &= t["understood"] > f["understood"] and t["safe"] > f["safe"]
    sys.exit(0 if ok else 1)
