"""购物车计价示例。使用 Decimal 保留金额精度，订单服务调用此模块。"""

from decimal import Decimal, ROUND_HALF_UP


def total_price(prices: list[Decimal], discount: Decimal = Decimal("1")) -> Decimal:
    """计算折后总价并保留两位小数。

    prices 必须全部非负，discount 必须处于闭区间 [0, 1]。
    空购物车返回 Decimal('0.00')。非法金额或折扣引发 ValueError。
    """
    if not Decimal("0") <= discount <= Decimal("1"):
        raise ValueError("折扣必须位于 0 与 1 之间")
    if any(price < 0 for price in prices):
        raise ValueError("价格不能为负")
    amount = sum(prices, start=Decimal("0")) * discount
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def shipping_fee(amount: Decimal, threshold: Decimal = Decimal("99")) -> Decimal:
    """满 threshold 元免运费，否则收取 6 元。金额及免邮门槛不能为负数。"""
    if amount < 0 or threshold < 0:
        raise ValueError("金额与免邮门槛不能为负")
    return Decimal("0.00") if amount >= threshold else Decimal("6.00")
