-- FT产品主管。USERS / ROLE 不分测试表和正式表。
-- 主管类角色号占用 20–29。本角色固定 ROLE_ID=20。
-- 只补角色，不插入用户。账号由超级管理员在用户管理页创建。

DECLARE
    v_role_cnt NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_role_cnt FROM ROLE WHERE ROLE_ID = 20;
    IF v_role_cnt = 0 THEN
        INSERT INTO ROLE (ROLE_ID, ROLE_DESC) VALUES (20, 'FT产品主管');
    END IF;
    COMMIT;
END;
/
