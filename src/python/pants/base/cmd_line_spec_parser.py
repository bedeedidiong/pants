# coding=utf-8
# Copyright 2014 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

import os

from pants.base.specs import (AscendantAddresses, DescendantAddresses, SiblingAddresses,
                              SingleAddress)


class CmdLineSpecParser(object):
  """Parses address selectors as passed from the command line.

  See the `specs` package for more information on the types of objects returned.
  This class supports some flexibility in the path portion of the spec to allow for more natural
  command line use cases like tab completion leaving a trailing / for directories and relative
  paths, ie both of these::

    ./src/::
    /absolute/path/to/project/src/::

  Are valid command line specs even though they are not a valid BUILD file specs.  They're both
  normalized to::

    src::

  The above expression would choose every target under src.
  """

  class BadSpecError(Exception):
    """Indicates an unparseable command line address selector."""

  def __init__(self, root_dir):
    self._root_dir = os.path.realpath(root_dir)

  def _normalize_spec_path(self, path):
    is_abs = not path.startswith('//') and os.path.isabs(path)
    if is_abs:
      path = os.path.realpath(path)
      if os.path.commonprefix([self._root_dir, path]) != self._root_dir:
        raise self.BadSpecError('Absolute address path {0} does not share build root {1}'
                                .format(path, self._root_dir))
    else:
      if path.startswith('//'):
        path = path[2:]
      path = os.path.join(self._root_dir, path)

    normalized = os.path.relpath(path, self._root_dir)
    if normalized == '.':
      normalized = ''
    return normalized

  def parse_spec(self, spec):
    """Parse the given spec into a `specs.Spec` object.

    :param spec: a single spec string.
    :return: a single specs.Specs object.
    :raises: CmdLineSpecParser.BadSpecError if the address selector could not be parsed.
    """

    if spec.endswith('::'):
      spec_path = spec[:-len('::')]
      return DescendantAddresses(self._normalize_spec_path(spec_path))
    elif spec.endswith('^'):
      spec_path = spec[:-len('^')]
      return AscendantAddresses(self._normalize_spec_path(spec_path))
    elif spec.endswith(':'):
      spec_path = spec[:-len(':')]
      return SiblingAddresses(self._normalize_spec_path(spec_path))
    else:
      spec_parts = spec.rsplit(':', 1)
      return SingleAddress(self._normalize_spec_path(spec_parts[0]),
                           spec_parts[1] if len(spec_parts) > 1 else None)

  def _iter_resolve_and_parse_specs(self, rel_path, specs):
    for spec in specs:
      if spec.startswith(':'):
        yield self.parse_spec(''.join((rel_path, spec)))
      else:
        yield self.parse_spec(spec)

  def resolve_and_parse_specs(self, rel_path, specs):
    """Given a relative path and set of input specs, produce a list of absolute specs.

    :param string rel_path: The relative path to the input specs from the build root.
    :param iterable specs: An iterable of `specs.Spec` objects.
    """
    return list(self._iter_resolve_and_parse_specs(rel_path, specs))
