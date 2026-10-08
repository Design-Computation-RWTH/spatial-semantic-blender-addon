"""Derive placements for spaces that SPOT-AM locates only approximately.

A damage observed "in the back left of the room" carries no transformation
matrix. Its position is stated with ``reloc:`` relations between boundary
points of the damage and the room it sits in. Each relation narrows one axis to
a third of the room's extent, and the surviving box is the region the damage is
known to be inside.

This mirrors ``deriveSpotAmFuzzyEntityPlacements`` in the validation viewer's
``spot-q`` package so both tools agree on where an approximate damage sits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mathutils import Matrix, Vector

DIRECTION_FRONT = "Front"
DIRECTION_REAR = "Rear"
DIRECTION_LEFT = "Left"
DIRECTION_RIGHT = "Right"
DIRECTION_TOP = "Top"
DIRECTION_BOTTOM = "Bottom"

DIRECTIONAL_SUFFIXES = (
    DIRECTION_FRONT,
    DIRECTION_REAR,
    DIRECTION_LEFT,
    DIRECTION_RIGHT,
    DIRECTION_TOP,
    DIRECTION_BOTTOM,
)

OPPOSITE_DIRECTIONS = {
    DIRECTION_FRONT: DIRECTION_REAR,
    DIRECTION_REAR: DIRECTION_FRONT,
    DIRECTION_LEFT: DIRECTION_RIGHT,
    DIRECTION_RIGHT: DIRECTION_LEFT,
    DIRECTION_TOP: DIRECTION_BOTTOM,
    DIRECTION_BOTTOM: DIRECTION_TOP,
}

# A space that declares no axis directions of its own inherits these.
DEFAULT_AXIS_DIRECTIONS = {
    "X": DIRECTION_FRONT,
    "Y": DIRECTION_RIGHT,
    "Z": DIRECTION_TOP,
}

AXIS_NAMES = ("X", "Y", "Z")

CENTER_SUFFIX_DIRECTIONS = {
    "VerticalCenter": DIRECTION_TOP,
    "TransversalCenter": DIRECTION_RIGHT,
    "LongitudinalCenter": DIRECTION_FRONT,
}


@dataclass
class BoundaryPointMapping:
    """One ``reloc:`` relation between a source point and a target space."""

    relation_name: str
    source_point_iri: str
    target_iri: str
    normalized_coordinate: float | None = None


@dataclass
class SpotAmPlacement:
    """The region a space is known to occupy, in its parent's coordinates."""

    space_iri: str
    min_corner: tuple[float, float, float]
    max_corner: tuple[float, float, float]

    @property
    def center(self) -> tuple[float, float, float]:
        return tuple(
            (self.min_corner[index] + self.max_corner[index]) / 2.0
            for index in range(3)
        )

    def centered_local_bbox(self) -> list[float]:
        """The region as a bbox around its own origin."""
        center = self.center
        return [
            self.min_corner[0] - center[0],
            self.min_corner[1] - center[1],
            self.min_corner[2] - center[2],
            self.max_corner[0] - center[0],
            self.max_corner[1] - center[1],
            self.max_corner[2] - center[2],
        ]


@dataclass
class AxisInterval:
    axis_name: str
    lower_value: float
    upper_value: float


@dataclass
class SpotAmSpace:
    """The subset of a space this module needs."""

    iri: str
    local_bbox: list[float] | None = None
    parent_iri: str | None = None
    axis_directions: dict[str, str] = field(default_factory=dict)
    # Placement inside the parent. Composed down the chain to reach world
    # coordinates when a relation targets a space elsewhere in the tree.
    local_matrix: Matrix = field(default_factory=lambda: Matrix.Identity(4))


def axis_index(axis_name: str) -> int:
    return AXIS_NAMES.index(axis_name)


def opposite_direction(direction: str) -> str | None:
    return OPPOSITE_DIRECTIONS.get(direction)


def directional_suffix(relation_name: str) -> str | None:
    for direction in DIRECTIONAL_SUFFIXES:
        if relation_name.endswith(direction):
            return direction
    return None


