from abc import ABC
from pathlib import Path

from mootdx_next.localfiles import ExtBarReader
from mootdx_next.localfiles import StdDailyBarReader
from mootdx_next.localfiles import StdLCMinBarReader
from mootdx_next.localfiles import StdMinBarReader
from mootdx_next.symbols import get_stock_market


class Reader(object):
    @staticmethod
    def factory(market='std', **kwargs):
        """
        Reader 工厂方法

        :param market: std 标准市场, ext 扩展市场
        :param kwargs: 可变参数
        :return:
        """

        if market == 'ext':
            return ExtReader(**kwargs)

        return StdReader(**kwargs)


class ReaderBase(ABC):
    # 默认通达信安装目录
    tdxdir = 'C:/new_tdx'

    def __init__(self, tdxdir=None):
        """
        构造函数

        :param tdxdir: 通达信安装目录
        """

        supplied = Path(tdxdir or self.tdxdir).expanduser()
        if not supplied.is_dir():
            raise Exception('tdxdir 目录不存在')

        if supplied.name.lower() == 'vipdoc':
            self.vipdoc = supplied
            self.tdxdir = str(supplied.parent)
        else:
            self.tdxdir = str(supplied)
            self.vipdoc = supplied / 'vipdoc'

    def find_path(self, symbol=None, subdir='lday', suffix=None, **kwargs):
        """
        自动匹配文件路径，辅助函数

        :param symbol:
        :param subdir:
        :param suffix:
        :return: pd.dataFrame or None
        """

        raw_symbol = str(symbol).strip()

        # 判断市场, 带#扩展市场
        if '#' in raw_symbol:
            market = 'ds'
            normalized_symbol = raw_symbol
        # 通达信特有的板块指数88****开头的日线数据放在 sh 文件夹下
        elif raw_symbol.lower().startswith('88'):
            market = 'sh'
            normalized_symbol = raw_symbol.lower()
        else:
            # 判断是sh还是sz
            market = get_stock_market(raw_symbol, True)
            normalized_symbol = raw_symbol.lower()

        # 判断前缀
        if market.lower() in ['sh', 'sz', 'bj']:
            for prefix in ('sh', 'sz', 'bj'):
                if normalized_symbol.startswith(prefix):
                    normalized_symbol = normalized_symbol[len(prefix):]
                    break
            normalized_symbol = market + normalized_symbol

        # 判断后缀
        suffix = suffix if isinstance(suffix, list) else [suffix]

        # 调试使用
        if kwargs.get('debug'):
            return market, normalized_symbol, suffix

        # 遍历扩展名
        for ex_ in suffix:
            ex_ = ex_.strip('.')
            vipdoc = self.vipdoc / market / subdir / f'{normalized_symbol}.{ex_}'

            if Path(vipdoc).exists():
                return vipdoc

        return None


class StdReader(ReaderBase):
    """股票市场"""

    def daily(self, symbol=None, **kwargs):
        """
        获取日线数据

        :param symbol: 证券代码
        :return: pd.dataFrame or None
        """
        symbol = Path(symbol).stem
        reader = StdDailyBarReader()
        vipdoc = self.find_path(symbol=symbol, subdir='lday', suffix='day')

        result = reader.get_df(str(vipdoc)) if vipdoc else None
        return result

    def minute(self, symbol=None, suffix=1, **kwargs):  # noqa
        """
        获取1, 5分钟线

        :param suffix: 文件前缀
        :param symbol: 证券代码
        :return: pd.dataFrame or None
        """
        symbol = Path(symbol).stem
        subdir = 'fzline' if str(suffix) == '5' else 'minline'
        suffix = ['lc5', '5'] if str(suffix) == '5' else ['lc1', '1']
        symbol = self.find_path(symbol, subdir=subdir, suffix=suffix)

        if symbol is not None:
            reader = StdMinBarReader() if 'lc' not in symbol.suffix else StdLCMinBarReader()
            return reader.get_df(str(symbol))

        return None

    def fzline(self, symbol=None):
        """
        分钟线数据

        :param symbol: 自定义板块股票列表, 类型 list
        :return: pd.dataFrame or Bool
        """
        return self.minute(symbol, suffix=5)

    def block_new(self, name: str = None, symbol: list = None, group=False, **kwargs):
        """
        自定义板块数据操作

        :param name: 自定义板块名称
        :param symbol: 自定义板块股票列表, 类型 list
        :param group:
        :return: pd.dataFrame or Bool
        """
        from mootdx_next.customize import Customize

        reader = Customize(tdxdir=self.tdxdir)

        if symbol:
            return reader.create(name=name, symbol=symbol, **kwargs)

        return reader.search(name=name, group=group)

    def block(self, symbol='', group=False, **kwargs):
        """
        获取板块数据

        :param symbol:  板块文件
        :param group:   分组解析
        :return: pd.dataFrame or None
        """
        from mootdx_next.parse import BaseParse

        return BaseParse(self.tdxdir).parse(symbol, group=group, **kwargs)


class ExtReader(ReaderBase):
    """扩展市场读取"""

    def __init__(self, tdxdir=None):
        super(ExtReader, self).__init__(tdxdir)
        self.reader = ExtBarReader()

    def daily(self, symbol=None):
        """
        获取扩展市场日线数据

        :return: pd.dataFrame or None
        """

        vipdoc = self.find_path(symbol=symbol, subdir='lday', suffix='day')
        return self.reader.get_df(str(vipdoc)) if vipdoc else None

    def minute(self, symbol=None):
        """
        获取扩展市场分钟线数据

        :return: pd.dataFrame or None
        """

        if not symbol:
            return None

        vipdoc = self.find_path(symbol=symbol, subdir='minline', suffix=['lc1', '1'])
        return self.reader.get_df(str(vipdoc)) if vipdoc else None

    def fzline(self, symbol=None):
        """
        获取日线数据

        :return: pd.dataFrame or None
        """

        vipdoc = self.find_path(symbol=symbol, subdir='fzline', suffix='lc5')
        return self.reader.get_df(str(vipdoc)) if vipdoc else None
