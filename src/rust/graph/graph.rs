use std::collections::HashMap;

type Id = i64;

pub struct Entry {
  id: Id
}

pub struct Graph {
  nodes: HashMap<Id,Entry>
}

impl Graph {
}

#[no_mangle]
pub extern fn graph_new() -> *const Graph {
  // allocate on the heap via `Box`.
  let graph = Box::new(Graph { nodes: HashMap::new() });
  // and return a raw pointer to the boxed value.
  Box::into_raw(graph)
}

#[no_mangle]
pub extern fn graph_destroy(graph_ptr: *mut Graph) {
  // convert the raw pointer back to a Box in order to cause it to be destroyed at the end
  // of this function.
  println!("destroying {:?}", graph_ptr);
  let _ = unsafe { Box::from_raw(graph_ptr) };
}
