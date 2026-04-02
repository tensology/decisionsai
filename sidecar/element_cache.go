// element_cache.go — shared element cache for desktop tools (all platforms)
package main

import "sync"

// elementCache stores the last accessibility tree snapshot so click_element
// can reference elements by ID without re-walking the tree on every call.
var elementCache struct {
	mu       sync.Mutex
	elements []map[string]any
}
