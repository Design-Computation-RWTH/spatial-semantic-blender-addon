# Spatial-Semantic Blender Add-on

This Blender add-on authors datasets for the
Spatial-Semantic Demonstrator.
The demonstrator integrates heterogeneous multimodal building data in one
spatial reference system (SPOT).

The add-on turns a BIM-style Blender scene into a SPOT / SPOT-SG graph. It does
not use the IfcOpenShell or Bonsai runtime APIs. It also places plans,
photographs, damage observations, and BCF issues in that scene, and exports
them as RDF dataset snapshots.

The add-on package is `spatial_semantic_addon/`. You can start its actions from
the F3 search menu or from the `SPOT` tab in the 3D View sidebar. The scripts in
the repository root run the same workflow in background mode.

The code came from the `tools/blender` folder of the demonstrator repository.

## Scripts

- `build_spot_scene.py`
  - Scans the open Blender file.
  - Detects either IFC-style project/site/building/storey collections or a semantic AssetSpace/storey hierarchy from Blender collection names.
  - Considers only geometry objects that are currently visible in the active view layer.
  - Parses optional trailing semantic tags from Blender names.
  - Creates a non-destructive helper collection named `SPOT Scene` made of wireframe bounding-box helper objects.

- `export_spot_ttl.py`
  - Traverses the `SPOT Scene` helper hierarchy.
  - Exports the SPOT scene graph together with BOT referents and optional BEO element classes and BROT bridge classes.
  - Uses `spot:represents` to link each SPOT node to its BOT referent.
  - Declares the local CRS unit of every exported space with `qudt:hasUnit`.

- `import_spot_ttl.py`
  - Reads a SPOT / SPOT-SG scene-graph Turtle file or an RDF dataset folder.
  - For a folder, it loads all `.ttl` and `.trig` files below that folder.
  - It replays `rdf/mutations.trig` in commit order after it loads the other RDF files, so a value that was removed and later restored stays restored.
  - If the folder has `dump/dataset.trig`, it reads only that file. The dump is the dataset after the replay. If the importer read the dump and the other files together, each superseded value would come back.
  - Rebuilds the `SPOT Scene` helper hierarchy as wireframe bounding-box helpers for visual checking inside Blender.
  - Requires the full `scene-graph` profile because it needs `gom:hasTransformationMatrix` data and uses `spot-sg:hasBoundingBox` when present.

## Addon

- Addon package: `spatial_semantic_addon/`
- Search actions after enabling the addon:
  - `SPOT: Build Helper Scene`
  - `SPOT: Export Scene Graph Turtle`
  - `SPOT: Export Scene Graph Turtle As`
  - `SPOT: Export Topology Turtle`
  - `SPOT: Export Topology Turtle As`
  - `SPOT: Import Scene Graph Turtle`
  - `SPOT: Import Dataset Folder`
  - `SPOT: Create IFC Bounding Box`
  - `SPOT: Project Asset Space Origin`
  - `SPOT: Set Asset Space Origin`

The addon also adds a `SPOT` tab in the 3D View sidebar with grouped controls for building, importing, exporting, and re-anchoring the asset-space origin.

The Export panel exposes both the local Base IRI and its Turtle Prefix. For example, Base IRI `http://localhost:4000/scene/` with Prefix `scene` produces `@prefix scene: <http://localhost:4000/scene/> .` and uses slash IRIs such as `http://localhost:4000/scene/ref_storey_03`. A trailing colon in the Prefix field is optional.

### Dataset Snapshot Export

Use `SPOT: Export Dataset Snapshot` to publish a folder under
`<datasets>/exports/<project>/<timestamp>/`. Each project writes into a folder of
its own, so exporting one project never replaces the newest snapshot of another.
Set **Project** at the top of the SPOT panel to name the folder. A Blender
file holds one project. If the name is empty, the folder takes the label of the
root AssetSpace. The action uses one UTC timestamp and
writes deterministic scene RDF. Exports in the same second receive a numeric
suffix.

