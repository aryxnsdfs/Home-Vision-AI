Low-poly GLB assets live here.

Target:
- 10-25 KB per asset for hackathon/mobile builds.
- Geometry-only meshes where possible.
- Materials are injected at runtime by React Three Fiber.

Suggested filenames:
- sofa.glb
- chair.glb
- bed.glb
- dining_table.glb
- lamp.glb
- wardrobe.glb

The current MVP renders procedural low-poly fallback furniture so the app works
without external assets. Drop compressed `.glb` files into this folder when
ready and wire them into the furniture manifest.
