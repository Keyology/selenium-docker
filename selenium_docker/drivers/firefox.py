#! /usr/bin/env python
# -*- coding: utf-8 -*-
# >>
#     vivint-selenium-docker, 2017
# <<

from selenium.webdriver import DesiredCapabilities, FirefoxProfile

from selenium_docker.drivers import DockerDriverBase, VideoDriver


class FirefoxDriver(DockerDriverBase):
    """ Firefox browser inside Docker. """

    BROWSER = 'Firefox'
    CONTAINER = dict(
        image='selenium/standalone-firefox',
        detach=True,
        labels={'role': 'browser',
                'dynamic': 'true',
                'browser': 'firefox',
                'hub': 'false'},
        mem_limit='480mb',
        ports={DockerDriverBase.SELENIUM_PORT: None},
        publish_all_ports=True)
    DEFAULT_ARGUMENTS = [
        ('browser.startup.homepage', 'about:blank')
    ]

    def _capabilities(self, arguments, extensions, proxy, user_agent):
        """ Compile the capabilities of FirefoxDriver inside the Container.

        Args:
            arguments (list): unused.
            extensions (list): unused.
            proxy (Proxy): adds proxy instance to DesiredCapabilities.
            user_agent (str): unused.

        Returns:
            dict
        """
        c = DesiredCapabilities.FIREFOX.copy()
        if proxy:
            proxy.add_to_capabilities(c)
        return c

    def _profile(self, arguments, extensions, proxy, user_agent):
        """ Compile the capabilities of ChromeDriver inside the Container.

        Args:
            arguments (list):
            extensions (list):
            proxy (Proxy): unused.
            user_agent (str):

        Returns:
            FirefoxProfile
        """
        profile = FirefoxProfile()
        for ext in extensions:
            profile.add_extension(ext)
        args = list(self.DEFAULT_ARGUMENTS)
        args.extend(arguments)
        for arg_k, value in args:
            profile.set_preference(arg_k, value)
        if user_agent:
            profile.set_preference('general.useragent.override', user_agent)
        return profile


class FirefoxVideoDriver(VideoDriver, FirefoxDriver):
    CONTAINER = dict(
        image='standalone-firefox-ffmpeg',
        detach=True,
        labels={'role': 'browser',
                'dynamic': 'true',
                'browser': 'firefox',
                'hub': 'false'},
        mem_limit='700mb',
        ports={DockerDriverBase.SELENIUM_PORT: None},
        publish_all_ports=True)
