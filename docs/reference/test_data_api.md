# Test Data API 对接说明

本文档说明 `test_data_routes.py` 提供的测试日志（bysite）查询接口，供外部系统对接。

相关实现：

- 路由：`app/routes/test_data_routes.py`
- 业务：`app/controllers/testlog_ctrl.py`

---

## 1. 基本信息

| 项 | 说明 |
| --- | --- |
| 协议 | HTTP / HTTPS |
| 默认端口 | `50001` |
| Base URL 示例 | `http://{host}:50001` |
| Blueprint 前缀 | `/api/test_data` |
| 数据格式 | JSON（UTF-8） |
| 鉴权 | **当前接口无需登录**（无 Session 校验） |

> 数据来源：库表 `FtWltTestlog` 定位最新测试记录 → 从 FTP 下载 testlog（CSV/XML）→ 解析 bysite。

---

## 2. 接口一览

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/test_data/bysite` | 无 | 按 wafer + 工步类型获取最新 testlog 的 bysite 统计 |

---

## 3. 获取 Bysite 数据

### 3.1 请求

`GET /api/test_data/bysite`

**Query 参数**

| 参数 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `wafer_id` | 是 | string | 晶圆 ID |
| `step` | 是 | string | 工步类型，仅允许：`ATE` \| `WLT` |

**`step` 映射（内部查询工步）**

| 入参 `step` | 实际查询 STEP 列表 |
| --- | --- |
| `ATE` | `FA` |
| `WLT` | `WLTA`, `WLTB` |

服务端取该 wafer 在对应 STEP 中 **TEST_DATE 最新** 的一条记录，再下载并解析其 FTP 上的 testlog。

**请求示例**

```http
GET /api/test_data/bysite?wafer_id=LOT001-01&step=ATE HTTP/1.1
Host: {host}:50001
```

```bash
curl "http://{host}:50001/api/test_data/bysite?wafer_id=LOT001-01&step=ATE"
```

```bash
curl "http://{host}:50001/api/test_data/bysite?wafer_id=LOT001-01&step=WLT"
```

---

### 3.2 成功响应

HTTP `200`

```json
{
  "code": 200,
  "msg": "bysite获取成功",
  "bysite": {
    "test_die": 12000,
    "end_dttm": "20260801120000",
    "product_id": "PRODUCT-X",
    "test_program": "PROG_V1",
    "wafer_id": "LOT001-01",
    "lot_id": "LOT001",
    "equip_id": "EQP01",
    "step": "FA",
    "bysite": {
      "1": { "1": 100, "2": 5 },
      "2": { "1": 98, "3": 2 }
    }
  }
}
```

> 注意：本接口成功时业务数据在字段 **`bysite`**，不是通用的 `data`。

**`bysite`（外层对象）字段说明**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `test_die` | int | 测试 die 数 |
| `end_dttm` | string | 结束时间（解析自文件名或 XML） |
| `product_id` | string | 型号（CSV 解析时有；XML 视实现而定） |
| `test_program` | string | 测试程序 |
| `wafer_id` | string | 晶圆 ID |
| `lot_id` | string | Lot ID |
| `equip_id` | string | 设备号 |
| `step` | string | 工步（文件内实际值，如 `FA` / `WLTA` 等） |
| `bysite` | object | **按 site 汇总的 bin 计数** |

**内层 `bysite` 结构**

```text
{
  "<site号>": {
    "<bin_code>": <数量>,
    ...
  },
  ...
}
```

- key：site 编号（字符串或数字，JSON 中通常为字符串键）
- value：该 site 下各 bin code 的出现次数

示例含义：`site=1` 上 bin `1` 有 100 颗，bin `2` 有 5 颗。

---

### 3.3 失败响应

#### 缺少 `wafer_id`

HTTP `400`

```json
{
  "error": "wafer_id is required"
}
```

#### `step` 非法

HTTP `400`

```json
{
  "error": "invalid step param.Must in ATE | WLT"
}
```

#### 查询/解析失败（无记录、FTP/解析异常等）

HTTP `200`（状态码仍为 200，靠 `code` 区分）

```json
{
  "code": 500,
  "bysite": null,
  "msg": "bysite获取失败"
}
```

> 对接方请以响应体中的 **`code`** 判断业务成败，不要仅看 HTTP 状态码。

---

## 4. 处理逻辑（对接参考）

```text
客户端
  │  GET /api/test_data/bysite?wafer_id=&step=ATE|WLT
  ▼
