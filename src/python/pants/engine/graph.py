# coding=utf-8
# Copyright 2015 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

from collections import deque

from pants.engine.nodes import Node, Noop, Return, SelectNode, State, TaskNode, Throw, Waiting


class CompletedNodeException(ValueError):
  """Indicates an attempt to change a Node that is already completed."""


class IncompleteDependencyException(ValueError):
  """Indicates an attempt to complete a Node that has incomplete dependencies."""


class Graph(object):
  """A graph of Nodes which produce a Product for a Subject."""

  class Entry(object):
    """An entry representing a Node in the Graph.

    Equality for this object is intentionally `identity` for efficiency purposes: structural
    equality can be implemented by comparing the result of the `structure` method.
    """
    __slots__ = ('node', 'state')

    def __init__(self, node):
      self.node = node
      # The computed value for a Node: if a Node hasn't been computed yet, it will be None.
      self.state = None

    @property
    def is_complete(self):
      return self.state is not None

    def structure(self):
      return (self.node,
              self.state,
              {d.node for d in self.dependencies},
              {d.node for d in self.dependents},
              self.cyclic_dependencies)

  def __init__(self, native, validator=None):
    self._validator = validator or Node.validate_node
    # A dict of Node->Entry, and a dict of id(Entry)->Node
    self._nodes = dict()
    self._node_ids = dict()
    # A native underlying graph.
    self._native = native
    self._graph = native.gc(native.lib.graph_create(Waiting.type_id), native.lib.graph_destroy)

  def __len__(self):
    native_len = self._native.lib.len(self._graph)
    actual_len = len(self._nodes)
    assert native_len == actual_len
    return actual_len

  def state(self, node):
    entry = self._nodes.get(node, None)
    if not entry:
      return None
    return entry.state

  def update_state(self, node, state):
    """Updates the Node with the given State, creating any Nodes which do not already exist."""
    entry = self.ensure_entry(node)
    if type(state) in [Return, Throw, Noop]:
      # Validate that a completed Node depends only on other completed Nodes.
      entry.state = state
      self._native.lib.complete_node(self._graph, id(entry), state.type_id)
    elif type(state) is Waiting:
      dep_entries = [self.ensure_entry(d) for d in state.dependencies]
      deps_ptr = self._native.as_uint64_ptr([id(e) for e in dep_entries])
      self._native.lib.add_dependencies(self._graph,
                                          id(entry),
                                          deps_ptr, len(dep_entries))
    else:
      raise State.raise_unrecognized(state)

  def _detect_cycle(self, src, dest):
    """Detect whether adding an edge from src to dest would create a cycle.

    :param src: Source entry: must exist in the graph.
    :param dest: Destination entry: must exist in the graph.

    Returns True if a cycle would be created by adding an edge from src->dest.
    """
    # We disallow adding new edges outbound from completed Nodes, and no completed Node can have
    # a path to an uncompleted Node. Thus, we can truncate our search for cycles at any completed
    # Node.
    is_not_completed = lambda e: e.state is None
    for entry in self._walk_entries([dest], entry_predicate=is_not_completed):
      if entry is src:
        return True
    return False

  def ensure_entry(self, node):
    """Returns the Entry for the given Node, creating it if it does not already exist."""
    entry = self._nodes.get(node, None)
    if not entry:
      self._validator(node)
      self._nodes[node] = entry = self.Entry(node)
      self._node_ids[id(entry)] = entry
    return entry

  def _entry_for_id(self, node_id):
    """Returns the Entry for the given node id, which must exist"""
    return self._node_ids[node_id]

  def completed_nodes(self):
    """In linear time, yields the states of any Nodes which have completed."""
    for node, entry in self._nodes.items():
      if entry.state is not None:
        yield node, entry.state

  def dependents(self):
    """In linear time, yields the dependents lists for all Nodes."""
    for node, entry in self._nodes.items():
      yield node, [d.node for d in entry.dependents]

  def dependencies(self):
    """In linear time, yields the dependencies lists for all Nodes."""
    for node, entry in self._nodes.items():
      yield node, [d.node for d in entry.dependencies]

  def cyclic_dependencies(self):
    """In linear time, yields the cyclic_dependencies lists for all Nodes."""
    for node, entry in self._nodes.items():
      yield node, entry.cyclic_dependencies

  def dependents_of(self, node):
    entry = self._nodes.get(node, None)
    if entry:
      for d in entry.dependents:
        yield d.node

  def _dependency_entries_of(self, node):
    entry = self._nodes.get(node, None)
    if entry:
      for d in entry.dependencies:
        yield d

  def dependencies_of(self, node):
    for d in self._dependency_entries_of(node):
      yield d.node

  def cyclic_dependencies_of(self, node):
    entry = self._nodes.get(node, None)
    if not entry:
      return set()
    return entry.cyclic_dependencies

  def invalidate(self, predicate=None):
    """Invalidate nodes and their subgraph of dependents given a predicate.

    :param func predicate: A predicate that matches Node objects for all nodes in the graph.
    """
    def _sever_dependents(entry):
      for associated_entry in entry.dependencies:
        associated_entry.dependents.discard(entry)

    def _delete_node(entry):
      actual_entry = self._nodes.pop(entry.node)
      self._node_ids.pop(id(actual_entry))
      assert entry is actual_entry

    def all_predicate(node, state): return True
    predicate = predicate or all_predicate

    invalidated_root_entries = list(entry for entry in self._nodes.values()
                                    if predicate(entry.node, entry.state))
    invalidated_root_ids = [id(e) for e in invalidated_root_entries]
    native_invalidated = self._native.lib.invalidate(self._graph,
                                                     self._native.as_uint64_ptr(invalidated_root_ids),
                                                     len(invalidated_root_ids))
    invalidated_entries = list(entry for entry in self._walk_entries(invalidated_root_entries,
                                                                     lambda _: True,
                                                                     dependents=True))

    # Sever dependee->dependent relationships in the graph for all given invalidated nodes.
    for entry in invalidated_entries:
      _sever_dependents(entry)

    # Delete all nodes based on a backwards walk of the graph from all matching invalidated roots.
    for entry in invalidated_entries:
      _delete_node(entry)

    invalidated_count = len(invalidated_entries)
    assert native_invalidated == invalidated_count
    return invalidated_count

  def walk(self, roots, predicate=None, dependents=False):
    """Yields Nodes and their States depth-first in pre-order, starting from the given roots.

    Each node entry is a tuple of (Node, State).

    The given predicate is applied to entries, and eliminates the subgraphs represented by nodes
    that don't match it. The default predicate eliminates all `Noop` subgraphs.
    """
    def _default_entry_predicate(entry):
      return type(entry.state) is not Noop
    def _entry_predicate(entry):
      return predicate(entry.node, entry.state)
    entry_predicate = _entry_predicate if predicate else _default_entry_predicate

    root_entries = []
    for root in roots:
      entry = self._nodes.get(root, None)
      if entry:
        root_entries.append(entry)

    for entry in self._walk_entries(root_entries, entry_predicate, dependents=dependents):
      yield (entry.node, entry.state)

  def _walk_entries(self, root_entries, entry_predicate, dependents=False):
    stack = deque(root_entries)
    walked = set()
    while stack:
      entry = stack.pop()
      if entry in walked:
        continue
      walked.add(entry)
      if not entry_predicate(entry):
        continue
      stack.extend(entry.dependents if dependents else entry.dependencies)

      yield entry

  def execution(self, root_entries):
    roots_ptr = self._native.as_uint64_ptr([id(r) for r in root_entries])
    execution = self._native.gc(self._native.lib.execution_create(roots_ptr, len(root_entries)),
                                self._native.lib.execution_destroy)
    return execution

  def execution_next(self, execution, waiting_entries, completed_entries):
    # Prepare arrays for each input.
    waiting = self._native.as_uint64_ptr([id(e) for e in waiting_entries])
    completed = self._native.as_uint64_ptr([id(e) for e in completed_entries])
    states = self._native.as_uint8_ptr([e.state.type_id for e in completed_entries])

    # Convert the output Steps to a list of tuples of Node, dependencies, cyclic_dependencies.
    raw_steps = self._native.lib.execution_next(self._graph,
                                                execution,
                                                waiting, len(waiting_entries),
                                                completed, len(completed_entries),
                                                states, len(completed_entries))
    def entries(ptr, count):
      return [self._entry_for_id(d) for d in self._native.unpack(ptr, count)]

    def unpack_step(step):
      return (
        self._entry_for_id(step.node),
        entries(step.dependencies_ptr, step.dependencies_len),
        entries(step.cyclic_dependencies_ptr, step.cyclic_dependencies_len),
      )

    return [unpack_step(s)
            for s in self._native.unpack(raw_steps[0].steps_ptr, raw_steps[0].steps_len)]

  def trace(self, root):
    """Yields a stringified 'stacktrace' starting from the given failed root.

    TODO: This could use polish. In particular, the `__str__` representations of Nodes and
    States are probably not sufficient for user output.
    """

    traced = set()

    def is_bottom(entry):
      return type(entry.state) in (Noop, Return) or entry in traced

    def is_one_level_above_bottom(parent_entry):
      return all(is_bottom(child_entry) for child_entry in parent_entry.dependencies)

    def _format(level, entry, state):
      output = '{}Computing {} for {}'.format('  ' * level,
                                              entry.node.product.__name__,
                                              entry.node.subject)
      if is_one_level_above_bottom(entry):
        output += '\n{}{}'.format('  ' * (level + 1), state)

      return output

    def _trace(entry, level):
      if is_bottom(entry):
        return
      traced.add(entry)
      yield _format(level, entry, entry.state)
      for dep in entry.cyclic_dependencies:
        yield _format(level, entry, Noop.cycle(entry.node, dep))
      for dep_entry in entry.dependencies:
        for l in _trace(dep_entry, level+1):
          yield l

    for line in _trace(self._nodes[root], 1):
      yield line

  def visualize(self, roots):
    """Visualize a graph walk by generating graphviz `dot` output.

    :param iterable roots: An iterable of the root nodes to begin the graph walk from.
    """
    viz_colors = {}
    viz_color_scheme = 'set312'  # NB: There are only 12 colors in `set312`.
    viz_max_colors = 12

    def format_color(node, node_state):
      if type(node_state) is Throw:
        return 'tomato'
      elif type(node_state) is Noop:
        return 'white'
      return viz_colors.setdefault(node.product, (len(viz_colors) % viz_max_colors) + 1)

    def format_type(node):
      return node.func.__name__ if type(node) is TaskNode else type(node).__name__

    def format_subject(node):
      if node.variants:
        return '({})@{}'.format(node.subject,
                                ','.join('{}={}'.format(k, v) for k, v in node.variants))
      else:
        return '({})'.format(node.subject)

    def format_product(node):
      if type(node) is SelectNode and node.variant_key:
        return '{}@{}'.format(node.product.__name__, node.variant_key)
      return node.product.__name__

    def format_node(node, state):
      return '{}:{}:{} == {}'.format(format_product(node),
                                     format_subject(node),
                                     format_type(node),
                                     str(state).replace('"', '\\"'))

    def format_edge(src_str, dest_str, cyclic):
      style = " [style=dashed]" if cyclic else ""
      return '    "{}" -> "{}"{}'.format(node_str, format_node(dep, dep_state), style)

    yield 'digraph plans {'
    yield '  node[colorscheme={}];'.format(viz_color_scheme)
    yield '  concentrate=true;'
    yield '  rankdir=LR;'

    predicate = lambda n, s: type(s) is not Noop

    for (node, node_state) in self.walk(roots, predicate=predicate):
      node_str = format_node(node, node_state)

      yield '  "{}" [style=filled, fillcolor={}];'.format(node_str, format_color(node, node_state))

      for cyclic, adjacencies in ((False, self.dependencies_of), (True, self.cyclic_dependencies_of)):
        for dep in adjacencies(node):
          dep_state = self.state(dep)
          if not predicate(dep, dep_state):
            continue
          yield format_edge(node_str, format_node(dep, dep_state), cyclic)

    yield '}'
