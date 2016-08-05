use std::collections::HashMap;
use std::collections::HashSet;
use std::collections::VecDeque;

pub type Node = u64;
pub type StateType = u8;

/**
 * An Entry and its adjacencies.
 */
pub struct Entry {
  node: Node,
  state: StateType,
  dependencies: HashSet<Node>,
  dependents: HashSet<Node>,
  cyclic_dependencies: HashSet<Node>,
}

/**
 * A DAG (enforced on mutation) of Entries.
 */
pub struct Graph {
  empty_state: StateType,
  nodes: HashMap<Node,Entry>,
}

impl Graph {
  fn len(&self) -> u64 {
    self.nodes.len() as u64
  }

  fn is_complete(&self, node: Node) -> bool {
    self.nodes.get(&node).map(|e| e.state == self.empty_state).unwrap_or(false)
  }

  fn is_complete_entry(&self, entry: &Entry) -> bool {
    entry.state == self.empty_state
  }

  /**
   * A Node is 'ready' (to run) when it is not complete, but all of its dependencies
   * are complete.
   */
  fn is_ready(&self, node: Node) -> bool {
    !self.is_complete(node) && (
      self.nodes.get(&node).map(|e| {
        e.dependencies
          .into_iter()
          .all(|d| { self.is_complete(d) })
      }).unwrap_or(true)
    )
  }

  fn ensure_entry(&mut self, node: Node) -> &mut Entry {
    let empty_state = self.empty_state;
    self.nodes.entry(node).or_insert_with(||
      Entry {
        node: node,
        state: empty_state,
        dependencies: HashSet::new(),
        dependents: HashSet::new(),
        cyclic_dependencies: HashSet::new(),
      }
    )
  }

  fn add_dependency(&mut self, src: Node, dst: Node) {
    if self.ensure_entry(src).dependencies.contains(&dst) {
      return;
    }

    if self.detect_cycle(src, dst) {
      self.ensure_entry(src).cyclic_dependencies.insert(dst);
    } else {
      self.ensure_entry(src).dependencies.insert(dst);
      self.ensure_entry(dst).dependents.insert(src);
    }
  }

  /**
   * Detect whether adding an edge from src to dst would create a cycle.
   *
   * Returns true if a cycle would be created by adding an edge from src->dst.
   */
  fn detect_cycle(&self, src: Node, dst: Node) -> bool {
    for node in self.walk(&vec![dst], { |entry| !self.is_complete_entry(entry) }, false) {
      if node == src {
        return true;
      }
    }
    return false;
  }

  /**
   * Begins a topological Walk from the given roots.
   */
  fn walk<P>(&self, roots: &Vec<Node>, predicate: P, dependents: bool) -> Walk<P>
      where P: Fn(&Entry)->bool {
    Walk {
      graph: self,
      dependents: dependents,
      deque: roots.iter().map(|&x| x).collect(),
      walked: HashSet::new(),
      predicate: predicate,
    }
  }

  /**
   * Removes the given invalidation roots and their transitive dependents from the Graph.
   */
  fn invalidate(&mut self, roots: &Vec<Node>) -> usize {
    // eagerly collect all Nodes before we begin mutating anything.
    let nodes: Vec<Node> = self.walk(roots, { |_| true }, true).collect();

    for node in &nodes {
      // remove the roots from their dependencies' dependents lists.
      // FIXME: Because the lifetime of each Entry is the same as the lifetime of the entire Graph,
      // I can't figure out how to iterate over one immutable Entry while mutating a different
      // mutable Entry... so I clone() here. Perhaps this is completely sane, because what's to say
      // they're not the same Entry after all? But regardless, less efficient than it could be.
      for dependency in self.ensure_entry(*node).dependencies.clone() {
        match self.nodes.get_mut(&dependency) {
          Some(entry) => { entry.dependents.remove(node); () },
          _ => {},
        }
      }

      // delete each Node
      self.nodes.remove(node);
    }

    nodes.len()
  }