The graph gateway loads the newest snapshot of any project by default. Use
`npm start -- project <project>` in the demonstrator to load the newest
snapshot of one project.

Plans and photographs belong in `<datasets>/assets/`. Blender records stable
`/assets/plans/<name>` or `/assets/images/<name>` references in RDF. It does not
copy their bytes into each snapshot.

Each image-backed perspective camera becomes a standalone `sosa:Observation`.
Its SPOT `PointSpace` is the capture pose, and its image is a `sosa:Result`.

Select a source picture camera or its PointSpace helper to use the **Picture Observation / BCF Issue** controls. The searchable damage list adds exported `dot:Damage` resources as `sosa:hasFeatureOfInterest` values of the observation and as `foaf:depicts` values of the snapshot, whether or not the picture is a BCF issue. Enable **Make it an issue** to persist synthetic test metadata on the source camera. Optional controls add a generated comment, status history, BOT-backed components, coloring, clipping, line, and bitmap examples.

Dataset snapshots place scene and observation data in `rdf/scene.ttl`. If a
picture is an issue, the export also writes `rdf/bcf.trig`.

Spatial history simulation is disabled by default. Enable **Simulate spatial
history** to write `rdf/spatial-history.trig`. Set **History Start** to choose
the first activity time. The export groups space registrations by phase. For
example, all room registrations use one activity.

Select an eligible plan, image camera, or its helper. Then enable **Simulate
placement change** to add one correction after all registration activities.
Unflagged plans and cameras keep only their final placement.

The generated history describes digital registration and correction. It does
not describe physical construction or movement.

The coordination graph references scene resources instead of duplicating them.
The tool never promotes an export into `<datasets>/canonical/`. Orthographic and
panoramic camera observations are not supported.

`<datasets>` is the `datasets/` directory of the demonstrator repository. The
add-on reads it from these locations, in this order:

1. The **Datasets Directory** field in the add-on preferences.
2. The `SPATIAL_SEMANTIC_DATASETS_DIR` environment variable.

If neither location has a value, the snapshot export stops with an error.

It now also includes a `SPOT Test` panel in the same sidebar so you can verify Blender-side spatial results directly against the helper scene before or alongside the FSFL queries.

### Install the add-on

1. Make a zip file of the `spatial_semantic_addon` folder.
2. In Blender, open `Edit > Preferences > Add-ons`.
3. Click `Install...` and select the zip file.
4. Enable `Spatial-Semantic Dataset Tools`.
5. In the add-on preferences, set **Datasets Directory** to the `datasets/`
   directory of the demonstrator repository.

After that, press `F3` and search for `SPOT`, or open the `SPOT` tab in the 3D View sidebar.

### Live-link the addon during development

If you want Blender to load the addon directly from this repo, create a symlink into Blender's versioned `scripts/addons` directory:

```bash
python3 link_addon.py
```

- By default this links the addon into the newest detected Blender version.
- Use `python3 link_addon.py --all` to link all installed Blender versions.
- Use `python3 link_addon.py --version 5.1` to target the supported Blender version.
- Use `python3 link_addon.py --unlink` to remove the managed symlink again.

The script links only the `spatial_semantic_addon` package. The wrapper scripts stay in the repository.

### Run the Blender tests

Run the integration tests from the repository root:

```bash
python3 run_tests.py
```

To compile the Python sources without Blender, run:

```bash
python3 -m py_compile *.py spatial_semantic_addon/*.py tests/*.py
```

The test launcher finds Blender on `PATH` or in the standard macOS location.
Set `BLENDER_EXECUTABLE` if Blender is in a different location.

## Spatial Rules