def frame_axis_for_direction(
    frame: dict[str, str],
    direction: str,
) -> tuple[str, bool] | None:
    """Find which axis of ``frame`` points along ``direction``.

    Returns the axis name and whether the direction is the axis's positive end.
    """
    for axis_name in AXIS_NAMES:
        configured = frame.get(axis_name)
        if not configured:
            continue
        if configured == direction:
            return axis_name, True
        if opposite_direction(configured) == direction:
            return axis_name, False
    return None


def relation_axis_name(relation_name: str, frame: dict[str, str]) -> str | None:
    direction = directional_suffix(relation_name)
    if direction is not None:
        resolved = frame_axis_for_direction(frame, direction)
        return resolved[0] if resolved else None

    for suffix, center_direction in CENTER_SUFFIX_DIRECTIONS.items():
        if relation_name.endswith(suffix):
            resolved = frame_axis_for_direction(frame, center_direction)
            return resolved[0] if resolved else None
    return None


def relation_interval(
    relation_name: str,
    target_bbox: list[float],
    frame: dict[str, str],
    normalized_coordinate: float | None = None,
) -> AxisInterval | None:
    """Narrow one axis of ``target_bbox`` to the region the relation describes."""
    axis_name = relation_axis_name(relation_name, frame)
    if axis_name is None:
        return None

    index = axis_index(axis_name)
    lower = target_bbox[index]
    upper = target_bbox[index + 3]
    span = upper - lower

    if normalized_coordinate is not None:
        value = lower + span * normalized_coordinate
        return AxisInterval(axis_name, value, value)

    positive_direction = frame.get(axis_name) or DEFAULT_AXIS_DIRECTIONS[axis_name]
    opposite = opposite_direction(positive_direction)

    if relation_name.startswith("containedIn"):
        if any(
            relation_name.endswith(suffix) for suffix in CENTER_SUFFIX_DIRECTIONS
        ):
            lower_fraction, upper_fraction = 1.0 / 3.0, 2.0 / 3.0
        else:
            direction = directional_suffix(relation_name)
            if direction is None:
                return None
            if direction == positive_direction:
                lower_fraction, upper_fraction = 2.0 / 3.0, 1.0
            elif direction == opposite:
                lower_fraction, upper_fraction = 0.0, 1.0 / 3.0
            else:
                return None
    elif relation_name.startswith("meet"):
        direction = directional_suffix(relation_name)
        if direction is None:
            return None
        if direction == positive_direction:
            lower_fraction = upper_fraction = 1.0
        elif direction == opposite:
            lower_fraction = upper_fraction = 0.0
        else:
            return None
    else:
        return None

    return AxisInterval(
        axis_name,
        lower + span * lower_fraction,
        lower + span * upper_fraction,
    )


def resolve_directional_frame(
    space_iri: str,
    spaces: dict[str, SpotAmSpace],
) -> dict[str, str]:
    """Find the axis directions for a space, inheriting from its ancestors.

    Only some spaces declare directions. A room usually inherits the frame of
    the asset space that contains it.
    """
    visited: set[str] = set()
    current: str | None = space_iri

    while current is not None and current not in visited:
        visited.add(current)
        space = spaces.get(current)
        if space is None:
            break
        if space.axis_directions:
            frame = dict(DEFAULT_AXIS_DIRECTIONS)
            frame.update(space.axis_directions)
            return frame
        current = space.parent_iri

    return dict(DEFAULT_AXIS_DIRECTIONS)


def resolve_world_matrices(
    spaces: dict[str, SpotAmSpace],
) -> dict[str, Matrix]:
    """Compose each space's local matrix down the chain to world coordinates.

    A parent cycle would otherwise recurse forever, so a space that is already
    being resolved falls back to the identity.
    """
    world_matrices: dict[str, Matrix] = {}
    resolving: set[str] = set()

    def resolve(space_iri: str) -> Matrix:
        cached = world_matrices.get(space_iri)
        if cached is not None:
            return cached
        space = spaces.get(space_iri)
        if space is None or space_iri in resolving:
            return Matrix.Identity(4)

        resolving.add(space_iri)
        parent_world = (
            resolve(space.parent_iri)
            if space.parent_iri is not None
            else Matrix.Identity(4)
        )
        world_matrix = parent_world @ space.local_matrix
        resolving.discard(space_iri)

        world_matrices[space_iri] = world_matrix
        return world_matrix

    for space_iri in spaces:
        resolve(space_iri)
    return world_matrices


