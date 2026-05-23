# Lumeri Dependency Boundary

本文档说明 Lumeri public workflow kit 的依赖边界。公开层只描述依赖关系和安全使用方式，不再分发第三方产品、密钥、素材或私有运行状态。

## 本地运行边界

Lumeri Desktop 当前依赖本地 7788 sidecar 和本地媒体处理能力。

可以公开说明：

- 本地服务入口形态
- 媒体库、时间轴、planner、执行引擎的大致分工
- 需要本地媒体处理依赖才能处理真实文件
- 输出文件位于本地工作区或用户指定目录

不要公开：

- 用户本机完整目录结构
- 私有 LaunchAgent 细节中的 secret
- 真实运行日志、真实任务队列、真实模型输入正文
- 真实账号目录内容

## 模型与 planner 边界

Lumeri 可以通过 OpenRouter 或其他模型 provider 做 planner。公开文档可以说明“需要模型 provider”，但不能泄露凭证。

可以公开：

- planner provider 是可替换边界
- 模型只应接收经过清洗的项目状态和必要上下文
- `latest.txt` 这类模型输入可观察性用于本地调试

必须私有：

- OpenRouter API key
- Google API key
- Authorization headers
- 完整真实模型输入 TXT
- 私有 system prompt 链
- 私有 router/benchmark 细节

## Blender / LumeriLink 边界

LumeriLink to Blender 是可选的本地增强能力。

可以公开：

- Blender 是外部依赖
- LumeriLink 可用于空间效果、三维场景、视差、体积光等
- Blender 不可用时应降级到 2D 或报告依赖缺失

不要公开：

- 第三方插件的未授权再分发
- 付费资产包
- 私有 Blender 场景库
- 客户素材生成的 `.blend` 文件

## 素材边界

Lumeri 的媒体库可以导入视频、图片、音频。public demo 必须使用假素材、公共授权素材或自制素材。

可以公开：

- 假素材名
- 占位素材结构
- 公共授权素材链接（确认许可后）
- 素材类型、时长、用途说明

必须私有：

- 用户真实素材
- 客户视频、图片、音频
- 第三方素材下载归档
- 未确认授权的音乐、字体、图片、视频
- 可反推出真实项目的缩略图、波形、预览图

## 账号与 secret 边界

Lumeri 支持 Google 登录和本地账号隔离。

可以公开：

- 支持 Google 登录
- 账号之间 memory、会话历史、媒体库隔离
- OAuth client id 是公开标识，不等于 secret

必须私有：

- OAuth client secret
- refresh token
- access token
- ID token
- 本地账号 profile 内容
- 真实邮箱、头像、账号名
- `~/.gemia/accounts/<account>/` 下的私有数据

## 输出文件边界

公开输出只应包含安全 demo 结果。

可以公开：

- 假 demo 输出
- 不含个人信息的公共样例
- 抽象化的工作流报告

必须私有：

- 真实客户输出
- 真实用户素材导出的成片
- 含账号、客户、合同、未公开产品信息的输出

## Release-day 检查

公开发布前，必须重新检查：

- 第三方服务条款是否变化
- 素材授权是否可公开
- 截图是否含账号或私有路径
- README 是否清楚说明 public kit 不是完整内部版
- demo 是否只使用假素材和假项目
