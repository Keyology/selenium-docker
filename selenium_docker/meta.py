#! /usr/bin/env python
# -*- coding: utf-8 -*-
# >>
#     vivint-selenium-docker, 2017
# <<

import os
import logging

from dotmap import DotMap

logger = logging.getLogger(__name__)


OS_ENV_PREFIX = 'SELENIUM_'     # type: str

SETTINGS = {
    'date_format':          '%m-%d-%Y',
    'ffmpeg_description':   'automatic recording generated by selenium-docker',
    'ffmpeg_extension':     'mkv',
    'ffmpeg_fps':           '25',
    'ffmpeg_language':      'EN',
    'ffmpeg_location':      '/recordings',
    'ffmpeg_resolution':    '1280x800',
    'time_format':          '%I:%M:%S'
}
"""dict: default settings directory, used as a read-only store of data. 

Note:
    These values are defined inside the project, but are also read at 
    instantiation time as environment variables. To replace the value at a 
    given key, just set an operating system environment variable with the 
    prefix ``SELENIUM_``. The type will be inferred based on the type of the 
    default value already provided.

Example:
    ``browser=firefox`` => ``SELENIUM_BROWSER=chrome``, ``str``

    ``docker_cache_secs=0.5`` => ``SELENIUM_DOCKER_CACHE_SECS=0.5``, ``float``
"""

for key, value in SETTINGS.items():
    value_type = type(value)
    env_value = os.environ.get('%s%s' % (OS_ENV_PREFIX, key.upper()), value)
    if env_value != value:
        # this statement will not evaluate in .coverage if no environment
        # variables are set to change the default configuration.
        env_value = value_type(env_value)
        logger.debug(
            'settings `%s` : "%s" to "%s"' % (key.upper(), value, env_value))
    SETTINGS[key] = env_value


config = DotMap(SETTINGS)
""":obj:`dotmap.DotMap`: exports the compiled settings with operating
system environment variables applied to the existing keys."""