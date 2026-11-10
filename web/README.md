# 网页版

发布在 claude.ai 上的 artifact：<https://claude.ai/artifact/GdDJFJPiXi7DRhfjxkeSqw>

浏览器里用 Pyodide 跑同一个 `weights.py`，`bridge.py` 负责去掉状态栏、给页面提供当前能做的事（`ui()`）、存取存档。进度存在浏览器的 localStorage。

重新发布需要的文件（artifact 的 `files`）：

| 路径 | 来源 |
| --- | --- |
| `py/pyodide.asm.wasm` | npm `pyodide@0.26.4` 包里的同名文件，contentType `application/wasm` |
| `py/pyodide-lock.json` | 同上 |
| `py/stdlib.b64.txt` | 包里的 `python_stdlib.zip` 做 base64（artifact 不能发 zip，页面里再还原） |
| `game/weights.py` | 仓库 main 分支的打包文件，contentType `text/plain` |
| `game/bridge.py` | 本目录的 `bridge.py`，contentType `text/plain` |

`pyodide.js` 和 `pyodide.asm.js` 从 jsDelivr 加载（artifact 允许的脚本来源）；其余从页面自己的文件取。

```bash
npm pack pyodide@0.26.4 && tar xzf pyodide-0.26.4.tgz
base64 -w0 package/python_stdlib.zip > stdlib.b64.txt
```