参数校验（wafer_id、step）
  │
  ▼
step → STEP 列表（ATE→[FA]；WLT→[WLTA,WLTB]）
  │
  ▼
查 FtWltTestlog：WAFER_ID + STEP IN (...)，按 TEST_DATE 降序取最新
  │
  ▼
FTP 下载 FTP_PATH 对应文件到本地临时目录
  │
  ▼
按扩展名解析
  ├─ .csv → parse_CSV → bysite 统计
  └─ .xml → parse_XML → bysite 统计
  │
  ▼
返回 { code, msg, bysite }
```

---

## 5. 注意事项

1. **无需登录**：当前路由未挂鉴权装饰器；若后续加登录，请同步更新本文档。
2. **最新一条**：同一 wafer + step 组只取 `TEST_DATE` 最新记录，不会返回历史全部 testlog。
3. **文件类型**：依赖 FTP 路径扩展名（`.csv` / `.xml`）；其他格式会导致失败。
4. **响应字段名**：成功数据在 `bysite`，与 Hold Record 文档中的 `data` 约定不同。
5. **耗时**：涉及 FTP 下载与文件解析，超时时间建议放宽（视网络与文件大小）。
6. **临时文件**：解析后会删除本地临时文件；并发量大时注意服务端磁盘与 FTP 连接池。

---

## 7. 相关：`/api/raw_data`（无需登录）

路由：`app/routes/rawdata_routes.py`。与 bysite 一样当前无 Session 校验。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/raw_data/yield` | 本地 `TEST_WAFER` 良率 / BIN 比率 |
| GET | `/api/raw_data/defect_bincode` | 本地最新测试缺陷 BIN |
| GET | `/api/raw_data/mes_defect_bin` | MES `DEFECT_BIN_RELATION_H` 缺陷 BIN（code + qty） |

`yield` / `defect_bincode`：Query `wafer_id`、`operation_id` 必填。传入 `FA` 或 `FATE-FA` 时同时命中库中两种写法（取 ID 最新一条）。`VBOX-FA` 精确匹配。`RT` / `FT` 无别名。

### 7.1 `GET /api/raw_data/mes_defect_bin`

MES：`LOT_ID` + `LINE_TYPE` → `LOT_RRN`，再筛 `BIN_NAME`（默认 `F`），只返回 `DEFECT_CODE`、`QTY`。同一 code 多行保留第一条；`has_duplicate` / `duplicate_codes` 告知调用方是否出现过重复。

**Query**

| 参数 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `lot_id` | 是 | | MES `LOT_ID`，如 `C200161-027` |
| `line_type` | 否 | `FT` | MES `LINE_TYPE` |
| `bin_name` | 否 | `F` | MES `BIN_NAME` |

```http
GET /api/raw_data/mes_defect_bin?lot_id=C200161-027 HTTP/1.1
```

成功 HTTP `200`：

```json
{
  "code": 200,
  "msg": "获取成功",
  "data": {
    "lot_id": "C200161-027",
    "line_type": "FT",
    "bin_name": "F",
    "qty": 12,
    "items": [
      {"defect_code": "xxx", "qty": 7},
      {"defect_code": "yyy", "qty": 5}
    ],
    "has_duplicate": false,
    "duplicate_codes": []
  }
}
```

`qty` 为去重后数量之和。缺 `lot_id` → HTTP 400；MES 查询失败 → HTTP 500。

---

## 8. 变更说明

| 日期 | 说明 |
| --- | --- |
| 2026-09-02 | 增加 `/api/raw_data/mes_defect_bin`；附录补充 yield / defect_bincode |
| 2026-08-03 | 初版：补充 `/api/test_data/bysite` 对接说明 |
