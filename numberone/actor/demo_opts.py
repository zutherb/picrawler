# -*- coding: utf-8 -*-
# Copyright (c) 2014-18 Richard Hull and contributors
# See LICENSE.rst for details.

import sys
import logging

from luma.core import cmdline, error


# logging
logging.basicConfig(
  level=logging.CRITICAL,
  format='%(asctime)-15s - %(message)s'
)
# ignore PIL debug messages
logging.getLogger('PIL').setLevel(logging.ERROR)


def display_settings(device, args):
  """
  Display a short summary of the settings.

  :rtype: str
  """
  iface = ''
  display_types = cmdline.get_display_types()
  if args.display not in display_types['emulator']:
    iface = 'Interface: {}\n'.format(args.interface)

  lib_name = cmdline.get_library_for_display_type(args.display)
  if lib_name is not None:
    lib_version = cmdline.get_library_version(lib_name)
  else:
    lib_name = lib_version = 'unknown'

  import luma.core
  version = 'luma.{} {} (luma.core {})'.format(
    lib_name, lib_version, luma.core.__version__)

  return 'Version: {}\nDisplay: {}\n{}Dimensions: {} x {}\n{}'.format(
    version, args.display, iface, device.width, device.height, '-' * 60)


def get_device(actual_args=None):
  """
  Create device from command-line arguments and return it.
  """
  if actual_args is None:
    actual_args = sys.argv[1:]
  parser = cmdline.create_parser(description='luma.examples arguments')
  args = parser.parse_args(actual_args)

  if args.config:
    # load config from file
    config = cmdline.load_config(args.config)
    args = parser.parse_args(config + actual_args)

  # create device
  try:
    device = cmdline.create_device(args)
    print(display_settings(device, args))
    return device

  except error.Error as e:
    parser.error(e)
    return None
