# 处置行为
## 工程师处置
| 行为 | DISPOSE | NEXT_OWNER_ID | DISPOSED_OWNER_ID |
| ----- | ----- | ----- | ----- |
| 放行 | 1 | 181 | ~ |
| 降级 | 2 | 181 | ~ |
| 重测 | 3 | 181 | ~ |
| 分析 | 5 |  | ~ |
| 转交 | 7 | PRODUCT_INFO.PRO_ENG_ID | ~ |


## 生产处置
| 行为 | DISPOSE | NEXT_OWNER_ID | DISPOSED_OWNER_ID |
| ----- | ----- | ----- | ----- |
| 分析(返回) | 66 | ~ | 181 |
| 回退 | 8 | ~ | 181 |
| 关闭 | 99 |  | 181 |

# 系统行为
| 行为 | DISPOSE | NEXT_OWNER_ID | DISPOSED_OWNER_ID |
| ----- | ----- | ----- | ----- |
| 创建 | 0 | ~ | 1 |
| 关闭 | 99 | | 1 |


> 181: 是生产op的ID  
~: 代指型号所属工程师的ID(PRODUCT_INFO.PRO_ENG_ID)

> 工程师处置仅为意见：写入 CIRCULATION_HISTORY，并回写 FT_HOLD_RECORD.LAST_CIRCULATION_ID / STATUS；**不改写 GRADE_NUM**。真正落地由生产执行。


# 处置单划分
| 处置单大类 | PRODUCT_ID | HOLD_CODE | STATION | RECORD_TYPE |
| ----- | ----- | ----- | ----- | ----- |
| FT异常反馈单 | *-3.5 | 023、024、025、027 | NOT IN ('FAOIFINISH', 'FFVI') | 0 |
| FVI异常反馈单 | * | 023 | IN('FAOIFINISH', 'FFVI') | 1 |
| WLT 异常反馈单 | *-2.6 | 004、022 | WOQC | 2 |

> '*'是正则化写法，代表任意匹配  
> 不满足表中规则的，无需转成record


# WLT 按片工程师处置（RECORD_TYPE=2）

- 一个 WLT record 可含 1 或多个 wafer（`WAFER_ID` 展示如 `#01#02`）。
- 多片时须对**每个 wafer**下达处置；一次提交必须覆盖该 record 全部 wafer，否则拒绝。
- 各片顶层处置可混用（放行 / 降级 / 重测 / 分析）。
- 请求体使用 `wafer_actions`（必填）；顶层 `dispose` 可省略，由各片汇总；若传入须与汇总一致。

## 记录级 STATUS / DISPOSE 汇总

流转仍写一条 CIRCULATION、一个 `DISPOSE` 整型：

**任一片为 5 → 5；否则任一为 3 → 3；否则任一为 2 → 2；否则 1。**

生产落地以 `DISPOSE_DETAIL` 为准。

## DISPOSE_DETAIL（服务端生成，直白中文）

片间用 `;` 分隔，片内用中文逗号 `，`。示例：

`#02，降级，降main拆批;#03，降级，降main不拆批;#05，重测，整片重测;#06，重测，重测A夹具，@1@361`

| 场景 | 片段 |
| ----- | ----- |
| 放行 / 分析 | `#01，放行` / `#02，可靠性分析` |
| 降main拆批 / 不拆批 | `#03，降级，降main拆批` / `#04，降级，降main不拆批` |
| 整片重测 | `#06，重测，整片重测` |
| A/B 夹具重测 + code | `#07，重测，重测A夹具，@1@361` / `#08，重测，重测B夹具，@1` |

- 降级仅两种：`main_split`（降main拆批）、`main_nosplit`（降main不拆批）；**不指定等级**。
- 重测仅三种：`full`（整片重测，不填 code）、`fixture_a` / `fixture_b`（夹具重测，**须填** code，如 `@1@361`）。
- 列表页片数较多时，UI 预览前几片并支持点击弹窗查看全部。

## wafer_actions 入参示例

```json
{
  "hold_record_id": 123,
  "wafer_actions": [
    {"wafer": "#01", "dispose": 1},
    {"wafer": "#02", "dispose": 2, "downgrade_mode": "main_split"},
    {"wafer": "#03", "dispose": 2, "downgrade_mode": "main_nosplit"},
    {"wafer": "#05", "dispose": 3, "retest_mode": "full"},
    {"wafer": "#06", "dispose": 3, "retest_mode": "fixture_a", "retest_codes": "@1"},
    {"wafer": "#07", "dispose": 3, "retest_mode": "fixture_b", "retest_codes": "@1@361"},
    {"wafer": "#09", "dispose": 5}
  ],
  "dispose_note": null,
  "dispose_manual_note": null
}
```

## 非 WLT（FT/FVI）

仍为整单一次处置：`dispose` + `downgrades` / `retest_grades`（`DISPOSE_DETAIL` 为 `DG:` / `RT:`）。
