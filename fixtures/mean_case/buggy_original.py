"""一个用于练习的、故意包含 Bug 的平均数程序。"""


def mean(values):
    """返回数字列表的平均值。"""
    if not values:
        raise ValueError("values must not be empty")

    # 这里故意多加了 1，是本练习要被测试发现的 Bug。
    return sum(values) / (len(values) + 1)
