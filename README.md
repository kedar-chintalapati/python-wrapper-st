# LOST Python API

A Python interface to [LOST](https://github.com/UWCubeSat/lost), the Husky Satellite Lab's open-source
star tracker. Use it to generate synthetic star images and to run the star tracker (centroiding, star
identification, attitude estimation) on generated or real images.

## How it works

- LOST is compiled once into a single program, `./lost`.
- Each Python call builds a `lost` command from your settings, runs it, and reads LOST's output files
  back into Python objects. You never call C++ directly.
- You only recompile when the C++ code in `src/` changes. Python changes take effect immediately.

[COMMANDS.md](COMMANDS.md) lists the exact `lost` command behind each Python function.

## Setup

Needs Linux or WSL with Python 3.10+.

```bash
sudo apt install g++ make groff xxd libcairo2-dev libeigen3-dev python3-venv
git clone https://github.com/kedar-chintalapati/python-wrapper-st.git
cd python-wrapper-st
make release                       # builds ./lost
python3 -m venv .venv              # a Python environment for this repo
source .venv/bin/activate
pip install -e '.[images]'         # installs lost_api, plus numpy and Pillow
```

`[images]` is optional. Without it the API still works and images come back as `GrayImage` objects
instead of numpy arrays.

## Writing and running a script

1. Save your script anywhere, for example `~/star-tests/run.py`. It does not need to be inside this
   repo. `pip install -e` makes `lost_api` importable everywhere and it finds `./lost` in this repo
   by itself.
2. Open a terminal and activate the environment (once per terminal):
   ```bash
   source ~/python-wrapper-st/.venv/bin/activate
   ```
3. Run it:
   ```bash
   python ~/star-tests/run.py
   ```

Relative `output_dir` paths are relative to the folder you run the script from. Results without an
`output_dir` go to a temporary folder that is deleted when the `with LostRunner()` block ends.

If you skipped `pip install`, run scripts with `PYTHONPATH=~/python-wrapper-st/python python3 run.py`
instead. If `./lost` lives somewhere else, set `LOST_BINARY=/path/to/lost` and
`LOST_BSC_PATH=/path/to/bright-star-catalog.tsv`.

## Example

This is [python/examples/example.py](python/examples/example.py). Run it with
`python python/examples/example.py`.

```python
from dataclasses import replace

from lost_api import (AttitudeConfig, Camera, CentroidConfig, GenerationConfig, LostRunner,
                      StarIdConfig)

camera = Camera.from_fov(20)
scene = GenerationConfig(x_resolution=512, y_resolution=512, ra_deg=88, de_deg=7, roll_deg=0,
                         false_stars=20, seed=7)
stages = dict(centroid=CentroidConfig(), star_id=StarIdConfig(), attitude=AttitudeConfig())

with LostRunner() as lost:
    # 1. Generate an image and read its ground truth.
    truth = lost.generate(scene, camera, output_dir="example-output", plots=True)
    print("image:", truth.image_path)
    print("true attitude:", truth.expected_attitude)
    print("stars in frame:", len(truth.expected_centroids))

    # 2. Run the star tracker on the same scene and score it against the truth.
    result = lost.run_generated(scene, camera, **stages)
    print("centroids found:", len(result.actual_centroids))
    print("stars identified:", len(result.identified_stars))
    print("estimated attitude:", result.attitude)
    print("attitude error (deg):", result.attitude_error_deg)

    # 3. Treat the generated PNG as a real photo.
    photo = lost.identify(truth.image_path, camera)
    print("attitude from the PNG:", photo.attitude)

    # 4. Sweep read noise, one image per setting, three at a time.
    noise_levels = [0.0, 0.1, 0.2]
    runs = lost.run_generated_many([replace(scene, read_noise_stddev=n) for n in noise_levels],
                                   camera, max_workers=3, **stages)
    for noise, run in zip(noise_levels, runs):
        print(f"noise {noise}: {len(run.identified_stars)} stars identified, "
              f"error {run.attitude_error_deg} deg")
```

Output (shortened):

```
image: /home/you/example-output/raw_input.png
true attitude: Attitude(ra_deg=88.0, de_deg=7.0, roll_deg=0.0, quaternion=(...))
stars in frame: 155
centroids found: 6
stars identified: 5
estimated attitude: Attitude(ra_deg=88.00046, de_deg=6.99366, roll_deg=0.08425, quaternion=(...))
attitude error (deg): 0.0844
attitude from the PNG: Attitude(ra_deg=88.00045, de_deg=6.99367, roll_deg=0.08414, quaternion=(...))
noise 0.0: 33 stars identified, error 0.0054 deg
noise 0.1: 4 stars identified, error 0.134 deg
noise 0.2: 0 stars identified, error None deg
```

What happens:

1. `generate` renders one image. `example-output/` gets `raw_input.png`, annotated `input.png` and
   `expected.png`, and LOST's text files with the true centroids and attitude.
2. `run_generated` renders the same scene (same seed) and runs all three stages on it. Because LOST
   knows the truth for generated images, the result includes the error.
3. `identify` runs the stages on a PNG file, the same as it would for a real camera image.
4. `run_generated_many` runs one image per config, in parallel. `replace` copies a config with one
   field changed.

## Reference

Everything is imported from `lost_api`. All config objects are immutable and check their values when
created, raising `LostConfigError` for bad values. Use `dataclasses.replace(config, field=value)` to
make variants.

### LostRunner

```python
LostRunner(binary=None, *, repo_root=None, catalog_path=None, auto_build=True, work_dir=None,
           cache_dir=None, timeout=None, default_database=None)
```

| Parameter | Meaning |
|---|---|
| `binary` | Path to `lost`. Default: `$LOST_BINARY`, else `./lost` in this repo, else `lost` on `PATH`. |
| `repo_root` | LOST checkout to build in. Default: `$LOST_REPO_ROOT`, else this repo. |
| `catalog_path` | Star catalog. Default: `$LOST_BSC_PATH`, else `bright-star-catalog.tsv` in the repo. |
| `auto_build` | Run `make release` if `./lost` is missing. |
| `work_dir` | Where results without an `output_dir` go. Default: a temporary folder. |
| `cache_dir` | Where built databases are cached. Default: `~/.cache/lost_api`. |
| `timeout` | Seconds before a LOST run is stopped. Default: no limit. |
| `default_database` | `DatabaseConfig` used when `StarIdConfig(database=None)`. |

Use it as `with LostRunner() as lost:`. Leaving the block calls `lost.close()`, which deletes the
temporary `work_dir`. Folders you passed as `output_dir` are kept.

Every method below except the two database methods also takes:

| Parameter | Meaning |
|---|---|
| `output_dir` | Folder for this run's files. Default: a new folder inside `work_dir`. |
| `plots` | Also save annotated PNGs (listed in `result.plots`). Not available for batches. |
| `timeout` | Overrides the runner's timeout for this call. |

#### Methods

| Method | What it does | Returns |
|---|---|---|
| `generate(generation=None, camera=None)` | Renders one image and reports its truth. | `PipelineResult` |
| `run_generated(generation=None, camera=None, *, centroid=None, star_id=None, attitude=None, comparison=None)` | Renders one image and runs the stages you pass, scored against the truth. | `PipelineResult` |
| `run_image(image, camera=None, *, centroid=None, star_id=None, attitude=None)` | Runs the stages you pass on a real image. `centroid` is required. | `PipelineResult` |
| `identify(image, camera=None, *, centroid=None, star_id=None, attitude=None)` | `run_image` with all three stages. Stages you leave out use their default config. | `PipelineResult` |
| `run_generated_many(generations, camera=None, *, centroid=None, star_id=None, attitude=None, comparison=None, max_workers=1, return_exceptions=False)` | `run_generated` for each config in the list. `max_workers` runs that many at once (`None` means one per CPU). With `return_exceptions=True` a failed image gives its error in the list instead of stopping. Each image gets its own subfolder of `output_dir`. | list of `PipelineResult` |
| `run_generated_batch(num_images, generation=None, camera=None, *, centroid=None, star_id=None, attitude=None, comparison=None)` | Renders `num_images` images in one LOST run. Faster, but only summary scores come back. Set `random_attitudes=True` to vary the attitude. | `BatchResult` |
| `database(config=None)` | Builds a star-ID database, or reuses the cached one. | `DatabaseResult` |
| `build_database(config=None, output_path=None)` | Builds a database at `output_path` (or cached, if no path). | `DatabaseResult` |

`image` can be a PNG path, a `GrayImage`, or a 2-D `uint8` numpy array.

Stages left as `None` are skipped. On generated images you can start partway through:

- `star_id` without `centroid` identifies the generated centroids directly. With
  `GenerationConfig(centroids_only=True)` no pixels are rendered, which is the fast way to test star
  identification alone.
- `attitude` without `star_id` or `centroid` uses the true star identities.

### Camera

Set either `fov_deg`, or both `focal_length_mm` and `pixel_size_um`. With nothing set, the field of
view is 20 degrees.

| Field | Meaning |
|---|---|
| `fov_deg` | Horizontal field of view, in degrees. |
| `focal_length_mm` | Lens focal length, in mm. |
| `pixel_size_um` | Pixel side length, in micrometers. |

Shortcuts: `Camera.from_fov(20)` and `Camera.from_optics(focal_length_mm, pixel_size_um)`.

### GenerationConfig

Settings for generated images. Defaults are LOST's defaults. Magnitudes are astronomical: smaller is
brighter.

| Field | Default | Meaning |
|---|---|---|
| `x_resolution`, `y_resolution` | 1024, 1024 | Image size in pixels. |
| `ra_deg`, `de_deg`, `roll_deg` | 88, 7, 0 | Camera attitude: right ascension, declination, roll. |
| `random_attitudes` | False | Use a random attitude (chosen from `seed`) instead. |
| `seed` | 394859 | Random seed for noise and random attitudes. Seeds 0 and 1 give the same image. |
| `time_based_seed` | False | Use the clock as the seed. |
| `zero_mag_photons` | 20000 | Photons per time unit from a magnitude 0 star. Raise it for brighter stars. |
| `saturation_photons` | 150 | Photons at which a pixel is fully white. |
| `exposure` | 0.2 | Exposure time. Affects brightness and motion blur. |
| `spread_stddev` | 1.0 | Star blur radius (Gaussian standard deviation, pixels). |
| `oversampling` | 4 | Samples per pixel (antialiasing). Must be a perfect square. |
| `shot_noise` | True | Simulate photon shot noise. |
| `dark_current` | 0.1 | Dark current brightness, 0 (none) to 1 (white). |
| `read_noise_stddev` | 0.05 | Read noise, as a fraction of full brightness. |
| `blur_ra_deg`, `blur_de_deg`, `blur_roll_deg` | 0, 0, 0 | Motion blur direction. |
| `readout_time` | 0 | Rolling shutter readout time. 0 turns it off. |
| `false_stars` | 0 | Number of fake stars placed randomly on the sky. |
| `false_min_mag`, `false_max_mag` | 8.0, 1.0 | Dimmest and brightest fake star magnitude. |
| `cutoff_mag` | 6.0 | Magnitude with a 50% chance of being included in the input centroids. 100 includes all. |
| `perturb_centroids_stddev` | 0 | Random error added to the input centroids, in pixels. |
| `centroids_only` | False | Produce centroids but no image. |
| `interesting_threshold` | None (0.05) | Photon level below which a star's faint edge is not drawn. Lower is more accurate for very bright stars, but slower. |
| `extra_options` | {} | Extra `lost` flags passed through as-is, for LOST forks, e.g. `{"generate-obstruct": 2}`. |

At the default brightness only the brightest few stars are detectable in most random fields. Raise
`zero_mag_photons` or `exposure` for more.

### Stage configs

| Class | Field | Default | Meaning |
|---|---|---|---|
| `CentroidConfig` | `algorithm` | `"cog"` | `"cog"` (center of gravity), `"iwcog"` (iterative weighted), `"dummy"` (random, for testing). |
| | `magnitude_filter` | None | Drop centroids whose LOST brightness value (higher is brighter) is below this. |
| | `brightest_only` | None | Keep only this many of the brightest centroids. |
| | `dummy_stars` | None (5) | Number of centroids the `dummy` algorithm makes. |
| `StarIdConfig` | `algorithm` | `"py"` | `"py"` (pyramid), `"gv"` (geometric voting), `"dummy"`. |
| | `database` | None | A `.dat` path, a `DatabaseConfig`, or `None` for the runner's default. Configs are built once and cached. |
| | `angular_tolerance_deg` | 0.04 | Expected centroid error, in degrees. |
| | `false_stars_estimate` | 500 | Expected number of false stars. |
| | `max_mismatch_probability` | 0.001 | Highest allowed chance of a wrong identification. |
| `AttitudeConfig` | `algorithm` | `"dqm"` | `"dqm"` (Davenport Q), `"triad"`, `"quest"`. |
| `ComparisonConfig` | `centroid_threshold_px` | 2.0 | A found centroid this close to a true one counts as correct. |
| | `attitude_threshold_deg` | 1.0 | An attitude this close to the truth counts as correct. |

### DatabaseConfig

The star identification database. Defaults follow LOST's README.

| Field | Default | Meaning |
|---|---|---|
| `max_stars` | 5000 | Keep this many of the brightest catalog stars. |
| `min_mag` | None | Drop stars dimmer than this magnitude. None keeps all. |
| `min_separation_deg` | 0.08 | Drop stars closer than this to another star. |
| `kvector_min_distance_deg` | 0.2 | Smallest star pair distance stored. |
| `kvector_max_distance_deg` | 15.0 | Largest star pair distance stored. Keep it near your field of view. |
| `kvector_distance_bins` | 10000 | Number of distance bins. |
| `swap_integer_endianness`, `swap_decimal_endianness` | False | Write the database for a machine with the other byte order. |

### Results

`PipelineResult` (one image):

| Field | Meaning |
|---|---|
| `attitude` | Estimated `Attitude`, or `None` if it was not found. |
| `expected_attitude` | True `Attitude` (generated images). |
| `attitude_error_deg` | Angle between the two, in degrees. |
| `actual_centroids` | Centroids the pipeline found, as `Centroid` objects. |
| `identified_stars` | The actual centroids that were matched to a catalog star. |
| `input_centroids` | Generated centroids given to star ID (generated images). |
| `expected_centroids` | Every catalog star in the frame (generated images). |
| `centroid_comparison` | `num_correct`, `num_extra`, `mean_error_px`. |
| `star_id_comparison` | `num_correct`, `num_incorrect`, `num_total`, `num_images_correct`, `num_images_incorrect`. |
| `attitude_comparison` | `mean_error_deg`, `availability` (fraction correct), `error_rate` (fraction wrong). |
| `timing` | Per stage (`"centroiding"`, `"starid"`, `"attitude"`, `"total"`): `average_ns`, `min_ns`, `max_ns`, `p95_ns`, `average_ms`. |
| `image_path` | The generated PNG, or the input image. |
| `plots` | Annotated PNGs by name, when `plots=True`. |
| `output_dir` | Folder with LOST's raw output files. |
| `metrics` | Every raw value LOST printed. |
| `command_line` | The `lost` command that ran. Paste it in a terminal to rerun. |
| `load_image()` | The image as a numpy array (or a `GrayImage` without numpy). |
| `load_gray_image()` | The image as a `GrayImage`. |

`BatchResult` (from `run_generated_batch`) has `num_images`, `centroid_comparison`,
`star_id_comparison`, `attitude_comparison`, `timing`, `metrics`, and the average centroid counts
`mean_num_expected_centroids`, `mean_num_input_centroids`, `mean_num_actual_centroids`.

`Centroid`: `x`, `y` (pixels from the top left), `catalog_id` (catalog star number, or `None`),
`expected_catalog_ids` (correct matches, generated images), `correctly_identified`.

`Attitude`: `ra_deg`, `de_deg`, `roll_deg`, `quaternion` as `(real, i, j, k)`, and
`angle_to(other)` in degrees.

`DatabaseResult`: `path`, `config`, `cached`, `size_bytes`.

### Images

| Function | What it does |
|---|---|
| `read_png(path, lost_view=False)` | Reads a PNG as a `GrayImage`. `lost_view=True` gives exactly what LOST sees, which is about 1% darker because of LOST's gray conversion. |
| `write_png(path, image)` | Saves a `GrayImage` or 2-D `uint8` array as a PNG. |
| `GrayImage(width, height, pixels)` | An 8-bit grayscale image. `image[y, x]`, `.shape`, `.to_numpy()`, `GrayImage.from_array(array)`. |

### Errors

All errors are subclasses of `LostError`.

| Error | When |
|---|---|
| `LostConfigError` | A bad setting or stage combination. Raised before LOST runs. |
| `LostBinaryNotFoundError` | `lost` or the star catalog was not found. |
| `LostBuildError` | `make release` failed. |
| `LostExecutionError` | LOST failed. Has `command`, `returncode`, `stdout`, `stderr`, `diagnostics`, and `crashed`. |
| `LostUnsupportedOptionError` | The `lost` program does not have a flag you used. `.option` names it. |
| `LostTimeoutError` | A run went past its timeout. |

`LostWarning` is a warning (not an error) for a database that does not suit the camera's field of view.

## Changes to LOST

This repo is LOST plus the `python/` folder and a few small C++ changes the API relies on. Use the
`lost` built here, not a stock LOST build.

- Text outputs print full precision instead of 6 digits.
- `--print-actual-centroids` writes each centroid's expected ID to its file instead of the terminal.
- `--print-actual-centroids` also works when star ID runs on generated centroids.
- New option `--generate-interesting-threshold`.

## Tests

```bash
python -m unittest discover -s python/tests -t python/tests
```
