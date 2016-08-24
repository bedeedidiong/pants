# coding=utf-8
# Copyright 2014 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

from pants.scm.subsystems.changed import Changed, ChangedRequest
from pants.task.console_task import ConsoleTask


class WhatChanged(ConsoleTask):
  """Emits the targets that have been modified since a given commit."""

  @classmethod
  def register_options(cls, register):
    super(WhatChanged, cls).register_options(register)
    # N.B. The bulk of options relevant to this task now come from the `Changed` subsystem.
    register('--files', type=bool,
             help='Show changed files instead of the targets that own them.')

  @classmethod
  def subsystem_dependencies(cls):
    return super(WhatChanged, cls).subsystem_dependencies() + (Changed.Factory,)

  def console_output(self, _):
    # N.B. This task shares an options scope ('changed') with the `Changed` subsystem.
    options = self.get_options()
    changed = Changed.Factory.global_instance().create()
    changed_request = changed.changed_request

    # Allow options-based passthrough for easier testing.
    merged_changed_request = ChangedRequest(
      options.get('changes_since', changed_request.changes_since),
      options.get('diffspec', changed_request.diffspec),
      options.get('include_dependees', changed_request.include_dependees),
      options.get('fast', changed_request.fast)
    )

    change_calculator = changed.change_calculator(
      merged_changed_request,
      build_graph=self.context.build_graph,
      address_mapper=self.context.address_mapper,
      scm=self.context.scm,
      workspace=self.context.workspace,
      # N.B. `exclude_target_regexp` is a global scope option registered elsewhere.
      exclude_target_regexp=options.exclude_target_regexp
    )

    if options.files:
      for f in sorted(change_calculator.changed_files()):
        yield f
    else:
      for addr in sorted(change_calculator.changed_target_addresses()):
        yield addr.spec
