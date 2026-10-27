# Python functions and the LOST commands they run

Each call runs one `lost` command (except `run_generated_many`, which runs one per image). Any
result's `command_line` shows the exact command. `DIR` below is the run's output folder.

## Functions

### `database(config)` / `build_database(config, path)`

```
lost database --kvector [DatabaseConfig flags] --output PATH
```

`database()` writes to `~/.cache/lost_api/databases/` and reuses the file next time.

### `generate(generation, camera)`

```
lost pipeline [Camera flags] --generate 1 [GenerationConfig flags] [ComparisonConfig flags]
  --plot-raw-input DIR/raw_input.png
  --print-input-centroids DIR/input_centroids.txt
  --print-expected-centroids DIR/expected_centroids.txt
  --print-expected-attitude DIR/expected_attitude.txt
```

`plots=True` adds `--plot-input DIR/input.png --plot-expected DIR/expected.png`.
With `centroids_only=True`, `--plot-raw-input` is left out.

### `run_generated(generation, camera, centroid, star_id, attitude, comparison)`

The `generate` command, plus the flags for each stage you pass and their outputs:

| Stage passed | Added flags |
|---|---|
| `centroid` | `[CentroidConfig flags] --print-actual-centroids DIR/actual_centroids.txt --compare-centroids DIR/compare_centroids.txt` |
| `star_id` | `--database DB [StarIdConfig flags] --print-actual-centroids DIR/actual_centroids.txt --compare-star-ids DIR/compare_star_ids.txt` |
| `attitude` | `[AttitudeConfig flags] --print-attitude DIR/attitude.txt --compare-attitudes DIR/compare_attitudes.txt` |
| any stage | `--print-speed DIR/speed.txt` |

With `plots=True` and a `centroid` or `star_id` stage, it also adds
`--plot-output DIR/output.png --plot-centroid-indices DIR/centroid_indices.png`.

### `run_image(image, camera, centroid, star_id, attitude)` and `identify(...)`

```
lost pipeline --png IMAGE [Camera flags] [stage flags]
  --print-actual-centroids DIR/actual_centroids.txt
  --print-attitude DIR/attitude.txt          (with attitude)
  --print-speed DIR/speed.txt
```

There are no `--compare-*` or `expected` outputs, since a real image has no known truth. Arrays
and `GrayImage`s are first saved as `DIR/input_image.png`. `plots=True` adds `--plot-output` and
`--plot-centroid-indices`. `identify` is `run_image` with all three stages.

### `run_generated_many(generations, ...)`

Runs the `run_generated` command once per config, each in `DIR/image_0000`, `DIR/image_0001`, and
so on.

### `run_generated_batch(num_images, generation, ...)`

```
lost pipeline [Camera flags] --generate NUM_IMAGES [GenerationConfig flags] [stage flags]
  [ComparisonConfig flags]
  --print-input-centroids DIR/input_centroids.txt
  --print-expected-centroids DIR/expected_centroids.txt
  --print-actual-centroids DIR/actual_centroids.txt     (with centroid or star_id)
  --compare-centroids / --compare-star-ids / --compare-attitudes   (per stage)
  --print-speed DIR/speed.txt
```

With more than one image, LOST prints only averages and totals, so no per-image data comes back.

## Config fields and their flags

Every flag is always passed with its value, except fields set to `None`, which are left out so
LOST uses its own default.

### Camera

| Field | Flag |
|---|---|
| `fov_deg` | `--fov` |
| `focal_length_mm` | `--focal-length` |
| `pixel_size_um` | `--pixel-size` |

### GenerationConfig

| Field | Flag |
|---|---|
| `x_resolution` | `--generate-x-resolution` |
| `y_resolution` | `--generate-y-resolution` |
| `centroids_only` | `--generate-centroids-only` |
| `zero_mag_photons` | `--generate-zero-mag-photons` |
| `saturation_photons` | `--generate-saturation-photons` |
| `spread_stddev` | `--generate-spread-stddev` |
| `shot_noise` | `--generate-shot-noise` |
| `dark_current` | `--generate-dark-current` |
| `read_noise_stddev` | `--generate-read-noise-stddev` |
| `ra_deg` | `--generate-ra` |
| `de_deg` | `--generate-de` |
| `roll_deg` | `--generate-roll` |
| `random_attitudes` | `--generate-random-attitudes` |
| `blur_ra_deg` | `--generate-blur-ra` |
| `blur_de_deg` | `--generate-blur-de` |
| `blur_roll_deg` | `--generate-blur-roll` |
| `exposure` | `--generate-exposure` |
| `readout_time` | `--generate-readout-time` |
| `oversampling` | `--generate-oversampling` |
| `false_stars` | `--generate-false-stars` |
| `false_min_mag` | `--generate-false-min-mag` |
| `false_max_mag` | `--generate-false-max-mag` |
| `perturb_centroids_stddev` | `--generate-perturb-centroids` |
| `cutoff_mag` | `--generate-cutoff-mag` |
| `seed` | `--generate-seed` |
| `time_based_seed` | `--generate-time-based-seed` |
| `interesting_threshold` | `--generate-interesting-threshold` |
| `extra_options` | each key becomes `--key value` |

### Stages and comparison

| Field | Flag |
|---|---|
| `CentroidConfig.algorithm` | `--centroid-algo` |
| `CentroidConfig.dummy_stars` | `--centroid-dummy-stars` |
| `CentroidConfig.magnitude_filter` | `--centroid-mag-filter` |
| `CentroidConfig.brightest_only` | `--centroid-filter-brightest` |
| `StarIdConfig.database` | `--database` (resolved to a file path) |
| `StarIdConfig.algorithm` | `--star-id-algo` |
| `StarIdConfig.angular_tolerance_deg` | `--angular-tolerance` |
| `StarIdConfig.false_stars_estimate` | `--false-stars-estimate` |
| `StarIdConfig.max_mismatch_probability` | `--max-mismatch-probability` |
| `AttitudeConfig.algorithm` | `--attitude-algo` |
| `ComparisonConfig.centroid_threshold_px` | `--centroid-compare-threshold` |
| `ComparisonConfig.attitude_threshold_deg` | `--attitude-compare-threshold` |

### DatabaseConfig

| Field | Flag |
|---|---|
| `max_stars` | `--max-stars` |
| `min_mag` | `--min-mag` |
| `min_separation_deg` | `--min-separation` |
| `kvector_min_distance_deg` | `--kvector-min-distance` |
| `kvector_max_distance_deg` | `--kvector-max-distance` |
| `kvector_distance_bins` | `--kvector-distance-bins` |
| `swap_integer_endianness` | `--swap-integer-endianness` |
| `swap_decimal_endianness` | `--swap-decimal-endianness` |

## Output files and result fields

| File | Parsed into |
|---|---|
| `raw_input.png` | `image_path`, `load_image()` |
| `input_centroids.txt` | `input_centroids` |
| `expected_centroids.txt` | `expected_centroids` |
| `expected_attitude.txt` | `expected_attitude` |
| `actual_centroids.txt` | `actual_centroids`, `identified_stars` |
| `attitude.txt` | `attitude` |
| `compare_centroids.txt` | `centroid_comparison` |
| `compare_star_ids.txt` | `star_id_comparison` |
| `compare_attitudes.txt` | `attitude_comparison` |
| `speed.txt` | `timing` |
| other `.png` files | `plots` |

All values from the text files are also in `metrics`.
