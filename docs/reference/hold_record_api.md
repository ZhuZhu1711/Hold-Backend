# Hold Record API 对接说明

本文档面向**外部系统 / 第三方对接**，说明 Hold Record 相关 HTTP 接口。

业务规则（处置码、处置单划分）见同目录 [`dispose_api.md`](./dispose_api.md)。

---

## 1. 基本信息

| 项 | 说明 |
| --- | --- |
| 协议 | HTTP / HTTPS |
| 默认端口 | `50001` |
| Base URL 示例 | `http://{host}:50001` |
| 数据格式 | 请求/响应均为 JSON（`Content-Type: application/json`） |
| 字符编码 | UTF-8 |
| 鉴权 | Session Cookie **或** Header `X-Hold-Token`（二选一） |

### 1.1 鉴权流程

**方式 A：登录 Cookie（人类用户 / 已有对接）**

1. 调用 `POST /api/login` 获取 Session Cookie（Cookie 名：`hold_session`）
2. 后续接口请求带上该 Cookie（`credentials: include` / `Cookie: hold_session=...`）
3. 退出：`GET /logout`（清空 Session）

> 生产系统对接若走 Cookie，建议使用**生产 OP 账号**登录后调用生产处置接口。  
> 默认生产 OP 用户 ID = `181`（配置项 `PRODUCTION_OP_ID`）。

**方式 B：固定 Token（推荐给外部系统）**

不必登录。每次请求加 Header：

```
X-Hold-Token: <双方约定的固定值>
```

服务端按启动模式读环境变量：release 用 `HOLD_API_TOKEN`，`--mode debug` 用 `HOLD_API_TOKEN_DEBUG`。与 Header 值一致才放行；未配置则此通道关闭。匹配后不做角色校验。操作人记为系统用户（`SYSTEM_USER_ID=1`）。

```http
GET /admin/hold/api/holding_records?page=1&page_size=20 HTTP/1.1
Host: {host}:50001
X-Hold-Token: <固定值>
Accept: application/json
```

### 1.2 统一响应结构

成功：

```json
{
  "code": 200,
  "msg": "获取成功",
  "data": {}
}
```

分页列表额外字段：

```json
{
  "code": 200,
  "msg": "获取成功",
  "data": [],
  "total": 100,
  "page": 1,
  "page_size": 20,
  "pages": 5
}
```

失败常见 `code`：`400` 参数/业务错误，`401` 未登录，`403` 权限不足，`404` 不存在，`500` 服务异常。

### 1.3 分页约定

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `page` | int | 1 | 页码，从 1 起 |
| `page_size` | int | 20 | 每页条数，最大 200 |

列表类接口**不做全量返回**，请按页拉取。

---

## 2. 领域约定

### 2.1 RECORD_TYPE（处置单大类）

| RECORD_TYPE | 名称 |
| --- | --- |
| `0` | FT异常反馈单 |
| `1` | FVI异常反馈单 |
| `2` | WLT异常反馈单 |

划分规则详见 `dispose_api.md`「处置单划分」。

### 2.2 DISPOSE（处置行为码）

| DISPOSE | 名称 | 发起方 | 说明 |
| --- | --- | --- | --- |
| `0` | 创建 | 系统 | 创建 hold_record 时写入 |
| `1` | 放行 | 工程师 | NEXT → 生产 OP(181) |
| `2` | 降级 | 工程师 | NEXT → 生产 OP(181) |
| `3` | 重测 | 工程师 | NEXT → 生产 OP(181) |
| `5` | 可靠性分析 | 工程师 | NEXT → 操作工程师（自循环） |
| `6` | 分析(返回) | 生产 | **已废弃**，仅历史展示 |
| `7` | 转交 | 工程师 | **当前暂未开放** |
| `8` | 回退 | 生产 | NEXT → 型号工程师 |
| `65` | 留样完成 | 生产 | 不改当前节点 / STATUS |
| `66` | 分析(返回) | 生产 | **已废弃**，仅历史展示 |
| `99` | 关闭 | 系统/root | 记录关闭，不可再处置 |

> 规则细节（`NEXT_OWNER_ID` / `DISPOSED_OWNER_ID`）见 `dispose_api.md`。

### 2.3 STATUS

`FT_HOLD_RECORD.STATUS` 与最近一次处置码同步；`STATUS = 99` 表示已关闭。

当前负责人 = 最新流转记录 `CIRCULATION_HISTORY.NEXT_OWNER_ID`（通过 `LAST_CIRCULATION_ID` 关联）。

