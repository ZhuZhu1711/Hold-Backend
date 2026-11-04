-- PRODUCT_INFO 不分测试表和正式表。
-- 一个产品一个 PIE，一个 PIE 可对应多个产品。
-- 内容是工号或姓名，原样保存。已有产品保持空，表示尚未绑定。
--
-- DBeaver 用「执行 SQL 脚本」(Alt+X)，三条都会跑。
-- 某一句报「名称已由现有对象使用」或「列已经存在」时，跳过那一句，执行剩下的。

ALTER TABLE PRODUCT_INFO ADD PIE VARCHAR2(50);

COMMENT ON COLUMN PRODUCT_INFO.PIE IS '所属 PIE（工号或姓名）。一个产品一个 PIE，一个 PIE 可对应多个产品。';

CREATE INDEX IDX_PRODUCT_INFO_PIE ON PRODUCT_INFO (PIE);
