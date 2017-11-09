#! /usr/bin/env python
# -*- coding: utf-8 -*-
# >>
#     vivint-selenium-docker, 2017
# <<

import math
from collections import Mapping
from logging import getLogger

import gevent
from gevent.pool import Pool
from gevent.queue import JoinableQueue, Queue
from toolz.itertoolz import count, isiterable

from selenium_docker.base import ContainerFactory
from selenium_docker.drivers.chrome import ChromeDriver
from selenium_docker.errors import SeleniumDockerException
from selenium_docker.proxy import SquidProxy
from selenium_docker.utils import gen_uuid


class DriverPoolRuntimeException(RuntimeError, SeleniumDockerException):
    """ Pool RunTime Exception. """


class DriverPoolValueError(ValueError, SeleniumDockerException):
    """ Pool interaction ValueError. """


class DriverPool(object):
    """ Create a pool of available Selenium containers for processing.

    Args:
        size (int): maximum concurrent tasks. Must be at least ``2``.
        driver_cls (WebDriver):
        driver_cls_args (tuple):
        driver_cls_kw (dict):
        use_proxy (bool):
        factory (:obj:`~selenium_docker.base.ContainerFactory`):
        name (str):
        logger (:obj:`logging.Logger`):

    Example::

        pool = DriverPool(size=2)

        urls = [
            'https://google.com',
            'https://reddit.com',
            'https://yahoo.com',
            'http://ksl.com',
            'http://cnn.com'
        ]

        def get_title(driver, url):
            driver.get(url)
            return driver.title

        for result in pool.execute(get_title, urls):
            print(result)



    """

    INNER_THREAD_SLEEP = 0.5
    """float: essentially our polling interval between tasks and checking
    when tasks have completed.
    """

    def __init__(self, size, driver_cls=ChromeDriver, driver_cls_args=None,
                 driver_cls_kw=None, use_proxy=True, factory=None, name=None,
                 logger=None):
        self.size = max(2, size)
        self.name = name or gen_uuid(6)
        self.factory = factory or ContainerFactory.get_default_factory()
        self.logger = logger or getLogger('DriverPool.%s' % self.name)

        self._driver_cls = driver_cls
        self._driver_cls_args = driver_cls_args or tuple()
        self._driver_cls_kw = driver_cls_kw or dict()
        self._drivers = Queue(maxsize=self.size)

        # post init inspections
        if not hasattr(self._driver_cls, 'CONTAINER'):
            raise DriverPoolValueError('driver_cls must extend DockerDriver')

        if not isiterable(self._driver_cls_args):
            raise DriverPoolValueError(
                '%s is not iterable' % self._driver_cls_args)

        if not isinstance(self._driver_cls_kw, Mapping):
            raise DriverPoolValueError(
                '%s is not a valid mapping' % self._driver_cls_kw)

        # determine proxy usage
        self.proxy = None
        self._use_proxy = use_proxy  # type: bool

        # deferred instantiation
        self._pool = None  # type: Pool
        self._results = None  # type: Queue
        self._tasks = None  # type: JoinableQueue
        self._processing = False  # type: bool
        self.__feeder_green = None  # type: gevent.Greenlet

    @property
    def is_processing(self):
        """bool: whether or not we're currently processing tasks. """
        return self._processing

    def __bootstrap(self):
        """ Prepare this driver pool instance to batch execute task items. """
        if self.is_processing:
            # cannot run two executions simultaneously
            raise DriverPoolRuntimeException(
                'cannot bootstrap pool, already running')
        if self._pool:  # pragma: no cover
            self.logger.debug('killing processing pool')
            self._pool.join(timeout=10.0)
            self._pool.kill()
            self._pool = None
        if self._use_proxy and not self.proxy:
            # defer proxy instantiation
            self.logger.debug('bootstrapping squid proxy')
            self.proxy = SquidProxy(factory=self.factory)
        self.logger.debug('bootstrapping pool processing')
        self._processing = True
        self._results = Queue()
        self._tasks = JoinableQueue()
        self._load_drivers()
        if not self._pool:
            self._pool = Pool(
                size=self.size + math.ceil(self.size * 0.25))  # headroom

    def __cleanup(self, force=False):
        """ Stop and remove the web drivers and their containers. """
        if self.is_processing and not force:  # pragma: no cover
            raise DriverPoolRuntimeException(
                'cannot cleanup driver pool while executing')
        self._processing = False
        squid = None  # type: gevent.Greenlet
        error = None  # type: SeleniumDockerException
        if self.proxy:
            self.logger.debug('closing squid proxy')
            squid = gevent.spawn(self.proxy.quit)
        self.logger.debug('closing all driver containers')
        while not self._drivers.empty():
            d = self._drivers.get(block=True)
            try:
                d.quit()
            except SeleniumDockerException as e:  # pragma: no cover
                self.logger.exception(e, exc_info=True)
                if not force:
                    error = e
        if self.proxy:
            squid.join()
            self.proxy = None
        if error:  # pragma: no cover
            raise error

    def _load_drivers(self):
        """ Load the web driver instances and containers. """
        if not self._drivers.empty():  # pragma: no cover
            return
        # we need to spin up our driver instances
        args = self._driver_cls_args
        kw = dict(self._driver_cls_kw)
        kw.update({
            'proxy': self.proxy,
            'factory': self.factory,
        })

        def make_container(a, k):
            d = self._driver_cls(*a, **k)
            self._drivers.put(d)
            self.logger.debug('available drivers %d', self._drivers.qsize())

        threads = []
        for o in range(self.size):
            self.logger.debug('creating driver %d of %d', o + 1, self.size)
            thread = gevent.spawn(make_container, args, kw)
            threads.append(thread)
        for t in reversed(threads):
            t.join()
        if not self._drivers.full():
            raise DriverPoolRuntimeException(
                'unable to fulfill required concurrent drivers, %d of %d' % (
                    self._drivers.qsize(), self.size))

    def add_async(self, *items):
        """ Add additional items to the asynchronous processing queue.

        Args:
            items (list(Any)): list of items that need processing. Each item is
                applied one at a time to an available driver from the pool.

        Raises:
            StopIteration: when all items have been added.
        """
        if len(items) == 1 and isinstance(items[0], list):
            items = iter(items[0])
        if not items:
            raise DriverPoolValueError(
                'cannot add items with value: %s' % str(items))
        item_count = count(items)
        self.logger.debug('adding %d additional items to tasks', item_count)
        for o in items:
            self._tasks.put(o)

    def close(self):
        """ Force close all the drivers and cleanup their containers.

        Returns:
            None
        """
        self.__cleanup(force=True)

    def execute(self, fn, items, preserve_order=False, auto_clean=True,
                no_wait=False):
        """ Execute a fixed function, blocking for results.

        Args:
            fn (Callable): function that takes two parameters, ``driver`` and
                ``task``.
            items (list(Any)): list of items that need processing. Each item is
                applied one at a time to an available driver from the pool.
            preserve_order (bool): should the results be returned in the order
                they were supplied via ``items``. It's more performant to
                allow results to return in any order.
            auto_clean (bool): cleanup docker containers after executing. If
                multiple processing tasks are going to be used, it's more
                performant to leave the containers running and reuse them.
            no_wait (bool): forgo a small sleep interval between finishing
                a task and putting the driver back in the available drivers
                pool.

        Yields:
            results: the result for each item as they're finished.
        """

        def worker(o):
            job_num, item = o
            self.logger.debug('doing work on item %d' % job_num)
            driver = self._drivers.get(block=True)
            ret_val = fn(driver, item)
            if not no_wait:
                gevent.sleep(self.INNER_THREAD_SLEEP)
            self._drivers.put(driver)
            return ret_val

        if self.__feeder_green:
            raise DriverPoolRuntimeException(
                'cannot perform a blocking execute while async processing')

        self.__bootstrap()
        self.logger.debug('starting sync processing')

        if preserve_order:
            ittr = self._pool.imap
        else:
            ittr = self._pool.imap_unordered

        self.logger.debug('yielding processed results')
        for o in ittr(worker, enumerate(items)):
            yield o
        self.logger.debug('stopping sync processing')
        if auto_clean:
            self.logger.debug('auto cleanup pool environment')
            self.__cleanup(force=True)

    def execute_async(self, fn, items=None, callback=None):
        """ Execute a fixed function in the background, streaming results.

        Args:
            fn (Callable): function that takes two parameters, ``driver`` and
                ``task``.
            items (list(Any)): list of items that need processing. Each item is
                applied one at a time to an available driver from the pool.
            callback (Callable): function that takes a single parameter, the
                return value of ``fn`` when its finished processing and has
                returned the driver to the queue.

        Raises:
            DriverPoolValueError: if ``callback`` is not ``None``
                or ``callable``.

        Returns:
            None
        """

        def worker(fn, task):
            async_task_id = gen_uuid(12)
            self.logger.debug('starting async task %s', async_task_id)
            driver = self._drivers.get(block=True)
            ret_val = fn(driver, task)
            self._results.put(ret_val)
            self._drivers.put(driver)
            gevent.sleep(self.INNER_THREAD_SLEEP)
            return ret_val

        def worker_cb(task_result):
            self.logger.debug('finished async task, %s', task_result)
            return True

        def feeder():
            self.logger.debug('starting async feeder thread')
            while True:
                while not self._tasks.empty():
                    task = self._tasks.get()
                    self._pool.apply_async(
                        worker,
                        args=(fn, task,),
                        callback=callback)
                gevent.sleep(self.INNER_THREAD_SLEEP)

        if callback is None:
            callback = worker_cb

        for f in [fn, callback]:
            if not callable(f):
                raise DriverPoolValueError(
                    'cannot use %s, is not callable' % callback)

        self.logger.debug('starting async processing')
        self.__bootstrap()
        if not self.__feeder_green:
            self.__feeder_green = gevent.spawn(feeder)
        if items:
            self.add_async(items)

    def quit(self):
        """ Alias for :func:`~DriverPool.close()`. Included for consistency
        with driver instances that generally call ``quit`` when they're no
        longer needed.

        Returns:
            None
        """
        return self.close()

    def results(self, block=True):
        """ Iterate over available results from processed tasks.

        Args:
            block (bool): when ``True``, block this call until all tasks have
                been processed and all results have been returned. Otherwise
                this will continue indefinitely while tasks are dynamically
                added to the async processing queue.

        Yields:
            results: one result at a time as they're finished.

        Raises:
            StopIteration: when the processing is finished.
        """
        est_size = self._results.qsize()
        self.logger.debug('there are an estimated %d results', est_size)
        if block:
            self.logger.debug('blocking for results to finish processing')
            while self.is_processing:
                while not self._results.empty():
                    yield self._results.get()
                gevent.sleep(self.INNER_THREAD_SLEEP)
                if self._tasks.empty() and self._results.empty():
                    break
            raise StopIteration
        else:
            if est_size > 0:
                self.logger.debug('returning as many results as have finished')
            self._results.put(StopIteration)
            for result in self._results:
                yield result

    def stop_async(self, timeout=None, auto_clean=True):
        """ Stop all the async worker processing from executing.

        Args:
            timeout (float): number of seconds to wait for pool to finish
                processing before killing and closing out the execution.
            auto_clean (bool): cleanup docker containers after executing. If
                multiple processing tasks are going to be used, it's more
                performant to leave the containers running and reuse them.

        Returns:
            None
        """
        self.logger.debug('stopping async processing')
        if self.__feeder_green:
            self.logger.debug('killing async feeder thread')
            gevent.kill(self.__feeder_green)
            self.__feeder_green = None
        if self._pool:
            self.logger.debug('joining async pool before kill')
            self._pool.join(timeout=timeout or 1.0)
            self._pool.kill(block=True)
        tasks_count = self._tasks.qsize()
        self.logger.info('%d tasks remained unprocessed', tasks_count)
        if auto_clean:
            self.logger.debug('auto cleanup pool environment')
            self.__cleanup(force=True)
