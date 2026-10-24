# 代码说明文档

生成模式：offline。基于静态分析，未执行输入代码。

## 项目概览

静态分析了 2 个 Python 文件，提取 5 个公开定义（含类与方法）。

## 文件结构

以下列出本次选择的文件。未选择的依赖文件不在本次分析范围内。

- `pricing.py`：24 行；SHA-256 `8d15ce9841416dead504a376c11a1b3d91639a7d6f7264dab865b75d2fae74b0`。
  模块说明：购物车计价示例。使用 Decimal 保留金额精度，订单服务调用此模块。
  导入：decimal。

- `orders.py`：26 行；SHA-256 `6cdebf41a13dd1cc70189904712f8cededb6d28335793ae639fd82602dcc68a9`。
  模块说明：订单汇总示例。展示跨文件依赖、类方法、异常与业务边界。
  导入：.pricing, dataclasses, decimal。

## 接口说明

公开定义及其类方法如下。下划线开头的内部定义除 __init__ 外不纳入接口清单。

### total_price

```python
total_price(prices: list[Decimal], discount: Decimal=Decimal('1')) -> Decimal
```

计算折后总价并保留两位小数。

prices 必须全部非负，discount 必须处于闭区间 [0, 1]。
空购物车返回 Decimal('0.00')。非法金额或折扣引发 ValueError。

证据：`pricing.py:L6-L17`。

源码出现的 raise 表达式：`ValueError`。

### shipping_fee

```python
shipping_fee(amount: Decimal, threshold: Decimal=Decimal('99')) -> Decimal
```

满 threshold 元免运费，否则收取 6 元。金额及免邮门槛不能为负数。

证据：`pricing.py:L20-L24`。

源码出现的 raise 表达式：`ValueError`。

### Order

```python
class Order
```

不可变订单，包含订单编号和商品价格元组。

证据：`orders.py:L10-L21`。

### Order.payable

```python
payable(self, discount: Decimal=Decimal('1')) -> Decimal
```

返回折后商品金额加运费。空订单拒绝支付并引发 ValueError。

证据：`orders.py:L16-L21`。

源码出现的 raise 表达式：`ValueError`。

### summarize

```python
summarize(order: Order) -> dict[str, str]
```

将订单编号与应付金额转为便于显示的字符串字典。

证据：`orders.py:L24-L26`。

## 使用与边界

调用参数以签名为准。以下说明来自静态证据；不保证运行时行为、依赖可用性或业务正确性。docstring 由输入代码作者提供，应对照实现核实。

## 验证建议

对正常输入、边界值和异常路径分别编写测试。本文没有执行输入代码，也没有宣称测试已经通过。
