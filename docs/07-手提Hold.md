# 07 手提 Hold / 外部创建 API

> **已下架（2026-09-04）。** 创建页、`POST /admin/hold/api/manual_hold`、附件 FTP 上传以及 `GET /admin/hold/api/annex_image` / `annex_zip` 下载一律返回 **HTTP 410**。数据分析用 testlog FTP 探活 `GET /api/common_data/ftp/status` **仍可用**。下文为下架前约定，仅供对照历史数据（`SOURCE=1`）。

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
| `FT` | 须 `*-3.5`，可关键字匹配 `PRODUCT_INFO` | 当前仅 `AQL_HOLD`（可扩） | `0` FT 异常反馈单 | 从合批 FT 站点中选（不含 FAOIFINISH / FFVI） |
| `WLT` | 须 `*-2.6`，可关键字匹配 `PRODUCT_INFO` | `004` 或 `022` | `2` WLT 异常反馈单 | 固定 `WLT2` |

其它必填：`equip_id`、`lot_id`、`wafer_id`、`hold_reason`。

- **FT**：`LOT_ID` 与 `WAFER_ID` 相同，均手输。
- **WLT**：`LOT_ID` 手输，格式 `LOT.NO`（`NO` 为本 lot 第一片片号，如 `C123456.01`）。未带 `.NO` 时用所选最小片号补上。`WAFER_ID` 由勾选的 1~25 片拼接，如 `#01#03#13`。也可传 `wafer_nos: [1,3,13]`。
- 型号：`GET /admin/hold/api/manual_hold/products?line=FT|WLT`。创建时精确匹配，否则唯一前缀/包含命中则采用。工程师仅所属型号。

FT `hold_code` 从允许列表中选，目前只有 `AQL_HOLD`；未传时默认该项。后续加码时同步更新允许列表、页面下拉，以及合批「FT 异常反馈单」码表。分析是否改看附件仍按码本身判断（目前仅 `AQL_HOLD` 跳过 `/api/analysis`）。

`SOURCE`：`0` = MES 合批，`1` = 手提。流转 `DISPOSE_SOURCE` 在 `SOURCE=1` 时仍写 `JDY`（兼容旧数据）。

创建时按与合批相同的规则写入 `HOLD_WAFER_ATTR`（见 [04-处置规范.md](./04-处置规范.md) §4.1）；无法判定则为 `0`。

### 附件 `ANNEX_FTP_PATH`

单条记录最多 **25** 张。附件走独立 FTP（`Config.ANNEX_FTP_HOST` / `USER` / `PASSWD`），与 TESTLOG 不是同一台。按产线平铺，不再按型号/lot 分子目录：

- FT：`/JDY_UPLOAD/FT_MANUAL/`
- WLT：`/JDY_UPLOAD/WLT_MANUAL/`

服务端上传文件名：`{recordId}_{n}.ext`（如 `188_1.jpg`）。`ANNEX_FTP_PATH` 存相对名 `@188_1.jpg@188_2.jpg`，下载时再拼对应产线根目录，避免 VARCHAR2(1024) 写满。

读写都只允许这两个目录下的图片（jpg/jpeg/png/gif/bmp/webp）。带 `/` 的绝对路径必须落在上述根目录内，入库时改存相对文件名。`..`、其它目录（如 `/RAW_DATA/`）或非图片扩展名一律拒绝，避免把应用当成内网 FTP 任意读通道。

多图路径以 `@` 引导拼接，例如：

```
@188_1.jpg@188_2.jpg
```

解析：`split('@')` 后丢掉空段。字段可空。

---

## 2. 创建接口（外部系统）

鉴权：`Cookie: hold_session` **或** `X-Hold-Token`（release 对 `HOLD_API_TOKEN`，debug 对 `HOLD_API_TOKEN_DEBUG`）。走 Cookie 时角色须为 **root（0）、产品工程师（1）或生产（9）**，产品工程师只能创建自己负责的型号。走 Token 时跳过角色校验，操作人记为系统用户。

### `POST /admin/hold/api/manual_hold`

JSON（调用方已把图传到 FTP）：

```json
{
  "line": "FT",
  "product_id": "XX-3.5",
  "station": "FIQC_MERGE",
  "equip_id": "MANUAL",
  "lot_id": "ABC01",
  "wafer_id": "ABC01",
  "hold_code": "AQL_HOLD",
  "hold_reason": "AQL 抽检不合格",
  "annex_ftp_path": "@/JDY_UPLOAD/FT_MANUAL/a.jpg@/JDY_UPLOAD/FT_MANUAL/b.jpg"
}
```

WLT 示例：

```json
{
  "line": "WLT",
  "product_id": "XX-2.6",
  "hold_code": "004",
  "equip_id": "MANUAL",
  "lot_id": "C123456.01",
  "wafer_nos": [1, 3, 13],
  "hold_reason": "WLT 抽检"
}
```

写入 `STATION=WLT2`，`WAFER_ID=#01#03#13`。也可直接传 `"wafer_id":"#01#03#13"`。`annex_paths: ["a.jpg","b.jpg"]` 同样支持（须为当前产线 MANUAL 目录下的图片文件名，或该目录下的绝对路径）。

`multipart/form-data`：同样字段 + `files`（或多张 `images`）。先插入 Record，再把文件平铺存到 `/JDY_UPLOAD/FT_MANUAL/` 或 `/JDY_UPLOAD/WLT_MANUAL/`，文件名为 `{recordId}_{n}.ext`。库内 `ANNEX_FTP_PATH` 存相对名以控制 1024 长度。

**成功：** `{ code: 200, msg: "创建成功", data: { ID, PRODUCT_ID, HOLD_CODE, RECORD_TYPE, ANNEX_COUNT, ... } }`

---

## 3. 附件下载（分析入口）

### `GET /admin/hold/api/annex_image?record_id=&index=`

- Auth：`@login_required`
- 只返回该 record 的 `ANNEX_FTP_PATH` 第 `index` 张（从 0 起），禁止任意 FTP 路径
- 实际 FTP 读取前会校验路径落在 `FT_MANUAL` / `WLT_MANUAL` 且为图片；否则 400
- 成功：图片 bytes；无图 / 越界：404

`HOLD_CODE` 含 `AQL_HOLD` 时：**不要**调 `/api/analysis`。客户端/Web「分析」改为展示附件；`ANNEX_COUNT=0` 则不显示图。

WLT 手提（004/022）仍走原 bysite/binmap；若同时有附件，分析页额外展示。

### `GET /admin/hold/api/annex_zip?record_id=`

打包该 record 全部附件，路径规则与 `annex_image` 相同（仅 MANUAL 目录下的图片）。
