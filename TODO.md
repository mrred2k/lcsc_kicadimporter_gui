# Open Features / Backlog

## Footprint Deduplication (Standard KiCad Footprints)

**Problem:**  
EasyEDA always provides a component-specific footprint (e.g. `DRV8317.pretty/DRV8317.kicad_mod`)
even for generic packages like SOT-23 or QFN-16 that have existed in the KiCad standard libraries
for years — complete with correct 3D models attached. Over time this leads to many near-identical
footprints stored per component instead of referencing the shared standard ones.

The KiCad standard footprint also comes with the 3D model already wired up correctly, so importing
the EasyEDA version can break the 3D view if the variable path is not configured perfectly.

**Goal:**  
Detect when the EasyEDA-generated footprint matches (or closely resembles) an existing KiCad
standard footprint and offer to reuse that instead of importing a duplicate.

**Possible approaches:**

1. **Name-based mapping table** (quick win, imperfect coverage)  
   Maintain a `footprint_map.json` that maps common EasyEDA package names to KiCad standard
   footprint references, e.g.:  
   `"SOT-23-3" → "Package_TO_SOT_SMD:SOT-23"`.  
   After symbol generation, offer to rewrite the `Footprint` property in the `.kicad_sym` file
   and skip the footprint/3D import entirely.

2. **Geometry comparison** (accurate, high effort)  
   Parse both `.kicad_mod` files and compare pad count, pitch, courtyard dimensions.  
   Requires reading KiCad's installed library path from `kicad_common.json` — the same config
   already read for 3D variable suggestions.

3. **UI option: import Symbol only, assign footprint manually in KiCad** (no code needed today)  
   The import checkboxes (Symbol / Footprint / 3D) already allow this. Document the workflow
   in README: import Symbol only, then assign a standard footprint inside KiCad.

**Notes:**
- This is non-trivial to do automatically because EasyEDA package names don't map 1:1 to KiCad
  standard library names.
- Approach 3 is already possible with the current tool; needs documentation.
- Approaches 1+2 likely require a KiCad plugin for good UX (access to loaded library list).
- Related to the git-submodule library workflow — if the library is shared/versioned, duplicates
  become a real maintenance problem over time.
