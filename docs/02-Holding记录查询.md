# 02 Holding 记录查询

工程师只能看到 **`PRODUCT_INFO.PRO_ENG_ID` = 本人** 的型号下的在线 Hold Record。

「在线」含义：

- MES 合批：关联 hold_info 且 `HOLDING = 0`（命名反直觉：`0` 表示仍在 hold）
- 手提（`SOURCE=1`）：无 hold_info，以 `STATUS <> 99` 视为在线

Auth：全部为工程师 Session（`@engineer_required`）。

---

## 1. 所属型号列表（可选筛选）

### `GET /eng/api/products`

| Query | 说明 |
| --- | --- |
| `search` | 可选，型号模糊搜索 |

**成功：** `{ code: 200, msg, data: [ ...产品对象 ] }`

用于列表页型号下拉；非必须。

---

## 2. Holding 列表（核心）

### `GET /eng/api/holding_records`

| Query | 类型 | 说明 |
| --- | --- | --- |
| `product_id` | string | 型号模糊匹配 |
| `station` | string | 站点模糊匹配 |
| `keyword` | string | 匹配 `WAFER_ID` / `LOT_ID` / `HOLD_CODE` / `HOLD_REASON` |
| `record_type` | int | `0` FT / `1` FVI / `2` WLT；空则不过滤 |
| `pending_only` | bool-ish | `1`/`true`/`yes`/`y`：仅当前负责人为本人的待办 |
| `page` | int | 默认 1 |
| `page_size` | int | 默认 20 |

**建议：** 待办工作台使用 `pending_only=1`。

**成功示例：**

```json
{
  "code": 200,
  "msg": "获取成功",
  "data": [ { "...": "见下表" } ],
  "total": 42,
  "page": 1,
  "page_size": 20,
  "pages": 3
}
```

### 列表项主要字段

| 字段 | 说明 |
| --- | --- |
| `ID` | hold_record 主键 |
| `PRODUCT_ID` | 型号 |
| `STATION` | 站点 |
| `LOT_ID` / `WAFER_ID` | 批号 / 片号（展示可能为 `#01#02`） |
| `HOLD_CODE` / `HOLD_REASON` | Hold 码 / 原因 |
| `SOURCE` | `0` MES 合批；`1` 手提 |
| `ANNEX_FTP_PATH` | 附件 FTP 路径，多图 `@path1@path2`；可空 |
| `IS_AQL_HOLD` | `HOLD_CODE` 含 `AQL_HOLD` |
| `ANNEX_COUNT` | 附件张数（解析自 `ANNEX_FTP_PATH`） |
| `GRADE_NUM` | 原始等级串 |
| `GRADE_NUM_DISPLAY` | 展示用等级 |
| `GRADES` | 解析后的等级列表（供降级/重测 UI） |
| `RECORD_TYPE` / `RECORD_TYPE_NAME` | `0`/`1`/`2` 及中文名 |
| `STATUS` | 当前状态（等于最近一次处置码；`99` 已关闭） |
| `IS_CLOSED` | `STATUS == 99` |
| `CURRENT_OWNER_ID` / `CURRENT_OWNER_NAME` | 当前负责人（最新流转 `NEXT_OWNER_ID`） |
| `LAST_DISPOSE` / `LAST_DISPOSE_LABEL` | 最近一次处置码及标签 |
| `LAST_DISPOSE_DETAIL` / `LAST_DISPOSE_NOTE` / `LAST_DISPOSE_MANUAL_NOTE` | 最近流转详情/备注 |
| `HOLD_DTTM` | Hold 时间 |
| `CAN_DISPOSE` | **是否可处置**：当前负责人是本人且未关闭 |

`CAN_DISPOSE === false` 时：可分析查看，但不应调用处置接口（服务端也会拒绝）。

---

## 3. 处置前加载单条

### `GET /eng/api/records/<record_id>`

加载处置页完整 record；**须为所属型号**，否则 403。

**成功 `data` 在列表字段基础上额外强调：**

| 字段 | 说明 |
| --- | --- |
| `GRADES` | 解析等级，非 WLT 降级/重测用 |
| `WAFERS` | wafer 展示列表（如 `["#01","#02"]`），**WLT 按片处置必用** |
| `CAN_DISPOSE` | 是否可处置 |
| `CURRENT_OWNER_ID` | 当前负责人 |
| `IS_CLOSED` | 是否已关闭 |
| `RECORD_TYPE_NAME` | 处置单中文名 |

失败常见：

| HTTP | msg 关键词 |
| --- | --- |
| 404 | `不存在` |
| 403 | `不属于您负责的型号` |
| 400 | `参数无效` |

---

## 4. 与分析 / 处置的衔接

从列表行进入分析时，需要带上：

- `WAFER_ID`、`LOT_ID`（展示串 `#..` 时 lot 必填）
- `RECORD_TYPE`、`STATION`
- 若 `IS_AQL_HOLD`：**不要**调 `/api/analysis`；桌面端按 `ANNEX_FTP_PATH` 直连附件 FTP 取图（后端 `annex_image` 已下架）

进入处置时：

1. 确认 `CAN_DISPOSE`
2. `GET /eng/api/records/{ID}` 取 `GRADES` / `WAFERS`
3. 再 `POST /eng/api/dispose`

详见 [03-数据分析.md](./03-数据分析.md)、[05-处置接口与示例.md](./05-处置接口与示例.md)。
