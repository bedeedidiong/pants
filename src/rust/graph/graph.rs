use std::collections::HashMap;
use std::collections::HashSet;
use std::collections::VecDeque;

pub type Node = u64;
pub type StateType = u8;

pub struct Entry {
  node: Node,
  state: StateType,
  dependencies: HashSet<Node>,
  dependents: HashSet<Node>,
  cyclic_dependencies: HashSet<Node>,
}

pub struct Graph {
  empty_state: StateType,
  nodes: HashMap<Node,Entry>,
}

impl Graph {
  fn len(&self) -> u64 {
    self.nodes.len() as u64
  }

  fn is_complete(&self, entry: &Entry) -> bool {
    entry.state == self.empty_state
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
    for node in self.walk(&vec![dst], { |entry| !self.is_complete(entry) }, false) {
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
      dependents: dependents,
      graph: self,
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
}

struct Walk<'a, P: Fn(&Entry)->bool> {
  dependents: bool,
  graph: &'a Graph,
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

#[no_mangle]
pub extern fn new(empty_state: StateType) -> *const Graph {
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
pub extern fn destroy(graph_ptr: *mut Graph) {
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