  /**
   * Begins an Execution from the given root Nodes.
   */
  fn execution(&self, roots: &Vec<Node>) -> Execution {
    let ready = Vec::new();
    Execution {
      candidates: roots.iter().map(|n| *n).collect(),
      ready: ready,
      ready_raw: Box::new(
        RawNodes {
          nodes_ptr: ready.as_mut_ptr(),
          nodes_len: ready.len() as u64,
        }
      ),
    }
  }
}

/**
 * Represents the state of a particular topological walk through a Graph. Implements Iterator and
 * has the same lifetime as the Graph itself.
 */
struct Walk<'a, P: Fn(&Entry)->bool> {
  graph: &'a Graph,
  dependents: bool,
  deque: VecDeque<Node>,
  walked: HashSet<Node>,
  predicate: P,
}

impl<'a, P: Fn(&Entry)->bool> Iterator for Walk<'a, P> {
  type Item = Node;

  fn next(&mut self) -> Option<Node> {
    while let Some(node) = self.deque.pop_front() {
      if self.walked.contains(&node) {
        continue;
      }
      self.walked.insert(node);

      match self.graph.nodes.get(&node) {
        Some(entry) if (self.predicate)(entry) => {
          if self.dependents {
            self.deque.extend(&entry.dependents);
          } else {
            self.deque.extend(&entry.dependencies);
          }
          return Some(entry.node);
        }
        _ => {},
      }
    };
    None
  }
}

/**
 * A primitive struct to allow a list of Nodes to be directly exposed to python.
 */
pub struct RawNodes {
  nodes_ptr: *mut Node,
  nodes_len: u64,
}

/**
 * Represents the state of an execution of (a subgraph of) a Graph.
 */
pub struct Execution {
  candidates: HashSet<Node>,
  ready: Vec<Node>,
  ready_raw: Box<RawNodes>,
}

impl Execution {
  /**
   * Continues execution after the waiting Nodes given Nodes have completed with the given states.
   */
  fn next(
    &mut self,
    graph: &mut Graph,
    waiting: &Vec<Node>,
    completed: &Vec<Node>,
    completed_states: &Vec<StateType>
  ) {
    assert!(
      completed.len() == completed_states.len(),
      "The completed Node and State vectors did not have the same length: {} vs {}",
      completed.len(),
      completed_states.len()
    );

    // Complete any completed Nodes and mark their dependents as candidates.
    for (&node, &state) in completed.iter().zip(completed_states.iter()) {
      let entry = graph.ensure_entry(node);
      self.candidates.extend(entry.dependents);
      entry.state = state;
    }

    // Mark the dependencies of any waiting Nodes as candidates.
    for &node in waiting {
      match graph.nodes.get(&node) {
        Some(entry) => {
          // If all dependencies of the Node are completed, the Node itself is a candidate.
          let incomplete_deps: Vec<Node> =
            graph.ensure_entry(node).dependencies
              .into_iter()
              .filter(|&d| { !graph.is_complete(d) })
              .collect();
          if incomplete_deps.len() > 0 {
            // Mark incomplete deps as candidates for Steps.
            self.candidates.extend(incomplete_deps);
          } else {
            // All deps are already completed: mark this Node as a candidate for another step.
            self.candidates.insert(node);
          }
        },
        _ => {},
      };
    }

    // Move all ready candidates to the ready set.
    let (ready_candidates, next_candidates) =
      self.candidates.into_iter().partition(|&c| graph.is_ready(c));
    self.candidates = next_candidates;
    self.ready.clear();
    self.ready.extend(ready_candidates);

    // Update references in the raw ready struct and return it.
    self.ready_raw.nodes_ptr = self.ready.as_mut_ptr();
    self.ready_raw.nodes_len = self.ready.len() as u64;
  }
}

/** TODO: Make the next four functions generic in the type being operated on? */

fn with_execution<F,T>(execution_ptr: *mut Execution, f: F) -> T
    where F: Fn(&mut Execution)->T {
  let mut execution = unsafe { Box::from_raw(execution_ptr) };
  let t = f(&mut execution);
  std::mem::forget(execution);
  t
}

