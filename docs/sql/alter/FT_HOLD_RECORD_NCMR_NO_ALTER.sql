-- FT_HOLD_RECORD / FT_HOLD_RECORD_TEST 增加上游异常反馈单号
-- 有值表示已反馈上游，本系统不再处置，等待上游下发指令。
-- 本脚本只提交，不在此任务中执行。正式表请在正式环境单独执行。

ALTER TABLE FT_OWEN.FT_HOLD_RECORD ADD (
    NCMR_NO VARCHAR2(64)
);

COMMENT ON COLUMN FT_OWEN.FT_HOLD_RECORD.NCMR_NO IS
'上游异常反馈单号。有值则本系统不再处置，等待上游指令';

ALTER TABLE FT_OWEN.FT_HOLD_RECORD_TEST ADD (
    NCMR_NO VARCHAR2(64)
);

COMMENT ON COLUMN FT_OWEN.FT_HOLD_RECORD_TEST.NCMR_NO IS
'上游异常反馈单号。有值则本系统不再处置，等待上游指令';