---

## 3. 认证

### 3.1 登录

`POST /api/login`

**Body**

```json
{
  "employee_no": "工号",
  "password": "密码",
  "remember": true
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `employee_no` | 是 | 工号 |
| `password` | 是 | 密码 |
| `remember` | 否 | 是否持久 Cookie（默认 `true`，约 30 天） |

**成功响应示例**

```json
{
  "code": 200,
  "msg": "登录成功",
  "data": {
    "id": 181,
    "name": "生产OP",
    "role": 0,
    "employee_no": "xxxx",
    "remember": true,
    "redirect": "/dashboard"
  }
}
```

| ROLE | 含义 |
| --- | --- |
| `0` | root 超级管理员 |
| `1` | 产品工程师 |
| `9` | 生产（生产节点 Hold / 流转查询） |

响应头会 `Set-Cookie: hold_session=...`，后续请求需携带。

### 3.2 退出

`GET /logout`

---

## 4. 查询类 API

以下接口（除特别说明）均需已登录 Session。

### 4.1 当前在线 Hold Record 列表（root）

`GET /admin/hold/api/holding_records`

> 权限：仅 root。  
> 「在线」= MES 关联 hold_info 且 `HOLDING = 0`，**或** `SOURCE=1` 且 `STATUS <> 99`。

**Query**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `product_id` | 否 | 型号模糊匹配 |
| `station` | 否 | 站点模糊匹配 |
| `keyword` | 否 | wafer / lot / hold_code / hold_reason |
| `record_type` | 否 | `0` / `1` / `2` |
| `page` | 否 | 默认 1 |
| `page_size` | 否 | 默认 20 |

**data[] 主要字段**

| 字段 | 说明 |
| --- | --- |
| `ID` | hold_record 主键 |
| `PRODUCT_ID` / `WAFER_ID` / `LOT_ID` / `STATION` | 基础信息 |
| `HOLD_CODE` / `HOLD_REASON` / `HOLD_DTTM` | Hold 信息 |
| `SOURCE` | `0` MES；`1` 手提 |
| `ANNEX_FTP_PATH` / `ANNEX_COUNT` / `IS_AQL_HOLD` | 附件路径、张数、是否 AQL |
| `RECORD_TYPE` / `RECORD_TYPE_NAME` | 处置单类型 |
| `STATUS` / `IS_CLOSED` | 状态 |
| `CURRENT_OWNER_ID` / `CURRENT_OWNER_NAME` | 当前负责人 |
| `LAST_DISPOSE` / `LAST_DISPOSE_LABEL` | 最近处置 |
| `LAST_CIRCULATION_ID` | 最新流转 ID |
| `INFO_CNT` | 关联 info 条数 |

---

### 4.2 按 Wafer 统计 Hold 次数

`GET /admin/hold/api/hold_count`

> 权限：已登录即可。

**Query**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `wafer_id` | 是 | Wafer ID（精确） |

**成功 data**

```json
{
  "wafer_id": "XXXX",
  "hold_count": 3
}
```

---

### 4.3 FVI 缺陷明细

`GET /admin/hold/api/fvi_defect_details`

> 权限：root。工程师侧等价接口见 §6.2。

**Query**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `lot_id` | 是 | Lot ID |
| `line_type` | 否 | 默认 `FT` |

---

### 4.4 Hold 历史统计（图表）

`GET /admin/hold/api/history`

> 权限：root。按 `RECORD_TYPE` 拆分的聚合数据，非明细列表。

**Query**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `product_id` | 是 | 型号 |
| `period_type` | 是 | `month` 或 `week` |
| `year` | 是 | 年 |
| `month` | 条件 | `period_type=month` 时必填（1–12） |
| `week` | 条件 | `period_type=week` 时必填（ISO 周 1–53） |

---

### 4.5 型号下拉选项

`GET /admin/hold/api/products`

> 权限：root。

**Query**：`keyword`（可选，模糊）

---

### 4.6 流转记录查询（全量可读，含他人型号）

`GET /admin/hold/api/circulations`

> 权限：已登录。默认**不按型号归属过滤**。客户端传 `mine=1` 时仅本人相关。

**Query**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `hold_record_id` | 否 | 指定 record |
| `product_id` | 否 | 型号模糊 |
| `wafer_id` / `lot_id` | 否 | 精确/模糊见实现 |
| `dispose` | 否 | 行为码 |
| `keyword` | 否 | wafer/lot/型号/hold_code/备注 |
| `mine` | 否 | `1`：经办人 / 下一 owner / 所属型号 命中当前登录人 |
| `page` / `page_size` | 否 | 分页 |

---

### 4.7 单条 Record 流转时间线

`GET /admin/hold/api/records/{record_id}/circulations`

> 权限：已登录。返回 record 摘要 + 该单全部流转步骤（条数通常较少，未分页）。

**成功 data 结构**

```json
{
  "record": { "ID": 1, "PRODUCT_ID": "...", "STATUS": 1, "...": "..." },
  "circulations": [
    {
      "ID": 10,
      "HOLD_RECORD_ID": 1,
      "DISPOSE": 1,
      "DISPOSE_LABEL": "放行",
      "DISPOSED_OWNER_ID": 12,
      "NEXT_OWNER_ID": 181,
      "DISPOSE_DTTM": "2026-08-01 10:00:00",
      "DISPOSE_DETAIL": "备注"
    }
  ]
}
```

---

### 4.8 待办列表

`GET /admin/hold/api/pending_records`

> 权限：root / 工程师。  
> 条件：最新流转 `NEXT_OWNER_ID` = 当前用户（root 可不传 `owner_id` 看全量，或传 `owner_id` 过滤）。

**Query**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `product_id` | 否 | 型号 |
| `keyword` | 否 | 关键字 |
| `owner_id` | 否 | **仅 root** 可指定负责人 |
| `page` / `page_size` | 否 | 分页 |

---

## 5. 处置类 API（对接重点）

处置成功后会：

1. 向 `CIRCULATION_HISTORY` 插入一条流转  
2. 回写 `FT_HOLD_RECORD.LAST_CIRCULATION_ID`、`STATUS`

**成功 data 统一结构**

```json
{
  "hold_record_id": 123,
  "circulation_id": 456,
  "dispose": 1,
  "dispose_label": "放行",
  "disposed_owner_id": 12,
  "next_owner_id": 181,
  "status": 1
}
```

### 5.1 查询可发起的处置码

`GET /admin/hold/api/dispose_actions`

> 权限：root / 工程师。

**Query**

| 参数 | 说明 |
| --- | --- |
| `group` | 可选：`engineer` / `production` / `system` |

---

### 5.2 工程师 / Root 处置

`POST /admin/hold/api/dispose`

> 权限：root / 工程师。  
> 工程师只能对「当前负责人是自己」的单做 `1/2/3/5`；root 可代操作更广范围。

**Body**

```json
{
  "hold_record_id": 123,
  "dispose": 1,
  "dispose_detail": "可选备注，最长100字"
}
```

| dispose | 含义 |
| --- | --- |
| `1` | 放行 |
| `2` | 降级 |
| `3` | 重测 |
| `5` | 分析 |
| `7` | 转交（**暂屏蔽，调用会失败**） |

---

### 5.3 生产处置（外部生产系统对接入口）

`POST /admin/hold/api/production/dispose`

> **本后台无生产处置 UI，专供外部生产系统调用。**  
> 权限：已登录；操作人应为生产 OP（`USERS.ID = 181`），root 可代操作。  
> 当前负责人必须是生产 OP（最新流转 `NEXT_OWNER_ID = 181`）。

**Body**

```json
{
  "hold_record_id": 123,
  "dispose": 8,
  "dispose_detail": "回退原因（可选，最长100）"
}
```

| dispose | 含义 | 结果 |
| --- | --- | --- |
| `65` | 留样完成 | 不改当前节点 / STATUS |
| `8` | 回退 | NEXT → 型号工程师 |

**调用示例（curl）**

```bash
# 1) 登录（保存 Cookie）
curl -c cookies.txt -X POST "http://{host}:50001/api/login" \
  -H "Content-Type: application/json" \
  -d "{\"employee_no\":\"生产OP工号\",\"password\":\"密码\",\"remember\":true}"

