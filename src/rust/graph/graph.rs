use std::collections::HashMap;
use std::collections::HashSet;
use std::collections::VecDeque;
use std::iter::IntoIterator;

pub type Node = u64;
pub type State = u64;

pub struct Entry {
  node: Node,
  state: State,
  dependencies: HashSet<Node>,
  dependents: HashSet<Node>,
  cyclic_dependencies: HashSet<Node>,
}

pub struct Graph {
  empty_state: State,
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
   * Eagerly generates a topologically ordered walk of the graph.
   *
   * TODO: Should use an Iterator instead.
   */
  fn walk<P>(&self, roots: &Vec<Node>, predicate: P, dependents: bool) -> Vec<Node>
      where P: Fn(&Entry) -> bool {
    let mut stack: VecDeque<&Node> = roots.into_iter().collect();
    let mut walked = HashSet::new();
    let mut result = Vec::new();
    while let Some(node) = stack.pop_front() {
      if walked.contains(&node) {
        continue;
      }
      walked.insert(node);

      match self.nodes.get(&node) {
        Some(entry) if predicate(entry) => {
          if dependents {
            stack.extend(&entry.dependents);
          } else {
            stack.extend(&entry.dependencies);
          }
          result.push(entry.node);
        }
        _ => {},
      }
    }
    result
  }
}

fn with_graph<F,T>(graph_ptr: *mut Graph, f: F) -> T
    where F: Fn(&mut Graph) -> T {
  let mut graph = unsafe { Box::from_raw(graph_ptr) };
  let t = f(&mut graph);
  std::mem::forget(graph);
  t
}

#[no_mangle]
pub extern fn new(empty_state: State) -> *const Graph {
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
pub extern fn complete_node(graph_ptr: *mut Graph, node: Node, state: State) {
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
