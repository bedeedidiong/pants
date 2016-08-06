# coding=utf-8
# Copyright 2015 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

import logging
import threading
import time
from collections import defaultdict
from contextlib import contextmanager

from pants.base.specs import DescendantAddresses, SiblingAddresses, SingleAddress
from pants.build_graph.address import Address
from pants.engine.addressable import Addresses
from pants.engine.fs import PathGlobs
from pants.engine.graph import Graph
from pants.engine.isolated_process import ProcessExecutionNode, SnapshotNode
from pants.engine.nodes import (DependenciesNode, FilesystemNode, Node, Noop, SelectNode,
                                StepContext, TaskNode)
from pants.engine.objects import Closable
from pants.engine.selectors import Select, SelectDependencies
from pants.util.objects import datatype


logger = logging.getLogger(__name__)


class ExecutionRequest(datatype('ExecutionRequest', ['roots'])):
  """Holds the roots for an execution, which might have been requested by a user.

  To create an ExecutionRequest, see `LocalScheduler.build_request` (which performs goal
  translation) or `LocalScheduler.execution_request`.

  :param roots: Root Nodes for this request.
  :type roots: list of :class:`pants.engine.nodes.Node`
  """


class Promise(object):
  """An extremely simple _non-threadsafe_ Promise class."""

  def __init__(self):
    self._success = None
    self._failure = None
    self._is_complete = False

  def is_complete(self):
    return self._is_complete

  def success(self, success):
    self._success = success
    self._is_complete = True

  def failure(self, exception):
    self._failure = exception
    self._is_complete = True

  def get(self):
    """Returns the resulting value, or raises the resulting exception."""
    if not self._is_complete:
      raise ValueError('{} has not been completed.'.format(self))
    if self._failure:
      raise self._failure
    else:
      return self._success


class SnapshottedProcess(datatype('SnapshottedProcess', ['product_type',
                                                         'binary_type',
                                                         'input_selectors',
                                                         'input_conversion',
                                                         'output_conversion'])):
  """A task type for defining execution of snapshotted processes."""

  def as_node(self, subject, product_type, variants):
    return ProcessExecutionNode(subject, variants, self)

  @property
  def output_product_type(self):
    return self.product_type

  @property
  def input_selects(self):
    return self.input_selectors


class TaskNodeFactory(datatype('Task', ['input_selects', 'task_func', 'product_type'])):
  """A set-friendly curried TaskNode constructor."""

  def as_node(self, subject, product_type, variants):
    return TaskNode(subject, product_type, variants, self.task_func, self.input_selects)


class NodeBuilder(Closable):
  """Holds an index of tasks and intrinsics used to instantiate Nodes."""

  @classmethod
  def create(cls, task_entries):
    """Creates a NodeBuilder with tasks indexed by their output type."""
    serializable_tasks = defaultdict(set)
    for entry in task_entries:
      if isinstance(entry, (tuple, list)) and len(entry) == 3:
        output_type, input_selects, task = entry
        serializable_tasks[output_type].add(
          TaskNodeFactory(tuple(input_selects), task, output_type)
        )
      elif isinstance(entry, SnapshottedProcess):
        serializable_tasks[entry.output_product_type].add(entry)
      else:
        raise Exception("Unexpected type for entry {}".format(entry))

    intrinsics = dict()
    intrinsics.update(FilesystemNode.as_intrinsics())
    intrinsics.update(SnapshotNode.as_intrinsics())
    return cls(serializable_tasks, intrinsics)

  @classmethod
  def create_task_node(cls, subject, product_type, variants, task_func, clause):
    return TaskNode(subject, product_type, variants, task_func, clause)

  def __init__(self, tasks, intrinsics):
    self._tasks = tasks
    self._intrinsics = intrinsics

  def gen_nodes(self, subject, product_type, variants):
    # Intrinsics that provide the requested product for the current subject type.
    intrinsic_node_factory = self._lookup_intrinsic(product_type, subject)
    if intrinsic_node_factory:
      yield intrinsic_node_factory(subject, product_type, variants)
    else:
      # Tasks that provide the requested product.
      for node_factory in self._lookup_tasks(product_type):
        yield node_factory(subject, product_type, variants)

  def _lookup_tasks(self, product_type):
    for entry in self._tasks[product_type]:
      yield entry.as_node

  def _lookup_intrinsic(self, product_type, subject):
    return self._intrinsics.get((type(subject), product_type))


