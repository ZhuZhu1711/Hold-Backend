# 工程师客户端对接文档

本文档面向**产品工程师客户端**（`USERS.ROLE = 1`），依据当前后端代码整理。根目录旧文档（`dispose_api.md`、`hold_record_api.md`、`test_data_api.md`）保留作内部参考；**客户端开发请以本目录为准**。

## 客户端能力范围

| 能力 | 说明 |
| --- | --- |
| 登录 | Session Cookie 鉴权 |
| Holding 查询 | 登录工程师所属型号的在线 Hold Record |
| 数据分析 | bysite + 缺陷 BIN；FVI 可查缺陷明细 |
| 处置 | 工程师意见：放行 / 降级 / 重测 / 可靠性分析（含 WLT 按片） |

## 明确不做

- 流转历史列表 / 时间线查询
- 生产处置（65 留样完成 / 8 回退 / 99 关闭）
- Root 报表、合批失败管理、用户/型号后台 CRUD

## 推荐阅读顺序

1. [01-鉴权与通用约定.md](./01-鉴权与通用约定.md)
2. [02-Holding记录查询.md](./02-Holding记录查询.md)
3. [03-数据分析.md](./03-数据分析.md)
4. [04-处置规范.md](./04-处置规范.md)（重点）
5. [05-处置接口与示例.md](./05-处置接口与示例.md)（重点）
6. [06-客户端集成流程.md](./06-客户端集成流程.md)

## 概念区分

| 名称 | 含义 |
| --- | --- |
| **数据分析** | `GET /admin/hold/api/analysis`：加载测试 bysite / 缺陷 BIN，供研判 |
| **可靠性分析** | 处置码 `dispose=5`：工程师处置意见，下一节点仍是自己；生产并行留样 |

## 主源码对照

| 模块 | 路径 |
| --- | --- |
| 鉴权 | `app/routes/auth_routes.py`、`app/utils/auth_decorators.py` |
| 工程师 API | `app/routes/engineer_routes.py`、`app/controllers/engineer_ctrl.py` |
| 处置 | `app/controllers/dispose_ctrl.py` |
| 数据分析 | `app/controllers/hold_report_ctrl.py` → `get_hold_analysis` |
