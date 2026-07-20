from mootdx import config as config
from mootdx.consts import EX_HOSTS as EX_HOSTS
from mootdx.consts import GP_HOSTS as GP_HOSTS
from mootdx.consts import HQ_HOSTS as HQ_HOSTS
from mootdx.utils import get_config_path as get_config_path

__version__ = '0.11.11'
__author__ = 'bopo.wang <ibopo@126.com> & HarmonSir <git@pylab.me>'


def __getattr__(name):
    if name == 'server':
        from mootdx.server import server as server_fn

        return server_fn

    raise AttributeError(name)
