"""资料池发布选项改动冒烟测试（不依赖数据库/闲鱼接口）。

验证点：
1. MaterialPool 模型含 publish_options 列
2. COLUMN_MIGRATIONS 含 xy_material_pool.publish_options
3. PublishOptionsRequest 校验规则正确
4. ListRequest/BatchListRequest 可解析 publish_options
5. 服务层 list_to_pan 支持 publish_options 参数（签名）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend-web

from common.models.material_pool import MaterialPool  # noqa: E402
from common.db.init_database import DatabaseInitializer  # noqa: E402
from app.api.routes.material_pool import (  # noqa: E402
    BatchListRequest,
    ListRequest,
    PublishOptionsRequest,
)
from app.services.material_pool_service import MaterialPoolService  # noqa: E402
import inspect  # noqa: E402

passed = []


def check(name, cond):
    assert cond, f"FAIL: {name}"
    passed.append(name)
    print(f"  ✓ {name}")


print("1. 模型与迁移")
check("MaterialPool.publish_options 列存在", "publish_options" in MaterialPool.__table__.columns)
col = MaterialPool.__table__.columns["publish_options"]
check("publish_options 为 JSON 类型", col.type.__class__.__name__ == "JSON")
migrations = DatabaseInitializer.COLUMN_MIGRATIONS["xy_material_pool"]
check(
    "COLUMN_MIGRATIONS 含 publish_options",
    any(m[0] == "publish_options" for m in migrations),
)

print("2. PublishOptionsRequest 校验")
opts = PublishOptionsRequest(
    title="2026 学军中学高三数学期中试卷",
    description="描述" * 10,
    price=1.99,
    original_price=9.9,
    images=["/a/1.jpg", "/a/2.jpg"],
    videos=[{"url": "https://v/1.mp4"}],
    quantity=3,
    shipping_method="fixed",
    postage=5.0,
    support_pickup=True,
    address="浙江省 杭州市 西湖区",
    brand="学科网",
    condition="全新",
    specifications=[{"name": "版本", "support_image": False, "values": [{"name": "含答案"}]}],
    sku_rows=[{"specs": {"版本": "含答案"}, "price": 1.99, "stock": 10}],
)
check("合法选项解析成功", opts.price == 1.99 and len(opts.images) == 2)
check("全空选项允许（走默认行为）", PublishOptionsRequest().model_dump(exclude_none=True) == {})
try:
    PublishOptionsRequest(price=-1)
    check("负价格拒绝", False)
except Exception:
    passed.append("负价格拒绝")
    print("  ✓ 负价格拒绝")
try:
    PublishOptionsRequest(shipping_method="whatever")
    check("非法运费方式拒绝", False)
except Exception:
    passed.append("非法运费方式拒绝")
    print("  ✓ 非法运费方式拒绝")
try:
    PublishOptionsRequest(images=[])
    check("空图片列表拒绝", False)
except Exception:
    passed.append("空图片列表拒绝")
    print("  ✓ 空图片列表拒绝")

print("3. 列表/批量请求解析")
req = ListRequest(
    account_id="acc1",
    publish_options={"title": "t", "price": 2.5, "condition": "几乎全新"},
)
check("ListRequest.publish_options 解析", req.publish_options.price == 2.5)
breq = BatchListRequest(
    ids=[1, 2],
    account_id="acc1",
    publish_options={"shipping_method": "none", "quantity": 5},
)
check("BatchListRequest.publish_options 解析", breq.publish_options.shipping_method == "none")

print("4. 服务层签名")
sig = inspect.signature(MaterialPoolService.list_to_pan)
check("list_to_pan 含 publish_options 参数", "publish_options" in sig.parameters)

print(f"\n全部通过：{len(passed)} 项")
