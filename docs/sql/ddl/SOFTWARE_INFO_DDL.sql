-- FT_OWEN.SOFTWARE_INFO：工程师客户端版本卡控（按单行使用）
-- 发新包到软件中心时，同步更新 LATEST_VERSION（与 hold_client/version.py 的 APP_VERSION 对齐）
-- "comment" 为带引号列名，查询必须写成 "comment"

CREATE TABLE "FT_OWEN"."SOFTWARE_INFO"
   (	"LATEST_VERSION" VARCHAR2(100),
	"comment" VARCHAR2(2048)
   );

-- 若表已存在但还没有行，插入当前客户端版本：
-- INSERT INTO FT_OWEN.SOFTWARE_INFO (LATEST_VERSION, "comment")
-- VALUES ('1.0.0', '请到软件中心安装最新 Hold 处置客户端');
