import asyncio
import functools
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from mootdx.consts import EX_HOSTS
from mootdx.consts import HQ_HOSTS
from mootdx.exceptions import MootdxValidationException
from mootdx.logger import logger
from mootdx_next.candidates import probe_hq_candidate
from mootdx_next.candidates import refresh_hq_candidates
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.protocol.report_files import decode_ex_instrument_count
from mootdx_next.transport.constants import EX_INSTRUMENT_COUNT_PAYLOAD
from mootdx_next.transport.constants import EX_SETUP_PAYLOADS
from mootdx_next.transport.socket_transport import SyncSocketTransport


hosts = {
    'HQ': [{'addr': hs[1], 'port': hs[2], 'time': 0, 'site': hs[0]} for hs in HQ_HOSTS],
    'EX': [{'addr': hs[1], 'port': hs[2], 'time': 0, 'site': hs[0]} for hs in EX_HOSTS],
}

results = {k: [] for k in hosts}


def callback(res, key):
    """
    异步回调函数

    :param res:
    :param key:
    """
    result = res.result()

    if result.get('time'):
        results[key].append(result)

    # logger.debug(f"callback: {res.result()}")


def connect(proxy: dict) -> dict:
    """
    连接服务器函数

    :param proxy: 代理IP信息
    :return:
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(.7)

        start = time.perf_counter()

        sock.connect((proxy.get('addr'), int(proxy.get('port'))))
        sock.close()

        proxy['time'] = (time.perf_counter() - start) * 1000

        logger.debug('{addr}:{port} 验证通过，响应时间：{time} ms.'.format(**proxy))
    except socket.timeout as ex:  # noqa
        logger.debug('{addr},{port} time out.'.format(**proxy))
        proxy['time'] = None
    except ConnectionRefusedError as ex:  # noqa
        logger.debug('{addr},{port} 验证失败.'.format(**proxy))
        proxy['time'] = None

    return proxy


def connect2(proxy, index='HQ'):
    proxy['time'] = None
    ok = False

    try:
        tms = time.perf_counter()
        if index == 'HQ':
            ok = _probe_hq(proxy)
        elif index == 'EX':
            ok = _probe_ex(proxy)
        if ok:
            proxy['time'] = (time.perf_counter() - tms) * 1000
            logger.debug('{addr}:{port} 验证通过，响应时间：{time} ms.'.format(**proxy))
        else:
            logger.debug('{addr}:{port} 验证失败.'.format(**proxy))
    except socket.timeout:  # noqa
        logger.debug('{addr}:{port} time out.'.format(**proxy))
        proxy['time'] = None
    except Exception:  # noqa
        logger.debug('{addr}:{port} 验证失败.'.format(**proxy))

    return proxy


async def verify(proxy: dict, index):
    """
    检验代理连通性函数

    :param index:
    :param proxy: 代理IP信息
    :return:
    """
    return await asyncio.get_event_loop().run_in_executor(None, functools.partial(connect2, proxy=proxy, index=index))


def _build_server_endpoint(proxy: dict) -> ServerEndpoint:
    return ServerEndpoint(host=proxy.get('addr'), port=int(proxy.get('port')), label=proxy.get('site'))


def _probe_hq(proxy: dict) -> bool:
    return (
        probe_hq_candidate(
            str(proxy.get('site') or proxy.get('addr')),
            str(proxy.get('addr')),
            int(proxy.get('port')),
        )
        is not None
    )


def _probe_ex(proxy: dict) -> bool:
    transport = SyncSocketTransport(setup_payloads=EX_SETUP_PAYLOADS)
    server = _build_server_endpoint(proxy)
    try:
        envelope = transport.send(
            RequestContext(api='ex_instrument_count', params={}, timeout_ms=700),
            EX_INSTRUMENT_COUNT_PAYLOAD,
            server,
        )
        return decode_ex_instrument_count(envelope.body or b'') > 0
    finally:
        transport.close()


def _unsupported_gp():
    exc = MootdxValidationException()
    exc.args = ('GP 财务下载线路已经废弃且不再支持',)
    return exc


def server(index=None, limit=5, console=False, sync=True):
    if index == 'GP':
        raise _unsupported_gp()

    if index not in hosts:
        raise KeyError(index)

    _hosts = [dict(item) for item in hosts[index]]

    if sync:
        measured = [connect2(proxy, index=index) for proxy in _hosts]
    else:
        with ThreadPoolExecutor() as executor:
            measured = list(executor.map(partial(connect2, index=index), _hosts))

    servers = [item for item in measured if item.get('time')]
    servers.sort(key=lambda item: item['time'])
    results[index] = list(servers)

    if limit:
        servers = servers[:limit]

    # 结果按响应时间从小到大排序
    if console:
        from prettytable import PrettyTable

        logger.debug('[√] 最优服务器:')

        t = PrettyTable(['Name', 'Addr', 'Port', 'Time'])
        t.align['Name'] = 'l'
        t.align['Addr'] = 'l'
        t.align['Port'] = 'l'
        t.align['Time'] = 'r'
        t.padding_width = 1

        for host in servers:
            t.add_row(
                [
                    host['site'],
                    host['addr'],
                    host['port'],
                    '{:5.2f} ms'.format(host['time']),
                ]
            )

        logger.debug('\n' + str(t))

    return [(item['addr'], int(item['port'])) for item in servers]


def check_server(console=False, limit=5, sync=False) -> None:
    return bestip(console=console, limit=limit, sync=sync)


def bestip(console=False, limit=5, sync=False) -> None:
    logger.info('[-] 选择最快的服务器...')
    logger.debug(f'sync => {sync}')
    candidates = refresh_hq_candidates()
    selected = candidates[:limit] if limit else candidates
    results['HQ'] = [
        {
            'addr': item.host,
            'port': item.port,
            'time': item.latency_ms,
            'site': item.label or item.host,
        }
        for item in selected
    ]

    if console:
        from prettytable import PrettyTable

        table = PrettyTable(['Name', 'Addr', 'Port', 'Time'])
        table.align['Name'] = 'l'
        table.align['Addr'] = 'l'
        table.align['Port'] = 'l'
        table.align['Time'] = 'r'
        table.padding_width = 1
        for item in results['HQ']:
            latency = item['time']
            table.add_row(
                [
                    item['site'],
                    item['addr'],
                    item['port'],
                    'n/a' if latency is None else f'{latency:5.2f} ms',
                ]
            )
        logger.debug('\n' + str(table))

    if candidates:
        logger.info('[√] 最优服务器已缓存到当前进程，10 分钟内复用。')
    else:
        logger.warning('[×] 没有探测到同时支持行情与 K 线的服务器。')


if __name__ == '__main__':
    bestip(sync=False, limit=5, console=True)
