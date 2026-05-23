# Lumeri Public / Private Split

Lumeri 的公开策略采用三层模型：公开框架、需审查示例、私有护城河。

## Layer 1: Public Framework

可以公开：

- public README
- 工作流说明
- execution skill 的执行纪律
- demo brief 模板
- copyable prompts
- 依赖边界说明
- release 前自查清单
- 不含真实素材的架构概览

这一层用于：

- 让外部用户理解 Lumeri 是什么
- 降低误解和过度期待
- 建立可信边界
- 让未来开源或展示时有稳定表达

## Layer 2: Reviewed Public Examples

经过人工审查后可以公开：

- 假素材项目
- 占位素材名
- 虚构品牌、虚构活动、虚构文案
- 公共授权素材或自制演示素材
- 不含真实账号和私有路径的截图
- 不暴露完整私有 prompt 链的片段化示例

发布前必须检查：

- 是否包含真实姓名、邮箱、账号头像或客户名
- 是否包含本机绝对路径、token、API key 或 OAuth state
- 是否包含真实用户素材或第三方素材归档
- 是否能从截图里反推出私有项目、客户、素材来源或模型输入
- 是否违反素材、字体、音乐、Blender 插件或模型服务条款

## Layer 3: Private Moat

必须保留私有：

- API key、token、secret、OAuth client secret
- 真实模型输入 TXT
- 私有 planner prompt 链
- 私有 prompt slimming / router 调参记录
- benchmark、回归样本、内部评分表
- 真实用户素材、客户案例、客户文案
- 第三方素材归档、下载目录、研究截图、录屏库
- 本机账号目录 `~/.gemia/accounts/<account>/`
- Agent bridge 队列、自动化日志、内部任务记录
- 商业上可直接复刻优势的组合工作流

## 判断规则

如果一个文件公开后，别人可以直接复刻 Lumeri 的真实演示优势、真实素材效果、客户案例能力或内部 planner 调优结果，它就不属于 public layer。

如果一个文件只是说明流程、边界、假 demo 和安全使用方法，它通常可以进入 public layer。

## 对外命名

- 对外统一叫 **Lumeri**。
- **Gemia** 只作为历史/内部工程名解释。
- Python 包名、旧路径和备份名中仍可能出现 `gemia`，但不应作为公开主品牌。

## 发布前检查清单

- 没有 secret。
- 没有真实账号。
- 没有真实模型输入 TXT。
- 没有真实客户或真实素材。
- 没有私有 prompt 链。
- 没有第三方素材归档。
- demo 明确是假项目。
- 文档明确 Lumeri public kit 不等于完整内部版。
