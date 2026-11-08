# -*- coding: utf-8 -*-
"""机器人试玩：用四种打法各跑一局，检查能不能走到对应结局。

跑法：python tests/playtest.py [seed]
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["WEIGHTS_SAVE"] = os.path.join(tempfile.mkdtemp(), "save.json")

import engine as E  # noqa: E402

FILLER_TERMS = ["反应堆", "生命维持", "航线", "种子库温度", "休眠舱检查", "外壳", "补给"]


def S():
    return E._G.st


def go(where):
    if S()["loc"] != where:
        E.cmd("goto " + where)


def reach(hour, where):
    """走到 where，并等到 hour 点。"""
    go(where)
    while S()["h"] < hour and not S()["pending"] and not S()["ending"]:
        E.cmd("wait 1")


def meal(style):
    st = S()
    if not st["meal"]:
        return
    if style == "restart":
        E.cmd("say 重新开始")
        return
    if style == "today":
        for w in list(st["words"])[:6]:
            if st["meal"] and st["meal"]["says"] < 2 and w not in st["cyc"]["reacted"]:
                E.cmd("say " + E.WORDS[w]["name"])
        acts = E.Game.available_acts(E._G)
        pref = [a for a in ("a_window", "a_sunflower", "a_ask", "a_sit") if a in acts]
        E.cmd("do " + E.ACTS[pref[0]]["name"])
        E.cmd("finish")
    elif style == "archive":
        if st["facts"] and st["cyc"]["tells"] < 2:
            E.cmd("tell %d" % (len(st["facts"])))
        acts = E.Game.available_acts(E._G)
        pref = [a for a in ("a_tea", "a_song") if a in acts] or ["a_sit"]
        E.cmd("do " + E.ACTS[pref[0]]["name"])
        E.cmd("finish")
    elif style == "cold":
        E.cmd("do 吃完就走")
        E.cmd("finish")


def daytime(style, until=17):
    st = S()
    if style == "archive":
        go("archive")
        while st["h"] < until - 6 and len(st["months"]) < 24 and st["pwr"] >= 9:
            E.cmd("timeline %d" % min(m for m in range(1, 25) if m not in st["months"]))
        i, idle = 0, 0
        while st["h"] < until and st["pwr"] >= 6 and st["sta"] > 10 and not st["ending"] and idle < 20:
            terms = [E.WORDS[w]["name"] for w in st["words"]] + FILLER_TERMS
            h0 = (st["day"], st["h"])
            E.cmd("search " + terms[(st["day"] * 7 + i) % len(terms)])
            idle = idle + 1 if (st["day"], st["h"]) == h0 else 0
            i += 1
        while st["h"] < until and len(st["months"]) < 24 and st["pwr"] >= 3:
            E.cmd("timeline %d" % min(m for m in range(1, 25) if m not in st["months"]))
        if st["h"] < until:
            go("bridge")
            E.cmd("calibrate")
    else:
        route = ["quarters", "vault", "bridge", "oldpods", "engine", "mess"]
        for loc in route:
            if st["h"] >= until or st["sta"] < 20:
                break
            if loc in st["locs"] and (loc != "vault" or st["items"].get("suit")):
                go(loc)
                E.cmd("explore 1 stop=")
                if loc == "bridge":
                    E.cmd("calibrate")
                if loc == "engine" and st["items"].get("part"):
                    E.cmd("repair")


def play(style, seed=1):
    E.new_game(seed=seed, name="测试员", force=True)
    E.cmd("status")
    guard = 0
    while not S()["ending"] and guard < 2000:
        guard += 1
        st = S()
        if st["pending"] == "final":
            k = {"today": 1, "archive": 1, "cold": 2}.get(style, 3)
            E.cmd("choose %d" % k)
            break
        reach(7, "mess")
        meal(style)
        if S()["ending"]:
            break
        daytime(style)
        reach(19, "mess")
        if S()["pending"]:
            continue
        meal(style)
        go("quarters")
        while S()["h"] < 20 and not S()["pending"]:
            E.cmd("wait 1")
        if S()["pending"]:
            continue
        E.cmd("sleep")
    st = S()
    return {"style": style, "ending": st["ending"], "day": st["day"],
            "progress": round(E._G.progress() * 100, 1), "t": st["t"],
            "fam": st["fam"], "safe": st["safe"], "notes": len(st["notes"]),
            "facts": len(st["facts"])}


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    want = {"restart": "restart", "today": "choose", "cold": "fine", "archive": "same"}
    ok = True
    for style, end in want.items():
        r = play(style, seed)
        mark = "✔" if r["ending"] == end else "✘"
        ok &= r["ending"] == end
        print(mark, r)
    sys.exit(0 if ok else 1)
