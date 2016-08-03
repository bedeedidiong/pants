use std::collections::HashMap;
use std::collections::HashSet;

type Id = u64;

pub struct Entry {
  id: Id,
  // state: ...,
  dependencies: HashSet<Id>,
  dependents: HashSet<Id>,
  cyclic_dependencies: HashSet<Id>,
}

/**
 * NB: Because python's CFFI doesn't provide any particular support for "methods of
 * structs" (as would be idiomatic in rust via `impl Graph`), we simply have a series
 * of extern top-level methods to define all ops.
 */
pub struct Graph {
  nodes: HashMap<Id,Entry>
}

impl Graph {
}

#[no_mangle]
pub extern fn new() -> *const Graph {
  // allocate on the heap via `Box`.
  let graph = Box::new(Graph { nodes: HashMap::new() });
  // and return a raw pointer to the boxed value.
  Box::into_raw(graph)
}

#[no_mangle]
pub extern fn destroy(graph_ptr: *mut Graph) {
  // convert the raw pointer back to a Box in order to cause it to be destroyed at the end
  // of this function.
  println!("destroying {:?}", graph_ptr);
  let _ = unsafe { Box::from_raw(graph_ptr) };
}

#[no_mangle]
pub extern fn len(graph_ptr: *mut Graph) -> u64 {
  let graph = unsafe { Box::from_raw(graph_ptr) };
  let len = graph.nodes.len() as u64;
  std::mem::forget(graph);
  len
}
