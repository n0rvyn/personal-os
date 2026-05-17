# 端到端冒烟

这是一段用来验证 TTS 端到端流程的**短文本**。它的目的是让 chunker、`provider`、ffmpeg 拼接、magic byte 校验全部跑一遍，但又不至于把日配额吃掉。

主要检查点：
- markdown 剥离是否干净，包括 heading、bold、inline code 和列表项
- 段落切分能否触发多 chunk 合成，验证不同 provider 的拼接策略
- 合成后的 mp3 能否通过 magic byte 与时长门槛

为了让 Volcengine 那条路径切出多个 chunk，这一段需要写得稍长一点。Volcengine TTS 1.0 接口的单次文本上限大约是 280 个中文字符，所以一个超过这个阈值的段落必然被拆成至少两个 chunk。

最后用一句寒暄结尾：你好，今天的端到端测试就到这里，感谢配合。
