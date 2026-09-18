-- FT_OWEN.VW_WAFER_YIELD 源码
-- 按 (WAFER_ID, STATION) 各取最新一条。STATION：WLT2→WLT；FA/FATE-FA/VBOX-FA→FA。

CREATE OR REPLACE FORCE EDITIONABLE VIEW "FT_OWEN"."VW_WAFER_YIELD" (
    "ID", "WAFER_ID", "PRODUCT_ID", "EQUIP_ID",
    "OPERATION_ID", "STATION",
    "F_VALUE", "WAFER_NUM", "YIELD", "NG_NUM", "RECORD_TIME"
) AS
SELECT
    ID,
    WAFER_ID,
    PRODUCT_ID,
    EQUIP_ID,
    OPERATION_ID,
    STATION,
    F_VALUE,
    WAFER_NUM,
    YIELD,
    NG_NUM,
    RECORD_TIME
FROM (
    SELECT
        ID,
        WAFER_ID,
        PRODUCT_ID,
        EQUIP_ID,
        OPERATION_ID,
        CASE
            WHEN OPERATION_ID = 'WLT2' THEN 'WLT'
            ELSE 'FA'
        END AS STATION,
        NVL(TO_NUMBER(JSON_VALUE(GRADES_QTY, '$."F"')), 0)
        + NVL(TO_NUMBER(JSON_VALUE(GRADES_QTY, '$."F1"')), 0)
        + NVL(TO_NUMBER(JSON_VALUE(GRADES_QTY, '$."F2"')), 0)
        + NVL(TO_NUMBER(JSON_VALUE(GRADES_QTY, '$."F3"')), 0) AS F_VALUE,
        WAFER_NUM,
        ROUND(
            (GROSS_DIE - NVL(NG_NUM, 0)
             - (
                   NVL(TO_NUMBER(JSON_VALUE(GRADES_QTY, '$."F"')), 0)
                 + NVL(TO_NUMBER(JSON_VALUE(GRADES_QTY, '$."F1"')), 0)
                 + NVL(TO_NUMBER(JSON_VALUE(GRADES_QTY, '$."F2"')), 0)
                 + NVL(TO_NUMBER(JSON_VALUE(GRADES_QTY, '$."F3"')), 0)
               )
            ) / NULLIF(GROSS_DIE, 0) * 100,
            2
        ) AS YIELD,
        NG_NUM,
        RECORD_DTTM AS RECORD_TIME,
        ROW_NUMBER() OVER (
            PARTITION BY WAFER_ID,
                CASE WHEN OPERATION_ID = 'WLT2' THEN 'WLT' ELSE 'FA' END
            ORDER BY RECORD_DTTM DESC, ID DESC
        ) AS rn
    FROM TEST_WAFER
    WHERE OPERATION_ID IN ('FATE-FA', 'VBOX-FA', 'FA', 'WLT2')
)
WHERE rn = 1;
