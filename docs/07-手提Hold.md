# 07 手提 Hold / 外部创建 API

手提 Hold **直接插入** `FT_HOLD_RECORD`（`SOURCE=1`），不写 `FT_HOLD_INFO`，也不走合批任务。创建时同时写入 `CIRCULATION_HISTORY`（`DISPOSE=0`），`NEXT_OWNER_ID` 取型号工程师。

后台页：

- Root：`GET /admin/hold/manual`
- 产品工程师：`GET /eng/manual`（仅所属型号）
- 生产：`GET /prod/manual`

创建 API 三种角色均可调用（须先登录）。

---

## 1. 规则

| 产线 `line` | PRODUCT_ID | HOLD_CODE | RECORD_TYPE | STATION |
| --- | --- | --- | --- | --- |
| `FT` | 须 `*-3.5` | 当前仅 `AQL_HOLD`（可扩） | `0` FT 异常反馈单 | 必填 |
| `WLT` | 须 `*-2.6` | `004` 或 `022` | `2` WLT 异常反馈单 | 默认 `WOQC` |

其它必填：`equip_id`、`lot_id`、`wafer_id`、`hold_reason`。

FT `hold_code` 从允许列表中选，目前只有 `AQL_HOLD`；未传时默认该项。后续加码时同步更新允许列表、页面下拉，以及合批「FT 异常反馈单」码表。分析是否改看附件仍按码本身判断（目前仅 `AQL_HOLD` 跳过 `/api/analysis`）。

`SOURCE`：`0` = MES 合批，`1` = 手提。流转 `DISPOSE_SOURCE` 在 `SOURCE=1` 时仍写 `JDY`（兼容旧数据）。

### 附件 `ANNEX_FTP_PATH`

多图路径以 `@` 引导拼接，例如：

```
@/JDY_UPLOAD/HOLD_ANNEX/a.jpg@/JDY_UPLOAD/HOLD_ANNEX/b.jpg
```

解析：`split('@')` 后丢掉空段。字段可空。

---

## 2. 创建接口（外部系统）

鉴权：先 `POST /api/login`，携带 `hold_session` Cookie。角色须为 **root（0）、产品工程师（1）或生产（9）**。产品工程师只能创建自己负责的型号。

### `POST /admin/hold/api/manual_hold`

JSON（调用方已把图传到 FTP）：

```json
{
  "line": "FT",
  "product_id": "XX-3.5",
  "station": "FIQC",
  "equip_id": "MANUAL",
  "lot_id": "ABC01",
  "wafer_id": "ABC01",
  "hold_code": "AQL_HOLD",
  "hold_reason": "AQL 抽检不合格",
  "annex_ftp_path": "@/JDY_UPLOAD/HOLD_ANNEX/a.jpg@/JDY_UPLOAD/HOLD_ANNEX/b.jpg"
}
```

WLT 示例：`"line":"WLT"`，`"hold_code":"004"`（或 `022`）。也可传 `annex_paths: ["/a.jpg","/b.jpg"]`。

`multipart/form-data`：同样字段 + `files`（或多张 `images`）由服务端上传到 `/JDY_UPLOAD/HOLD_ANNEX/{product}/{lot}/`。

**成功：** `{ code: 200, msg: "创建成功", data: { ID, PRODUCT_ID, HOLD_CODE, RECORD_TYPE, ANNEX_COUNT, ... } }`

---

## 3. 附件下载（分析入口）

### `GET /admin/hold/api/annex_image?record_id=&index=`

- Auth：`@login_required`
- 只返回该 record 的 `ANNEX_FTP_PATH` 第 `index` 张（从 0 起），禁止任意 FTP 路径
- 成功：图片 bytes；无图 / 越界：404

`HOLD_CODE` 含 `AQL_HOLD` 时：**不要**调 `/api/analysis`。客户端/Web「分析」改为展示附件；`ANNEX_COUNT=0` 则不显示图。

WLT 手提（004/022）仍走原 bysite/binmap；若同时有附件，分析页额外展示。
