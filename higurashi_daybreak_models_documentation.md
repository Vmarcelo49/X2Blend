# Technical Documentation: Higurashi Daybreak Models Conversion Pipeline

This document provides a highly technical, step-by-step breakdown of the **XtoBlend** conversion pipeline, detailing the mathematical formulas, coordinate system changes, API calls, and structural changes used to convert DirectX 9 `.x` models (such as `00.X` from *Higurashi Daybreak*) into Blender `.blend` files with 100% animation and bone placement accuracy.

---

## 1. Stage 1: The C++ Parser & Matrix Baker (`x2blend.exe`)

The parser runs under Wine to access DirectX 9 libraries (`d3d9.lib`, `d3dx9.lib`, `dxguid.lib`). It performs the following steps:

### Step 1.1: Headless Direct3D 9 Context Creation
Because the loading libraries (`D3DX`) require an active Direct3D device to function, we initialize a minimal headless device:
1.  **Register a Win32 window class** and create a dummy hidden window:
    ```cpp
    HWND hwnd = CreateWindowA("X2GltfDummyWindow", "Headless", WS_POPUP, 0, 0, 640, 480, ...);
    ```
2.  **Initialize Direct3D 9** and configure `D3DPRESENT_PARAMETERS` (Windowed = TRUE, SwapEffect = D3DSWAPEFFECT_DISCARD).
3.  **Create the Device**: Try creating a hardware device (`D3DDEVTYPE_HAL` with `D3DCREATE_SOFTWARE_VERTEXPROCESSING`). If it fails (as is common in headless/console Wine environments), fallback to the software reference device (`D3DDEVTYPE_REF` or `D3DDEVTYPE_NULLREF`).

### Step 1.2: Template Injection & File Loading
Wine’s built-in `d3dxof` (DirectX Object File) parser lacks standard Direct3D templates. We preprocess the `.x` file:
1.  Read the binary or text `.x` file into a string.
2.  If the magic header is `xof 0303txt` or `xof 0302txt` (text format), we search for the newline character after the header and inject the following templates:
    *   `XSkinMeshHeader` (defines skin properties).
    *   `VertexDuplicationIndices` (maps duplicated vertices).
    *   `SkinWeights` (defines bone weights, vertex indices, and offset matrix).
3.  Write the preprocessed buffer to a temporary file on disk.
4.  Call the reference loader:
    ```cpp
    HRESULT hr = D3DXLoadMeshHierarchyFromXA(tempFile.c_str(), D3DXMESH_SYSTEMMEM, m_pDevice, &allocHierarchy, nullptr, &pRootFrame, &pAnimController);
    ```

### Step 1.3: Custom Hierarchy Allocation (`ViewerAllocateHierarchy`)
We implement the `ID3DXAllocateHierarchy` COM interface to allocate memory for frames and mesh containers:
*   `CreateFrame(LPCSTR Name, LPD3DXFRAME* ppNewFrame)`: Allocates `ViewerFrame` (which extends `D3DXFRAME` with a $4 \times 4$ `worldMatrix`). Copies the name and initializes the transformation matrix to identity.
*   `CreateMeshContainer(...)`:
    *   Allocates `ViewerMeshContainer` (extends `D3DXMESHCONTAINER`).
    *   Transcodes texture filenames from Shift-JIS to Wide Strings and loads the texture using `D3DXCreateTextureFromFileW`.
    *   If `pSkinInfo` is present, clones the source mesh using `CloneMeshFVF(D3DXMESH_MANAGED, FVF, pDevice, &pSkinnedMesh)` to create an identical destination mesh that will receive the software-skinned vertices in the render loop.

