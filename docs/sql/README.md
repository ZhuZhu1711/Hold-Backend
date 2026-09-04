# Oracle 脚本

根目录散落的 `.sql` 已按用途归到本目录。运行环境为 schema `FT_OWEN`。
机器学习盘点 SQL 仍放在 [`app/hold_predict/sql/`](../../app/hold_predict/sql/)，随预测模块维护。

## 目录

| 子目录 | 用途 |
| --- | --- |
| [ddl/](./ddl/) | 建表 / 序列 |
| [alter/](./alter/) | 已有表字段变更 |
| [migrate/](./migrate/) | 存量数据修复（执行前先跑预览 SELECT） |
| [views/](./views/) | 视图 |

## 建表（ddl）

| 脚本 | 对象 |
| --- | --- |
| [HOLD_INFO_DDL.sql](./ddl/HOLD_INFO_DDL.sql) | 旧系统 `HOLD_INFO` |
| [HISTORY_DISPOSITION_DDL.sql](./ddl/HISTORY_DISPOSITION_DDL.sql) | 旧系统 `HISTORY_DISPOSITION` |
| [DEFECT_CODE_DDL.sql](./ddl/DEFECT_CODE_DDL.sql) | `DEFECT_CODE` |
| [FT_ENG_NOTES.sql](./ddl/FT_ENG_NOTES.sql) | `FT_ENG_NOTES` + 序列 |
| [FT_HOLD_RECORD_DDL.sql](./ddl/FT_HOLD_RECORD_DDL.sql) | `FT_HOLD_RECORD` |
| [FT_HOLD_RECORD_TEST_DDL.sql](./ddl/FT_HOLD_RECORD_TEST_DDL.sql) | debug 表 `FT_HOLD_RECORD_TEST` |
| [CIRCULATION_HISTORY_DDL.sql](./ddl/CIRCULATION_HISTORY_DDL.sql) | `CIRCULATION_HISTORY` |
| [FT_HOLD_INFO_TEST_DDL.sql](./ddl/FT_HOLD_INFO_TEST_DDL.sql) | `FT_HOLD_INFO_TEST` |
| [FT_HOLD_PREDICT_DDL.sql](./ddl/FT_HOLD_PREDICT_DDL.sql) | `FT_HOLD_PREDICT` |
| [FT_CLIENT_ERROR_DDL.sql](./ddl/FT_CLIENT_ERROR_DDL.sql) | `FT_CLIENT_ERROR` |
| [SOFTWARE_INFO_DDL.sql](./ddl/SOFTWARE_INFO_DDL.sql) | `SOFTWARE_INFO`（客户端版本卡控） |
| [USERS_MUST_CHANGE_PWD.sql](./ddl/USERS_MUST_CHANGE_PWD.sql) | 给已有 `USERS` 增加 `MUST_CHANGE_PWD`（debug/release 共用表，执行一次；**先跑脚本再部署代码**） |

新环境建议顺序：旧表（若仍对接）→ `DEFECT_CODE` / `FT_ENG_NOTES` → `FT_HOLD_RECORD`（及 TEST）→ `CIRCULATION_HISTORY` → 其余。

## 变更（alter）

按时间先后（后执行覆盖先生效）：

1. [CIRCULATION_HISTORY_DISPOSE_DETAIL_ALTER.sql](./alter/CIRCULATION_HISTORY_DISPOSE_DETAIL_ALTER.sql) — `DISPOSE_DETAIL` 扩到 1024
2. [CIRCULATION_HISTORY_DISPOSE_NOTE_ALTER.sql](./alter/CIRCULATION_HISTORY_DISPOSE_NOTE_ALTER.sql) — 加 `DISPOSE_NOTE`
3. [CIRCULATION_HISTORY_DISPOSE_MANUAL_NOTE_ALTER.sql](./alter/CIRCULATION_HISTORY_DISPOSE_MANUAL_NOTE_ALTER.sql) — 加 `DISPOSE_MANUAL_NOTE`
4. [CIRCULATION_HISTORY_DISPOSE_DETAIL_4000_ALTER.sql](./alter/CIRCULATION_HISTORY_DISPOSE_DETAIL_4000_ALTER.sql) — `DISPOSE_DETAIL` 扩到 4000
5. [FT_HOLD_RECORD_WAFER_ID_ALTER.sql](./alter/FT_HOLD_RECORD_WAFER_ID_ALTER.sql) — `WAFER_ID` 扩到 100
6. [FT_HOLD_RECORD_HOLD_WAFER_ATTR_ALTER.sql](./alter/FT_HOLD_RECORD_HOLD_WAFER_ATTR_ALTER.sql) — 加 `HOLD_WAFER_ATTR` 比特位属性

当前 [CIRCULATION_HISTORY_DDL.sql](./ddl/CIRCULATION_HISTORY_DDL.sql) 已包含 NOTE / MANUAL_NOTE 和 1024 详情；全新库执行 ddl 后，仍需跑 **4000 扩容** 与 **WAFER_ID** alter。已有库若 DDL 未含 `HOLD_WAFER_ATTR`，再跑第 6 步。

## 数据修复（migrate）

| 脚本 | 说明 |
| --- | --- |
| [ANALYZE_SELF_LOOP_MIGRATE.sql](./migrate/ANALYZE_SELF_LOOP_MIGRATE.sql) | 可靠性分析改为工程师自循环后，把仍停在生产 OP 的存量单交回工程师。先预览再更新。 |

## 视图（views）

| 脚本 | 对象 |
| --- | --- |
| [VW_WAFER_YIELD_DDL.sql](./views/VW_WAFER_YIELD_DDL.sql) | `VW_WAFER_YIELD` |
