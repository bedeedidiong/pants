# coding=utf-8
# Copyright 2014 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

import os
import re

from pants.backend.codegen.targets.jaxb_library import JaxbLibrary
from pants.backend.codegen.tasks.simple_codegen_task import SimpleCodegenTask
from pants.backend.jvm.subsystems.shader import Shader
from pants.backend.jvm.targets.jar_dependency import JarDependency
from pants.backend.jvm.targets.java_library import JavaLibrary
from pants.backend.jvm.tasks.nailgun_task import NailgunTask
from pants.base.exceptions import TaskError
from pants.base.workunit import WorkUnitLabel
from pants.java.distribution.distribution import DistributionLocator


class JaxbGen(SimpleCodegenTask, NailgunTask):
  """Generates java source files from jaxb schema (.xsd)."""

  _MAIN = 'com.sun.tools.xjc.Driver'

  def __init__(self, *args, **kwargs):
    """
    :param context: inherited parameter from Task
    :param workdir: inherited parameter from Task
    """
    super(JaxbGen, self).__init__(*args, **kwargs)
    self.gen_langs = set()
    lang = 'java'
    if self.context.products.isrequired(lang):
      self.gen_langs.add(lang)

  @classmethod
  def register_options(cls, register):
    super(JaxbGen, cls).register_options(register)

    cls.register_jvm_tool(register,
                          'jaxb-compiler',
                          classpath=[
                            JarDependency(org='org.glassfish.jaxb', name='jaxb-xjc', rev='2.2.11'),
                          ],
                          main=cls._MAIN,
                          custom_rules=[
                            # Loads plugins from its classpath via reflection.
                            Shader.exclude_package('com.sun.tools.xjc', recursive=True),
                          ],)

  def synthetic_target_type(self, target):
    return JavaLibrary

  def is_gentarget(self, target):
    return isinstance(target, JaxbLibrary)

  def execute_codegen(self, target, target_workdir):
    if not isinstance(target, JaxbLibrary):
      raise TaskError('Invalid target type "{class_type}" (expected JaxbLibrary)'
                      .format(class_type=type(target).__name__))

    for source in target.sources_relative_to_buildroot():
      path_to_xsd = source
      output_package = target.package

      if output_package is None:
        output_package = self._guess_package(source)
      output_package = self._correct_package(output_package)

      # NB(zundel): The -no-header option keeps it from writing a timestamp, making the
      # output non-deterministic.  See https://github.com/pantsbuild/pants/issues/1786
      args = ['-p', output_package, '-d', target_workdir, '-no-header', path_to_xsd]
      result = self.runjava(classpath=self.tool_classpath('jaxb-compiler'),
                            main=self._MAIN,
                            args=args,
                            workunit_name='xjc',
                            workunit_labels=[WorkUnitLabel.TOOL])

      if result != 0:
        raise TaskError('xjc ... exited non-zero ({code})'.format(code=result))

  @classmethod
  def _guess_package(self, path):
    """Used in execute_codegen to actually invoke the compiler with the proper arguments, and in
    _sources_to_be_generated to declare what the generated files will be.
    """
    supported_prefixes = ('com', 'org', 'net',)
    package = ''
    slash = path.rfind(os.path.sep)
    prefix_with_slash = max(path.rfind(os.path.join('', prefix, ''))
                            for prefix in supported_prefixes)
    if prefix_with_slash < 0:
      package = path[:slash]
    elif prefix_with_slash >= 0:
      package = path[prefix_with_slash:slash]
    package = package.replace(os.path.sep, ' ')
    package = package.strip().replace(' ', '.')
    return package

  @classmethod
  def _correct_package(self, package):
    package = package.replace('/', '.')
    package = re.sub(r'^\.+', '', package)
    package = re.sub(r'\.+$', '', package)
    if re.search(r'\.{2,}', package) is not None:
      raise ValueError('Package name cannot have consecutive periods! ({})'.format(package))
    return package

  @property
  def _copy_target_attributes(self):
    """Propagate the provides attribute to the synthetic java_library() target for publishing."""
    return ['provides']
