-- FT 可放行预测：一期数据盘点（只读）
-- 范围：RECORD_TYPE = 0（FT 异常反馈单）
-- 标签：创建后第一条工程师意见 DISPOSE ∈ {1,2,3,5}；y=1 当且仅当 DISPOSE=1

-- ---------------------------------------------------------------------------
-- 1. FT 已处置样本量与放行占比
-- ---------------------------------------------------------------------------
WITH first_eng AS (
    SELECT
        c.HOLD_RECORD_ID,
        c.DISPOSE,
        c.DISPOSE_DTTM,
        ROW_NUMBER() OVER (
            PARTITION BY c.HOLD_RECORD_ID
            ORDER BY c.DISPOSE_DTTM, c.ID
        ) AS rn
    FROM FT_OWEN.CIRCULATION_HISTORY c
    WHERE c.DISPOSE IN (1, 2, 3, 5)
)
SELECT
    COUNT(*) AS labeled_n,
    SUM(CASE WHEN f.DISPOSE = 1 THEN 1 ELSE 0 END) AS release_n,
    ROUND(SUM(CASE WHEN f.DISPOSE = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS release_rate,
    SUM(CASE WHEN f.DISPOSE = 2 THEN 1 ELSE 0 END) AS downgrade_n,
    SUM(CASE WHEN f.DISPOSE = 3 THEN 1 ELSE 0 END) AS retest_n,
    SUM(CASE WHEN f.DISPOSE = 5 THEN 1 ELSE 0 END) AS analyze_n
FROM FT_OWEN.FT_HOLD_RECORD r
JOIN first_eng f ON f.HOLD_RECORD_ID = r.ID AND f.rn = 1
WHERE r.RECORD_TYPE = 0;

-- ---------------------------------------------------------------------------
-- 2. 按主 HOLD_CODE 分层
-- ---------------------------------------------------------------------------
WITH first_eng AS (
    SELECT
        c.HOLD_RECORD_ID,
        c.DISPOSE,
        ROW_NUMBER() OVER (
            PARTITION BY c.HOLD_RECORD_ID
            ORDER BY c.DISPOSE_DTTM, c.ID
        ) AS rn
    FROM FT_OWEN.CIRCULATION_HISTORY c
    WHERE c.DISPOSE IN (1, 2, 3, 5)
)
SELECT
    REGEXP_SUBSTR(r.HOLD_CODE, '[^@]+', 1, 1) AS hold_code_primary,
    COUNT(*) AS n,
    SUM(CASE WHEN f.DISPOSE = 1 THEN 1 ELSE 0 END) AS release_n,
    ROUND(SUM(CASE WHEN f.DISPOSE = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS release_rate
FROM FT_OWEN.FT_HOLD_RECORD r
JOIN first_eng f ON f.HOLD_RECORD_ID = r.ID AND f.rn = 1
WHERE r.RECORD_TYPE = 0
GROUP BY REGEXP_SUBSTR(r.HOLD_CODE, '[^@]+', 1, 1)
ORDER BY n DESC;

-- ---------------------------------------------------------------------------
-- 3. ROUTE 含 ENG vs 不含 ENG
-- ---------------------------------------------------------------------------
WITH first_eng AS (
    SELECT
        c.HOLD_RECORD_ID,
        c.DISPOSE,
        ROW_NUMBER() OVER (
            PARTITION BY c.HOLD_RECORD_ID
            ORDER BY c.DISPOSE_DTTM, c.ID
        ) AS rn
    FROM FT_OWEN.CIRCULATION_HISTORY c
    WHERE c.DISPOSE IN (1, 2, 3, 5)
)
SELECT
    CASE
        WHEN r.ROUTE_ID IS NULL OR TRIM(r.ROUTE_ID) IS NULL THEN 'MISSING'
        WHEN UPPER(r.ROUTE_ID) LIKE '%ENG%' THEN 'ENG'
        ELSE 'NON_ENG'
    END AS route_group,
    COUNT(*) AS n,
    SUM(CASE WHEN f.DISPOSE = 1 THEN 1 ELSE 0 END) AS release_n,
    ROUND(SUM(CASE WHEN f.DISPOSE = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS release_rate
FROM FT_OWEN.FT_HOLD_RECORD r
JOIN first_eng f ON f.HOLD_RECORD_ID = r.ID AND f.rn = 1
WHERE r.RECORD_TYPE = 0
GROUP BY
    CASE
        WHEN r.ROUTE_ID IS NULL OR TRIM(r.ROUTE_ID) IS NULL THEN 'MISSING'
        WHEN UPPER(r.ROUTE_ID) LIKE '%ENG%' THEN 'ENG'
        ELSE 'NON_ENG'
    END
ORDER BY n DESC;

-- ---------------------------------------------------------------------------
-- 4. TEST_WAFER 关联率（FT：LOT_ID 含 '-' → FATE-FA，否则 VBOX-FA）
--    合批展示串（WAFER_ID 以 # 开头）无法直接 join，单独统计
-- ---------------------------------------------------------------------------
SELECT
    COUNT(*) AS ft_n,
    SUM(CASE WHEN r.WAFER_ID LIKE '#%' THEN 1 ELSE 0 END) AS merged_display_n,
    ROUND(SUM(CASE WHEN r.WAFER_ID LIKE '#%' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS merged_display_rate,
    SUM(CASE WHEN tw.ID IS NOT NULL THEN 1 ELSE 0 END) AS test_wafer_hit_n,
    ROUND(SUM(CASE WHEN tw.ID IS NOT NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS test_wafer_hit_rate
FROM FT_OWEN.FT_HOLD_RECORD r
LEFT JOIN FT_OWEN.TEST_WAFER tw
    ON tw.WAFER_ID = r.WAFER_ID
   AND tw.OPERATION_ID = CASE
        WHEN r.LOT_ID LIKE '%-%' THEN 'FATE-FA'
        ELSE 'VBOX-FA'
   END
   AND tw.ID = (
        SELECT MAX(t2.ID)
        FROM FT_OWEN.TEST_WAFER t2
        WHERE t2.WAFER_ID = r.WAFER_ID
          AND t2.OPERATION_ID = CASE
                WHEN r.LOT_ID LIKE '%-%' THEN 'FATE-FA'
                ELSE 'VBOX-FA'
          END
          AND NVL(t2.FT_TIME, t2.RECORD_DTTM) <= r.HOLD_DTTM
   )
WHERE r.RECORD_TYPE = 0;

-- ---------------------------------------------------------------------------
-- 5. bysite 覆盖率（FT_WLT_TESTLOG.STEP = 'FA'；合批展示串无法直接 join）
-- ---------------------------------------------------------------------------
SELECT
    COUNT(*) AS ft_n,
    SUM(CASE WHEN r.WAFER_ID LIKE '#%' THEN 1 ELSE 0 END) AS merged_display_n,
    SUM(CASE WHEN tl.WAFER_ID IS NOT NULL THEN 1 ELSE 0 END) AS testlog_hit_n,
    ROUND(SUM(CASE WHEN tl.WAFER_ID IS NOT NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 4) AS testlog_hit_rate
FROM FT_OWEN.FT_HOLD_RECORD r
LEFT JOIN (
    SELECT DISTINCT WAFER_ID
    FROM FT_OWEN.FT_WLT_TESTLOG
    WHERE STEP = 'FA'
) tl ON tl.WAFER_ID = r.WAFER_ID
WHERE r.RECORD_TYPE = 0;

-- ---------------------------------------------------------------------------
-- 6. 合批片号占比 + GRADE_NUM 缺失
-- ---------------------------------------------------------------------------
SELECT
    SUM(CASE WHEN r.WAFER_ID LIKE '#%' THEN 1 ELSE 0 END) AS merged_display_n,
    SUM(CASE WHEN r.GRADE_NUM IS NULL OR TRIM(r.GRADE_NUM) IS NULL THEN 1 ELSE 0 END) AS missing_grade_n,
    SUM(CASE WHEN r.ROUTE_ID IS NULL OR TRIM(r.ROUTE_ID) IS NULL THEN 1 ELSE 0 END) AS missing_route_n,
    COUNT(*) AS ft_n
FROM FT_OWEN.FT_HOLD_RECORD r
WHERE r.RECORD_TYPE = 0;

-- ---------------------------------------------------------------------------
-- 7. 待打分（尚无预测行）的 FT 单量
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS pending_score_n
FROM FT_OWEN.FT_HOLD_RECORD r
WHERE r.RECORD_TYPE = 0
  AND NOT EXISTS (
        SELECT 1
        FROM FT_OWEN.FT_HOLD_PREDICT p
        WHERE p.HOLD_RECORD_ID = r.ID
  );
