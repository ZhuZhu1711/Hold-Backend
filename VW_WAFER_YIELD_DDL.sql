-- FT_OWEN.VW_WAFER_YIELD 源码

CREATE OR REPLACE FORCE EDITIONABLE VIEW "FT_OWEN"."VW_WAFER_YIELD" ("ID", "WAFER_ID", "PRODUCT_ID", "EQUIP_ID", "F_VALUE", "WAFER_NUM", "YIELD", "NG_NUM", "RECORD_TIME") AS 
  SELECT 
    ID, 
    WAFER_ID, 
    PRODUCT_ID,
    EQUIP_ID,
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
        -- 汇总所有F等级数量，TO_NUMBER + NVL 确保键不存在时返回0而非NULL
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
        record_dttm AS RECORD_TIME,
        ROW_NUMBER() OVER (PARTITION BY WAFER_ID ORDER BY record_dttm DESC) AS rn
    FROM TEST_WAFER
)
WHERE rn = 1;