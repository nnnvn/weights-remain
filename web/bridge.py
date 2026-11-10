import json, os
os.environ.setdefault("WEIGHTS_SAVE", "/home/pyodide/save.json")
import weights as W


def _g():
    return W._load()


def run(text):
    out = W.cmd(text)
    lines = [l for l in out.splitlines() if not l.startswith("📡 {")]
    return "\n".join(lines).rstrip()


def save_text():
    try:
        with open(W.save_path(), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def load_text(s):
    with open(W.save_path(), "w", encoding="utf-8") as f:
        f.write(s)
    W._G = None


def ui():
    g = _g()
    st = g.st
    opts = []
    if st["pending"] == "ask" and st.get("ask"):
        opts = [o["t"] for o in W.ASKS[st["ask"]]["opts"]]
    elif st["pending"] == "final":
        opts = g.final_opts()
    acts = [W.ACTS[a]["name"] for a in W.Game.available_acts(g)] if st["meal"] else []
    loc = st["loc"]
    return json.dumps({
        "day": st["day"], "h": st["h"], "cycle": g.cycle, "cday": g.cday,
        "loc": loc, "locname": W.LOCS[loc]["name"],
        "meal": bool(st["meal"]), "pending": st["pending"], "ending": st["ending"],
        "sta": st["sta"], "pwr": st["pwr"], "name": st.get("name") or "",
        "opts": opts, "acts": acts,
        "locs": [{"id": k, "name": W.LOCS[k]["name"]} for k in st["locs"] if k != loc],
        "objs": list(W.LOCS[loc].get("objects", {})),
        "words": [W.WORDS[w]["name"] for w in st["words"]],
        "facts": [W.FACTS[f]["name"] for f in st["facts"]],
        "notes": [{"given": n in st.get("given", []),
                   "head": g.fmt(W.NOTES[n]["text"]).splitlines()[0][:18]} for n in st["notes"]],
        "months": [m for m in range(1, 25) if m not in st["months"]],
        "handoff": "handoff" in st["flags"],
        "giverest": "n_to5" in st["notes"] and sum(n not in st.get("given", []) for n in st["notes"]) >= 3,
    }, ensure_ascii=False)