# 2) 生产处置：回退
curl -b cookies.txt -X POST "http://{host}:50001/admin/hold/api/production/dispose" \
  -H "Content-Type: application/json" \
  -d "{\"hold_record_id\":123,\"dispose\":8,\"dispose_detail\":\"产线回退\"}"
```

**典型错误**

| msg 关键字 | 含义 |
| --- | --- |
| 请先登录 | 未带 Session / Cookie 失效 |
| hold_record 不存在 | ID 无效 |
| 记录已关闭 | `STATUS=99`，不可再处置 |
| 非生产 / 仅生产 | 账号或当前负责人不是生产 OP |
| dispose 无效 / 不支持 | 传了非 `6/66/8` 的码 |

---

## 5.4 手提创建 Hold Record（已下架）

`POST /admin/hold/api/manual_hold`、`GET /admin/hold/api/manual_hold/products`、`GET /admin/hold/api/annex_image`、`GET /admin/hold/api/annex_zip` 均返回 **HTTP 410**（`手提 Hold 功能已下架` / `附件 FTP 上传/下载已关闭`）。testlog FTP 探活 `GET /api/common_data/ftp/status` 仍可用。

下架前字段约定见 [`docs/07-手提Hold.md`](./docs/07-手提Hold.md)。

---

## 6. 工程师侧等价接口（可选）

产品工程师后台前缀：`/eng`。数据范围默认限制为**所属型号**（`PRODUCT_INFO.PRO_ENG_ID`）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/eng/api/holding_records` | 所属型号在线 hold；支持 `pending_only=1` 仅待办 |
| GET | `/eng/api/dispose_actions` | 工程师可发起处置码 |
| POST | `/eng/api/dispose` | 工程师处置（同 §5.2） |
| GET | `/eng/api/fvi_defect_details` | 所属型号 FVI 缺陷明细 |

