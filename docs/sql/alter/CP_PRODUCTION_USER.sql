-- CP 生产账号。USERS / ROLE 不分测试表和正式表。
-- 不修改 ROLE_ID=9（FT 生产）。
--
-- 本文件不存放明文口令，也不存放 MD5。
-- 真正建用户时，把下面 v_pwd 的占位符换成 32 位 MD5 再执行。
-- 占位符仍在时只补角色，不插入用户。

DECLARE
    v_pwd VARCHAR2(100) := 'REPLACE_AT_EXECUTION';
    v_role_cnt NUMBER;
    v_user_cnt NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_role_cnt FROM ROLE WHERE ROLE_ID = 2;
    IF v_role_cnt = 0 THEN
        INSERT INTO ROLE (ROLE_ID, ROLE_DESC) VALUES (2, 'CP生产');
    END IF;

    IF v_pwd = 'REPLACE_AT_EXECUTION'
       OR NOT REGEXP_LIKE(v_pwd, '^[0-9a-fA-F]{32}$') THEN
        DBMS_OUTPUT.PUT_LINE('未提供口令 MD5，已跳过用户 88888');
    ELSE
        SELECT COUNT(*) INTO v_user_cnt FROM USERS WHERE EMPLOYEE_NO = '88888';
        IF v_user_cnt = 0 THEN
            INSERT INTO USERS (EMPLOYEE_NO, NAME, PASSWORD, ROLE, MUST_CHANGE_PWD)
            VALUES ('88888', 'CP生产', LOWER(v_pwd), 2, 1);
        END IF;
    END IF;
    COMMIT;
END;
/