class StepRequest(datatype('Step', ['step_id', 'node', 'dependencies', 'inline_nodes', 'project_tree'])):
  """Additional inputs needed to run Node.step for the given Node.

  TODO: Unclear why this has a ProjectTree reference; should be passed in by the Engine.

  :param step_id: A unique id for the step, to ease comparison.
  :param node: The Node instance that will run.
  :param dependencies: The declared dependencies of the Node from previous Waiting steps.
  :param inline_nodes: See `LocalScheduler._inline_nodes`.
  :param project_tree: A FileSystemProjectTree instance.
  """

  def __call__(self, node_builder):
    """Called by the Engine in order to execute this Step."""
    step_context = StepContext(node_builder, self.project_tree, self.dependencies, self.inline_nodes)
    state = self.node.step(step_context)
    return StepResult(state)

  def __eq__(self, other):
    return type(self) == type(other) and self.step_id == other.step_id

  def __ne__(self, other):
    return not (self == other)

  def __hash__(self):
    return hash(self.step_id)


class StepResult(datatype('Step', ['state'])):
  """The result of running a Step, passed back to the Scheduler via the Promise class.

  :param state: The State value returned by the Step.
  """


class LocalScheduler(object):
  """A scheduler that expands a product Graph by executing user defined tasks."""

  def __init__(self,
               goals,
               tasks,
               project_tree,
               native,
               graph_lock=None,
               inline_nodes=True,
               graph_validator=None):
    """
    :param goals: A dict from a goal name to a product type. A goal is just an alias for a
           particular (possibly synthetic) product.
    :param tasks: A set of (output, input selection clause, task function) triples which
           is used to compute values in the product graph.
    :param project_tree: An instance of ProjectTree for the current build root.
    :param native: An instance of engine.subsystem.native.Native.
    :param graph_lock: A re-entrant lock to use for guarding access to the internal product Graph
                       instance. Defaults to creating a new threading.RLock().
    :param inline_nodes: Whether to inline execution of `inlineable` Nodes. This improves
                         performance, but can make debugging more difficult because the entire
                         execution history is not recorded in the product Graph.
    :param graph_validator: A validator that runs over the entire graph after every scheduling
                            attempt. Very expensive, very experimental.
    """
    self._products_by_goal = goals
    self._project_tree = project_tree
    self._node_builder = NodeBuilder.create(tasks)

    self._graph_validator = graph_validator
    self._product_graph = Graph(native)
    self._product_graph_lock = graph_lock or threading.RLock()
    self._inline_nodes = inline_nodes
    self._step_id = 0

  def visualize_graph_to_file(self, roots, filename):
    """Visualize a graph walk by writing graphviz `dot` output to a file.

    :param iterable roots: An iterable of the root nodes to begin the graph walk from.
    :param str filename: The filename to output the graphviz output to.
    """
    with self._product_graph_lock, open(filename, 'wb') as fh:
      for line in self.product_graph.visualize(roots):
        fh.write(line)
        fh.write('\n')

  def _create_step(self, node_entry, dependencies, cyclic_dependencies):
    """Creates a Step and Promise with the given dependencies of the given Node."""
    Node.validate_node(node_entry.node)

    # See whether all of the dependencies for the node are available.
    deps = dict()
    for dep_entry in dependencies:
      deps[dep_entry.node] = dep_entry.state
    # Additionally, include Noops for any dependencies that were cyclic.
    for dep in cyclic_dependencies:
      deps[dep] = Noop.cycle(node_entry.node, dep)

    # Ready.
    self._step_id += 1
    step_request = StepRequest(self._step_id,
                               node_entry.node,
                               deps,
                               self._inline_nodes,
                               self._project_tree)
    return (step_request, Promise())

  def node_builder(self):
    """Return the NodeBuilder instance for this Scheduler.

    A NodeBuilder is a relatively heavyweight object (since it contains an index of all
    registered tasks), so it should be used for the execution of multiple Steps.
    """
    return self._node_builder

  def build_request(self, goals, subjects):
    """Translate the given goal names into product types, and return an ExecutionRequest.

    :param goals: The list of goal names supplied on the command line.
    :type goals: list of string
    :param subjects: A list of Spec and/or PathGlobs objects.
    :type subject: list of :class:`pants.base.specs.Spec`, `pants.build_graph.Address`, and/or
      :class:`pants.engine.fs.PathGlobs` objects.
    :returns: An ExecutionRequest for the given goals and subjects.
    """
    return self.execution_request([self._products_by_goal[goal_name] for goal_name in goals],
                                  subjects)

  def execution_request(self, products, subjects):
    """Create and return an ExecutionRequest for the given products and subjects.

    The resulting ExecutionRequest object will contain keys tied to this scheduler's product Graph, and
    so it will not be directly usable with other scheduler instances without being re-created.

    An ExecutionRequest for an Address represents exactly one product output, as does SingleAddress. But
    we differentiate between them here in order to normalize the output for all Spec objects
    as "list of product".

    :param products: A list of product types to request for the roots.
    :type products: list of types
    :param subjects: A list of Spec and/or PathGlobs objects.
    :type subject: list of :class:`pants.base.specs.Spec`, `pants.build_graph.Address`, and/or
      :class:`pants.engine.fs.PathGlobs` objects.
    :returns: An ExecutionRequest for the given products and subjects.
    """

    # Determine the root Nodes for the products and subjects selected by the goals and specs.
    def roots():
      for subject in subjects:
        for product in products:
          if type(subject) in [Address, PathGlobs]:
            yield SelectNode(subject, None, Select(product))
          elif type(subject) in [SingleAddress, SiblingAddresses, DescendantAddresses]:
            yield DependenciesNode(subject, None, SelectDependencies(product, Addresses))
          else:
            raise ValueError('Unsupported root subject type: {}'.format(subject))

    return ExecutionRequest(tuple(roots()))

  @property
  def product_graph(self):
    return self._product_graph

  @contextmanager
  def locked(self):
    with self._product_graph_lock:
      yield

  def root_entries(self, execution_request):
    """Returns the roots for the given ExecutionRequest as a dict from Node to State."""
    with self._product_graph_lock:
      return {root: self._product_graph.state(root) for root in execution_request.roots}

  def invalidate_files(self, filenames):
    """Calls `Graph.invalidate_files()` against an internal product Graph instance."""
    with self._product_graph_lock:
      subjects = set(FilesystemNode.generate_subjects(filenames))

      def predicate(node, state):
        return type(node) is FilesystemNode and node.subject in subjects

      return self._product_graph.invalidate(predicate)

  def schedule(self, execution_request):
    """Yields batches of Steps until the roots specified by the request have been completed.

    This method should be called by exactly one scheduling thread, but the Step objects returned
    by this method are intended to be executed in multiple threads, and then satisfied by the
    scheduling thread.
    """

    with self._product_graph_lock:
      # A dict from Node entry to a possibly executing Step. Only one Step exists for a Node at a time.
      outstanding = {}
      # Node entries that might need to have Steps created (after any outstanding Step returns).
      execution = self._product_graph.execution([self._product_graph.ensure_entry(r) for r in execution_request.roots])

      # Yield nodes that are ready, and then compute new ones.
      scheduling_iterations = 0
      start_time = time.time()
      while True:
        # Finalize any Steps that completed during the prior round.
        waiting = []
        completed = []
        for node_entry, value in outstanding.items()[:]:
          step, promise = value
          if not promise.is_complete():
            continue
          # The step has completed; see whether the Node is completed.
          outstanding.pop(node_entry)
          self._product_graph.update_state(node_entry.node, promise.get().state)
          if node_entry.is_complete:
            # The Node is completed: mark any of its dependents as candidates for Steps.
            completed.append(node_entry)
          else:
            # Waiting on dependencies.
            waiting.append(node_entry) 

        # Create Steps for candidates that are ready to run, and not already running.
        ready = {}
        steps = self._product_graph.execution_next(execution, waiting, completed)
        for candidate, deps, cyclic_deps in steps:
          if candidate in outstanding:
            # Node is still a candidate, but is currently running.
            continue
          ready[candidate] = self._create_step(candidate, deps, cyclic_deps)

        if not ready and not outstanding:
          # Finished.
          break
        yield ready.values()
        scheduling_iterations += 1
        outstanding.update(ready)

      logger.debug(
        'ran %s scheduling iterations in %f seconds. '
        'there have been %s total steps for %s total nodes.',
        scheduling_iterations,
        time.time() - start_time,
        self._step_id,
        len(self._product_graph)
      )

      if self._graph_validator is not None:
        self._graph_validator.validate(self._product_graph)
