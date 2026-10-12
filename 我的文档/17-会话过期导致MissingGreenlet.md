# 会话过期导致 MissingGreenlet

- 提交：`aff7bbf`

## 问题

下载流程里调用 `expire_all()` 会把当前数据库会话里的所有对象都标记为过期。异步环境下再次读取这些对象，会触发 `MissingGreenlet`。

## 改法

不再让整个会话过期。需要最新数据时，只对当前这一行调用 `refresh(row)` 重新查询。

文件：`backend-web/app/services/material_pool_service.py`。