fn with_graph<F,T>(graph_ptr: *mut Graph, f: F) -> T
    where F: Fn(&mut Graph)->T {
  let mut graph = unsafe { Box::from_raw(graph_ptr) };
  let t = f(&mut graph);
  std::mem::forget(graph);
  t
}

fn with_nodes<F,T>(nodes_ptr: *mut Node, nodes_len: usize, mut f: F) -> T
    where F: FnMut(&Vec<Node>)->T {
  let nodes = unsafe { Vec::from_raw_parts(nodes_ptr, nodes_len, nodes_len) };
  let t = f(&nodes);
  std::mem::forget(nodes);
  t
}

fn with_states<F,T>(states_ptr: *mut StateType, states_len: usize, mut f: F) -> T
    where F: FnMut(&Vec<StateType>)->T {
  let states = unsafe { Vec::from_raw_parts(states_ptr, states_len, states_len) };
  let t = f(&states);
  std::mem::forget(states);
  t
}

#[no_mangle]
pub extern fn graph_create(empty_state: StateType) -> *const Graph {
  // allocate on the heap via `Box`.
  let graph =
    Graph {
      empty_state: empty_state,
      nodes: HashMap::new()
    };
  // and return a raw pointer to the boxed value.
  let raw = Box::into_raw(Box::new(graph));
  println!(">>> rust creating {:?} with default state {}", raw, empty_state);
  raw
}

#[no_mangle]
pub extern fn graph_destroy(graph_ptr: *mut Graph) {
  // convert the raw pointer back to a Box (without `forget`ing it) in order to cause it
  // to be destroyed at the end of this function.
  println!(">>> rust destroying {:?}", graph_ptr);
  let _ = unsafe { Box::from_raw(graph_ptr) };
}

#[no_mangle]
pub extern fn len(graph_ptr: *mut Graph) -> u64 {
  with_graph(graph_ptr, |graph| {
    graph.len()
  })
}

#[no_mangle]
pub extern fn complete_node(graph_ptr: *mut Graph, node: Node, state: StateType) {
  with_graph(graph_ptr, |graph| {
    graph.ensure_entry(node).state = state;
  })
}

#[no_mangle]
pub extern fn add_dependency(graph_ptr: *mut Graph, src: Node, dst: Node) {
  with_graph(graph_ptr, |graph| {
    println!(">>> rust adding dependency from {} to {}", src, dst);
    graph.add_dependency(src, dst);
  })
}

#[no_mangle]
pub extern fn invalidate(graph_ptr: *mut Graph, roots_ptr: *mut Node, roots_len: u64) -> u64 {
  with_graph(graph_ptr, |graph| {
    with_nodes(roots_ptr, roots_len as usize, |roots| {
      println!(">>> rust invalidating upward from {:?}", roots);
      graph.invalidate(roots) as u64
    })
  })
}

#[no_mangle]
pub extern fn execution_create(graph_ptr: *mut Graph, roots_ptr: *mut Node, roots_len: u64) -> *const Execution {
  with_graph(graph_ptr, |graph| {
    with_nodes(roots_ptr, roots_len as usize, |roots| {
      println!(">>> rust beginning execution from {:?}", roots);
      // create on the heap, and return a raw pointer to the boxed value.
      Box::into_raw(Box::new(graph.execution(roots)))
    })
  })
}

#[no_mangle]
pub extern fn execution_next(
  graph_ptr: *mut Graph,
  execution_ptr: *mut Execution,
  waiting_ptr: *mut Node,
  waiting_len: u64,
  completed_ptr: *mut Node,
  completed_len: u64,
  states_ptr: *mut StateType,
  states_len: u64,
) -> *const RawNodes {
  with_graph(graph_ptr, |graph| {
    with_execution(execution_ptr, |execution| {
      with_nodes(waiting_ptr, waiting_len as usize, |waiting| {
        with_nodes(completed_ptr, completed_len as usize, |completed| {
          with_states(states_ptr, states_len as usize, |states| {
            println!(">>> rust continuing execution after {:?} completed", completed);
            execution.next(graph, waiting, completed, states);
            Box::into_raw(execution.ready_raw)
          })
        })
      })
    })
  })
}