- The site is the single `spot:AssetSpace` root.
- Exported spaces declare units with QUDT: physical spaces stay in metres (`unit:M`), while `DocumentSpace` roots and document-backed entities use pixels (`unit:PIXEL`).
- Exported asset spaces, document spaces, and AM-enabled spaces carry explicit `spot:hasXAxis`, `spot:hasYAxis`, and `spot:hasZAxis` links to `spot:Axis` nodes.
- Only asset and document spaces get a `spot:hasDirection`. The axes of an AM-enabled space exist to carry boundary points, so they stay undirected.
- An asset space uses the directions the author sets per axis, defaulting to the Blender `Z+` convention: `X` Front, `Y` Right, `Z` Top.
- A `DocumentSpace` is a sheet rather than a body, so it declares the image convention its geometry is built with: `X` Right across the sheet, `Y` Bottom down the page, `Z` Front out of the page. This makes the downward `Y` of plan coordinates explicit instead of implied by the exported numbers.
- The building, storeys, explicit BOT spaces, and all elements are exported as SPOT spaces.
- Site, building, storey, explicit room/space, and element referents are exported as BOT resources.
- Element referents can additionally carry a `beo:` class.
- Containment is strict parent-child hierarchy.
- The site bounding box encloses the entire exported model.
- The building bounding box encloses all of its descendant storeys, explicit spaces, and building-level elements.
- Each storey bounding box encloses all explicit spaces and elements assigned to that storey.
- Each element bounding box encloses only that element geometry and preserves the source object's current orientation.
- Helper object origins are centered on their represented bounding boxes when the helper scene is first built, and the helper mesh itself shows the full bounding box volume as a wireframe box.
- Bounding boxes are optional in RDF export. Point-like or otherwise extent-less spaces simply omit `spot-sg:hasBoundingBox`.
- The site helper origin can then be re-anchored to a projected asset-space corner or center without moving its child helpers in world space.
- Export treats the current site helper frame as the logical graph origin.

### Document Units

- Blender authoring still keeps document proxies in the existing workaround scale.
- Export converts document-local bounding boxes back to native pixel coordinates using `0.001` Blender units per pixel.
- Appearance matrices are exported in the declared source/target local units, so document-backed entities can keep pixel-local extents while still appearing in metre-based physical parents.
- `unit:PIXEL` is currently kept despite its deprecated status in QUDT because no replacement term is known.

### Axis Convention

- Blender scene orientation is preserved in the exported SPOT graph; the exporter does not rotate coordinates into a viewer-specific convention.
- The current export creates three core SPOT axes for the asset space.
- `Z+` is exported as `spot:Top`.
- `X` and `Y` axis resources are exported too, but the exporter does not yet assign `spot:hasDirection` for them until the left/right/front/rear convention is explicitly settled.

## Naming

