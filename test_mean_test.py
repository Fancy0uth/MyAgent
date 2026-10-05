"""mean 函数的独立验收测试。"""

import unittest

from buggy import mean


class MeanTests(unittest.TestCase):
    """验证 mean 函数应该表现出的行为。"""

    def test_mean_of_three_numbers(self):
        """三个数字的平均值应该正确计算。"""
        self.assertEqual(mean([2, 4, 6]), 4.0)

    def test_mean_of_one_number(self):
        """只有一个数字时，平均值应该等于这个数字。"""
        self.assertEqual(mean([5]), 5.0)

    def test_mean_of_positive_and_negative_numbers(self):
        """正负数字相互抵消时，平均值应该是 0。"""
        self.assertEqual(mean([-2, 2]), 0.0)

    def test_empty_list_raises_value_error(self):
        """空列表没有平均值，应该抛出 ValueError。"""
        with self.assertRaises(ValueError):
            mean([])


if __name__ == "__main__":
    unittest.main()
