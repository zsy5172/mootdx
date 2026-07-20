from pathlib import Path

from mootdx.financial import financial
from mootdx.financial.base import BaseFinancial
from mootdx.logger import logger


class Affair(object):
    @staticmethod
    def parse(downdir='.', filename=None, **kwargs):
        """
        按目录解析文件

        :param downdir:
        :param filename:
        :return:
        """

        if not filename:
            logger.critical('文件名不能为空!')
            return None

        filepath = Path(downdir) / filename
        filepath.exists() or Affair.fetch(downdir, filename)

        if Path(filepath).exists():
            return financial.FinancialReader().to_data(filepath, **kwargs)

        logger.warning(f'文件不存在：{filename}')

        return None

    @staticmethod
    def files():
        """
        财务文件列表

        :return:
        """
        raise BaseFinancial.unsupported_gp()

    @staticmethod
    def fetch(downdir: str = None, filename: str = None):  # noqa
        """
        财务数据下载

        :param downdir: 下载目录
        :param filename: 文件名
        :return:
        """

        raise BaseFinancial.unsupported_gp()