- Source names can append trailing semantic tags: `::BOT=<Site|Building|Storey|Space|Element>`, `::SPOT=<SpotClass>`, `::BEO=<BeoClass>`, `::BROT=<BrotClass>`, `::DOT=Damage`, `::IFC=<IfcClass>`, and `::PART=<Id>`.
- `::IFC=<IfcClass>` states the IFC class of a name that carries no `IfcWall/...` prefix. When both are present the tag wins. The value is kept as written, apart from an optional `ifc:` prefix, because this repository pins no list of IFC classes to validate against.
- Damage geometry can be marked explicitly with `::DOT=Damage`. Existing source names beginning with `Damage`, followed by a separator or the end of the name, retain the same meaning for compatibility with the current scene. The exported SPOT space then `spot:represents` a `dot:Damage` referent, and no BOT referent: damage is not building topology, so a damage space represents the `dot:Damage` alone. A DOT class therefore clears any BOT class the defaults or an explicit `::BOT=` tag would otherwise apply. Where the damage sits is said by its appearance — `spot:appearsIn` the containing space, refined by SPOT-AM — not by a BOT referent.
- Tags are parsed only from the trailing `::KEY=VALUE` segments, so the existing helper name prefixes remain valid.
- BOT classes are taken from the explicit trailing `::BOT=...` tag, from the `IfcSpace/` prefix (which gives `bot:Space`), or from the structural defaults the builder applies to generated site, building, and storey helpers. No other IFC prefix gives a BOT class.
- A space is never a `bot:Space` because of where it sits. Plans, sections, views, documents, pictures, and untagged entity spaces have no BOT class unless their names give one. Scenes built before this rule still carry `::BOT=Space` in their helper names. Rebuild the helper scene to remove it.
- A space can also represent the BOT entity of another space. For example, a floor plan can represent the storey that it shows. Select the plan or its helper. In the **Also Represents** panel, choose the storey helper and click **Add**. The export then gives the plan one more `spot:represents` that points at the BOT referent of the storey. The storey keeps that referent as its own. The link is stored on the source object of the plan, or on its collection when the plan is built from a collection, so a rebuild keeps it. On import, a plan or section that represents a BOT referent that another space also represents gets the link, and the other space keeps the BOT class.
- Semantic collection names are also supported for simplified example scenes. `AssetSpace_<Label>` creates the asset root, `EntitySpace_VolumeSpace_<Label>` is treated as a storey container, and plan-like names such as `Floorplan_<Label>`, `Section_<Label>`, or `View_<Label>` become direct space containers under the asset root.
- SPOT-native names are accepted too. For example, `HorizontalAreaSpace_<Label>` and `VerticalAreaSpace_<Label>` work as direct space containers in the same way as `Floorplan_<Label>` and `Section_<Label>`.
- The recommended convention is: keep authoring-friendly aliases where they carry domain meaning (`Floorplan`, `Section`, `View`), and use explicit trailing tags only for ambiguous containers. The one important missing signal in the screenshot convention is role disambiguation for generic volume/entity names: if a collection is not a storey, add trailing tags such as `::BOT=Space::SPOT=EntitySpace`; if it is a storey, prefer `::BOT=Storey::SPOT=VolumeSpace`.
- `::PART=<Id>` can be used on explicit room or space objects such as `VolumeSpace_2Flur::BOT=Space::PART=A` and `VolumeSpace_2Flur::BOT=Space::PART=B`. This exports separate SPOT spaces with their own bounding boxes while keeping one shared BOT room referent.
- Explicit BOT spaces are created from visible geometry objects whose names carry `::BOT=Space`.
- Generic objects without an explicit `::BOT=` tag and without a recognized IFC zone or element class are exported without a BOT class.
- Site helper: `SPOT::Asset::Site::<id>-<slug>::BOT=Site::SPOT=AssetSpace`
- Building helper: `SPOT::Entity::Building::<id>-<slug>::BOT=Building::SPOT=EntitySpace`
- Storey helper: `SPOT::Entity::Storey::<index>-<id>-<slug>::BOT=Storey::SPOT=EntitySpace`
- Space helper: `SPOT::Entity::Space::<id>-<slug>::BOT=Space::SPOT=EntitySpace`
- Element helper: `SPOT::Entity::Element::<id>-<slug>::BOT=Element::SPOT=EntitySpace::BEO=<Class>`

The exported `ex:` URI suffixes are derived from the original Blender source names, taking only the segment after the last `/`. If a name collides, the exporter adds a small disambiguating suffix.

## Assignment Rules

- Elements are assigned to storeys by this precedence:
  1. Direct membership in an `IfcBuildingStorey/...` Blender collection.
  2. Geometry fallback using overlap and distance against provisional storey boxes.
- Explicit BOT spaces use the same storey-assignment rules as elements.
- Site-level geometry stays directly under the site.
- Building-level geometry stays directly under the building.
- `IfcTypeProduct` content is ignored because it is type metadata, not placed scene geometry.
- Hidden geometry is ignored, so viewport visibility can be used to control which elements receive helper boxes.

## Interactive Usage

Open the `.blend` file in Blender and run the scripts from the Scripting workspace.

1. Run `build_spot_scene.py`.
2. Inspect the generated `SPOT Scene` collection.
3. Optional: in the addon sidebar, use `Project Asset Space Origin` to place the 3D cursor from the site helper bbox and `Set Asset Space Origin` to re-anchor the site helper origin.
4. Run `export_spot_ttl.py`.

### Import Existing Scene Graphs

