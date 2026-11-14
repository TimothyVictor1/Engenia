"""Out-of-process gmsh meshing. Run as ``python -m engenia.sim.structure._mesh_worker``.

gmsh bundles its own OpenCASCADE, which segfaults when loaded into a process that
already holds build123d's OCP. The solver therefore never imports gmsh; it runs
this module in a fresh interpreter and reads the mesh back from JSON.
Python floats round-trip exactly through JSON, so the mesh is unchanged.
"""

from __future__ import annotations

import json
import sys


def mesh_step(step: str, size_mm: float) -> dict:
    import gmsh

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.RandomSeed", 0)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", size_mm)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)
        gmsh.model.add("hull")
        gmsh.model.occ.importShapes(step)
        gmsh.model.occ.synchronize()
        gmsh.model.mesh.generate(3)
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        lookup = {int(tag): i for i, tag in enumerate(node_tags)}
        tetrahedra: list[list[int]] = []
        for element_type, _, node_blocks in zip(*gmsh.model.mesh.getElements(3), strict=True):
            props = gmsh.model.mesh.getElementProperties(element_type)
            if props[0].startswith("Tetrahedron") and props[3] == 4:
                tetrahedra.extend(
                    [lookup[int(tag)] for tag in cell] for cell in node_blocks.reshape(-1, 4)
                )
        return {"coordinates": [float(x) for x in coordinates], "tetrahedra": tetrahedra}
    finally:
        gmsh.finalize()


def main(argv: list[str]) -> int:
    request_path, result_path = argv
    with open(request_path) as handle:
        request = json.load(handle)
    try:
        result = mesh_step(request["step"], request["mesh_size_mm"])
    except ImportError as exc:
        result = {"error": "import", "message": str(exc)}
    with open(result_path, "w") as handle:
        json.dump(result, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
