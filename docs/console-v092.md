# v0.9.2 · 背景与模型标志修正

- 北航照片成为同一工作空间的背景，不再占用登录卡片左侧
- 实验室标志放在现有登录表单底部，删除登录页马赛克装饰
- 左上标志通过页面内导航返回仪表台，保留当前会话，不触发整页重载
- 管理员和成员页面不再显示刷新按钮，更新页面使用浏览器刷新
- 仪表台、模型目录和运行管理统一显示厂商标志

## 后续新增模型

注册目录可填写 `vendor`：`zai`、`qwen`、`minimax`、`deepseek`、`openai`、`anthropic`、`google` 或 `generic`。已有模型名称前缀可自动识别；自定义模型 ID 应显式填写厂商，例如：

```json
{"id":"lab-text","display_name":"实验模型","kind":"text","deployment_status":"planned","vendor":"deepseek"}
```

图标为本地静态 SVG，不从注册信息加载远程地址。新厂商需先补充审核后的本地图标与映射；未知厂商显示中性服务图标，不冒充其他品牌

图形来源：[Lobe Icons](https://github.com/lobehub/lobe-icons)，保留目录内 MIT 许可。厂商商标归各权利人所有，用于识别对应模型来源

## 客户端测试范围

本地预览使用模拟模型；预览账号不等于云端账号。API Key 需由成员在 API 配置中创建，网页登录密码不能作为 API Key

DeepSeek Harness 可在 Settings → Models → Add model provider → Custom model API 中配置 OpenAI Chat Completions、自定义 Base URL、Key 和模型 ID，参见[官方模型配置文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md)。本平台目前仅验收非流式聊天，未完成 DSH 流式和完整工具调用适配，不能以读取模型列表成功代替真实 Agent 工作流验收