def transform_bbox(matrix: Matrix, bbox: list[float]) -> list[float]:
    """Enclose a transformed bounding box in an axis-aligned one."""
    corners = [
        matrix @ Vector((x, y, z))
        for x in (bbox[0], bbox[3])
        for y in (bbox[1], bbox[4])
        for z in (bbox[2], bbox[5])
    ]
    return [
        min(corner[index] for corner in corners) for index in range(3)
    ] + [max(corner[index] for corner in corners) for index in range(3)]


def extent_in_reference_frame(
    space_iri: str,
    reference_iri: str | None,
    spaces: dict[str, SpotAmSpace],
    world_matrices: dict[str, Matrix],
) -> list[float] | None:
    """Express a space's extent in another space's coordinates.

    Relations usually target the parent, whose extent is already in the right
    frame. When one targets a space elsewhere in the tree, its extent has to be
    carried across through world coordinates first.
    """
    space = spaces.get(space_iri)
    if space is None or space.local_bbox is None:
        return None
    if reference_iri is None or reference_iri == space_iri:
        return list(space.local_bbox)

    reference_world = world_matrices.get(reference_iri)
    if reference_world is None:
        return None
    space_world = world_matrices.get(space_iri, Matrix.Identity(4))

    try:
        reference_from_world = reference_world.inverted()
    except ValueError:
        # A degenerate parent transform cannot be undone.
        return None

    return transform_bbox(reference_from_world @ space_world, space.local_bbox)


def derive_spot_am_placements(
    spaces: dict[str, SpotAmSpace],
    mappings_by_space: dict[str, list[BoundaryPointMapping]],
) -> dict[str, SpotAmPlacement]:
    """Work out the region each approximately located space occupies.

    Axes with no relation keep the parent's full extent, so a damage known only
    to be "in the back" still spans the room's width and height.
    """
    placements: dict[str, SpotAmPlacement] = {}
    world_matrices = resolve_world_matrices(spaces)

    for space_iri, mappings in mappings_by_space.items():
        space = spaces.get(space_iri)
        if space is None or not mappings:
            continue

        parent_iri = space.parent_iri
        default_bbox = (
            extent_in_reference_frame(
                parent_iri, parent_iri, spaces, world_matrices
            )
            if parent_iri is not None
            else None
        )
        if default_bbox is None:
            # Without a parent extent the untouched axes have no span to keep,
            # so fall back to the first target the relations name.
            default_bbox = extent_in_reference_frame(
                mappings[0].target_iri, parent_iri, spaces, world_matrices
            )
        if default_bbox is None:
            continue

        intervals_by_axis: dict[str, list[AxisInterval]] = {
            axis_name: [] for axis_name in AXIS_NAMES
        }

        for mapping in mappings:
            target_bbox = extent_in_reference_frame(
                mapping.target_iri, parent_iri, spaces, world_matrices
            )
            if target_bbox is None:
                continue

            frame = resolve_directional_frame(mapping.target_iri, spaces)
            interval = relation_interval(
                mapping.relation_name,
                target_bbox,
                frame,
                mapping.normalized_coordinate,
            )
            if interval is None:
                continue
            intervals_by_axis[interval.axis_name].append(interval)

        min_corner = [default_bbox[0], default_bbox[1], default_bbox[2]]
        max_corner = [default_bbox[3], default_bbox[4], default_bbox[5]]

        has_any_interval = False
        for axis_name in AXIS_NAMES:
            intervals = intervals_by_axis[axis_name]
            if not intervals:
                continue
            has_any_interval = True
            index = axis_index(axis_name)
            min_corner[index] = min(interval.lower_value for interval in intervals)
            max_corner[index] = max(interval.upper_value for interval in intervals)

        if not has_any_interval:
            continue

        placements[space_iri] = SpotAmPlacement(
            space_iri=space_iri,
            min_corner=(min_corner[0], min_corner[1], min_corner[2]),
            max_corner=(max_corner[0], max_corner[1], max_corner[2]),
        )

    return placements
