# PBR Bake Pro

A Blender 5.0+ add-on that bakes any node-based material down to clean PBR textures and rebuilds the object's material around the baked maps — ready for FBX / glTF export into Unreal Engine 5, Unity, Godot, or any modern PBR pipeline.

## Features

- **Bake all selected objects** in one click — each object gets its own texture set named after it.
- **PBR maps**: Base Color, Metallic, Roughness, Normal (tangent), AO, Emission, Alpha.
- **ORM channel packing** for UE5 (R=AO, G=Roughness, B=Metallic) in a single texture.
- **Resolution** presets (256 → 8K) and custom non-square sizes.
- **Cycles samples** control for quality vs. speed.
- **Naming presets** for Unreal (`T_Mesh_BC`, `T_Mesh_N`, `T_Mesh_ORM`), or generic.
- **Material replacement**: after baking, the object's material is rebuilt from the baked textures and slot count optionally collapsed to 1 — exports cleanly.
- **Auto UV unwrap** for objects that don't have a UV map (Smart UV Project).
- **High-Poly → Low-Poly** bake mode using Blender's Selected-to-Active with cage extrusion.
- **Engine presets** for UE5.
- **Original material kept as backup** by default — nothing is destroyed.
- Output as PNG / TGA / JPEG / EXR / TIFF.

## Install

### As an Extension (Blender 5.0+)

Directly download the latest release and install the addon from disc or download the code and:

1. Zip the `pbr_bake_pro/` folder so the archive contains `pbr_bake_pro/blender_manifest.toml` at its root.
2. In Blender: `Edit → Preferences → Get Extensions → ▾ menu → Install from Disk…`
3. Select the zip.
4. Enable **PBR Bake Pro** in the Add-ons list.

### As a Legacy Add-on

1. Zip the `pbr_bake_pro/` folder.
2. `Edit → Preferences → Add-ons → Install…` → pick the zip.
3. Tick to enable.

The panel appears in the **3D Viewport → N-panel → PBR Bake** tab.

## Usage

1. Select one or more mesh objects.
2. (Optional) Click the **UE5** preset.
3. Set resolution, samples, output folder.
4. Click **Bake N Object(s)**.

Each object becomes a self-contained material with baked textures saved to the output folder. Select the objects and export with `File → Export → FBX` or `glTF` — Unreal will pick up the textures automatically when you import the mesh.

### High-Poly → Low-Poly

1. Open the **High-Poly to Low-Poly** sub-panel and enable the toggle.
2. Select all the high-poly source objects, then Shift+click the low-poly *last* so it's the active object.
3. Tune cage extrusion (start ~0.01 m) and click bake.

### Progress and cancellation

The bake runs as a **modal operator**. Progress appears in the window header (`[3/12] Baking NORMAL for Chair ...`), in the window-manager progress bar, and in the system console. Press **ESC at any time** to cancel — work-in-progress materials are restored.

## Recommended settings by object size

Target ≈ **1024 texels per metre** for standard PC/console quality. Hero/closeup assets can go higher; tileable surfaces can go lower.

| Object | Approx size | Resolution | Samples | UV margin | Notes |
|---|---|---|---|---|---|
| Small prop (mug, book, frame, lamp) | < 0.5 m | **512** or 1024 | 8 (16 with AO) | 8 px | Small surface — high res is wasted |
| Chair, stool, side table | 0.5 – 1 m | **1024** | 8 (32 with AO) | 16 px | Sweet spot for furniture detail |
| Sofa, bed, dining table, cabinet | 1.5 – 3 m | **2048** | 16 (32 with AO) | 16 px | Hero furniture quality |
| Door, window, pillar, decorative trim | 1 – 3 m | **2048** | 16 | 16 px | Often viewed close |
| Wall section (non-tileable, baked unique) | 3 – 4 m | **2048** | 16 | 24 px | Per-wall unique textures |
| Wall (tileable / trim-sheet) | any | **1024** | 8 | 16 px | Tiles in-engine — lower res fine |
| Floor / ceiling tile (tileable) | 1 – 2 m | **1024** | 8 | 16 px | Same reason |
| Entire room / facade (single mesh) | 5 m+ | **4096** | 32 | 32 px | Reconsider whether to split |
| Hero / closeup beauty asset | any | **4096** | 32 – 64 | 32 px | Quality > efficiency |
| Terrain / landscape mesh | very large | **4096** tiled | 32+ | 32 px | Always tile in-engine |

**Avoid 8192** unless you genuinely need it — file sizes balloon (≈ 64 MB per PNG per map), bake times multiply 4× over 4K, and on a 12 GB GPU you can easily run out of VRAM and crash the GPU driver.

### Samples cheatsheet

Per-map type, not per resolution:

| Map | Samples needed | Why |
|---|---|---|
| Base Color | **1 – 4** | Direct texture/colour read, no ray tracing |
| Roughness | **1 – 4** | Same |
| Metallic | **1 – 4** | Same |
| Normal | **1 – 4** | Same |
| Alpha | **1 – 4** | Same |
| Emission | **8 – 16** | Slight bounce contribution |
| **AO** | **32 – 128** | Ray-traced — low samples = grainy |

Since the addon bakes all maps with one sample count, pick based on the **most expensive map** you have enabled:
- No AO enabled → **8 samples** is plenty
- With AO at 1K–2K → **32 samples**
- With AO at 4K or hero quality → **64 samples**

### UV margin

Roughly `texture_size / 128`. Too small = visible seams. Too large = wasted texels.

| Resolution | Margin |
|---|---|
| 512 | 4 – 8 px |
| 1024 | 8 – 16 px |
| 2048 | 16 – 24 px |
| 4096 | 24 – 32 px |
| 8192 | 48 – 64 px |

### Memory & crash safety

A 4K PBR set (BaseColor + Normal + ORM + Emission + Alpha) is ~67 MB per texture × 5 ≈ **335 MB on disk per object**, and several GB of VRAM transient during bake. On consumer GPUs (8–12 GB VRAM), 8K bakes commonly crash the GPU driver. If Blender locks up at 4K+, drop to 2K and re-bake the offending object only.

## Notes

- The engine is automatically switched to **Cycles** for the bake and restored afterward.
- Metallic and Alpha are baked through an Emission shader trick (Cycles has no direct bake type for these scalar inputs).
- AO is baked as a screen-space approximation by Cycles' AO bake type. For higher quality, increase samples.
- Normal maps are baked in **Tangent space**, **+X +Y +Z** — the convention Unreal/Unity expect.
- For Unreal Engine: in the UE Texture Editor set the ORM texture's compression to `Masks (no sRGB)` so the AO/R/M channels stay linear.

## License

GPL-3.0-or-later (matches Blender).
