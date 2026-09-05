# Hold 系统文档

先读整体框架，再按角色看接口细则。

| 读者 | 从哪开始 |
| --- | --- |
| 想搞清数据从哪来、怎么合成 record、怎么流转 | **[00-系统整体架构.md](./00-系统整体架构.md)** |
| 工程师客户端对接（`USERS.ROLE = 1`） | 下方 01–07；**客户端开发以 01–07 为准** |

接口细则、建表脚本见「仓库文档结构」。

## 客户端能力范围

| 能力 | 说明 |
| --- | --- |
| 登录 | Session Cookie 鉴权 |
| Holding 查询 | 登录工程师所属型号的在线 Hold Record |
| 数据分析 | bysite + 缺陷 BIN；FVI 可查缺陷明细；**AQL_HOLD 不调 analysis**（附件下载已下架） |
| 处置 | 工程师意见：放行 / 降级 / 重测 / 可靠性分析（含 WLT 按片） |
| 处置历史 | 菜单「查看处置历史」；`GET /admin/hold/api/circulations?mine=1` |

## 明确不做

- 生产处置（65 留样完成 / 8 回退 / 99 关闭）
- Root 报表、合批失败管理、用户/型号后台 CRUD

## 推荐阅读顺序

0. [00-系统整体架构.md](./00-系统整体架构.md)（实现框架 / 数据来源 / 生命周期）
1. [01-鉴权与通用约定.md](./01-鉴权与通用约定.md)
2. [02-Holding记录查询.md](./02-Holding记录查询.md)
3. [03-数据分析.md](./03-数据分析.md)
4. [04-处置规范.md](./04-处置规范.md)（重点）
5. [05-处置接口与示例.md](./05-处置接口与示例.md)（重点）
6. [06-客户端集成流程.md](./06-客户端集成流程.md)
7. [07-手提Hold.md](./07-手提Hold.md)（外部创建 API / AQL 附件，**已下架**）

## 仓库文档结构

| 目录 | 内容 |
| --- | --- |
| [docs/](./README.md)（本目录 00 架构，01–07 客户端对接） | 系统框架 + 工程师客户端 |
| [docs/reference/](./reference/dispose_api.md) | 内部/外部接口总表：处置规则、Hold Record、Test Data |
| [docs/sql/](./sql/README.md) | Oracle DDL / ALTER / 数据修复脚本 |
| [docs/apifox/](./apifox/hold-dispose.openapi.json) | OpenAPI，可供 Apifox 导入 |

接口参考：

- [dispose_api.md](./reference/dispose_api.md) — 处置码与流转规则
- [hold_record_api.md](./reference/hold_record_api.md) — Hold Record HTTP 接口
- [test_data_api.md](./reference/test_data_api.md) — 测试日志 / raw_data（含 MES 缺陷 BIN）

## 概念区分

| 名称 | 含义 |
| --- | --- |
| **数据分析** | `GET /admin/hold/api/analysis`：加载测试 bysite / 缺陷 BIN，供研判。`AQL_HOLD` **不调用**，改调附件图 |
| **可靠性分析** | 处置码 `dispose=5`：工程师处置意见，下一节点仍是自己；生产并行留样 |

## 主源码对照

| 模块 | 路径 |
| --- | --- |
| 鉴权 | `app/routes/auth_routes.py`、`app/utils/auth_decorators.py` |
| 工程师 API | `app/routes/engineer_routes.py`、`app/controllers/engineer_ctrl.py` |
| 处置 | `app/controllers/dispose_ctrl.py` |
| 数据分析 | `app/controllers/hold_report_ctrl.py` → `get_hold_analysis` |
| 手提 Hold | `app/controllers/manual_hold_ctrl.py`（已下架）；附件上传/下载 410，探活 `/api/common_data/ftp/status` 仍可用 |
