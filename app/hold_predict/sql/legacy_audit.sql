-- 前身 HOLD_INFO + HISTORY_DISPOSITION：放行预测冷启动训练盘点（只读）
-- 范围：PRODUCT_ID LIKE '%-3.5'（一期只做 FT；旧表无 HOLD_CODE/STATION，无法可靠排除 FVI）
-- 关联：同一 WAFER_ID，DISPOSE_TIME >= HOLD_DATETIME 的第一条 HISTORY_DISPOSITION
-- 标签：ENG_DISPOSE = 0 → y=1（放行）；其余 → y=0（勿与新系统 DISPOSE=0 创建码混淆）
-- HOLD_DATETIME 为 VARCHAR2，仅识别常见格式；无法解析的行在匹配查询中丢弃

-- HOLD_DATETIME 解析（各段 WITH holds 内复用同一 CASE）：
--   YYYY-MM-DD[ T]HH24:MI:SS / YYYY/MM/DD HH24:MI:SS / 纯日期 / YYYYMMDDHH24MISS

-- ---------------------------------------------------------------------------
-- 1. HOLD_INFO 总量、FT 产品占比、时间解析失败率
-- ---------------------------------------------------------------------------
SELECT
    COUNT(*) AS hold_n,
    SUM(CASE WHEN h.PRODUCT_ID LIKE '%-3.5' THEN 1 ELSE 0 END) AS ft_product_n,
    ROUND(SUM(CASE WHEN h.PRODUCT_ID LIKE '%-3.5' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS ft_product_rate,
    SUM(CASE
        WHEN h.HOLD_DATETIME IS NULL OR TRIM(h.HOLD_DATETIME) IS NULL THEN 1
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}') THEN 0
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}') THEN 0
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}$') THEN 0
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2}$') THEN 0
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{14}') THEN 0
        ELSE 1
    END) AS parse_fail_n,
    ROUND(SUM(CASE
        WHEN h.HOLD_DATETIME IS NULL OR TRIM(h.HOLD_DATETIME) IS NULL THEN 1
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}') THEN 0
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}') THEN 0
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}$') THEN 0
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2}$') THEN 0
        WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{14}') THEN 0
        ELSE 1
    END) / NULLIF(COUNT(*), 0), 4) AS parse_fail_rate
FROM FT_OWEN.HOLD_INFO h;

