"""Axisymmetric hull mesh and the geometric checks on it. Lengths in metres."""

from __future__ import annotations

import capytaine as cpt
import numpy as np


def build_mesh(radius: float, height: float, draft: float, resolution: dict) -> cpt.Mesh:
    """Closed upright cylinder, bottom at z = -draft, so the waterline is z = 0."""
    return cpt.mesh_vertical_cylinder(
        length=height,
        radius=radius,
        center=(0.0, 0.0, height / 2 - draft),
        resolution=(resolution["radial"], resolution["azimuthal"], resolution["vertical"]),
    )


def triangles(mesh: cpt.Mesh) -> tuple[np.ndarray, np.ndarray]:
    """Vertices and triangle faces; a quad is split in two, a triangle repeats its last vertex."""
    faces = np.asarray(mesh.faces)
    quads = faces[faces[:, 2] != faces[:, 3]]
    tris = np.vstack([faces[:, :3], quads[:, [0, 2, 3]]])
    return np.asarray(mesh.vertices, dtype=float), tris


def is_closed(mesh: cpt.Mesh, tol: float = 1e-9) -> bool:
    """True when every edge is used by exactly two faces, once in each direction.

    Vertices closer than `tol` count as one, so a mesh stitched from separate pieces is accepted.
    """
    vertices, tris = triangles(mesh)
    _, inverse = np.unique(np.round(vertices / tol).astype(np.int64), axis=0, return_inverse=True)
    tris = inverse.reshape(-1)[tris]
    a, b, c = tris.T
    tris = tris[(a != b) & (b != c) & (c != a)]  # drop faces collapsed by the merge
    if len(tris) == 0:
        return False
    edges = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    forward, counts = np.unique(edges, axis=0, return_counts=True)
    if (counts != 1).any():  # an edge used twice in one direction: a fold or duplicate face
        return False
    seen = {tuple(edge) for edge in forward}
    return all((b, a) in seen for a, b in seen)


def immersed_volume(mesh: cpt.Mesh) -> float:
    """Volume of the hull below z = 0, from the divergence theorem with the field (0, 0, z).

    The waterplane lies at z = 0, where that field is zero, so it needs no cap.
    """
    vertices, tris = triangles(mesh.immersed_part())
    a, b, c = (vertices[tris[:, i]] for i in range(3))
    area_z = 0.5 * np.cross(b - a, c - a)[:, 2]
    return float(np.sum(area_z * (a[:, 2] + b[:, 2] + c[:, 2]) / 3.0))
