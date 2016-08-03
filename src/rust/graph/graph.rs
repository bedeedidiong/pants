use std::collections::HashMap;
use std::collections::HashSet;

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
  default_state: State,
  nodes: HashMap<Node,Entry>,
}

impl Graph {
  fn len(&self) -> u64 {
    self.nodes.len() as u64
  }

  fn ensure_entry(&mut self, node: Node) -> &mut Entry {
    let default_state = self.default_state;
    self.nodes.entry(node).or_insert_with(||
      Entry {
        node: node,
        state: default_state,
        dependencies: HashSet::new(),
        dependents: HashSet::new(),
        cyclic_dependencies: HashSet::new(),
      }
    )
  }

  fn add_dependency(&mut self, src: Node, dst: Node) {
    {
      let src_entry = self.ensure_entry(src);
      if src_entry.dependencies.contains(&dst) {
        return;
      }
      src_entry.dependencies.insert(dst);
    }
    let dst_entry = self.ensure_entry(dst);
    dst_entry.dependents.insert(src);
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
pub extern fn new(default_state: State) -> *const Graph {
  // allocate on the heap via `Box`.
  let graph =
    Graph {
      default_state: default_state,
      nodes: HashMap::new()
    };
  // and return a raw pointer to the boxed value.
  let raw = Box::into_raw(Box::new(graph));
  println!(">>> rust creating {:?} with default state {}", raw, default_state);
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
