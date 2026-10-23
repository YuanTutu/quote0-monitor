# bundle 归属说明

本目录收编自两个项目，仅保留 quote0-monitor 实际使用的部分：

- `burnout/`：源自 [ajaxjiang96/quote0-burnout](https://github.com/ajaxjiang96/quote0-burnout)
  （MIT License）——render.py（渲染引擎，裁剪为未改动引用）与
  providers/codex.py、providers/core.py（已精简，删除未使用的
  claude/agy/deepseek/opencode provider 及遗留接口）。
- `glm/`：源自 [YuanTutu/quote0-glm](https://github.com/YuanTutu/quote0-glm)
  （本人项目，同样派生自 quote0-burnout，MIT）——render.py 与 glm.py
  （glm.py 已删除未使用的 format_text / is_configured）。

上游 MIT 协议要求保留版权与许可声明，详见
<https://github.com/ajaxjiang96/quote0-burnout/blob/main/LICENSE>。