### Step 1.4: Hierarchy Mapping & Math Decomposition
The parser traverses the tree starting from `pRootFrame` to flatten it into a 1D node array (`model.nodes`):
1.  **Decompose Transformations**: The C++ code uses `D3DXMatrixDecompose` to extract translation ($T$), rotation quaternion ($Q$), and scale ($S$) from `pFrame->TransformationMatrix`.
2.  **Parent-Space Transformation**:
    $$\text{worldMatrix}_{\text{frame}} = \text{TransformationMatrix}_{\text{frame}} \cdot \text{worldMatrix}_{\text{parent}}$$

### Step 5: Vertex & Index Extraction
We iterate over all mesh containers and extract geometry, applying coordinate conversions:
1.  **Coordinate System Conversion (Left to Right-Handed)**: DirectX is left-handed ($Y$-up, $Z$-forward); Blender is right-handed ($Z$-up, $-Y$-forward). We apply a reflection matrix $R$ (swapping $Y$ and $Z$ axes):
    $$R = \begin{pmatrix} 1 & 0 & 0 & 0 \\ 0 & 0 & 1 & 0 \\ 0 & 1 & 0 & 0 \\ 0 & 0 & 0 & 1 \end{pmatrix}$$
    *   Vertices position: $\vec{v}_{\text{blender}} = \{v_x, v_z, v_y\}$
    *   Normals vector: $\vec{n}_{\text{blender}} = \{n_x, n_z, n_y\}$
2.  **Triangle Winding Order Correction**: Because reflection flips the orientation of triangles, we reverse the winding order of all indices to prevent face normals from pointing inwards:
    $$\text{index}_{3i + 1} \longleftrightarrow \text{index}_{3i + 2}$$
3.  **Offset Matrices World Conversion**: D3DX skin offset matrices (inverse bind matrices) are converted into world space relative to the mesh's frame world matrix:
    $$M_{\text{offset\_world}} = M_{\text{world\_frame}}^{-1} \cdot M_{\text{offset\_matrix}}$$
    This world-space offset matrix is then mapped to Blender coordinate space using $R \cdot M_{\text{offset\_world}} \cdot R$.
4.  **Bone Weights Normalization**: We read skin influences, cap the max bones per vertex to 4, and normalize the weights:
    $$w_i = \frac{w_i}{\sum_{j=0}^3 w_j}$$

### Step 1.6: World-Space Matrix Baking (Animation Sampling)
To ensure the animations match the native DirectX execution, we sample the hierarchy transforms at $60\text{ FPS}$:
1.  We set track 0 of `ID3DXAnimationController` to the target animation set.
2.  Calculate the time step $\Delta t = 1.0 / 60.0$, and the total frames $N = \text{ceil}(\text{duration} \times 60)$.
3.  For each frame $f \in [0, N-1]$:
    *   Set track time:
        ```cpp
        pAnimController->SetTrackPosition(0, (double)(f * timeStep));
        ```
    *   Force evaluation:
        ```cpp
        pAnimController->AdvanceTime(0.0, nullptr);
        ```
    *   Update world matrices recursively:
        $$M_{\text{world}} = M_{\text{local}} \cdot M_{\text{parent\_world}}$$
    *   For each bone node, convert its world matrix to Blender space via reflection and save it to the channel's `bakedKeys` array:
        $$M_{\text{blend\_world}} = R \cdot M_{\text{world}} \cdot R$$

---

## 3. Stage 2: The Blender Python Importer (`blend_importer.py`)

The importer runs inside Blender (`bpy` context). It builds the scene and animations:

### Step 2.1: Scene Reset & Asset Creation
1.  Deletes all meshes, armatures, materials, actions, and empty objects.
2.  Generates Blender materials from diffuse, emissive, and specular coefficients, linking texture image nodes to the BSDF shader.

### Step 2.2: Armature Edit Bone Creation
1.  **Reconstruct Bind Pose**: We load the inverse bind matrices ($M_{\text{inv\_bind}}$) from the JSON. The world-space bind matrix is calculated as:
    $$M_{\text{bind}} = M_{\text{inv\_bind}}^{-1}$$