工程师 Web 流转页复用 `/admin/hold/api/circulations`（不按归属过滤）。桌面客户端传 `mine=1`，仅本人相关。

---

## 7. 推荐对接场景

### 场景 A：生产系统完成作业后回写处置

```
登录(生产OP) → POST /admin/hold/api/production/dispose
  dispose=65（留样完成）或 8（回退）
```

### 场景 B：查询某 wafer 历史 hold 次数

```
登录 → GET /admin/hold/api/hold_count?wafer_id=XXX
```

### 场景 C：拉取某 hold_record 流转履历

```
登录 → GET /admin/hold/api/records/{id}/circulations
```

### 场景 D：轮询生产 OP 待办

```
登录(生产OP) → GET /admin/hold/api/pending_records
```

---

## 8. 接口一览

| 方法 | 路径 | 权限 | 用途 |
| --- | --- | --- | --- |
| POST | `/api/login` | 公开 | 登录拿 Session |
| GET | `/logout` | 已登录 | 退出 |
| GET | `/admin/hold/api/holding_records` | root | 在线 hold 列表（分页） |
| GET | `/admin/hold/api/hold_count` | 登录 | wafer hold 次数 |
| GET | `/admin/hold/api/fvi_defect_details` | root | FVI 缺陷明细 |
| GET | `/admin/hold/api/history` | root | 历史聚合统计 |
| GET | `/admin/hold/api/products` | root | 型号选项 |
| GET | `/admin/hold/api/circulations` | 登录 | 流转列表（分页） |
| GET | `/admin/hold/api/records/{id}/circulations` | 登录 | 单条流转时间线 |
| GET | `/admin/hold/api/pending_records` | root/工程师 | 待办（分页） |
| GET | `/admin/hold/api/dispose_actions` | root/工程师 | 处置码说明 |
| POST | `/admin/hold/api/dispose` | root/工程师 | 工程师侧处置 |
| POST | `/admin/hold/api/production/dispose` | 登录(生产OP/root) | **生产侧处置（外部对接）** |
| POST | `/admin/hold/api/manual_hold` | — | **已下架（410）** |
| GET | `/admin/hold/api/manual_hold/products` | — | **已下架（410）** |
| GET | `/admin/hold/api/annex_image` | — | **已下架（410）**，附件 FTP 下载关闭 |
| GET | `/admin/hold/api/annex_zip` | — | **已下架（410）**，附件 FTP 下载关闭 |
| GET | `/eng/api/holding_records` | 工程师 | 所属型号 hold |
| POST | `/eng/api/dispose` | 工程师 | 工程师处置 |

---

## 9. 变更说明

| 日期 | 说明 |
| --- | --- |
| 2026-09-04 | 下架手提 Hold 与附件 FTP 上传/下载（410）；探活 `/api/common_data/ftp/status` 保留 |
| 2026-08-19 | 手提 Hold：型号智能匹配、FT 选站点、WLT 固定 WLT2 / LOT.NO / 勾选片号，附件上限 25 |
| 2026-08-19 | 手提 Hold 创建 API、AQL_HOLD、ANNEX_FTP_PATH 附件图 |
| 2026-08-03 | 初版：整理 Hold Record 查询 / 流转 / 处置对接接口 |

规则变更请同步维护 [`dispose_api.md`](./dispose_api.md)。
