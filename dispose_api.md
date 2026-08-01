# 处置行为
## 工程师处置
| 行为 | DISPOSE | NEXT_OWNER_ID | DISPOSED_OWNER_ID |
| ----- | ----- | ----- | ----- |
| 放行 | 1 | 181 | ~ |
| 降级 | 2 | 181 | ~ |
| 重测 | 3 | 181 | ~ |
| 分析 | 5 | 181 | ~ |
| 分析(返回) | 6 | ~ | 181 |
| 转交 | 7 | PRODUCT_INFO.PRO_ENG_ID | ~ |


## 生产处置
| 行为 | DISPOSE | NEXT_OWNER_ID | DISPOSED_OWNER_ID |
| ----- | ----- | ----- | ----- |
| 分析(返回) | 66 | ~ | 181 |
| 回退 | 8 | ~ | 181 |

# 系统行为
| 行为 | DISPOSE | NEXT_OWNER_ID | DISPOSED_OWNER_ID |
| ----- | ----- | ----- | ----- |
| 创建 | 0 | ~ | 1 |
| 关闭 | 99 | | 1 |


> 181: 是生产op的ID  
~: 代指型号所属工程师的ID(PRODUCT_INFO.PRO_ENG_ID)