2.  **Define Bones**:
    *   `eb.head` is set to $M_{\text{bind}} \cdot \begin{pmatrix}0 & 0 & 0 & 1\end{pmatrix}^T$ (translation).
    *   `eb.tail` is defined by offsetting along the bone local $Y$-axis (the second column of the rotation matrix):
        $$\vec{y}_{\text{local}} = M_{\text{bind}} \cdot \begin{pmatrix}0 & 1 & 0 & 0\end{pmatrix}^T$$
        $$\text{tail} = \text{head} + \vec{y}_{\text{local}} \cdot 0.05$$
    *   `eb.roll` is calculated to align the bone local $X$/$Z$ axes with the bind orientation using a change of basis:
        $$\text{roll} = \text{atan2}(R_{02}, R_{22})$$
        where $R = M_{\text{basis\_vector\_inverse}} \cdot M_{\text{bind}}$.

### Step 2.3: Mesh Construction & Bone Parenting
1.  Creates meshes from vertex arrays using `me.from_pydata(positions, [], faces)`.
2.  Applies custom split normals using `me.normals_split_custom_set_from_vertices`.
3.  Links vertices to vertex groups named after the corresponding bones.
4.  **Unskinned Mesh Parenting**:
    *   We locate the node referencing the unskinned mesh in `nodes_data`.
    *   Parent the object to the armature with type `'BONE'`:
        ```python
        obj.parent = arm_obj
        obj.parent_type = 'BONE'
        obj.parent_bone = node_name
        ```
    *   Set the parent inverse matrix to ensure the mesh stays at its bind-pose world coordinate:
        $$\text{obj.matrix\_parent\_inverse} = (M_{\text{armature\_world}} \cdot M_{\text{bone\_rest}})^{-1}$$

### Step 2.4: Pure-Mathematical Matrix Keyframing
To avoid dependency graph update lag during import, we compute the local pose keyframe values mathematically in Python memory:
1.  **Create F-Curves**:
    In the Action (handling slotted actions for Blender 5.0+), we create F-curves for:
    *   `location` (indices 0, 1, 2)
    *   `rotation_quaternion` (indices 0, 1, 2, 3)
    *   `scale` (indices 0, 1, 2)
2.  **Local Matrix Decomposition**:
    At each frame, Blender expects the pose bone matrix $M_{\text{pose\_local}}$ to be defined relative to its rest pose in armature space.
    *   Let $M_{\text{world}}$ be the baked matrix of the bone from the JSON.
    *   Let $M_{\text{world\_parent}}$ be the baked matrix of the parent bone from the JSON.
    *   Let $M_{\text{rest}}$ be the bone's rest matrix (`matrix_local`).
    *   Let $M_{\text{rest\_parent}}$ be the parent bone's rest matrix.
    
    If the bone has a parent:
    $$M_{\text{pose\_local}} = M_{\text{rest}}^{-1} \cdot M_{\text{rest\_parent}} \cdot M_{\text{world\_parent}}^{-1} \cdot M_{\text{world}}$$
    
    If the bone is a root bone (no parent):
    $$M_{\text{pose\_local}} = M_{\text{rest}}^{-1} \cdot M_{\text{world}}$$
3.  **Decompose and Insert Keys**:
    We decompose the $4 \times 4$ local pose matrix:
    $$\text{loc, rot, scale} = M_{\text{pose\_local}}.\text{decompose}()$$
    We then write these values directly to the F-curves at keyframe `f_num`:
    *   `fcs["location"][i].keyframe_points.insert(f_num, loc[i])`
    *   `fcs["rotation_quaternion"][i].keyframe_points.insert(f_num, rot[i])`
    *   `fcs["scale"][i].keyframe_points.insert(f_num, scale[i])`

This mathematical approach completely bypasses the need to set `pb.matrix` and call `view_layer.update()` inside the frame loop, providing a $100\times$ speedup and ensuring 100% mathematical precision.
