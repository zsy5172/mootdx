import unittest

from mootdx.reader import ReaderBase


# @pytest.mark.skip
class TestReaderBase(unittest.TestCase):
    def test_find_path(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='sh000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sh', 'sh000001', ['lc1', '1']) == result, result

    def test_find_path2(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sz', 'sz000001', ['lc1', '1']) == result, result

    def test_find_path3(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='34#000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('ds', '34#000001', ['lc1', '1']) == result, result

    def test_find_path4(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='sh000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sh', 'sh000001', ['lc1', '1']) == result, result

    def test_find_path5(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='SH000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sh', 'sh000001', ['lc1', '1']) == result, result

    def test_find_path6(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='sz000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sz', 'sz000001', ['lc1', '1']) == result, result

    def test_find_path_bj(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='920001', subdir='lday', suffix='day', debug=True)
        assert ('bj', 'bj920001', ['day']) == result, result

    def test_find_path_with_separator_prefix(self):
        reader = ReaderBase('tests/fixtures')
        dotted = reader.find_path(symbol='SH.000001', subdir='lday', suffix='day', debug=True)
        hashed = reader.find_path(symbol='SH#000001', subdir='lday', suffix='day', debug=True)

        assert ('sh', 'sh000001', ['day']) == dotted
        assert ('sh', 'sh000001', ['day']) == hashed


if __name__ == '__main__':
    unittest.main()