If you have a SPOT scene graph or dataset folder, you can inspect its spatial data in Blender.

1. Open a Blender scene.
2. Use `SPOT: Import Scene Graph Turtle` for one Turtle file.
3. Use `SPOT: Import Dataset Folder` for a complete dataset folder.
4. Alternatively, run `import_spot_ttl.py` with a file path or a folder path.
5. Inspect the reconstructed `SPOT Scene` helper collection.

The folder import uses the same current-state rules as the Graph Gateway. It merges authored RDF with `mutations.trig`. Then it removes recorded retractions. If the folder has `dump/dataset.trig`, the importer reads that file only, because the dump already contains the result of these steps.

The importer recreates the SPOT helper wireframes and hierarchy for checking. It uses the first `rdfs:label` when present. Otherwise, it uses the Turtle local name of the resource. When a space has multiple bounding boxes, the importer uses their enclosing axis-aligned bounding box. Multiple labels or bounding boxes produce import warnings. The importer does not recreate the original source meshes.
If a space has no exported bounding box, the importer uses a tiny placeholder wireframe only for Blender visualization and keeps the missing bbox metadata implicit.
The importer takes BOT, BEO, and BROT classes only from the `rdf:type` of the referent that a space `spot:represents`. It does not guess a class from the position of a space in the tree. If the graph gives no class, the helper gets no class. Use the `SPOT Setup` panel to set one.

#### Import into an existing AssetSpace

Some datasets locate their spaces only with SPOT-AM relations, for example "Span_2 meets the rear of Span_1", and give the root AssetSpace no bounding box. SPOT-AM relations are fractions of a target's extent, so without a box every space stays at the origin.

To give the relations an extent:

1. Make the AssetSpace helper of your model active, for example one built from an IFC file.
2. Set its axis directions in the SPOT panel (which axis points to the rear, left, and top).
3. In the import file browser, select `Place Inside Active AssetSpace`.

Run `Build` again first if the helper was built before scale handling was fixed: a helper built from a scaled object, such as a stretched cube, keeps a box of about 2 m in a stretched frame. The build now moves the source object's scale into the helper's box.

The active helper replaces the imported root AssetSpace. Its box and axis directions resolve every relation to the root. The imported spaces become its children. The rest of the helper collection stays. A repeated import replaces the helpers of the earlier import. When the root states other axis directions than the helper, the helper wins and the importer writes a warning.

The importer resolves SPOT-AM relations with these rules:

- `containedIn*` places a boundary point in one third of the target. `meet*` places it on a face. `disjoint*` places it in the band one third of the target's extent beyond a face.
- Axis mappings tell which end of a space a min or max boundary point bounds. A plain `spot-am:BoundaryPoint` means the space has no extent along that axis. The importer narrows its region to the middle, so a section stays a plane and a camera stays a point.
- Siblings that meet share a face. When the relations do not fix a face, the importer puts it at equal distances between the fixed faces on each side. For example, four spans chained from the front of a bridge to its rear split its length into four equal parts.
- A space that appears more than once in the same kind of space gets only its first appearance. The importer writes a warning.

#### Move spaces from SPOT-AM to SPOT-SG

SPOT-AM locates a space qualitatively. Its derived region can be wrong without the data being wrong: the spans of a bridge split evenly when nothing says where the faces between them lie. You can place such spaces exactly in Blender and record that refinement as provenance.

1. Import the dataset, for example into an existing AssetSpace.
2. Move or scale the helpers that you can place exactly.
3. Enter your name or IRI as `Agent` in the `SPOT-AM to SPOT-SG` box of the SPOT panel.
4. Select the helpers and click `Commit Selected as SPOT-SG`. Give a label, and optionally the IRIs of the sources you used, such as a plan.
5. Repeat for later corrections. A space committed again gets a revised matrix and box.
6. Click `Export Transition Dataset`.

