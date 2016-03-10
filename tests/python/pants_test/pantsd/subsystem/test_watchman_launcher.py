# coding=utf-8
# Copyright 2015 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

from contextlib import contextmanager

import mock

from pants.pantsd.subsystem.watchman_launcher import BinaryUtil, WatchmanLauncher
from pants.pantsd.watchman import Watchman
from pants_test.base_test import BaseTest
from pants_test.subsystem.subsystem_util import subsystem_instance


class TestWatchmanLauncher(BaseTest):
  @contextmanager
  def watchman_launcher(self):
    with subsystem_instance(WatchmanLauncher.Factory) as factory:
      yield factory.create()

  def create_mock_watchman(self, is_alive):
    mock_watchman = mock.create_autospec(Watchman, spec_set=False)
    mock_watchman.ExecutionError = Watchman.ExecutionError
    mock_watchman.is_alive.return_value = is_alive
    return mock_watchman

  def test_maybe_launch(self):
    mock_watchman = self.create_mock_watchman(False)

    with self.watchman_launcher() as wl:
      wl.watchman = mock_watchman
      self.assertTrue(wl.maybe_launch())

    mock_watchman.is_alive.assert_called_once_with()
    mock_watchman.launch.assert_called_once_with()

  def test_maybe_launch_already_alive(self):
    mock_watchman = self.create_mock_watchman(True)

    with self.watchman_launcher() as wl:
      wl.watchman = mock_watchman
      self.assertTrue(wl.maybe_launch())

    mock_watchman.is_alive.assert_called_once_with()
    assert not mock_watchman.launch.called

  def test_maybe_launch_error(self):
    mock_watchman = self.create_mock_watchman(False)
    mock_watchman.launch.side_effect = Watchman.ExecutionError('oops!')

    with self.watchman_launcher() as wl:
      wl.watchman = mock_watchman
      self.assertFalse(wl.maybe_launch())

    mock_watchman.is_alive.assert_called_once_with()
    mock_watchman.launch.assert_called_once_with()

  def test_watchman_property(self):
    with self.watchman_launcher() as wl:
      self.assertIsInstance(wl.watchman, Watchman)
