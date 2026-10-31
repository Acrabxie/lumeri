# Quanta

[English](README.md) | 简体中文

Lumeri Quanta 是下一代 PPT：会播放、会停下来等观众、会分支，也记得观众选过什么。一份
Quanta 作品就是一个 `.qua` 文件。

| 路径 | 内容 | 许可 |
|---|---|---|
| [`format/SPEC.md`](format/SPEC.md) | `.qua` 文件格式第 4 版（英文） | CC0 1.0 |
| [`format/examples/`](format/examples/) | 四个样片，都通过了 Lumeri 的校验 | CC0 1.0 |

只看规范就能写出 `.qua` 的读取器、写入器、播放器或转换器。Quanta 的排版引擎已经在
[`gemia/quanta/`](../gemia/quanta/)，画布渲染器 LumenFrame 在
[`lumenframe/`](../lumenframe/)。第 4 版的读取器、播放器和编辑器还没放进这个仓库，之后会以
MIT 许可放到这里。

## 许可

[`format/`](format/) 里的一切都按 [CC0 1.0](format/LICENSE) 贡献给公有领域：可以用在任何产品里、
配任何许可，不用问，也不用署名。

仓库里其他所有内容，包括 Quanta 的代码，都按仓库的 [MIT 许可](../LICENSE)。

产品页：[quanta.lumeri.io](https://quanta.lumeri.io/)
