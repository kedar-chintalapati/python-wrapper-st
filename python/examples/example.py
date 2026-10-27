"""The walkthrough from the README. Run it with: python python/examples/example.py"""

from dataclasses import replace

from lost_api import (
    AttitudeConfig,
    Camera,
    CentroidConfig,
    GenerationConfig,
    LostRunner,
    StarIdConfig,
)

camera = Camera.from_fov(20)
scene = GenerationConfig(
    x_resolution=512,
    y_resolution=512,
    ra_deg=88,
    de_deg=7,
    roll_deg=0,
    false_stars=20,
    seed=7,
)
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
    runs = lost.run_generated_many(
        [replace(scene, read_noise_stddev=n) for n in noise_levels],
        camera,
        max_workers=3,
        **stages,
    )
    for noise, run in zip(noise_levels, runs):
        print(f"noise {noise}: {len(run.identified_stars)} stars identified, "
              f"error {run.attitude_error_deg} deg")
