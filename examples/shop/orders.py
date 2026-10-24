"""订单汇总示例。展示跨文件依赖、类方法、异常与业务边界。"""

from dataclasses import dataclass
from decimal import Decimal

from .pricing import shipping_fee, total_price


@dataclass(frozen=True)
class Order:
    """不可变订单，包含订单编号和商品价格元组。"""

    number: str
    prices: tuple[Decimal, ...]

    def payable(self, discount: Decimal = Decimal("1")) -> Decimal:
        """返回折后商品金额加运费。空订单拒绝支付并引发 ValueError。"""
        if not self.prices:
            raise ValueError("空订单不能支付")
        subtotal = total_price(list(self.prices), discount)
        return subtotal + shipping_fee(subtotal)


def summarize(order: Order) -> dict[str, str]:
    """将订单编号与应付金额转为便于显示的字符串字典。"""
    return {"number": order.number, "payable": str(order.payable())}
