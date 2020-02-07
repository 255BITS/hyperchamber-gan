import hyperchamber as hc
import inspect
import copy
import os
import operator
from functools import reduce

from .base_generator import BaseGenerator
from hypergan.configurable_component import ConfigurableComponent

class ConfigurableGenerator(ConfigurableComponent):
    def __init__(self, gan, config, *args, **kw_args):
        ConfigurableComponent.__init__(self, gan, config,*args, **kw_args)