-- ---------------------------------------------------------------------------
-- 2. 已匹配样本量、放行率、ENG_DISPOSE 分布（首次处置）
-- ---------------------------------------------------------------------------
WITH holds AS (
    SELECT
        h.ID,
        h.WAFER_ID,
        CASE
            WHEN h.HOLD_DATETIME IS NULL OR TRIM(h.HOLD_DATETIME) IS NULL THEN CAST(NULL AS DATE)
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}') THEN
                TO_DATE(REPLACE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'T', ' '), 'YYYY-MM-DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'YYYY/MM/DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY-MM-DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY/MM/DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{14}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 14), 'YYYYMMDDHH24MISS')
            ELSE CAST(NULL AS DATE)
        END AS HOLD_DTTM
    FROM FT_OWEN.HOLD_INFO h
    WHERE h.PRODUCT_ID LIKE '%-3.5'
),
matched AS (
    SELECT
        holds.ID,
        d.ENG_DISPOSE,
        ROW_NUMBER() OVER (
            PARTITION BY holds.ID
            ORDER BY d.DISPOSE_TIME, d.ID
        ) AS rn
    FROM holds
    JOIN FT_OWEN.HISTORY_DISPOSITION d
      ON d.WAFER_ID = holds.WAFER_ID
     AND d.DISPOSE_TIME >= holds.HOLD_DTTM
    WHERE holds.HOLD_DTTM IS NOT NULL
)
SELECT
    COUNT(*) AS labeled_n,
    SUM(CASE WHEN m.ENG_DISPOSE = 0 THEN 1 ELSE 0 END) AS release_n,
    ROUND(SUM(CASE WHEN m.ENG_DISPOSE = 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS release_rate
FROM matched m
WHERE m.rn = 1;

-- ---------------------------------------------------------------------------
-- 3. 首次 ENG_DISPOSE 码分布
-- ---------------------------------------------------------------------------
WITH holds AS (
    SELECT
        h.ID,
        h.WAFER_ID,
        CASE
            WHEN h.HOLD_DATETIME IS NULL OR TRIM(h.HOLD_DATETIME) IS NULL THEN CAST(NULL AS DATE)
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}') THEN
                TO_DATE(REPLACE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'T', ' '), 'YYYY-MM-DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'YYYY/MM/DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY-MM-DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY/MM/DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{14}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 14), 'YYYYMMDDHH24MISS')
            ELSE CAST(NULL AS DATE)
        END AS HOLD_DTTM
    FROM FT_OWEN.HOLD_INFO h
    WHERE h.PRODUCT_ID LIKE '%-3.5'
),
matched AS (
    SELECT
        holds.ID,
        d.ENG_DISPOSE,
        ROW_NUMBER() OVER (
            PARTITION BY holds.ID
            ORDER BY d.DISPOSE_TIME, d.ID
        ) AS rn
    FROM holds
    JOIN FT_OWEN.HISTORY_DISPOSITION d
      ON d.WAFER_ID = holds.WAFER_ID
     AND d.DISPOSE_TIME >= holds.HOLD_DTTM
    WHERE holds.HOLD_DTTM IS NOT NULL
)
SELECT
    m.ENG_DISPOSE,
    COUNT(*) AS n,
    ROUND(COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS share
FROM matched m
WHERE m.rn = 1
GROUP BY m.ENG_DISPOSE
ORDER BY n DESC;

-- ---------------------------------------------------------------------------
-- 4. 弱关联一对多：同 wafer 多 hold / 多处置
-- ---------------------------------------------------------------------------
SELECT
    COUNT(*) AS ft_hold_n,
    COUNT(DISTINCT h.WAFER_ID) AS wafer_n,
    SUM(CASE WHEN w.hold_cnt > 1 THEN 1 ELSE 0 END) AS multi_hold_row_n,
    ROUND(SUM(CASE WHEN w.hold_cnt > 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS multi_hold_row_rate,
    SUM(CASE WHEN d.disp_cnt > 1 THEN 1 ELSE 0 END) AS multi_disp_row_n,
    ROUND(SUM(CASE WHEN d.disp_cnt > 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS multi_disp_row_rate
FROM FT_OWEN.HOLD_INFO h
LEFT JOIN (
    SELECT WAFER_ID, COUNT(*) AS hold_cnt
    FROM FT_OWEN.HOLD_INFO
    WHERE PRODUCT_ID LIKE '%-3.5'
    GROUP BY WAFER_ID
) w ON w.WAFER_ID = h.WAFER_ID
LEFT JOIN (
    SELECT WAFER_ID, COUNT(*) AS disp_cnt
    FROM FT_OWEN.HISTORY_DISPOSITION
    GROUP BY WAFER_ID
) d ON d.WAFER_ID = h.WAFER_ID
WHERE h.PRODUCT_ID LIKE '%-3.5';

-- ---------------------------------------------------------------------------
-- 5. HOLD_REASON 含 023/024/025/027 的覆盖率（FT 产品）
-- ---------------------------------------------------------------------------
SELECT
    COUNT(*) AS ft_hold_n,
    SUM(CASE WHEN REGEXP_LIKE(h.HOLD_REASON, '(^|[^0-9])(023|024|025|027)([^0-9]|$)') THEN 1 ELSE 0 END) AS hold_code_hit_n,
    ROUND(
        SUM(CASE WHEN REGEXP_LIKE(h.HOLD_REASON, '(^|[^0-9])(023|024|025|027)([^0-9]|$)') THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0),
        4
    ) AS hold_code_hit_rate,
    SUM(CASE WHEN REGEXP_LIKE(h.HOLD_REASON, '(^|[^0-9])023([^0-9]|$)') THEN 1 ELSE 0 END) AS n_023,
    SUM(CASE WHEN REGEXP_LIKE(h.HOLD_REASON, '(^|[^0-9])024([^0-9]|$)') THEN 1 ELSE 0 END) AS n_024,
    SUM(CASE WHEN REGEXP_LIKE(h.HOLD_REASON, '(^|[^0-9])025([^0-9]|$)') THEN 1 ELSE 0 END) AS n_025,
    SUM(CASE WHEN REGEXP_LIKE(h.HOLD_REASON, '(^|[^0-9])027([^0-9]|$)') THEN 1 ELSE 0 END) AS n_027
FROM FT_OWEN.HOLD_INFO h
WHERE h.PRODUCT_ID LIKE '%-3.5';

-- ---------------------------------------------------------------------------
-- 6. 已匹配样本的 TEST_WAFER 命中率
--    片号含 '-' → FATE-FA，否则 VBOX-FA
-- ---------------------------------------------------------------------------
WITH holds AS (
    SELECT
        h.ID,
        h.WAFER_ID,
        CASE
            WHEN h.HOLD_DATETIME IS NULL OR TRIM(h.HOLD_DATETIME) IS NULL THEN CAST(NULL AS DATE)
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}') THEN
                TO_DATE(REPLACE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'T', ' '), 'YYYY-MM-DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'YYYY/MM/DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY-MM-DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY/MM/DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{14}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 14), 'YYYYMMDDHH24MISS')
            ELSE CAST(NULL AS DATE)
        END AS HOLD_DTTM
    FROM FT_OWEN.HOLD_INFO h
    WHERE h.PRODUCT_ID LIKE '%-3.5'
),
matched AS (
    SELECT
        holds.ID,
        holds.WAFER_ID,
        holds.HOLD_DTTM,
        ROW_NUMBER() OVER (
            PARTITION BY holds.ID
            ORDER BY d.DISPOSE_TIME, d.ID
        ) AS rn
    FROM holds
    JOIN FT_OWEN.HISTORY_DISPOSITION d
      ON d.WAFER_ID = holds.WAFER_ID
     AND d.DISPOSE_TIME >= holds.HOLD_DTTM
    WHERE holds.HOLD_DTTM IS NOT NULL
)
SELECT
    COUNT(*) AS labeled_n,
    SUM(CASE WHEN tw.ID IS NOT NULL THEN 1 ELSE 0 END) AS test_wafer_hit_n,
    ROUND(SUM(CASE WHEN tw.ID IS NOT NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS test_wafer_hit_rate
FROM matched m
LEFT JOIN FT_OWEN.TEST_WAFER tw
  ON tw.WAFER_ID = m.WAFER_ID
 AND tw.OPERATION_ID = CASE
        WHEN m.WAFER_ID LIKE '%-%' THEN 'FATE-FA'
        ELSE 'VBOX-FA'
    END
 AND tw.ID = (
        SELECT MAX(t2.ID)
        FROM FT_OWEN.TEST_WAFER t2
        WHERE t2.WAFER_ID = m.WAFER_ID
          AND t2.OPERATION_ID = CASE
                WHEN m.WAFER_ID LIKE '%-%' THEN 'FATE-FA'
                ELSE 'VBOX-FA'
          END
          AND NVL(t2.FT_TIME, t2.RECORD_DTTM) <= m.HOLD_DTTM
 )
WHERE m.rn = 1;

-- ---------------------------------------------------------------------------
-- 7. 已匹配样本的 FT_WLT_TESTLOG（STEP=FA）命中率
-- ---------------------------------------------------------------------------
WITH holds AS (
    SELECT
        h.ID,
        h.WAFER_ID,
        CASE
            WHEN h.HOLD_DATETIME IS NULL OR TRIM(h.HOLD_DATETIME) IS NULL THEN CAST(NULL AS DATE)
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}') THEN
                TO_DATE(REPLACE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'T', ' '), 'YYYY-MM-DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'YYYY/MM/DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY-MM-DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY/MM/DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{14}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 14), 'YYYYMMDDHH24MISS')
            ELSE CAST(NULL AS DATE)
        END AS HOLD_DTTM
    FROM FT_OWEN.HOLD_INFO h
    WHERE h.PRODUCT_ID LIKE '%-3.5'
),
matched AS (
    SELECT
        holds.ID,
        holds.WAFER_ID,
        ROW_NUMBER() OVER (
            PARTITION BY holds.ID
            ORDER BY d.DISPOSE_TIME, d.ID
        ) AS rn
    FROM holds
    JOIN FT_OWEN.HISTORY_DISPOSITION d
      ON d.WAFER_ID = holds.WAFER_ID
     AND d.DISPOSE_TIME >= holds.HOLD_DTTM
    WHERE holds.HOLD_DTTM IS NOT NULL
)
SELECT
    COUNT(*) AS labeled_n,
    SUM(CASE WHEN tl.WAFER_ID IS NOT NULL THEN 1 ELSE 0 END) AS testlog_hit_n,
    ROUND(SUM(CASE WHEN tl.WAFER_ID IS NOT NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS testlog_hit_rate
FROM matched m
LEFT JOIN (
    SELECT DISTINCT WAFER_ID
    FROM FT_OWEN.FT_WLT_TESTLOG
    WHERE STEP = 'FA'
) tl ON tl.WAFER_ID = m.WAFER_ID
WHERE m.rn = 1;

-- ---------------------------------------------------------------------------
-- 8. 未匹配：FT hold 无后续处置 / 时间解析失败
-- ---------------------------------------------------------------------------
WITH holds AS (
    SELECT
        h.ID,
        h.WAFER_ID,
        CASE
            WHEN h.HOLD_DATETIME IS NULL OR TRIM(h.HOLD_DATETIME) IS NULL THEN CAST(NULL AS DATE)
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}') THEN
                TO_DATE(REPLACE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'T', ' '), 'YYYY-MM-DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 19), 'YYYY/MM/DD HH24:MI:SS')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}-\d{2}-\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY-MM-DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{4}/\d{2}/\d{2}$') THEN
                TO_DATE(TRIM(h.HOLD_DATETIME), 'YYYY/MM/DD')
            WHEN REGEXP_LIKE(TRIM(h.HOLD_DATETIME), '^\d{14}') THEN
                TO_DATE(SUBSTR(TRIM(h.HOLD_DATETIME), 1, 14), 'YYYYMMDDHH24MISS')
            ELSE CAST(NULL AS DATE)
        END AS HOLD_DTTM
    FROM FT_OWEN.HOLD_INFO h
    WHERE h.PRODUCT_ID LIKE '%-3.5'
)
SELECT
    COUNT(*) AS ft_hold_n,
    SUM(CASE WHEN HOLD_DTTM IS NULL THEN 1 ELSE 0 END) AS unparsed_n,
    SUM(CASE
        WHEN HOLD_DTTM IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM FT_OWEN.HISTORY_DISPOSITION d
            WHERE d.WAFER_ID = holds.WAFER_ID
              AND d.DISPOSE_TIME >= holds.HOLD_DTTM
        ) THEN 1 ELSE 0
    END) AS unmatched_n
FROM holds;