The output is a snapshot in `<datasets>/exports/<project>/<timestamp>/`, like a dataset snapshot. `rdf/scene.ttl` (or the input folder's `rdf` files) holds the input unchanged. `rdf/mutations.trig` holds one entry pair per commit, in the graph gateway's `#!lbcf-entry` format, and follows the reification pattern with the current state asserted:

- The retraction removes the link from the space to its SPOT-AM appearance.
- The delta asserts the link to a new SPOT-SG appearance with its matrix, the space's bounding box, and a `prov:Activity` with agent, time, and sources.
- RDF 1.2 reifiers name every changed triple. The old link is `prov:wasInvalidatedBy` the activity; the new link is `prov:wasGeneratedBy` it and a `prov:wasRevisionOf` the old one.
- The SPOT-AM appearance's own statements stay in the graph, as what SPOT-AM said.
- The first commit also states the root's bounding box, because its matrices refer to the root's frame. The root's axis directions must match the ones the input states.

`Import Dataset Folder` replays the log, so it shows the current state: committed spaces at their SPOT-SG placement, and the rest resolved by SPOT-AM around them.

A plain `Export TTL` writes every helper with a matrix and box. It warns when spaces are still placed by imported SPOT-AM relations, because their derived regions would then appear as exact SPOT-SG placements.

### IFC Bounding Box

A Bonsai/IfcOpenShell import lands in its own `IfcProject/...` collection tree, separate from the `AssetSpace_...` collection `build_spot_scene.py` walks. Re-parenting a large IFC hierarchy into `AssetSpace_...` by hand does not scale, and the builder only looks _inside_ the AssetSpace collection for the geometry it needs.

`SPOT: Create IFC Bounding Box` avoids that re-parenting:

1. Run `Create Bounding Box` from the sidebar or `F3`. Nothing needs to be selected. The action finds every collection whose name starts with `IfcProject/`. This is a name check only, so it also works when Bonsai is disabled.
2. For each of those collections it creates one wireframe box object named `VolumeSpace_<label>::IFC=IfcProject`. The name follows the scene's own convention: SPOT class, then label, then classification tags. The `::IFC=IfcProject` tag records that the box stands for a whole IFC model rather than a modelled space. It carries no `::BOT=` tag, because no BOT class describes an IFC file.
3. The action links each box directly into the `AssetSpace_...` collection. If the scene has no AssetSpace collection, or more than one, it links the box into a collection named `IFC Bounding Boxes` instead. Then you must drag the box into the AssetSpace collection yourself. The heavy IFC collection stays where it is and is never touched.
4. The action selects the new boxes. Move or rotate a box to the position of the model in the Blender scene.
5. Run `Build Helper Scene`, then export. The box becomes a `spot:VolumeSpace` under the asset space. Its `gom:hasTransformationMatrix` is the placement of the IFC root in the asset-space frame, so the matrix is the translation (and rotation) that the IFC model needs.

If you run the action again, it keeps a box that already exists for an `IfcProject/...` collection and selects it. It does not add a second box at the original position. To start again, delete the box.

The box spans only the placed geometry of the collection. It ignores type objects (`IfcTypeProduct...` collections) and objects without geometry. The IFC root point does not make the box larger. The origin of the box is on the IFC root, so the box keeps the frame of the IFC file. If the model is far from its IFC origin, the origin is outside the box. If an object exists whose name matches the collection name, its position is the root. If not, the root is the scene origin.

Once in the AssetSpace collection, the box is an ordinary source element to `build_spot_scene.py`. It carries the IFC extent into the whole-model space and the building bounding box, standing in for the geometry it covers.

The IFC file is exported like a plan sheet. The file is a `spot:DocumentSpace` named `documentspace-<name>`, and the `spot:VolumeSpace` is the physical situation that appears in that file. The export writes:

- `documentspace-<name> a spot:DocumentSpace` with `rdfs:label`, `qudt:hasUnit`, `spot:hasXAxis`, `spot:hasYAxis`, `spot:hasZAxis` and `spot-sg:hasBoundingBox`. The bounding box is the box of the VolumeSpace, because that box is stated around the IFC root in the coordinates of the file. The axes have no `spot:hasDirection`, because the image frame of a plan sheet (Y points down) is not the frame of a model file.
- `documentspace-<name> dcat:distribution distribution_documentspace-<name>`.
- `appearance_<volumespace>_in_documentspace-<name> a spot:DocumentAppearance` from the VolumeSpace into the DocumentSpace. Its matrix is the identity matrix, because the origin of the VolumeSpace is the IFC root and its unit is the unit of the file.

The VolumeSpace keeps its `spot:AssetAppearance` and matrix. It has no `spot:represents` for the file. It has a `spot:represents` only when you set a referent in Blender. The distribution carries `dcat:accessURL`, `dcat:downloadURL`, and `dcat:mediaType "application/x-step"`, the STEP physical file type IFC-SPF uses. The box stores the file name of the model in the `spot_ifc_file` property. When Bonsai has loaded the IFC, `Create Bounding Box` takes the file name and the box label from that file. To change the file, select the box or its helper and click `Set IFC File...` in the IFC Bounding Box panel. If the box has no file name, the add-on guesses `<label>.ifc` from the box label. The `IfcProject` name is often an export default such as `Projekt-001`, so do not trust this guess. Put the file in `<datasets>/assets/ifc/`. The export links it as `/assets/ifc/<file name>` and percent-encodes spaces in the name. Only the `::IFC=IfcProject` box names a model file; every other IFC-backed helper is one element inside such a model and shares the file rather than owning a distribution of its own. The viewer reads this media type to tell a model file apart from a plan, since both are a `spot:DocumentSpace`.

### Asset Space Origin Workflow

After the helper scene has been built, the addon sidebar exposes a small root-origin workflow for the site helper:

- Choose an anchor on the asset-space bbox: `Center`, `Min X / Min Y`, `Min X / Max Y`, `Max X / Min Y`, or `Max X / Max Y`.
- Enter an absolute scene-space Z value.
- Run `Project Asset Space Origin` to place the Blender 3D cursor at that projected location while keeping the asset-space orientation.
- Run `Set Asset Space Origin` to move the site helper origin to the cursor while preserving the world-space positions of all descendant helpers.

This makes it possible to switch the root asset-space frame from a centered bbox origin to a project-style anchor without rebuilding the helper hierarchy.

### SPOT Setup Panel

Use the `SPOT Setup` panel to set the class of the entity that a space represents.

1. Select one or more spaces, or their SPOT helpers.
2. Click a button under `Set as BOT` or `Set as BROT (bridge)`.
3. To remove the class, click `Clear Class`. The space then represents no entity.

A space has one class, from BOT or from [BROT](https://w3id.org/brot) (Bridge Topology Ontology 0.21). A BROT class replaces a BOT class, and a BOT class replaces a BROT class. Neither vocabulary implies the other. If no space uses BOT, the export contains no `bot:` term.

The panel changes the tags in the helper name, for example from `SPOT::Imported::Element::SubStructureZone::BOT=Element::SPOT=VolumeSpace` to `SPOT::Imported::Element::SubStructureZone::BROT=SubStructure::SPOT=VolumeSpace`. If the helper has a source object or collection, the panel also changes the tags in the source name, so a rebuild of the helper scene keeps the class. References to a renamed object, such as SPOT Test slots and SPOT-AM reference objects, follow the new name.

The export writes a hierarchy relation only if the parent and the child both have a class from the same vocabulary:

- BOT: `bot:hasBuilding`, `bot:hasStorey`, `bot:hasSpace`, `bot:hasElement`, or `bot:containsZone` from a zone, and `bot:hasSubElement` from an element to an element.
- BROT: `brot:containsZone` (zone to zone), `brot:containsComponent` (zone to component), `brot:aggregates` (component to component), `brot:hasJoint`, and `brot:hasCoating`. A joint or a coating directly under a zone gets no relation, because BROT does not make them components.

BCF components can be BOT elements or BROT components, joints, and coatings.

#### Set as Camera

The export writes a `sosa:Observation` for a photograph only if its PointSpace is a perspective Blender camera, or was built from one. An imported PointSpace is a wireframe point. To make it a camera:

1. Select one or more PointSpaces, or their helpers.
2. Click `Set as Camera` in the `Photograph` box.
3. If one PointSpace without an image is selected, choose the image file.

The importer keeps the image of a PointSpace. It reads `dcat:accessURL` or `dcat:downloadURL` on the space, on its `dcat:distribution`, or on the result of the observation that the space represents. The image file name goes to `/assets/images/<name>`.

A camera object replaces the helper in the `SPOT Scene` collection. It keeps the name, the properties, the parent, the transform, and all references of the helper. It looks along its local -Z axis, as a camera in a built scene does. To aim the camera, turn it. You can change the lens on the camera data. The camera holds the picture controls, such as damages and BCF issues.

### SPOT Test Panel

The `SPOT Test` panel is a Blender-side verification surface for the current helper scene.

- Pick `Left`, `Right`, and `Reference` helper objects using the search fields, or assign them from the active helper with the eyedropper buttons.
- The panel mirrors the current AABB-based runtime semantics for:
  - `distance`
  - `absolutePosition`
  - `contains`
  - `inside`
  - `intersects`
  - `equals`
  - `touches`
  - `overlaps`
  - `disconnected`
- The `Right` helper is also compared against the Blender 3D cursor so you can verify point-to-object distance and whether the cursor lies inside the helper volume.
- The `Reference` helper drives list-style checks similar to the manual HTTP requests: within-radius matches, helpers inside the reference, containers of the reference, intersecting helpers, and disconnected helpers.
- `Write SPOT Test Report` writes the full current result set into a Blender text datablock named `SPOT Test Results` for side-by-side checking.

This panel uses Blender helper transforms, helper wireframe bounding boxes, and the Blender 3D cursor directly, so it is useful for validating what the exported scene graph and the spot-q runtime should report.

The exporter supports two profiles:

- `scene-graph`
  - Includes `spot:hasAppearance`, `spot:appearsIn`, `spot:represents`, BOT referents and BOT hierarchy relations, `gom:hasTransformationMatrix`, and `spot-sg:hasBoundingBox`.
- `spot-only`
  - Includes `spot:hasAppearance`, `spot:appearsIn`, `spot:represents`, BOT referents and BOT hierarchy relations, `spot:hasSuperSpace`, and `spot:hasSubspace`.
  - Omits `spot-sg:*` and `gom:*` triples entirely.

## Headless Usage

Create the helper scene in a copy of the blend file:

```bash
/Applications/Blender.app/Contents/MacOS/Blender \
  --background "/path/to/scene.blend" \
  --python build_spot_scene.py \
  -- --save-blend "/tmp/Domhof_6_spot.blend"
```

Export Turtle from that helper scene:

```bash
/Applications/Blender.app/Contents/MacOS/Blender \
  --background "/tmp/Domhof_6_spot.blend" \
  --python export_spot_ttl.py \
  -- --output "/tmp/domhof_spot.ttl"

/Applications/Blender.app/Contents/MacOS/Blender \
  --background "/tmp/Domhof_6_spot.blend" \
  --python export_spot_ttl.py \
  -- --profile spot-only --output "/tmp/domhof_spot_topology.ttl"

/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup \
  --python import_spot_ttl.py \
  -- --input "/path/to/demonstrator/datasets/spatial/Domhof_6_spot.ttl" --save-blend "/tmp/Domhof_6_imported.blend"

/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup \
  --python import_spot_ttl.py \
  -- --input "/path/to/demonstrator/datasets/exports/20260731T111541Z-domhof-6-simpliified-am" \
  --save-blend "/tmp/Domhof_6_current.blend"
```

## Output

If `export_spot_ttl.py` is run without `--output`, it writes next to the open `.blend` file using a profile-specific default name:

- `scene-graph`: `<blend-name>_spot.ttl`
- `spot-only`: `<blend-name>_spot_topology.ttl`
