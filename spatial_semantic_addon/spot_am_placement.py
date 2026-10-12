"""Derive placements for spaces that SPOT-AM locates only approximately.

A damage observed "in the back left of the room" carries no transformation
matrix. Its position is stated with ``reloc:`` relations between boundary
points of the damage and the room it sits in. Each relation narrows one axis to
a third of the room's extent, and the surviving box is the region the damage is
known to be inside.

The region rules mirror ``deriveSpotAmFuzzyEntityPlacements`` in the validation
viewer's ``spot-q`` package so both tools agree on where an approximate damage
sits. Two things go further than the viewer:

- ``disjoint*`` relations place a point in the band one third of the target's
  extent beyond the named face.
- A plain ``spot-am:BoundaryPoint`` flattens its axis: the region it names
  is narrowed to its middle, so a section stays a plane and a camera a point.
- Siblings that ``meet`` each other share a face, and faces no relation pins
  down are spread evenly between the ones that are pinned. Spans chained from
  the front of a bridge to its rear then split its length equally.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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

# The two ends of a space along one axis of its parent.
LOWER_END = 0
UPPER_END = 1

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
    # The end of the source space this point bounds, on the parent's axes:
    # (axis name, LOWER_END or UPPER_END). None when that is not known; the
    # point then bounds both ends, so the space spans the region.
    source_end: tuple[str, int] | None = None
    # A plain spot-am:BoundaryPoint: the space has no extent along this axis,
    # as a section has none across its plane, so both ends are one point.
    flattens_axis: bool = False
    # The parent axis the point's own axis lies along, from the appearance's
    # axis mapping. A relation that constrains another axis contradicts it.
    source_axis: str | None = None


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
    elif relation_name.startswith("disjoint"):
        direction = directional_suffix(relation_name)
        if direction is None:
            return None
        if direction == positive_direction:
            lower_fraction, upper_fraction = 1.0, 4.0 / 3.0
        elif direction == opposite:
            lower_fraction, upper_fraction = -1.0 / 3.0, 0.0
        else:
            return None
    else:
        return None

    return AxisInterval(
        axis_name,
        lower + span * lower_fraction,
        lower + span * upper_fraction,
    )


def relation_face(
    relation_name: str,
    frame: dict[str, str],
) -> tuple[str, int] | None:
    """Name the face of the target that a ``meet*`` relation touches."""
    if not relation_name.startswith("meet"):
        return None
    direction = directional_suffix(relation_name)
    if direction is None:
        return None
    resolved = frame_axis_for_direction(frame, direction)
    if resolved is None:
        return None
    axis_name, is_positive = resolved
    return axis_name, UPPER_END if is_positive else LOWER_END


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


EndpointKey = tuple[str, str, int]


class EndpointSolver:
    """Ends of sibling spaces along the parent's axes, and what pins them.

    Ends that ``meet`` share a face and are merged into one class. A class is
    pinned by the regions relations give it; the rest are interpolated along
    the chains that lead from one pinned class to the next.

    A region taken from a sibling's derived box is "dependent": it places the
    end it describes, but never pins the chain the sibling belongs to. Section
    D-D lying "in the front of span 2" would otherwise drag the face between
    spans 1 and 2 forward, which moves span 2, which moves D-D again.
    """

    def __init__(self) -> None:
        self.parents: dict[EndpointKey, EndpointKey] = {}
        self.intervals: dict[EndpointKey, list[AxisInterval]] = {}
        self.dependent_intervals: dict[EndpointKey, list[AxisInterval]] = {}

    def find(self, key: EndpointKey) -> EndpointKey:
        self.parents.setdefault(key, key)
        root = key
        while self.parents[root] != root:
            root = self.parents[root]
        while self.parents[key] != root:
            self.parents[key], key = root, self.parents[key]
        return root

    def union(self, left: EndpointKey, right: EndpointKey) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root

    def add_interval(
        self,
        key: EndpointKey,
        interval: AxisInterval,
        dependent: bool = False,
    ) -> None:
        self.find(key)
        store = self.dependent_intervals if dependent else self.intervals
        store.setdefault(key, []).append(interval)

    def solve(self) -> dict[EndpointKey, tuple[float, float]]:
        """Give each end the range it is known to lie in."""
        members: dict[EndpointKey, list[EndpointKey]] = {}
        for key in self.parents:
            members.setdefault(self.find(key), []).append(key)

        ranges = self.pinned_ranges(members, self.intervals)
        ranges.update(self.interpolate(ranges))
        for root, value in self.pinned_ranges(
            members, self.dependent_intervals
        ).items():
            ranges.setdefault(root, value)
        return {
            key: ranges[self.find(key)]
            for key in self.parents
            if self.find(key) in ranges
        }

    @staticmethod
    def pinned_ranges(
        members: dict[EndpointKey, list[EndpointKey]],
        intervals_by_key: dict[EndpointKey, list[AxisInterval]],
    ) -> dict[EndpointKey, tuple[float, float]]:
        ranges: dict[EndpointKey, tuple[float, float]] = {}
        for root, keys in members.items():
            intervals = [
                interval
                for key in keys
                for interval in intervals_by_key.get(key, [])
            ]
            if not intervals:
                continue
            if len(keys) == 1:
                # A lone end keeps the whole region, as one relation only says
                # the end lies somewhere inside it.
                ranges[root] = (
                    min(interval.lower_value for interval in intervals),
                    max(interval.upper_value for interval in intervals),
                )
                continue
            # A shared face is one plane: take the middle of what all its
            # relations allow.
            lower = max(interval.lower_value for interval in intervals)
            upper = min(interval.upper_value for interval in intervals)
            if lower > upper:
                lower = min(interval.lower_value for interval in intervals)
                upper = max(interval.upper_value for interval in intervals)
            middle = (lower + upper) / 2.0
            ranges[root] = (middle, middle)
        return ranges

    def interpolate(
        self,
        pinned: dict[EndpointKey, tuple[float, float]],
    ) -> dict[EndpointKey, tuple[float, float]]:
        """Spread unpinned faces evenly between the pinned faces around them."""
        successors: dict[EndpointKey, set[EndpointKey]] = {}
        predecessors: dict[EndpointKey, set[EndpointKey]] = {}
        spaces_axes = {(key[0], key[1]) for key in self.parents}
        for space_iri, axis_name in spaces_axes:
            lower_key = (space_iri, axis_name, LOWER_END)
            upper_key = (space_iri, axis_name, UPPER_END)
            if lower_key not in self.parents or upper_key not in self.parents:
                continue
            lower_root, upper_root = self.find(lower_key), self.find(upper_key)
            if lower_root == upper_root:
                continue
            successors.setdefault(lower_root, set()).add(upper_root)
            predecessors.setdefault(upper_root, set()).add(lower_root)

        nodes = set(successors) | set(predecessors)
        order = topological_order(nodes, successors, predecessors)

        def anchor_value(root: EndpointKey) -> float:
            lower, upper = pinned[root]
            return (lower + upper) / 2.0

        # Nearest pinned value below each face, and how many steps away it is.
        below: dict[EndpointKey, tuple[float, int]] = {}
        for root in order:
            if root in pinned:
                below[root] = (anchor_value(root), 0)
                continue
            candidates = [
                (below[previous][0], below[previous][1] + 1)
                for previous in predecessors.get(root, ())
                if previous in below
            ]
            if candidates:
                below[root] = max(candidates)

        above: dict[EndpointKey, tuple[float, int]] = {}
        for root in reversed(order):
            if root in pinned:
                above[root] = (anchor_value(root), 0)
                continue
            candidates = [
                (above[following][0], above[following][1] + 1)
                for following in successors.get(root, ())
                if following in above
            ]
            if candidates:
                above[root] = min(candidates, key=lambda item: (item[0], -item[1]))

        interpolated: dict[EndpointKey, tuple[float, float]] = {}
        for root in order:
            if root in pinned or root not in below or root not in above:
                continue
            lower_value, lower_steps = below[root]
            upper_value, upper_steps = above[root]
            value = lower_value + (upper_value - lower_value) * (
                lower_steps / (lower_steps + upper_steps)
            )
            interpolated[root] = (value, value)
        return interpolated


def topological_order(
    nodes: set[EndpointKey],
    successors: dict[EndpointKey, set[EndpointKey]],
    predecessors: dict[EndpointKey, set[EndpointKey]],
) -> list[EndpointKey]:
    """Order faces so each comes after the faces below it.

    Faces caught in a cycle contradict each other and are left out.
    """
    remaining = {node: len(predecessors.get(node, ())) for node in nodes}
    ready = sorted(node for node, count in remaining.items() if count == 0)
    order: list[EndpointKey] = []
    while ready:
        node = ready.pop()
        order.append(node)
        for following in sorted(successors.get(node, ())):
            remaining[following] -= 1
            if remaining[following] == 0:
                ready.append(following)
    return order


def space_depth(space_iri: str | None, spaces: dict[str, SpotAmSpace]) -> int:
    depth = 0
    visited: set[str] = set()
    current = space_iri
    while current is not None and current not in visited and current in spaces:
        visited.add(current)
        depth += 1
        current = spaces[current].parent_iri
    return depth


def local_name(iri: str) -> str:
    for separator in ("#", "/", ":"):
        if separator in iri:
            iri = iri.rsplit(separator, 1)[-1]
    return iri.strip("<>")


def solve_sibling_group(
    parent_iri: str | None,
    group: list[str],
    spaces: dict[str, SpotAmSpace],
    mappings_by_space: dict[str, list[BoundaryPointMapping]],
    conflicts: set[str] | None = None,
) -> dict[str, SpotAmPlacement]:
    """Place the spaces of one parent that SPOT-AM locates, in one pass.

    Relations to a sibling that has not been placed yet are skipped, except
    ``meet*``, which ties the two spaces to one face either way.
    """
    world_matrices = resolve_world_matrices(spaces)
    members = set(group)
    solver = EndpointSolver()
    constrained_axes: dict[str, set[str]] = {space_iri: set() for space_iri in group}
    parent_bbox = (
        extent_in_reference_frame(parent_iri, parent_iri, spaces, world_matrices)
        if parent_iri is not None
        else None
    )

    for space_iri in group:
        for mapping in mappings_by_space[space_iri]:
            frame = resolve_directional_frame(mapping.target_iri, spaces)
            axis_name = relation_axis_name(mapping.relation_name, frame)
            if axis_name is None:
                continue
            if mapping.source_axis is not None and mapping.source_axis != axis_name:
                # The point lies on one axis, the relation places it on another.
                # Trusting the relation would turn a horizontal section into a
                # vertical one, so it is dropped; a plane keeps its flatness
                # in the middle of its parent.
                if conflicts is not None:
                    conflicts.add(
                        f"{local_name(space_iri)}: {mapping.relation_name} constrains "
                        f"{axis_name}, but its boundary point lies along "
                        f"{mapping.source_axis}"
                    )
                if mapping.flattens_axis and parent_bbox is not None:
                    index = axis_index(mapping.source_axis)
                    middle = (parent_bbox[index] + parent_bbox[index + 3]) / 2.0
                    keys = [
                        (space_iri, mapping.source_axis, LOWER_END),
                        (space_iri, mapping.source_axis, UPPER_END),
                    ]
                    solver.union(*keys)
                    solver.add_interval(
                        keys[0], AxisInterval(mapping.source_axis, middle, middle)
                    )
                    constrained_axes[space_iri].add(mapping.source_axis)
                continue
            if mapping.source_end is not None and mapping.source_end[0] == axis_name:
                ends = (mapping.source_end[1],)
            else:
                ends = (LOWER_END, UPPER_END)
            keys = [(space_iri, axis_name, end) for end in ends]
            if mapping.flattens_axis:
                keys = [(space_iri, axis_name, LOWER_END), (space_iri, axis_name, UPPER_END)]
                solver.union(*keys)

            face = (
                relation_face(mapping.relation_name, frame)
                if mapping.target_iri in members
                else None
            )
            if face is not None:
                face_key = (mapping.target_iri, face[0], face[1])
                for key in keys:
                    solver.union(key, face_key)
                constrained_axes[space_iri].add(axis_name)
                constrained_axes[mapping.target_iri].add(face[0])
                continue

            target_bbox = extent_in_reference_frame(
                mapping.target_iri, parent_iri, spaces, world_matrices
            )
            if target_bbox is None:
                continue
            interval = relation_interval(
                mapping.relation_name,
                target_bbox,
                frame,
                mapping.normalized_coordinate,
            )
            if interval is None:
                continue
            for key in keys:
                solver.add_interval(
                    key, interval, dependent=mapping.target_iri in members
                )
            constrained_axes[space_iri].add(axis_name)

    ranges = solver.solve()
    placements: dict[str, SpotAmPlacement] = {}

    for space_iri in group:
        mappings = mappings_by_space[space_iri]
        default_bbox = (
            extent_in_reference_frame(parent_iri, parent_iri, spaces, world_matrices)
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

        min_corner = [default_bbox[0], default_bbox[1], default_bbox[2]]
        max_corner = [default_bbox[3], default_bbox[4], default_bbox[5]]
        has_any_range = False

        for axis_name in sorted(constrained_axes[space_iri]):
            lower_range = ranges.get((space_iri, axis_name, LOWER_END))
            upper_range = ranges.get((space_iri, axis_name, UPPER_END))
            if lower_range is None and upper_range is None:
                continue
            # With one end unknown, the space spans the region of the other.
            lower_range = lower_range or upper_range
            upper_range = upper_range or lower_range
            low, high = lower_range[0], upper_range[1]
            index = axis_index(axis_name)
            min_corner[index] = min(low, high)
            max_corner[index] = max(low, high)
            has_any_range = True

        if not has_any_range:
            continue
        placements[space_iri] = SpotAmPlacement(
            space_iri=space_iri,
            min_corner=(min_corner[0], min_corner[1], min_corner[2]),
            max_corner=(max_corner[0], max_corner[1], max_corner[2]),
        )

    return placements


def placement_signature(
    placements: dict[str, SpotAmPlacement],
) -> dict[str, tuple[float, ...]]:
    return {
        space_iri: tuple(
            round(value, 9) for value in (*placement.min_corner, *placement.max_corner)
        )
        for space_iri, placement in placements.items()
    }


def derive_spot_am_placements(
    spaces: dict[str, SpotAmSpace],
    mappings_by_space: dict[str, list[BoundaryPointMapping]],
    conflicts: set[str] | None = None,
) -> dict[str, SpotAmPlacement]:
    """Work out the region each approximately located space occupies.

    Axes with no relation keep the parent's full extent, so a damage known only
    to be "in the back" still spans the room's width and height. Siblings are
    solved together and re-solved until stable, so a section that lies "in the
    middle of span 2" can follow span 2 once the spans are placed. Parents are
    placed before their children.
    """
    # Work on copies: placed siblings feed the next pass through their boxes.
    working = {space_iri: replace(space) for space_iri, space in spaces.items()}
    groups: dict[str | None, list[str]] = {}
    for space_iri, mappings in mappings_by_space.items():
        if space_iri in working and mappings:
            groups.setdefault(working[space_iri].parent_iri, []).append(space_iri)

    placements: dict[str, SpotAmPlacement] = {}
    for parent_iri in sorted(
        groups, key=lambda value: (space_depth(value, working), value or "")
    ):
        group = groups[parent_iri]
        group_placements: dict[str, SpotAmPlacement] = {}
        for _ in range(len(group) + 1):
            solved = solve_sibling_group(
                parent_iri, group, working, mappings_by_space, conflicts
            )
            for space_iri, placement in solved.items():
                working[space_iri].local_bbox = placement.centered_local_bbox()
                working[space_iri].local_matrix = Matrix.Translation(
                    Vector(placement.center)
                )
            stable = placement_signature(solved) == placement_signature(
                group_placements
            )
            group_placements = solved
            if stable:
                break
        placements.update(group_placements)

    return placements
